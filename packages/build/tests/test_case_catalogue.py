import unittest

from swisstip.build.case_catalogue import render_case_catalogue
from swisstip.core.acceptance import AcceptanceAnswers, AcceptanceFile

SEARCH = dict(search=dict(query="Bis wann muss ich mich anmelden?", expect_concept="deadline", expect_strength="strong"))
RESOLVE = dict(resolve=dict(concept_ids=["deadline"], jurisdiction={"canton_code": "CH-ZH"},
                            context={"population": "eu_efta"}, expect_status={"deadline": "SUPPORTED"}))


def case(case_id: str, steps: list[dict] | None = None, **fields) -> dict:
    return dict(dict(case_id=case_id, label=f"Label of {case_id}", question=f"Question of {case_id}?",
                     expected_answer=f"Answer of {case_id}.", steps=[SEARCH, RESOLVE] if steps is None else steps),
                **fields)


ACCEPTANCE = AcceptanceFile(pack="test", cases=[
    case("UAT-10", trap="Counting from the first working day.", spec="docs/product/uat.md#uat-10"),
    case("UAT-2"),
    case("DECLINE-1", steps=[dict(resolve=dict(concept_ids=["fees"], jurisdiction={"canton_code": "CH-ZH"},
                                               expect_status={"fees": "OUT_OF_COVERAGE"},
                                               expect_gap={"fees": "concept_not_published"}))]),
])
REGRESSION = AcceptanceFile(pack="test", cases=[
    case("Q-DE-1", language="de"),
    case("Q-EN-2", language="en", claims=[dict(claim="Within 14 days", concept="deadline", fact_id="deadline-1",
                                                 statement_contains=["within 14 days"])]),
    case("OOS-1", blocking=False, quarantine_reason="Reads strong on shared words.",
         steps=[dict(search=dict(query="Museum hours", expect_strength="weak", retrieval="hybrid"))]),
    case("CTX-1", steps=[dict(resolve=dict(concept_ids=["deadline"], jurisdiction={"city": "Buchs"},
                                           expect_error="jurisdiction.city"))], must_not_serve=["deadline-2"]),
    case("Q-FR-1", language="fr", steps=[dict(search=dict(query="Frist Anmeldung", translated=True,
                                                          expect_concept="deadline")), RESOLVE]),
])


def result(case_id: str, passed: bool = True, hits: list[str] | None = None, issues: list[str] | None = None,
           judged: bool = True) -> dict:
    searches = [dict(query="q", translated=False, hits=hits or ["deadline"], match_strength="strong", judged=judged)]
    return dict(case_id=case_id, blocking=True, passed=passed, issues=issues or [], searches=searches)


def report(acceptance_sha256: str | None = None) -> dict:
    lexical = [result("UAT-2"), result("UAT-10"), result("Q-EN-2", passed=False, hits=["other"],
                                                         issues=["Q-EN-2 (Label of Q-EN-2): search lost deadline"]),
               result("OOS-1", passed=True, judged=False)]
    hybrid = [result("UAT-2"), result("UAT-10"), result("Q-EN-2"), result("OOS-1", passed=False)]
    return dict(release_id="test-2026-09-18-v1", content_sha256="c" * 64, checked_at="2026-09-18T10:00:00+00:00",
                as_of="2026-09-18", acceptance_sha256=acceptance_sha256 or ACCEPTANCE.digest(),
                regression_sha256=REGRESSION.digest(),
                runs=dict(lexical=dict(results=lexical), hybrid=dict(results=hybrid)))


def answers(content_sha256: str) -> AcceptanceAnswers:
    return AcceptanceAnswers(pack="test", release_id="test-2026-09-16-v2", content_sha256=content_sha256,
                             suite_sha256="s" * 64, aggregated_at="2026-09-16T17:00:00Z",
                             cases={"UAT-2": dict(runs=3, graded=3, trap_held=3, criteria_pass=2,
                                                  unsupported_claims=["one"])})


