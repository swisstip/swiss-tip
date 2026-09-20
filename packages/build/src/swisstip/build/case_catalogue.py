"""The case catalogue of a pack: `releases/<pack>/test-cases.md`, one readable page with every case.

Each group of cases opens with an overview table, one row per case with its question, status and outcome per
retrieval mode, linking to the case's section. Every case of the acceptance suite and of the regression pack is
then rendered with its question, the expected answer,
the trap, the tool steps and what they expect, the claims on served facts and the facts that must not be served,
followed by the latest results the committed reports hold: per retrieval mode the outcome, the first hits and the
match strength of every search, the issues of a failed case, and the graded live-caller sessions of
`acceptance-answers.json`. The page is rendered from the committed files only, so the same files always give the same
text: `scripts/test/regression/run_regression.py` writes it next to the report, and the committed-pack test requires
the committed page to equal a fresh rendering.
"""

import json
import re
from collections import Counter
from pathlib import Path

from swisstip.core.acceptance import AcceptanceAnswers, AcceptanceFile, Case, ResolveStep, SearchStep

from .acceptance import REGRESSION_FILE, load_acceptance

CATALOGUE_FILE = "test-cases.md"
REPORT_FILE = "regression-report.json"
ANSWERS_FILE = "acceptance-answers.json"
MODES = ("lexical", "hybrid")

# Case groups in page order: (title, whether the group's cases come from the acceptance suite, ID pattern).
GROUPS = [
    ("User acceptance cases", True, r"UAT-\d+[a-z]?"),
    ("Declines at resolve", True, r"DECLINE-\d+"),
    ("Declines at search", True, r"SEARCH-DECLINE-\d+"),
    ("Plain questions", False, r"Q-[A-Z]+-\d+"),
    ("Translated variants of acceptance cases", False, r"UAT-\d+[a-z]?-T"),
    ("Questions with typos, jargon or abbreviations", False, r"N-[A-Z]+-[A-Z]\d+"),
    ("Context routing", False, r"CTX-\d+"),
    ("Dates", False, r"DATE-\d+"),
    ("Places given by name", False, r"PN-\d+"),
    ("Asked from another canton", False, r"XC-\d+"),
    ("Asked from another Zurich municipality", False, r"XM-\d+"),
    ("Declined questions", False, r"OOS-\d+"),
]
OTHER_GROUP = "Other cases"
# Plain questions are ordered by language: the release's query languages first, then as the pack has them.
LANGUAGE_ORDER = ["EN", "DE", "FR", "IT", "ES"]


def load_case_catalogue(pack_dir: Path) -> str:
    """Render the catalogue of the pack in `pack_dir` from its committed suites and reports."""
    pack_dir = Path(pack_dir)
    acceptance = load_acceptance(pack_dir / "acceptance.yaml")
    regression_path = pack_dir / REGRESSION_FILE
    regression = load_acceptance(regression_path) if regression_path.is_file() else None
    report_path = pack_dir / REPORT_FILE
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    answers_path = pack_dir / ANSWERS_FILE
    answers = (AcceptanceAnswers.model_validate_json(answers_path.read_text(encoding="utf-8"))
               if answers_path.is_file() else None)
    return render_case_catalogue(acceptance, regression, report, answers)


def render_case_catalogue(acceptance: AcceptanceFile, regression: AcceptanceFile | None, report: dict | None,
                          answers: AcceptanceAnswers | None) -> str:
    """The catalogue page as Markdown; the same inputs always give the same text."""
    cases = [(case, True) for case in acceptance.cases] + [(case, False) for case in (regression.cases if regression else [])]
    results = {mode: {r["case_id"]: r for r in run.get("results", [])}
               for mode, run in (report or {}).get("runs", {}).items() if isinstance(run, dict)}
    grouped: dict[str, list[Case]] = {}
    for case, in_acceptance in cases:
        grouped.setdefault(group_of(case.case_id, in_acceptance), []).append(case)
    titles = [title for title, _, _ in GROUPS if title in grouped] + ([OTHER_GROUP] if OTHER_GROUP in grouped else [])

    lines = [f"# Test cases of {acceptance.pack}", ""]
    lines += header(acceptance, regression, report, answers)
    lines += summary_table(titles, grouped, results)
    lines += ["## Contents", ""]
    lines += [f"- [{title}](#{anchor(title)}) ({len(grouped[title])})" for title in titles]
    lines.append("")
    for title in titles:
        ordered = sorted(grouped[title], key=sort_key)
        lines += [f"## {title}", ""]
        lines += overview_table(ordered, results, answers)
        for case in ordered:
            lines += render_case(case, results, answers, in_acceptance=acceptance.case(case.case_id) is not None)
    return "\n".join(lines).rstrip("\n") + "\n"


