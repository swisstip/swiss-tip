"""Capture, replay, attribution and quarantine for assistant exchanges."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from swisstip.concepts.checkpoints import checkpoint_key
from swisstip.concepts.providers.base import IdentityError, InvalidCompletion
from swisstip.concepts.providers.exchange import ExchangeProvider, MissingExchangeResponse, check_exchange
from swisstip.concepts.schemas import basis_schema, extraction_schema, review_schema


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.provider = ExchangeProvider(self.directory)
        self.request = dict(system_prompt="Extract.", user_prompt=json.dumps({"untrusted_page": {
            "document_id": "document-1", "title": "Permits", "chunk_index": 0, "chunk_count": 1,
            "evidence_spans": [{"evidence_id": "b00001:0:5", "section_id": "section-0001", "text": "Text."}]}}),
            response_schema=extraction_schema(6, ["b00001:0:5"], ["section-0001"]))
        self.context = dict(document_id="document-1", content_sha256="a" * 64, profile="assistant_exchange",
                            model=self.provider.model, generation_settings={"temperature": 0.0})
        self.key = checkpoint_key(self.request, self.context)

    def capture(self):
        self.provider.set_request_context(**self.context, key=self.key)
        return self.provider.generate_structured(**self.request)

    def respond(self, content='{"concepts": []}', **extra):
        (self.directory / "responses").mkdir(exist_ok=True)
        path = self.directory / "responses" / f"{self.key}.json"
        response = dict(content=content, observed_model=self.provider.model, **extra)
        path.write_text(json.dumps(response), encoding="utf-8")
        return path

    def test_capture_matches_checkpoint_key_and_metadata(self):
        completion = self.capture()
        self.assertTrue(completion.awaiting_response)
        self.assertIsNone(completion.observed_model)
        self.assertEqual(completion.finish_reason, "awaiting_response")
        self.assertEqual(self.provider.last_request_key, self.key)
        request = json.loads((self.directory / "requests" / f"{self.key}.json").read_text(encoding="utf-8"))
        self.assertEqual(request["schema_version"], "swisstip.assistant-extraction-request/v1")
        self.assertEqual(request["document_id"], "document-1")
        self.assertEqual(request["chunk_index"], 0)
        self.assertEqual(request["kind"], "extraction")
        self.assertEqual(request["model"], self.provider.model)
        self.assertEqual(request["system_prompt"], self.request["system_prompt"])
        self.provider.set_request_context(**self.context)
        self.provider.generate_structured(**self.request)
        self.assertEqual(self.provider.last_request_key, self.key)

    def test_response_replay_has_explicit_identity_and_no_fabricated_usage(self):
        self.capture()
        self.respond()
        completion = self.capture()
        self.assertFalse(completion.awaiting_response)
        self.assertEqual(completion.observed_model, self.provider.model)
        self.assertIsNone(completion.prompt_tokens)
        self.assertEqual(json.loads(completion.content), {"concepts": []})
        self.assertEqual(check_exchange(self.directory)["counts"], {"extraction": 1, "review": 0, "basis": 0, "missing": 0, "invalid": 0})

    def test_missing_fail_captures_then_stops(self):
        self.provider.on_missing = "fail"
        with self.assertRaises(MissingExchangeResponse):
            self.capture()
        self.assertTrue((self.directory / "requests" / f"{self.key}.json").exists())

    def test_missing_or_wrong_model_is_refused(self):
        self.capture()
        path = self.respond()
        for model in (None, "substituted-model"):
            with self.subTest(model=model):
                path.write_text(json.dumps({"content": '{"concepts": []}', "observed_model": model}), encoding="utf-8")
                with self.assertRaises(IdentityError):
                    self.capture()

    def test_invalid_raw_content_is_preserved_for_checkpointing(self):
        self.capture()
        self.respond('{"concepts": [')
        with self.assertRaises(InvalidCompletion) as caught:
            self.capture()
        self.assertEqual(caught.exception.completion.content, '{"concepts": [')

    def test_checker_quarantines_schema_violation_and_keeps_audit(self):
        self.capture()
        path = self.respond('{"concepts": [], "unknown": 1}')
        report = check_exchange(self.directory)
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertFalse(path.exists())
        self.assertTrue(path.with_suffix(".invalid.json").exists())
        self.assertTrue((self.directory / "check-report.json").exists())
        rerun = check_exchange(self.directory)
        self.assertEqual(rerun["counts"]["invalid"], 0)
        self.assertEqual(rerun["counts"]["missing"], 1)

    def test_checker_rejects_response_without_request(self):
        path = self.respond()
        report = check_exchange(self.directory)
        self.assertEqual(report["counts"]["invalid"], 1)
        self.assertFalse(path.exists())

    def test_review_placeholder_and_duplicate_review_ids(self):
        self.request = dict(system_prompt="Review.", user_prompt=json.dumps({"untrusted_review": {"proposals": [{}, {}]}}),
                            response_schema=review_schema(2))
        self.key = checkpoint_key(self.request, self.context)
        self.provider.set_request_context(**self.context, key=self.key, proposal_count=2, kind="review")
        completion = self.provider.generate_structured(**self.request)
        verdicts = json.loads(completion.content)["verdicts"]
        self.assertEqual([v["review_id"] for v in verdicts], [1, 2])
        self.assertEqual({v["decision"] for v in verdicts}, {"uncertain"})
        verdicts[1]["review_id"] = 1
        self.respond(json.dumps({"verdicts": verdicts}))
        self.assertEqual(check_exchange(self.directory)["counts"]["invalid"], 1)

    def test_basis_placeholder_and_checker_refuse_a_norm_kind_without_a_norm(self):
        self.request = dict(system_prompt="Classify.", user_prompt=json.dumps({"untrusted_basis": {"proposals": [{}, {}]}}),
                            response_schema=basis_schema(2))
        self.key = checkpoint_key(self.request, self.context)
        self.provider.set_request_context(**self.context, key=self.key, proposal_count=2)
        completion = self.provider.generate_structured(**self.request)
        self.assertTrue(completion.awaiting_response)
        bases = json.loads(completion.content)["bases"]
        self.assertEqual([b["basis_id"] for b in bases], [1, 2])
        self.assertEqual({b["kind"] for b in bases}, {"guidance"})
        request = json.loads((self.directory / "requests" / f"{self.key}.json").read_text(encoding="utf-8"))
        self.assertEqual((request["kind"], request["proposal_count"]), ("basis", 2))
        bases[0].update(kind="act", norm="")
        self.respond(json.dumps({"bases": bases}))
        self.assertEqual(check_exchange(self.directory)["counts"]["invalid"], 1)
        bases[0].update(norm="AIG, SR 142.20, Art. 12")
        self.respond(json.dumps({"bases": bases}))
        self.assertEqual(check_exchange(self.directory)["counts"], {"extraction": 0, "review": 0, "basis": 1, "missing": 0, "invalid": 0})

    def test_request_context_is_thread_local(self):
        def capture(index):
            context = self.context | {"document_id": f"document-{index}"}
            key = checkpoint_key(self.request, context)
            self.provider.set_request_context(**context, key=key)
            self.provider.generate_structured(**self.request)
            return key, self.provider.last_request_key
        with ThreadPoolExecutor(max_workers=4) as executor:
            keys = list(executor.map(capture, range(12)))
        self.assertEqual(len({key for key, _ in keys}), 12)
        self.assertTrue(all(key == observed for key, observed in keys))
        self.assertEqual(len(list((self.directory / "requests").glob("*.json"))), 12)

    def test_exchange_key_cannot_escape_directory(self):
        self.provider.set_request_context(key="../outside")
        with self.assertRaises(ValueError):
            self.provider.generate_structured(**self.request)


if __name__ == "__main__":
    unittest.main()
