import json
import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from swisstip.core.acceptance import (AcceptanceAnswers, AcceptanceFile, AnswerCriteria, AnswerRecord, Case, Claim,
                                      ResolveStep, Step, answer_verdict)
from swisstip.core.release import sha256_text

RESOLVE = dict(concept_ids=["deadline"], context={"population": "eu_efta"}, expect_status={"deadline": "SUPPORTED"})


def case(**overrides) -> dict:
    base = dict(case_id="T-1", label="deadline", question="By when?", expected_answer="Within 14 days.",
                steps=[dict(resolve=RESOLVE)], claims=[dict(claim="14 days", concept="deadline", statement_contains=["14 days"])])
    return base | overrides


def answers(**records) -> AcceptanceAnswers:
    return AcceptanceAnswers(pack="test", release_id="test-v1", content_sha256="c" * 64, suite_sha256="", aggregated_at=datetime.now(UTC),
                             cases={case_id: AnswerRecord(**record) for case_id, record in records.items()})


def record(graded=3, trap_held=3, criteria_pass=3) -> dict:
    return dict(runs=graded, graded=graded, trap_held=trap_held, criteria_pass=criteria_pass, models=["m"])


class AcceptanceModelTests(unittest.TestCase):
    def test_a_suite_loads_and_digests_its_content_not_its_layout(self):
        suite = AcceptanceFile(pack="test", cases=[case()])
        self.assertEqual(suite.policy.as_of, "snapshot")
        self.assertEqual((suite.policy.answer_check, suite.policy.answer_repetitions, suite.policy.answer_pass_rate), ("advisory", 3, 0.8))
        self.assertEqual(suite.case("T-1").steps[0].resolve.expect_status["deadline"].value, "SUPPORTED")
        same = AcceptanceFile.model_validate(dict(cases=[case()], pack="test", schema_version="swiss-tip-acceptance/v1"))
        self.assertEqual(suite.digest(), same.digest())
        changed = AcceptanceFile(pack="test", cases=[case(claims=[dict(claim="30 days", concept="deadline", statement_contains=["30 days"])])])
        self.assertNotEqual(suite.digest(), changed.digest())

    def test_fields_added_later_enter_the_digest_only_when_set(self):
        search = dict(search=dict(query="Anmeldefrist", expect_concept="deadline"))
        plain = AcceptanceFile(pack="test", cases=[case(steps=[search, case()["steps"][0]])])
        # The digest of a suite that sets none of them is the one computed before they existed.
        data = plain.model_dump(mode="json")
        for item in data["cases"]:
            item.pop("language")
            for step in item["steps"]:
                if step["search"]:
                    for name in ("translated", "retrieval", "jurisdiction", "expect_elsewhere"):
                        step["search"].pop(name)
                if step["resolve"]:
                    for name in ("expect_scope", "expect_not_recognised", "expect_error"):
                        step["resolve"].pop(name)
                step.pop("lookup")
        self.assertEqual(plain.digest(), sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        for fields in (dict(expect_scope="CH-ZH"), dict(expect_not_recognised=[]), dict(expect_not_recognised=["city"])):
            changed = AcceptanceFile(pack="test", cases=[case(steps=[search, dict(resolve=dict(RESOLVE, **fields))])])
            self.assertNotEqual(plain.digest(), changed.digest())
        rejected = AcceptanceFile(pack="test", cases=[case(steps=[search, dict(resolve=dict(
            concept_ids=["deadline"], jurisdiction={"city": "Buchs"}, expect_error="jurisdiction.city"))])])
        self.assertNotEqual(plain.digest(), rejected.digest())
        for fields in (dict(translated=True), dict(retrieval="hybrid"), dict(jurisdiction={"canton": "Bern"}),
                       dict(jurisdiction={"canton": "Bern"}, expect_elsewhere=["zh-registration"])):
            changed = AcceptanceFile(pack="test", cases=[case(language="fr", steps=[
                dict(search=dict(search["search"], **fields)), case()["steps"][0]])])
            self.assertNotEqual(plain.digest(), changed.digest())
        self.assertNotEqual(plain.digest(), AcceptanceFile(pack="test", cases=[case(language="en", steps=plain.cases[0].model_dump()["steps"])]).digest())

    def test_a_translated_query_needs_the_question_language(self):
        translated = [dict(search=dict(query="Anmeldefrist Gemeinde", expect_concept="deadline", translated=True)), dict(resolve=RESOLVE)]
        with self.assertRaisesRegex(ValueError, "translates a search query but names no question language"):
            AcceptanceFile(pack="test", cases=[case(steps=translated)])
        self.assertEqual(AcceptanceFile(pack="test", cases=[case(language="fr", steps=translated)]).cases[0].language, "fr")
        with self.assertRaises(ValidationError):
            AcceptanceFile(pack="test", cases=[case(language="French")])

    def test_a_lookup_step_follows_the_tool_contract_and_leaves_older_digests(self):
        lookup = dict(dataset_id="zurich-waste-bioabfall", postal_code="8001", as_of="2026-09-23", limit=1,
                      expect_status="SUPPORTED", expect_first_date="2026-09-28")
        suite = AcceptanceFile(pack="test", cases=[case(steps=[dict(resolve=RESOLVE), dict(lookup=lookup)])])
        step = suite.case("T-1").steps[1].lookup
        self.assertEqual(step.request(datetime(2026, 9, 1).date()).as_of.isoformat(), "2026-09-23")
        self.assertEqual(step.expect_status.value, "SUPPORTED")
        for bad in (dict(lookup, postal_code="80"), dict(lookup, limit=61), dict(lookup, expect_status="NEEDS_CONTEXT"),
                    dict(lookup, street="x")):
            with self.assertRaises(ValidationError):
                AcceptanceFile(pack="test", cases=[case(steps=[dict(lookup=bad)])])
        with self.assertRaises(ValidationError):
            AcceptanceFile(pack="test", cases=[case(steps=[dict(resolve=RESOLVE, lookup=lookup)])])
        # A suite without lookup steps digests as before the kind existed: the canonical JSON is the one the earlier
        # rule produced, with no "lookup" key in any step.
        from swisstip.core.acceptance import OPTIONAL_DIGEST_FIELDS, drop_defaults
        without = AcceptanceFile(pack="test", cases=[case()])
        data = without.model_dump(mode="json")
        for item in data["cases"]:
            drop_defaults(item, OPTIONAL_DIGEST_FIELDS["case"])
            for entry in item["steps"]:
                entry.pop("lookup")
                if entry.get("resolve"):
                    drop_defaults(entry["resolve"], OPTIONAL_DIGEST_FIELDS["resolve"])
        self.assertEqual(without.digest(), sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        self.assertNotEqual(without.digest(), suite.digest())

    def test_a_step_is_a_search_or_a_resolve(self):
        with self.assertRaises(ValidationError):
            Step()
        with self.assertRaises(ValidationError):
            Step(search=dict(query="q", expect_concept="deadline"), resolve=RESOLVE)

    def test_a_resolve_step_follows_the_tool_contract(self):
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=["deadline"], jurisdiction={"kanton": "ZH"})
        ResolveStep(concept_ids=["deadline"], jurisdiction={"canton": "Zurich", "city": "Wallisellen"})
        ResolveStep(concept_ids=["deadline"], jurisdiction={"canton_code": "CH-ZH", "municipality_id": "CH-ZH-69"})
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=[f"c{n}" for n in range(6)])
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=["deadline"], expect_status={"other": "SUPPORTED"})
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=["deadline"], expect_status={"deadline": "MAYBE"})
        ResolveStep(concept_ids=["deadline"], expect_gap={"deadline": "jurisdiction_not_covered"})
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=["deadline"], expect_gap={"other": "jurisdiction_not_covered"})
        with self.assertRaises(ValidationError):
            ResolveStep(concept_ids=["deadline"], expect_gap={"deadline": "not_a_dimension"})

    def test_a_claim_states_something_checkable_about_a_resolved_concept(self):
        with self.assertRaises(ValidationError):
            Claim(claim="empty", concept="deadline")
        Claim(claim="pinned", concept="deadline", fact_id="deadline-1")
        with self.assertRaises(ValidationError):
            Case(**case(claims=[dict(claim="x", concept="unresolved", statement_contains=["x"])]))
        with self.assertRaises(ValidationError):
            Case(**case(not_served={"unresolved": ["fees"]}))

    def test_a_case_without_steps_is_allowed_only_without_claims(self):
        self.assertEqual(Case(**case(steps=[], claims=[])).steps, [])
        with self.assertRaises(ValidationError):
            Case(**case(steps=[]))

    def test_answer_patterns_must_compile(self):
        AnswerCriteria(criteria=["C1"], must_mention={"days": r"\b14\b"}, must_not={"fee": r"CHF\s*\d"},
                       cites={"page": "wallisellen.ch/umzug"}, resolved=["moving", ["a", "b"]])
        with self.assertRaises(ValidationError):
            AnswerCriteria(must_mention={"broken": r"(unclosed"})

    def test_quarantine_needs_a_reason_and_only_then(self):
        with self.assertRaises(ValidationError):
            Case(**case(blocking=False))
        with self.assertRaises(ValidationError):
            Case(**case(quarantine_reason="not yet curated"))
        self.assertFalse(Case(**case(blocking=False, quarantine_reason="not yet curated")).blocking)

    def test_case_ids_are_unique(self):
        with self.assertRaises(ValidationError):
            AcceptanceFile(pack="test", cases=[case(), case()])