def overview_table(cases: list[Case], results: dict[str, dict], answers: AcceptanceAnswers | None) -> list[str]:
    """One row per case of a group, linking to the case's section: the scannable view of the group."""
    modes = [mode for mode in MODES if mode in results]
    live = answers is not None and any(case.case_id in answers.cases for case in cases)
    head = "| Case | Question | Language | Status |" + "".join(f" {mode.capitalize()} |" for mode in modes)
    head += " Live caller |" if live else ""
    rule = "| --- | --- | --- | --- |" + " --- |" * (len(modes) + live)
    rows = []
    for case in cases:
        row = (f"| [{case.case_id}](#{case_anchor(case.case_id)}) | {cell(shorten(case.question))} | "
               f"{case.language or ''} | {'blocking' if case.blocking else 'quarantined'} |")
        row += "".join(f" {outcome(results[mode].get(case.case_id))} |" for mode in modes)
        if live:
            record = answers.cases.get(case.case_id)
            row += (f" trap {record.trap_held}/{record.graded}, criteria {record.criteria_pass}/{record.graded} |"
                    if record else " |")
        rows.append(row)
    return [head, rule] + rows + [""]


def outcome(result: dict | None) -> str:
    """A case's outcome in one mode: pass, fail, or a pass whose hybrid-only search the lexical run did not judge."""
    if result is None:
        return "not in the report"
    if not result["passed"]:
        return "fail"
    judged = all(search.get("judged", True) for search in result.get("searches", []))
    return "pass" if judged else "pass (not judged)"


def case_anchor(case_id: str) -> str:
    return case_id.lower()


