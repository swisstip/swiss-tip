import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from swisstip.core.acceptance import AcceptanceFile  # noqa: E402
from swisstip.runtime.acceptance import check_acceptance, issues_of  # noqa: E402
from swisstip.runtime.service import ReleaseService  # noqa: E402
from test_service import SNAPSHOT, release_with_basis, release_with_places, sample_release  # noqa: E402

EU_EFTA = dict(concept_ids=["deadline"], context={"population": "eu_efta"}, expect_status={"deadline": "SUPPORTED"})


def case(case_id="T-1", label="EU/EFTA registration deadline", steps=None, **fields) -> dict:
    return dict(case_id=case_id, label=label, question="By when must I register?", expected_answer="Within 14 days.",
                steps=steps or [dict(resolve=EU_EFTA)], **fields)


def suite(*cases: dict, **policy) -> AcceptanceFile:
    return AcceptanceFile(pack="test", policy=policy, cases=list(cases))


class AcceptanceCheckTests(unittest.TestCase):
    def setUp(self):
        self.service = ReleaseService(sample_release())

    def check(self, *cases: dict, **policy) -> dict:
        return check_acceptance(self.service, suite(*cases, **policy))

    def issues(self, *cases: dict, **policy) -> list[str]:
        return issues_of(self.check(*cases, **policy))

    def test_an_expected_basis_is_checked_on_the_served_facts(self):
        service = ReleaseService(release_with_basis())
        step = dict(concept_ids=["kiosk-law", "zh-registration"], jurisdiction={"canton_code": "CH-ZH"},
                    expect_basis={"kiosk-law": "Federal act: AIG, SR 142.20, Art. 12", "zh-registration": "Cantonal authority guidance"})
        report = check_acceptance(service, suite(case(steps=[dict(resolve=step)])))
        self.assertTrue(report["passed"])
        self.assertEqual(report["results"][0]["steps"][0]["bases"]["kiosk-law"], ["Federal act: AIG, SR 142.20, Art. 12"])
        wrong = dict(step, expect_basis={"kiosk-law": "Portal summary"})
        issues = issues_of(check_acceptance(service, suite(case(steps=[dict(resolve=wrong)]))))
        self.assertEqual(len(issues), 1)
        self.assertIn("kiosk-law expected a fact resting on 'Portal summary', got ['Federal act: AIG, SR 142.20, Art. 12']", issues[0])
        plain = issues_of(check_acceptance(self.service, suite(case(steps=[dict(resolve=dict(
            EU_EFTA, expect_basis={"deadline": "Federal authority guidance"}))]))))
        self.assertIn("got no basis", plain[0])
        with self.assertRaisesRegex(ValueError, "expect_basis names concepts the step does not request"):
            suite(case(steps=[dict(resolve=dict(EU_EFTA, expect_basis={"other": "x"}))]))

    def test_a_jurisdiction_in_names_is_checked_by_the_scope_the_request_ran_for(self):
        service = ReleaseService(release_with_places())

        def run(**step) -> dict:
            return check_acceptance(service, suite(case(steps=[dict(resolve=dict(concept_ids=["zh-registration"], **step))])))

        named = run(jurisdiction={"city": "Wallisellen"}, expect_status={"zh-registration": "SUPPORTED"},
                    expect_scope="CH-ZH-69", expect_not_recognised=[])
        self.assertTrue(named["passed"])
        record = named["results"][0]["steps"][0]
        self.assertEqual((record["scope"], record["not_recognised"]), ("CH-ZH-69", {}))
        # A quarter is not a municipality: the request runs for the canton, and the case pins both halves of that.
        quarter = run(jurisdiction={"canton": "Zurich", "city": "Oerlikon"}, expect_scope="CH-ZH", expect_not_recognised=["city"])
        self.assertTrue(quarter["passed"])
        self.assertEqual(quarter["results"][0]["steps"][0]["not_recognised"], {"city": "Oerlikon"})
        self.assertEqual(issues_of(run(jurisdiction={"city": "Winterthur"}, expect_scope="CH-ZH-261")),
                         ["T-1 (EU/EFTA registration deadline): expected the request to run for CH-ZH-261, it ran for CH-ZH-230"])
        self.assertEqual(issues_of(run(jurisdiction={"canton": "Zurich", "city": "Oerlikon"}, expect_not_recognised=[])),
                         ["T-1 (EU/EFTA registration deadline): expected no part not recognised, got ['city']"])
        self.assertEqual(issues_of(run(jurisdiction={"city": "Zurich"}, expect_not_recognised=["city"])),
                         ["T-1 (EU/EFTA registration deadline): expected ['city'] not recognised, got none"])

    def test_a_rejected_request_passes_only_when_the_step_names_the_path_of_the_issue(self):
        service = ReleaseService(release_with_places())

        def run(**step) -> dict:
            return check_acceptance(service, suite(case(steps=[dict(resolve=dict(concept_ids=["zh-registration"], **step))])))

        shared = run(jurisdiction={"city": "Buchs"}, expect_error="jurisdiction.city")
        self.assertTrue(shared["passed"])
        self.assertEqual(shared["results"][0]["steps"][0]["error_paths"], ["jurisdiction.city"])
        self.assertEqual(issues_of(run(jurisdiction={"city": "Buchs"})),
                         ["T-1 (EU/EFTA registration deadline): resolve request rejected: INVALID_ARGUMENT on jurisdiction.city"])
        self.assertEqual(issues_of(run(jurisdiction={"city": "Buchs"}, expect_error="jurisdiction.canton")),
                         ["T-1 (EU/EFTA registration deadline): resolve request rejected: INVALID_ARGUMENT on jurisdiction.city, expected an issue on jurisdiction.canton"])
        self.assertEqual(issues_of(run(jurisdiction={"canton": "SG", "city": "Buchs"}, expect_error="jurisdiction.city")),
                         ["T-1 (EU/EFTA registration deadline): expected the request to be rejected on jurisdiction.city, but it was answered"])
        with self.assertRaisesRegex(ValueError, "a step that expects an error cannot expect"):
            run(jurisdiction={"city": "Buchs"}, expect_error="jurisdiction.city", expect_scope="CH-ZH")
        with self.assertRaisesRegex(ValueError, "names no jurisdiction part"):
            run(jurisdiction={"city": "Buchs"}, expect_not_recognised=["town"])

    def test_the_expectations_added_for_places_leave_the_digest_of_a_suite_without_them(self):
        plain = suite(case())
        self.assertEqual(plain.digest(), suite(case(steps=[dict(resolve=dict(EU_EFTA, expect_scope=None, expect_not_recognised=None,
                                                                             expect_error=None))])).digest())
        self.assertNotEqual(plain.digest(), suite(case(steps=[dict(resolve=dict(EU_EFTA, expect_scope="CH"))])).digest())
        self.assertNotEqual(plain.digest(), suite(case(steps=[dict(resolve=dict(EU_EFTA, expect_not_recognised=[]))])).digest())

    def test_a_matching_case_passes_on_the_statement_and_the_cited_excerpt(self):
        report = self.check(case(claims=[
            dict(claim="14 days after arrival", concept="deadline", statement_contains=["within 14 days", "before starting work"],
                 excerpt_contains=["Innert 14 Tagen", "vor Stellenantritt"])]))
        self.assertTrue(report["passed"])
        self.assertEqual((report["cases"], report["blocking"], report["failed"]), (1, 1, []))
        self.assertEqual(report["as_of"], SNAPSHOT.isoformat())
        self.assertEqual(report["results"][0]["claims"][0]["fact_ids"], ["deadline-1"])
        self.assertEqual(report["results"][0]["steps"][0]["facts"], {"deadline": ["deadline-1"]})
        self.assertEqual(report["release_id"], "test-v1")
        self.assertEqual(report["suite_sha256"], suite(case(claims=[
            dict(claim="14 days after arrival", concept="deadline", statement_contains=["within 14 days", "before starting work"],
                 excerpt_contains=["Innert 14 Tagen", "vor Stellenantritt"])])).digest())

    def test_a_wrong_expected_status_is_reported(self):
        wrong = dict(EU_EFTA, expect_status={"deadline": "NEEDS_CONTEXT"})
        self.assertEqual(self.issues(case(steps=[dict(resolve=wrong)])),
                         ["T-1 (EU/EFTA registration deadline): deadline expected NEEDS_CONTEXT, got SUPPORTED"])

    def test_a_missing_context_is_reported_as_the_actual_status(self):
        no_context = dict(concept_ids=["deadline"], expect_status={"deadline": "SUPPORTED"})
        self.assertIn("expected SUPPORTED, got NEEDS_CONTEXT", self.issues(case(steps=[dict(resolve=no_context)]))[0])

    def test_a_dropped_or_reworded_fact_is_reported(self):
        issues = self.issues(case(claims=[dict(claim="30 days", concept="deadline", statement_contains=["within 30 days"])]))
        self.assertEqual(issues, ["T-1 (EU/EFTA registration deadline): deadline fact statements no longer state "
                                  "'within 30 days' ('30 days')"])

    def test_a_statement_its_excerpt_does_not_back_is_reported(self):
        issues = self.issues(case(claims=[dict(claim="14 days", concept="deadline", statement_contains=["within 14 days"],
                                               excerpt_contains=["innert 30 Tagen"])]))
        self.assertEqual(issues, ["T-1 (EU/EFTA registration deadline): deadline-1 state(s) '14 days' but no cited excerpt "
                                  "contains 'innert 30 Tagen'"])

    def test_phrases_match_across_case_and_whitespace(self):
        self.assertEqual(self.issues(case(claims=[dict(claim="14 days", concept="deadline", statement_contains=["WITHIN  14 days"],
                                                       excerpt_contains=["innert 14 tagen"])])), [])

    def test_a_pinned_fact_that_is_not_served_is_reported(self):
        issues = self.issues(case(claims=[dict(claim="third-country permit", concept="deadline", fact_id="deadline-2")]))
        self.assertEqual(issues, ["T-1 (EU/EFTA registration deadline): fact deadline-2 is no longer served for deadline "
                                  "('third-country permit')"])

    def test_a_served_fact_the_case_excludes_is_reported(self):
        third = dict(concept_ids=["deadline"], context={"population": "third_country"})
        issues = self.issues(case(steps=[dict(resolve=third)], must_not_serve=["deadline-2"]))
        self.assertEqual(issues, ["T-1 (EU/EFTA registration deadline): fact deadline-2 was served although the case excludes it"])
        self.assertEqual(self.issues(case(must_not_serve=["deadline-2"])), [])

    def test_a_decline_is_checked_by_its_status_and_the_gap_it_names(self):
        abroad = dict(concept_ids=["deadline"], jurisdiction={"country_code": "DE"}, context={"population": "eu_efta"},
                      expect_status={"deadline": "OUT_OF_COVERAGE"}, expect_gap={"deadline": "jurisdiction_not_covered"})
        report = self.check(case(steps=[dict(resolve=abroad)]))
        self.assertTrue(report["passed"])
        self.assertEqual(report["results"][0]["steps"][0]["gaps"], {"deadline": ["jurisdiction_not_covered"]})
        unknown = dict(abroad, concept_ids=["voting"], expect_status={"voting": "OUT_OF_COVERAGE"},
                       expect_gap={"voting": "concept_not_published"})
        self.assertEqual(self.issues(case(steps=[dict(resolve=unknown)])), [])
        wrong = dict(abroad, expect_gap={"deadline": "context_not_covered"})
        self.assertEqual(self.issues(case(steps=[dict(resolve=wrong)])),
                         ["T-1 (EU/EFTA registration deadline): deadline expected a context_not_covered gap, "
                          "got ['jurisdiction_not_covered']"])
        served = dict(EU_EFTA, expect_gap={"deadline": "jurisdiction_not_covered"})
        self.assertEqual(self.issues(case(steps=[dict(resolve=served)])),
                         ["T-1 (EU/EFTA registration deadline): deadline expected a jurisdiction_not_covered gap, got none"])

    def test_a_lost_alias_is_reported_through_the_search_step(self):
        found = case(steps=[dict(search=dict(query="Anmeldefrist Wohngemeinde", expect_concept="deadline")), dict(resolve=EU_EFTA)])
        self.assertEqual(self.issues(found), [])
        lost = case(steps=[dict(search=dict(query="Familiennachzug Ehegatte", expect_concept="deadline")), dict(resolve=EU_EFTA)])
        issues = self.issues(lost)
        self.assertEqual(len(issues), 1)
        self.assertIn("no longer finds deadline within the first 3 hits", issues[0])

    def test_a_hybrid_only_search_step_is_recorded_but_not_judged_by_a_lexical_replay(self):
        step = dict(query="Familiennachzug Ehegatte", expect_concept="deadline", retrieval="hybrid")
        report = self.check(case(steps=[dict(search=step), dict(resolve=EU_EFTA)]))
        self.assertTrue(report["passed"])
        record = report["results"][0]["steps"][0]
        self.assertEqual((record["retrieval_mode"], record["judged"]), ("lexical", False))
        self.assertIn("no longer finds deadline", record["unjudged_issues"][0])
        # A translated query is replayed as written and marked in the report.
        translated = dict(query="Anmeldefrist Wohngemeinde", expect_concept="deadline", translated=True)
        report = self.check(case(language="fr", steps=[dict(search=translated), dict(resolve=EU_EFTA)]))
        self.assertTrue(report["passed"])
        self.assertTrue(report["results"][0]["steps"][0]["translated"])

    def test_a_search_step_can_send_the_users_place_and_pin_what_is_published_elsewhere(self):
        service = ReleaseService(release_with_places())

        def issues(**step) -> list[str]:
            return issues_of(check_acceptance(service, suite(case(steps=[dict(search=dict(query="registration", **step)),
                                                                         dict(resolve=EU_EFTA)]))))

        self.assertEqual(issues(jurisdiction={"canton": "Bern"}, expect_concept="deadline",
                                expect_elsewhere=["zh-registration", "city-arrival"]), [])
        # A concept that can apply at the place is ranked, so naming it as published elsewhere fails.
        ranked = issues(jurisdiction={"city": "Winterthur"}, expect_elsewhere=["zh-registration"])
        self.assertEqual(len(ranked), 1)
        self.assertIn("should name zh-registration in published_elsewhere and not rank it", ranked[0])
        self.assertIn("no longer finds zh-registration", issues(jurisdiction={"canton": "Bern"}, expect_concept="zh-registration")[0])
        with self.assertRaisesRegex(ValueError, "expect_elsewhere needs the jurisdiction"):
            suite(case(steps=[dict(search=dict(query="registration", expect_elsewhere=["zh-registration"]))]))
        record = check_acceptance(service, suite(case(steps=[dict(search=dict(
            query="registration", jurisdiction={"canton": "Bern"}, expect_concept="deadline")), dict(resolve=EU_EFTA)])))
        self.assertEqual(record["results"][0]["steps"][0]["published_elsewhere"], ["city-arrival", "zh-registration"])

    def test_a_lost_not_served_item_is_reported(self):
        release = sample_release()
        next(c for c in release.concepts if c.concept_id == "deadline").not_served = ["fees for the registration"]
        service = ReleaseService(release)
        report = check_acceptance(service, suite(case(not_served={"deadline": ["fees", "appointment availability"]})))
        self.assertEqual(issues_of(report), ["T-1 (EU/EFTA registration deadline): deadline not_served no longer names "
                                             "'appointment availability'"])

    def test_the_policy_date_decides_staleness_and_a_step_may_override_it(self):
        stale_day = (SNAPSHOT + timedelta(days=60)).isoformat()
        self.assertIn("expected SUPPORTED, got STALE", self.issues(case(), as_of=stale_day)[0])
        stale = dict(EU_EFTA, as_of=stale_day, expect_status={"deadline": "STALE"})
        self.assertEqual(self.issues(case(steps=[dict(resolve=stale)])), [])
        report = self.check(case(), as_of="today")
        self.assertEqual(report["as_of"], date.today().isoformat())

    def test_a_quarantined_case_is_run_and_reported_but_does_not_fail_the_suite(self):
        broken = case(case_id="T-2", label="quarantined", blocking=False, quarantine_reason="wording under review",
                      claims=[dict(claim="30 days", concept="deadline", statement_contains=["within 30 days"])])
        report = self.check(case(), broken)
        self.assertTrue(report["passed"])
        self.assertEqual((report["failed"], report["quarantined"], report["quarantined_failed"]), ([], ["T-2"], ["T-2"]))
        self.assertEqual(len(report["results"][1]["issues"]), 1)
        self.assertEqual(issues_of(report), [])

    def test_several_cases_report_independently(self):
        broken = case(case_id="T-5", label="broken", steps=[dict(resolve=dict(concept_ids=["deadline"], expect_status={"deadline": "SUPPORTED"}))])
        issues = self.issues(case(), broken)
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].startswith("T-5"))


if __name__ == "__main__":
    unittest.main()
