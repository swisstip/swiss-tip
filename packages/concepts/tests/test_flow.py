import hashlib
import json
import unittest

from swisstip.concepts.extract import canonical_json, extract_record
from swisstip.concepts.providers.base import Completion, ProviderError, ProviderResponseError
from test_core import make_record, proposal


class FakeProvider:
    def __init__(self, respond=None):
        self.calls, self.invalid = [], []
        self.respond = respond
        self.last_request_key = None
        self.context = {}
        self.fallbacks = 0

    def set_request_context(self, **context):
        self.context = context

    def mark_invalid(self, key, reason):
        self.invalid.append((key, reason))

    def record_review_fallback(self):
        self.fallbacks += 1

    def generate_structured(self, **request):
        self.calls.append(request)
        self.last_request_key = str(len(self.calls))
        payload = json.loads(request["user_prompt"])
        if self.respond:
            result = self.respond(payload, self)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, Completion):
                return result
        elif "untrusted_page" in payload:
            result = {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
        else:
            result = self.support(payload)
        return Completion(content=json.dumps(result), provider="fake", model="test", requested_model="test",
                          observed_model="test", prompt_tokens=20, output_tokens=10,
                          request_id=self.last_request_key, finish_reason="stop")

    @staticmethod
    def support(payload):
        if "untrusted_basis" in payload:
            return FakeProvider.classify(payload)
        return {"verdicts": [dict(review_id=item["review_id"], decision="supported", issue="none", reason="Belegt.")
                             for item in payload["untrusted_review"]["proposals"]]}

    @staticmethod
    def classify(payload):
        return {"bases": [dict(basis_id=item["basis_id"], kind="guidance", level="cantonal", norm="", refers_to="",
                               reason="The page explains the procedure in its own words.")
                          for item in payload["untrusted_basis"]["proposals"]]}


class FlowTests(unittest.TestCase):
    def test_candidate_identity_matches_predecessor_and_preserves_sharp_s(self):
        record = make_record(["Eine Regel."])

        def respond(payload, provider):
            if "untrusted_page" in payload:
                span = payload["untrusted_page"]["evidence_spans"][0]
                return {"concepts": [proposal(span, preferred_label="Straße"), proposal(span, preferred_label="Strasse")]}
            return provider.support(payload)
        report = extract_record(record, FakeProvider(respond))
        self.assertEqual(len(report["candidates"]), 2)
        candidate = report["candidates"][0]
        identity = (f"{record['content_sha256']}\nstraße\nzuziehende personen\nPROCESS\nANSWERABLE\n"
                    "Personen melden sich bei der Gemeinde an.\nsection-0001")
        expected = "candidate-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(candidate["candidate_id"], expected)

    def test_merges_across_chunks_preserves_conflict_and_review_rejection(self):
        record = make_record(["Regel gilt. " * 25] * 3)

        def respond(payload, provider):
            if "untrusted_page" in payload:
                page = payload["untrusted_page"]
                span = page["evidence_spans"][0]
                proposals = [proposal(span, alternative_labels=[f"Alias {page['chunk_index']}"],
                                      confidence=page["chunk_index"] / 4)]
                if page["chunk_index"] == 2:
                    proposals.append(proposal(span, description="Eine abweichende Aussage."))
                    proposals.append(proposal(span, preferred_label="Nicht belegt"))
                return {"concepts": proposals}
            result = provider.support(payload)
            if "verdicts" in result and len(result["verdicts"]) == 3:
                result["verdicts"][-1].update(decision="unsupported", issue="unsupported_claim", reason="Nicht belegt.")
            return result

        provider = FakeProvider(respond)
        report = extract_record(record, provider, settings=dict(chunk_content_characters=500, chunk_overlap_characters=100))
        self.assertEqual(report["request_count"], 9)
        self.assertFalse(report["failures"])
        self.assertEqual(len(report["candidates"]), 2)
        self.assertEqual(len(report["candidates"][0]["evidence"]), 3)
        self.assertEqual(report["candidates"][0]["confidence"], 0.75)
        self.assertEqual(report["candidates"][0]["alternative_labels"], ["Alias 1", "Alias 2", "Alias 3"])
        self.assertNotEqual(report["candidates"][0]["candidate_id"], report["candidates"][1]["candidate_id"])
        self.assertEqual(report["rejected"][0]["stage"], "review")
        self.assertEqual(report["quality_metrics"]["semantic_rejections"], 1)
        self.assertEqual(report["quality_metrics"]["sections_cited"], 1)
        self.assertEqual(report["prompt_tokens"], 180)
        self.assertEqual(report["quality_metrics"]["basis_request_count"], 3)
        self.assertEqual(report["candidates"][0]["basis"]["kind"], "guidance")
        self.assertEqual(report["output_sha256"], hashlib.sha256(canonical_json(report["candidates"]).encode()).hexdigest())
        again = extract_record(record, FakeProvider(respond), settings=dict(chunk_content_characters=500, chunk_overlap_characters=100))
        self.assertEqual(report["output_sha256"], again["output_sha256"])

    def test_bad_chunk_keeps_later_work_and_raw_completion(self):
        def respond(payload, provider):
            if provider.context["chunk_index"] == 1:
                return Completion("bad json", "fake", "test", finish_reason="stop")
            if "untrusted_page" in payload:
                return {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
            return provider.support(payload)
        provider = FakeProvider(respond)
        report = extract_record(make_record(["Regel " * 60] * 2), provider,
                                settings=dict(chunk_content_characters=500, chunk_overlap_characters=100))
        self.assertEqual([c["status"] for c in report["chunks"]], ["failed", "extracted"])
        self.assertEqual(report["failures"][0]["raw_completion"], "bad json")
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(provider.invalid[0][0], "1")
        self.assertIsNone(report["prompt_tokens"])

    def test_basis_classification_labels_the_retained_candidates(self):
        record = make_record([dict(kind="paragraph", text="Art. 12 Der Aufenthalt ist bewilligungspflichtig.",
                                   heading_path=["AIG", "Art. 12 Bewilligungspflicht"], source_locator={})])

        def respond(payload, provider):
            if "untrusted_page" in payload:
                span = payload["untrusted_page"]["evidence_spans"][0]
                return {"concepts": [proposal(span, preferred_label="Bewilligungspflicht"), proposal(span, preferred_label="Unbelegt")]}
            if "untrusted_review" in payload:
                result = provider.support(payload)
                result["verdicts"][1].update(decision="unsupported", issue="unsupported_claim", reason="Nicht belegt.")
                return result
            basis = payload["untrusted_basis"]
            self.assertEqual([item["preferred_label"] for item in basis["proposals"]], ["Bewilligungspflicht"])
            self.assertEqual(basis["proposals"][0]["heading_path"], ["AIG", "Art. 12 Bewilligungspflicht"])
            self.assertIn("Art. 12", basis["proposals"][0]["quotes"][0])
            self.assertEqual(basis["primary_sections"][0]["section_id"], "section-0001")
            return {"bases": [dict(basis_id=1, kind="act", level="federal", norm="AIG, SR 142.20, Art. 12", refers_to="",
                                   reason="The evidence reproduces Article 12 under the heading Art. 12.")]}
        provider = FakeProvider(respond)
        report = extract_record(record, provider)
        self.assertEqual(report["request_count"], 3)
        self.assertTrue(provider.calls[2]["system_prompt"].startswith("Classify the basis of proposed concepts"))
        self.assertEqual(report["candidates"][0]["basis"],
                         dict(kind="act", level="federal", norm="AIG, SR 142.20, Art. 12", refers_to=None,
                              reason="The evidence reproduces Article 12 under the heading Art. 12."))
        self.assertEqual(report["quality_metrics"]["candidates_with_basis"], 1)
        self.assertEqual(report["quality_metrics"]["basis_classification"], "separate_model_assessment")
        self.assertEqual(report["prompts"]["basis"]["sources"], ["package:swisstip.concepts/prompts/basis_classification_v1.md"])

    def test_basis_failure_keeps_the_candidates_and_the_call_can_be_switched_off(self):
        def respond(payload, provider):
            if "untrusted_basis" in payload:
                return {"bases": [dict(basis_id=1, kind="act", level="federal", norm="", refers_to="", reason="No norm.")]}
            if "untrusted_review" in payload:
                return provider.support(payload)
            return {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
        provider = FakeProvider(respond)
        report = extract_record(make_record(["Regel."]), provider)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertIsNone(report["candidates"][0]["basis"])
        self.assertEqual(report["failures"][0]["reason"], "basis_unparseable")
        self.assertIn("must name its norm", report["failures"][0]["detail"])
        self.assertEqual(provider.invalid[0][0], "3")
        self.assertEqual(report["chunks"][0]["status"], "extracted")
        provider = FakeProvider()
        report = extract_record(make_record(["Regel."]), provider, settings=dict(classify_basis=False))
        self.assertEqual(report["request_count"], 2)
        self.assertIsNone(report["candidates"][0]["basis"])
        self.assertEqual(report["quality_metrics"]["basis_classification"], "not_performed")
        self.assertFalse(report["settings"]["classify_basis"])

    def test_review_failure_rejects_and_marks_checkpoint(self):
        def respond(payload, provider):
            if "untrusted_page" in payload:
                return {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
            return {"verdicts": []}
        provider = FakeProvider(respond)
        report = extract_record(make_record(["Regel."]), provider)
        self.assertEqual(report["rejected"][0]["reason"], "review_unparseable")
        self.assertEqual(report["failures"][0]["reason"], "review_unparseable")
        self.assertEqual(provider.invalid[0][0], "2")
        self.assertFalse(report["candidates"])

    def test_provider_refusal_with_parseable_content_is_never_accepted(self):
        def respond(payload, provider):
            page = payload["untrusted_page"]
            completion = Completion(json.dumps({"concepts": [proposal(page["evidence_spans"][0])]}), "fake", "test", finish_reason="stop")
            return ProviderResponseError("provider refused", completion=completion)
        report = extract_record(make_record(["Regel."]), FakeProvider(respond))
        self.assertTrue(report["job_stopped"])
        self.assertFalse(report["candidates"])

    def test_over_limit_completion_fails_whole_chunk_before_any_review(self):
        def respond(payload, provider):
            span = payload["untrusted_page"]["evidence_spans"][0]
            return {"concepts": [proposal(span)] * 7}
        provider = FakeProvider(respond)
        report = extract_record(make_record(["Regel."]), provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(report["chunks"][0]["status"], "failed")
        self.assertEqual(report["rejected"], [])
        self.assertFalse(report["reviews"])
        self.assertEqual(len(provider.invalid), 1)

    def test_structural_rejection_keeps_valid_sibling_and_original_index(self):
        def respond(payload, provider):
            if "untrusted_page" in payload:
                span = payload["untrusted_page"]["evidence_spans"][0]
                return {"concepts": [proposal(span, scope="page"), proposal(span)]}
            return provider.support(payload)
        report = extract_record(make_record(["Regel."]), FakeProvider(respond))
        self.assertFalse(report["failures"])
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["rejected"][0]["stage"], "structural")
        self.assertEqual(report["rejected"][0]["proposal_index"], 1)
        self.assertEqual(report["reviews"][0]["proposal_index"], 2)


    def test_truncated_review_split_renumbers_verdicts(self):
        def respond(payload, provider):
            if "untrusted_page" in payload:
                span = payload["untrusted_page"]["evidence_spans"][0]
                return {"concepts": [proposal(span, preferred_label=f"Begriff {i}") for i in range(5)]}
            if "untrusted_review" in payload and len(payload["untrusted_review"]["proposals"]) > 2:
                return Completion("truncated", "fake", "test", finish_reason="length")
            return provider.support(payload)
        provider = FakeProvider(respond)
        report = extract_record(make_record(["Regel."]), provider)
        self.assertFalse(report["failures"])
        self.assertEqual([v["review_id"] for v in report["reviews"]], [1, 2, 3, 4, 5])
        self.assertEqual(report["quality_metrics"]["review_fallbacks"], 2)
        self.assertEqual(provider.fallbacks, 2)
        self.assertEqual(len(report["candidates"]), 5)

    def test_stop_preserves_prior_candidates_and_lists_remaining_chunks(self):
        def respond(payload, provider):
            if provider.context["chunk_index"] == 2:
                return ProviderError("budget reached")
            if "untrusted_page" in payload:
                return {"concepts": [proposal(payload["untrusted_page"]["evidence_spans"][0])]}
            return provider.support(payload)
        report = extract_record(make_record(["Regel " * 60] * 3), FakeProvider(respond),
                                settings=dict(chunk_content_characters=500, chunk_overlap_characters=100))
        self.assertTrue(report["job_stopped"])
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(len(report["failures"]), 2)
        self.assertEqual(report["failures"][-1]["reason"], "job_stopped")

    def test_footnotes_only_in_review_and_siblings_absent(self):
        record = make_record(["Meldepflicht besteht.", dict(kind="paragraph", text="Ausnahme für Diplomaten.",
                              heading_path=["Aufenthalt"], source_locator={"is_footnote": True}),
                              dict(kind="paragraph", text="Geschwisterabschnitt.", heading_path=["Andere"], source_locator={})])
        provider = FakeProvider()
        extract_record(record, provider)
        extract_text = provider.calls[0]["user_prompt"]
        review_text = provider.calls[1]["user_prompt"]
        self.assertNotIn("Ausnahme", extract_text)
        self.assertIn("Ausnahme", review_text)
        self.assertNotIn("Geschwisterabschnitt", review_text)

    def test_split_section_review_marks_partial_and_keeps_footnote_context(self):
        record = make_record(["Erste Bedingung. " * 20, "Zweite Bedingung. " * 20,
                              dict(kind="paragraph", text="Ausnahme für Diplomaten.", heading_path=["Aufenthalt"],
                                   source_locator={"is_footnote": True})])
        provider = FakeProvider()
        report = extract_record(record, provider, settings=dict(chunk_content_characters=500, chunk_overlap_characters=100))
        self.assertFalse(report["failures"])
        contexts = [json.loads(call["user_prompt"])["untrusted_review"]["primary_sections"][0]
                    for call in provider.calls if "untrusted_review" in call["user_prompt"]]
        self.assertEqual(len(contexts), 2)
        for context in contexts:
            self.assertTrue(context["context_may_be_partial"])
            self.assertIn("Ausnahme für Diplomaten.", context["text"])
        self.assertIn("Erste Bedingung.", contexts[0]["text"])
        self.assertNotIn("Zweite Bedingung.", contexts[0]["text"])
        self.assertGreater(contexts[1]["fragment_start"], 0)

    def test_no_content_no_calls_and_capture_not_reusable(self):
        record = make_record([dict(kind="control", text="Submit", heading_path=[], source_locator={})])
        provider = FakeProvider()
        report = extract_record(record, provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(report["warnings"], ["no_content_sections"])
        placeholder = FakeProvider(lambda payload, p: Completion('{"concepts":[]}', "fake", "test", finish_reason="stop", awaiting_response=True))
        report = extract_record(make_record(["Regel."]), placeholder)
        self.assertEqual(report["chunks"][0]["status"], "awaiting_response")
        self.assertEqual(report["failures"][0]["reason"], "awaiting_response")