class CaseCatalogueTests(unittest.TestCase):
    def render(self, **overrides) -> str:
        arguments = dict(acceptance=ACCEPTANCE, regression=REGRESSION, report=report(), answers=None) | overrides
        return render_case_catalogue(**arguments)

    def test_cases_are_grouped_and_ordered_by_kind_language_and_number(self):
        page = self.render()
        order = ["## User acceptance cases", "### UAT-2:", "### UAT-10:", "## Declines at resolve", "### DECLINE-1:",
                 "## Plain questions", "### Q-EN-2:", "### Q-DE-1:", "### Q-FR-1:", "## Context routing", "### CTX-1:",
                 "## Declined questions", "### OOS-1:"]
        positions = [page.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("- [Plain questions](#plain-questions) (3)", page)
        self.assertIn("| All | 8 | 1 | 3 | 3 |", page)

    def test_a_case_shows_its_question_answer_trap_steps_claims_and_quarantine(self):
        page = self.render()
        self.assertIn("> Question of UAT-10?", page)
        self.assertIn("- **Expected answer:** Answer of UAT-10.", page)
        self.assertIn("- **Trap:** Counting from the first working day.", page)
        self.assertIn("spec: [docs/product/uat.md#uat-10](../../docs/product/uat.md#uat-10)", page)
        self.assertIn("1. `search` \"Bis wann muss ich mich anmelden?\" - expects `deadline` among the first 3 hits and "
                      "match strength `strong`", page)
        self.assertIn("2. `resolve` `deadline` for canton_code CH-ZH, context population=eu_efta - expects `deadline` "
                      "SUPPORTED", page)
        self.assertIn("`fees` OUT_OF_COVERAGE with gap `concept_not_published`", page)
        self.assertIn("`search` \"Frist Anmeldung\" (key terms translated by the author)", page)
        self.assertIn("match strength `weak` (hybrid search only)", page)
        self.assertIn("for city Buchs - expects rejection with an issue on `jurisdiction.city`", page)
        self.assertIn("- Within 14 days: `deadline-1` states \"within 14 days\"", page)
        self.assertIn("**Must not be served:** `deadline-2`", page)
        self.assertIn("Regression pack, quarantined.", page)
        self.assertIn("- **Quarantined because:** Reads strong on shared words.", page)

    def test_results_show_every_mode_with_hits_issues_and_cases_missing_from_the_report(self):
        page = self.render()
        q_en_2 = page[page.index("### Q-EN-2:"):page.index("### Q-DE-1:")]
        self.assertIn("| lexical | fail | `other` | strong |", q_en_2)
        self.assertIn("| hybrid | pass | `deadline` | strong |", q_en_2)
        self.assertIn("- lexical: search lost deadline", q_en_2)
        self.assertIn("| lexical | pass (not judged) | `deadline` | strong |", page)
        q_de_1 = page[page.index("### Q-DE-1:"):page.index("### Q-FR-1:")]
        self.assertIn("| lexical | not in the report | | |", q_de_1)
        self.assertIn("Results: release `test-2026-09-18-v1`, replayed on 2026-09-18", page)
        self.assertNotIn("other suites", page)
        self.assertIn("The report is for other suites", self.render(report=report(acceptance_sha256="0" * 64)))
        self.assertIn("No `regression-report.json`", self.render(report=None))

    def test_live_caller_grades_name_the_release_they_were_graded_on(self):
        earlier = self.render(answers=answers("e" * 64))
        self.assertIn("3 graded sessions of 1 acceptance cases on the earlier release `test-2026-09-16-v2`, not the "
                      "current one", earlier)
        self.assertIn("Live caller on `test-2026-09-16-v2`: 3 of 3 sessions graded, trap held in 3, every criterion "
                      "met in 2; 1 unsupported specifics noted by the graders.", earlier)
        self.assertIn("on the current release", self.render(answers=answers("c" * 64)))

    def test_each_group_opens_with_an_overview_row_per_case_linking_to_its_section(self):
        page = self.render(answers=answers("e" * 64))
        self.assertIn("| Case | Question | Language | Status | Lexical | Hybrid | Live caller |", page)
        self.assertIn("| [UAT-2](#uat-2) | Question of UAT-2? |  | blocking | pass | pass | trap 3/3, criteria 2/3 |", page)
        self.assertIn("| [UAT-10](#uat-10) | Question of UAT-10? |  | blocking | pass | pass | |", page)
        self.assertIn("| [Q-EN-2](#q-en-2) | Question of Q-EN-2? | en | blocking | fail | pass |", page)
        self.assertIn("| [Q-DE-1](#q-de-1) | Question of Q-DE-1? | de | blocking | not in the report | not in the report |", page)
        self.assertIn("| [OOS-1](#oos-1) | Question of OOS-1? |  | quarantined | pass (not judged) | fail |", page)
        self.assertIn("<a id=\"q-en-2\"></a>\n\n### Q-EN-2:", page)
        self.assertLess(page.index("| [Q-EN-2](#q-en-2)"), page.index("### Q-EN-2:"))
        long_question = "Wie lange | darf ich " + "sehr " * 30 + "bleiben?"
        regression = AcceptanceFile(pack="test", cases=[case("Q-DE-9", language="de", question=long_question)])
        row = next(line for line in self.render(regression=regression).splitlines() if line.startswith("| [Q-DE-9]"))
        self.assertIn("| Wie lange \\| darf ich sehr sehr", row)
        self.assertIn("sehr... | de |", row)

    def test_the_same_inputs_give_the_same_page(self):
        self.assertEqual(self.render(answers=answers("e" * 64)), self.render(answers=answers("e" * 64)))
        self.assertTrue(self.render().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
