import json
import tempfile
import unittest
from pathlib import Path

from swisstip.extraction.records import attach_offsets
from swisstip.concepts.checkpoints import atomic_write_json, read_json
from swisstip.concepts.concepts_cli import check_output_location, run_extraction
from swisstip.concepts.providers.config import default_config_path
from swisstip.concepts.providers.base import Completion
from swisstip.concepts.providers.config import validate_config
from swisstip.concepts.providers.exchange import check_exchange
from test_core import make_record, proposal
from test_flow import FakeProvider


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.run, self.output = root / "run", root / "concepts"
        self.config = validate_config(dict(schema_version="swisstip.semantic-models/v2", active_profile="fake",
                                           profiles={"fake": dict(adapter="exchange", model="test", provider="fake")}))
        self.providers = []
        self.respond = None
        self.records = []
        self.add_record("first", ["Regel."])

    def add_record(self, document_id, blocks):
        record = make_record(blocks)
        record.update(document_id=document_id, source_url=f"https://example.invalid/{document_id}")
        attach_offsets(record)
        atomic_write_json(self.run / "text" / "documents" / f"{document_id}.json", record)
        self.records.append(dict(document_id=document_id, source_url=record["source_url"],
                                 source_ids=["canton"], eligible_for_processing=True,
                                 preferred_representation=True, attribution_kind="catalogue",
                                 content_sha256=record["content_sha256"], text_characters=len(record["content_text"]), language_declared="de"))
        atomic_write_json(self.run / "text" / "index.json", self.records)

    def factory(self, profile, generation, **kwargs):
        provider = FakeProvider(self.respond)
        self.providers.append(provider)
        return provider

    def invoke(self, **kwargs):
        return run_extraction(self.run, self.output, config=self.config, profile="fake", provider_factory=self.factory, **kwargs)

    def test_dry_run_and_plan_refusal_never_create_provider(self):
        code, plan = self.invoke(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(plan["request_count"], 3)
        self.assertEqual(len(plan["records"][0]["sections"]), 1)
        self.assertEqual(self.providers, [])
        self.config["extraction"].update(max_model_requests_per_page=3, max_model_requests_per_run=3)
        self.add_record("second", ["Zweite Regel."])
        code, plan = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("refused", plan)
        self.assertEqual(self.providers, [])

    def test_report_reuse_force_checkpoint_hits_and_fresh_inference(self):
        code, first = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(first["statistics"]["network_attempts"], 3)
        code, reused = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(reused["plan"]["records"][0]["status"], "reused")
        code, forced = self.invoke(force=True)
        self.assertEqual(code, 0)
        self.assertEqual(forced["statistics"]["checkpoint_hits"], 3)
        self.assertEqual(self.providers[-1].calls, [])
        code, fresh = self.invoke(fresh_inference=True)
        self.assertEqual(code, 0)
        self.assertEqual(fresh["statistics"]["network_attempts"], 3)

    def test_changed_plan_searches_other_job_checkpoints(self):
        self.add_record("second", ["Zweite Regel."])
        code, first = self.invoke(document_ids=["first"])
        code, expanded = self.invoke(force=True)
        self.assertEqual(code, 0)
        self.assertNotEqual(first["job_id"], expanded["job_id"])
        self.assertEqual(expanded["statistics"]["checkpoint_hits"], 3)
        self.assertEqual(expanded["statistics"]["network_attempts"], 3)

    def test_changed_ceiling_preserves_old_job_plan_and_summarizes_reused_reports(self):
        code, first = self.invoke()
        old_plan = read_json(self.output / "jobs" / first["job_id"] / "plan.json")
        self.config["extraction"]["max_model_requests_per_run"] = 500
        code, again = self.invoke()
        self.assertEqual(code, 0)
        self.assertNotEqual(first["job_id"], again["job_id"])
        self.assertEqual(read_json(self.output / "jobs" / first["job_id"] / "plan.json"), old_plan)
        self.assertEqual(again["statistics"]["network_attempts"], 0)
        self.assertEqual(again["candidates"], 1)
        self.assertEqual(len(again["consolidated_concepts"]), 1)

    def test_retry_failed_preserves_successful_chunks(self):
        self.add_record("multiple", ["Regel " * 60] * 2)
        self.config["extraction"].update(chunk_content_characters=500, chunk_overlap_characters=100)

        def invalid_first(payload, provider):
            if provider.context["document_id"] == "multiple" and provider.context["chunk_index"] == 1:
                return Completion("not json", "fake", "test", observed_model="test", finish_reason="stop")
            if "untrusted_page" in payload:
                return {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
            return provider.support(payload)
        self.respond = invalid_first
        code, failed = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(failed["failed_reports"], 1)
        self.respond = None
        code, retried = self.invoke(retry_failed=True)
        self.assertEqual(code, 0)
        self.assertEqual(retried["statistics"]["network_attempts"], 3)
        self.assertEqual(retried["statistics"]["checkpoint_hits"], 3)
        self.assertEqual(retried["plan"]["records"][0]["reason"], "retry_failed_only")

    def test_truncated_review_and_children_all_resume_without_calls(self):
        def split(payload, provider):
            if "untrusted_page" in payload:
                span = payload["untrusted_page"]["evidence_spans"][0]
                return {"concepts": [proposal(span, preferred_label=f"Begriff {i}") for i in range(5)]}
            if "untrusted_review" in payload and len(payload["untrusted_review"]["proposals"]) > 2:
                return Completion("truncated", "fake", "test", observed_model="test", finish_reason="length")
            return provider.support(payload)
        self.respond = split
        code, first = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(first["statistics"]["review_fallbacks"], 2)
        code, again = self.invoke(force=True)
        self.assertEqual(code, 0)
        self.assertEqual(again["statistics"]["network_attempts"], 0)
        self.assertEqual(again["statistics"]["checkpoint_hits"], 7)
        self.assertEqual(self.providers[-1].calls, [])

    def test_exchange_capture_check_and_two_stage_resume(self):
        def exchange_run():
            return run_extraction(self.run, self.output, config=self.config, profile="fake")
        code, captured = exchange_run()
        self.assertEqual(code, 1)
        exchange = self.output / "jobs" / captured["job_id"] / "exchange"

        def answer_requests():
            for request_path in (exchange / "requests").glob("*.json"):
                request = read_json(request_path)
                payload = json.loads(request["user_prompt"])
                if request["kind"] == "extraction":
                    content = {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
                else:
                    content = FakeProvider.support(payload)
                    self.assertEqual(request["kind"], "basis" if "untrusted_basis" in payload else "review")
                atomic_write_json(exchange / "responses" / request_path.name,
                                  dict(key=request["key"], model="test", provider="fake", content=json.dumps(content)))

        answer_requests()
        self.assertFalse(check_exchange(exchange)["invalid"])
        code, second = exchange_run()
        self.assertEqual(code, 1)
        self.assertEqual(len(list((exchange / "requests").glob("*.json"))), 2)
        answer_requests()
        self.assertFalse(check_exchange(exchange)["invalid"])
        code, third = exchange_run()
        self.assertEqual(code, 1)
        self.assertEqual(len(list((exchange / "requests").glob("*.json"))), 3)
        answer_requests()
        self.assertEqual(check_exchange(exchange)["counts"], {"extraction": 1, "review": 1, "basis": 1, "missing": 0, "invalid": 0})
        code, final = exchange_run()
        self.assertEqual(code, 0)
        self.assertEqual(final["candidates"], 1)
        code, again = exchange_run()
        self.assertEqual(code, 0)
        self.assertEqual(again["statistics"]["network_attempts"], 0)

    def test_unsafe_output_paths_rejected(self):
        # The packs of the repository the package is installed from, found the way the function finds them; the
        # folder need not exist.
        committed_pack = default_config_path().parent.parent / "releases" / "swiss-residence" / "concepts"
        for output in (self.run, self.run / "text" / "concepts", self.run / "pages", self.run / "plugin-documents", committed_pack):
            with self.subTest(output=output), self.assertRaises(ValueError):
                check_output_location(self.run, output)