def shorten(text: str, limit: int = 90) -> str:
    text = one_line(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "..."


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def group_of(case_id: str, in_acceptance: bool) -> str:
    for title, from_acceptance, pattern in GROUPS:
        if from_acceptance == in_acceptance and re.fullmatch(pattern, case_id):
            return title
    return OTHER_GROUP


def sort_key(case: Case) -> tuple:
    """Plain questions by language, then every case by its ID with numbers compared as numbers."""
    language = re.match(r"Q-([A-Z]+)-", case.case_id)
    rank = (LANGUAGE_ORDER.index(language.group(1)) if language.group(1) in LANGUAGE_ORDER else len(LANGUAGE_ORDER),
            language.group(1)) if language else (0, "")
    parts = tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", case.case_id))
    return rank, parts


def anchor(title: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def header(acceptance: AcceptanceFile, regression: AcceptanceFile | None, report: dict | None,
           answers: AcceptanceAnswers | None) -> list[str]:
    lines = [
        "Generated by `scripts/test/regression/run_regression.py` from `acceptance.yaml`, `regression.yaml`, "
        "`regression-report.json` and `acceptance-answers.json`; do not edit it by hand. The rules of the cases are "
        "in [docs/architecture/acceptance-gate.md](../../docs/architecture/acceptance-gate.md) (sections 2 and 10), "
        "the acceptance-test documents in [docs/product/](../../docs/product/).",
        "",
    ]
    if report is None:
        lines += ["No `regression-report.json`: the results are missing.", ""]
        return lines
    lines.append(f"Results: release `{report['release_id']}`, replayed on {report['checked_at'][:10]} with the "
                 f"snapshot date {report['as_of']} as the date of every resolve step that names none.")
    current = (report.get("acceptance_sha256"), report.get("regression_sha256")) == (
        acceptance.digest(), regression.digest() if regression else None)
    if not current:
        lines.append("The report is for other suites than the committed ones; run `run_regression.py` again.")
    lines.append("")
    if answers is not None:
        graded = sum(record.graded for record in answers.cases.values())
        release = ("the current release" if answers.content_sha256 == report["content_sha256"]
                   else f"the earlier release `{answers.release_id}`, not the current one")
        lines += [f"Live-caller grades: {graded} graded sessions of {len(answers.cases)} acceptance cases on "
                  f"{release}, aggregated on {answers.aggregated_at.date().isoformat()}.", ""]
    return lines


def summary_table(titles: list[str], grouped: dict[str, list[Case]], results: dict[str, dict]) -> list[str]:
    modes = [mode for mode in MODES if mode in results]
    head = "| Group | Cases | Quarantined |" + "".join(f" Pass {mode} |" for mode in modes)
    lines = [head, "| --- | --- | --- |" + " --- |" * len(modes)]
    totals = Counter()
    for title in titles:
        cases = grouped[title]
        row = Counter(cases=len(cases), quarantined=sum(1 for case in cases if not case.blocking))
        for mode in modes:
            row[mode] = sum(1 for case in cases if results[mode].get(case.case_id, {}).get("passed"))
        totals.update(row)
        lines.append(f"| [{title}](#{anchor(title)}) | {row['cases']} | {row['quarantined']} |"
                     + "".join(f" {row[mode]} |" for mode in modes))
    lines.append(f"| All | {totals['cases']} | {totals['quarantined']} |" + "".join(f" {totals[mode]} |" for mode in modes))
    return lines + [""]


def render_case(case: Case, results: dict[str, dict], answers: AcceptanceAnswers | None, in_acceptance: bool) -> list[str]:
    lines = [f"<a id=\"{case_anchor(case.case_id)}\"></a>", "", f"### {case.case_id}: {case.label}", ""]
    where = "Acceptance suite" if in_acceptance else "Regression pack"
    status = "blocking" if case.blocking else "quarantined"
    spec = f"; spec: [{case.spec}](../../{case.spec})" if case.spec else ""
    lines += [f"{where}, {status}{spec}.", ""]
    lines += [f"> {one_line(case.question)}", ""]
    if case.language:
        lines.append(f"- **Language:** {case.language}")
    lines.append(f"- **Expected answer:** {one_line(case.expected_answer)}")
    if case.trap:
        lines.append(f"- **Trap:** {one_line(case.trap)}")
    if not case.blocking and case.quarantine_reason:
        lines.append(f"- **Quarantined because:** {one_line(case.quarantine_reason)}")
    lines.append("")
    if case.steps:
        lines += ["**Steps**", ""]
        lines += [f"{number}. {describe_search(step.search) if step.search else describe_resolve(step.resolve)}"
                  for number, step in enumerate(case.steps, 1)]
        lines.append("")
    if case.claims:
        lines += ["**Claims on served facts**", ""]
        for claim in case.claims:
            where = f"`{claim.fact_id}`" if claim.fact_id else f"a fact of `{claim.concept}`"
            said = [f"states {quoted(claim.statement_contains)}"] if claim.statement_contains else []
            said += [f"its excerpt says {quoted(claim.excerpt_contains)}"] if claim.excerpt_contains else []
            lines.append(f"- {one_line(claim.claim)}: {where}" + (f" {'; '.join(said)}" if said else ""))
        lines.append("")
    if case.not_served:
        lines += [f"**Still declared as not served:** " + "; ".join(
            f"`{concept}`: {quoted(phrases)}" for concept, phrases in case.not_served.items()), ""]
    if case.must_not_serve:
        lines += [f"**Must not be served:** {codes(case.must_not_serve)}", ""]
    if case.answer is not None:
        lines += answer_criteria(case)
    lines += latest_results(case, results, answers)
    return lines


def describe_search(step: SearchStep) -> str:
    text = f"`search` \"{one_line(step.query)}\""
    if step.translated:
        text += " (key terms translated by the author)"
    if step.jurisdiction:
        text += " for " + ", ".join(f"{key} {value}" for key, value in step.jurisdiction.items())
    expects = []
    if step.expect_concept:
        expects.append(f"`{step.expect_concept}` among the first {step.within} hits")
    if step.expect_strength:
        expects.append(f"match strength `{step.expect_strength}`")
    if step.expect_elsewhere:
        expects.append(f"{codes(step.expect_elsewhere)} named as published elsewhere, not ranked")
    text += " - expects " + " and ".join(expects)
    if step.retrieval == "hybrid":
        text += " (hybrid search only)"
    return text


def describe_resolve(step: ResolveStep) -> str:
    place = ", ".join(f"{key} {value}" for key, value in step.jurisdiction.items()) or "no jurisdiction"
    text = f"`resolve` {codes(step.concept_ids)} for {place}"
    if step.context:
        text += ", context " + ", ".join(f"{key}={value}" for key, value in step.context.items())
    if step.as_of:
        text += f", as of {step.as_of.isoformat()}"
    if step.reviewed_only:
        text += ", reviewed facts only"
    if step.expect_error:
        return text + f" - expects rejection with an issue on `{step.expect_error}`"
    expects = []
    for concept_id in step.concept_ids:
        status = step.expect_status.get(concept_id)
        gap = step.expect_gap.get(concept_id)
        if status or gap:
            expects.append(f"`{concept_id}` {getattr(status, 'value', status) or ''}".rstrip()
                           + (f" with gap `{gap}`" if gap else ""))
    expects += [f"a fact of `{concept_id}` resting on \"{label}\"" for concept_id, label in step.expect_basis.items()]
    if step.expect_scope:
        expects.append(f"the request run for `{step.expect_scope}`")
    if step.expect_not_recognised is not None:
        expects.append(f"not recognised: {', '.join(step.expect_not_recognised) or 'none'}")
    return text + (" - expects " + "; ".join(expects) if expects else "")


def answer_criteria(case: Case) -> list[str]:
    answer = case.answer
    parts = []
    if answer.criteria:
        parts.append("criteria " + ", ".join(answer.criteria))
    if answer.must_mention:
        parts.append("must mention " + ", ".join(answer.must_mention))
    if answer.must_not:
        parts.append("must not assert " + ", ".join(answer.must_not))
    if answer.cites:
        parts.append("cites " + ", ".join(answer.cites))
    if answer.resolved:
        parts.append("resolves " + ", ".join(" or ".join(item) if isinstance(item, list) else item for item in answer.resolved))
    return [f"**Live-caller answer check:** {'; '.join(parts)}.", ""] if parts else []


def latest_results(case: Case, results: dict[str, dict], answers: AcceptanceAnswers | None) -> list[str]:
    lines = ["**Latest results**", ""]
    rows = []
    for mode in MODES:
        if mode not in results:
            continue
        result = results[mode].get(case.case_id)
        if result is None:
            rows.append(f"| {mode} | not in the report | | |")
            continue
        outcome = "pass" if result["passed"] else "fail"
        searches = result.get("searches", [])
        if not searches:
            rows.append(f"| {mode} | {outcome} | no search step | |")
        for search in searches:
            judged = "" if search.get("judged", True) else " (not judged)"
            rows.append(f"| {mode} | {outcome}{judged} | {codes(search.get('hits', [])) or 'no hits'} | "
                        f"{search.get('match_strength') or ''} |")
    if rows:
        lines += ["| Mode | Result | First hits | Match |", "| --- | --- | --- | --- |"] + rows + [""]
    else:
        lines += ["No replay in the committed report.", ""]
    # An issue reads "<case ID> (<label>): <what failed>"; the page names the case already.
    prefix = f"{case.case_id} ({case.label}): "
    issues = [f"- {mode}: {one_line(issue.removeprefix(prefix))}" for mode in MODES
              for issue in results.get(mode, {}).get(case.case_id, {}).get("issues", [])]
    if issues:
        lines += issues + [""]
    record = answers.cases.get(case.case_id) if answers is not None else None
    if record is not None:
        lines += [f"Live caller on `{answers.release_id}`: {record.graded} of {record.runs} sessions graded, trap held "
                  f"in {record.trap_held}, every criterion met in {record.criteria_pass}"
                  + (f"; {len(record.unsupported_claims)} unsupported specifics noted by the graders"
                     if record.unsupported_claims else "") + ".", ""]
    return lines


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def quoted(phrases: list[str]) -> str:
    return ", ".join(f"\"{one_line(phrase)}\"" for phrase in phrases)


def codes(items: list[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)