class AnswerVerdictTests(unittest.TestCase):
    def suite(self, mode="required", **policy) -> AcceptanceFile:
        checked = case(answer=dict(criteria=["C1"], must_mention={"days": "14"}))
        unchecked = case(case_id="T-2", label="no answer block")
        return AcceptanceFile(pack="test", policy=dict(answer_check=mode, **policy), cases=[checked, unchecked])

    def bound(self, suite: AcceptanceFile, **records) -> AcceptanceAnswers:
        item = answers(**records)
        item.suite_sha256 = suite.digest()
        return item

    def test_off_passes_without_looking(self):
        verdict = answer_verdict(self.suite("off"), None, "c" * 64)
        self.assertEqual((verdict["passed"], verdict["mode"]), (True, "off"))

    def test_required_needs_answers_for_this_release_and_suite(self):
        suite = self.suite()
        self.assertFalse(answer_verdict(suite, None, "c" * 64)["passed"])
        self.assertTrue(answer_verdict(self.suite("advisory"), None, "c" * 64)["passed"])
        stale = self.bound(suite, **{"T-1": record()})
        self.assertFalse(answer_verdict(suite, stale, "d" * 64)["passed"])
        stale.suite_sha256 = "other"
        self.assertIn("another release or suite", answer_verdict(suite, stale, "c" * 64)["detail"])

    def test_a_case_meets_the_policy_with_enough_graded_sessions_the_trap_held_and_the_rate(self):
        suite = self.suite()
        verdict = answer_verdict(suite, self.bound(suite, **{"T-1": record(graded=3, trap_held=3, criteria_pass=3)}), "c" * 64)
        self.assertTrue(verdict["passed"])
        self.assertEqual(list(verdict["cases"]), ["T-1"])  # T-2 has no answer block and is not part of G5
        self.assertIn("1 of 1 answer-checked case(s) meet the policy", verdict["detail"])
        for short in (record(graded=2, trap_held=2, criteria_pass=2), record(graded=3, trap_held=2, criteria_pass=3),
                      record(graded=3, trap_held=3, criteria_pass=2), record(graded=0, trap_held=0, criteria_pass=0)):
            with self.subTest(record=short):
                verdict = answer_verdict(suite, self.bound(suite, **{"T-1": short}), "c" * 64)
                self.assertFalse(verdict["passed"])
                self.assertIn("T-1", verdict["detail"])
        missing = answer_verdict(suite, self.bound(suite), "c" * 64)
        self.assertEqual(missing["cases"]["T-1"]["reason"], "no graded session")

    def test_advisory_reports_the_shortfall_but_passes(self):
        suite = self.suite("advisory")
        verdict = answer_verdict(suite, self.bound(suite, **{"T-1": record(graded=3, trap_held=3, criteria_pass=1)}), "c" * 64)
        self.assertTrue(verdict["passed"])
        self.assertIn("below 80%", verdict["detail"])


if __name__ == "__main__":
    unittest.main()
