"""Replay a pack's acceptance suite against a release: the model-free check of the acceptance gate.

Every case of `swisstip.core.acceptance.AcceptanceFile` is answered by the same
:class:`ReleaseService` the server uses, with no model and no network: each
search step must find its concept within the first hits and report the expected match
strength (a covered question strong, an off-topic one weak or none), each resolve step
must be accepted and return the expected status and gap per concept, every claim of
the expected answer must hold on a served fact (and, where the claim quotes
the source, on one of that fact's cited excerpts), every `not_served` phrase
must still be declared, and no `must_not_serve` fact may be returned. A
curation or build change that drops a fact, weakens its wording, breaks its
jurisdiction or context match, or loses the alias a German question needs
fails here instead of at the next live demo. What a calling LLM composes from
the facts is judged by the harness under `scripts/test/mock-mcp/`, not here.
docs/architecture/acceptance-gate.md describes the whole gate.
"""

from datetime import UTC, date, datetime

from swisstip.core.acceptance import AcceptanceFile, Case, Claim, ResolveStep, SearchStep
from swisstip.core.contracts import ConceptResolution, ToolError
from swisstip.core.text import contains_phrase

from .service import ReleaseService

REPORT_SCHEMA_VERSION = "swiss-tip-acceptance-report/v1"
REGRESSION_REPORT_SCHEMA_VERSION = "swiss-tip-regression-report/v1"


def policy_date(service: ReleaseService, suite: AcceptanceFile) -> date:
    if suite.policy.as_of == "snapshot":
        return service.freshness.snapshot_date
    if suite.policy.as_of == "today":
        return date.today()
    return suite.policy.as_of


def check_acceptance(service: ReleaseService, suite: AcceptanceFile) -> dict:
    """The report of every case; `passed` is true when no blocking case failed."""
    as_of = policy_date(service, suite)
    results = [check_case(service, case, as_of) for case in suite.cases]
    failed = [r["case_id"] for r in results if r["blocking"] and not r["passed"]]
    quarantined = [r["case_id"] for r in results if not r["blocking"]]
    return dict(schema_version=REPORT_SCHEMA_VERSION, pack=suite.pack, release_id=service.release_id,
                content_sha256=service.release.manifest.content_sha256, suite_sha256=suite.digest(),
                checked_at=datetime.now(UTC).isoformat(), as_of=as_of.isoformat(), passed=not failed,
                cases=len(results), blocking=sum(1 for r in results if r["blocking"]), failed=failed,
                quarantined=quarantined, quarantined_failed=[r["case_id"] for r in results if not r["blocking"] and not r["passed"]],
                results=results)


def issues_of(report: dict) -> list[str]:
    """Every issue of every failed blocking case, in suite order."""
    return [issue for result in report["results"] if result["blocking"] for issue in result["issues"]]


def check_case(service: ReleaseService, case: Case, as_of: date) -> dict:
    prefix = f"{case.case_id} ({case.label})"
    issues: list[str] = []
    steps: list[dict] = []
    served: dict[str, list[ConceptResolution]] = {}
    for step in case.steps:
        if step.search is not None:
            steps.append(check_search(service, step.search, prefix, issues))
        else:
            steps.append(check_resolve(service, step.resolve, as_of, prefix, issues, served))
    claims = [check_claim(service, claim, served, prefix, issues) for claim in case.claims]
    for concept_id, phrases in case.not_served.items():
        declared = " ".join(item for resolution in served.get(concept_id, []) for item in resolution.not_served)
        for phrase in phrases:
            if not contains_phrase(declared, phrase):
                issues.append(f"{prefix}: {concept_id} not_served no longer names '{phrase}'")
    returned = {fact.fact_id for resolutions in served.values() for resolution in resolutions for fact in resolution.facts}
    for fact_id in case.must_not_serve:
        if fact_id in returned:
            issues.append(f"{prefix}: fact {fact_id} was served although the case excludes it")
    return dict(case_id=case.case_id, label=case.label, blocking=case.blocking, passed=not issues, issues=issues,
                steps=steps, claims=claims)


def check_search(service: ReleaseService, step: SearchStep, prefix: str, issues: list[str]) -> dict:
    result = service.search(step.request())
    if isinstance(result, ToolError):
        issues.append(f"{prefix}: search {step.query!r} rejected: {result.error.code.value}")
        return dict(tool="search", query=step.query, passed=False)
    hits = [hit.concept_id for hit in result.results]
    found: list[str] = []
    if step.expect_concept is not None and step.expect_concept not in hits:
        found.append(f"{prefix}: search {step.query!r} no longer finds {step.expect_concept} within the first "
                     f"{step.within} hits: {hits}")
    if step.expect_strength is not None and result.match_strength != step.expect_strength:
        found.append(f"{prefix}: search {step.query!r} reports match_strength {result.match_strength}, expected "
                     f"{step.expect_strength} (signals {result.match_signals.model_dump(exclude_none=True)})")
    elsewhere = [item.concept_id for item in result.published_elsewhere]
    for concept_id in step.expect_elsewhere:
        if concept_id in hits or concept_id not in elsewhere:
            found.append(f"{prefix}: search {step.query!r} for {step.jurisdiction} should name {concept_id} in "
                         f"published_elsewhere and not rank it: hits {hits}, published_elsewhere {elsewhere}")
    record = dict(tool="search", query=step.query, expect_concept=step.expect_concept, hits=hits,
                  retrieval_mode=result.retrieval_mode, match_strength=result.match_strength,
                  expect_strength=step.expect_strength, passed=not found)
    if step.translated:
        record["translated"] = True
    if step.jurisdiction:
        record.update(jurisdiction=step.jurisdiction, published_elsewhere=elsewhere)
    if step.retrieval == "hybrid" and result.retrieval_mode != "hybrid":
        # The expectation is made for semantic search; without it the step is recorded, not judged.
        record.update(passed=True, judged=False, unjudged_issues=found)
        return record
    issues.extend(found)
    return record


def check_resolve(service: ReleaseService, step: ResolveStep, as_of: date, prefix: str, issues: list[str],
                  served: dict[str, list[ConceptResolution]]) -> dict:
    request = step.request(as_of)
    result = service.resolve(request)
    if isinstance(result, ToolError):
        # A rejection is what the step asks for when it names the path of the issue: a shared place name, a city
        # outside the given canton.
        paths = [issue.path for issue in result.error.issues]
        rejected = dict(tool="resolve", concept_ids=step.concept_ids, error=result.error.code.value, error_paths=paths)
        if step.expect_error and result.error.code.value == "INVALID_ARGUMENT" and step.expect_error in paths:
            return dict(rejected, passed=True)
        issues.append(f"{prefix}: resolve request rejected: {result.error.code.value} on {', '.join(paths) or 'no path'}"
                      + (f", expected an issue on {step.expect_error}" if step.expect_error else ""))
        return dict(rejected, passed=False)
    per = {item.concept_id: item for item in result.results}
    passed = True
    if step.expect_error:
        issues.append(f"{prefix}: expected the request to be rejected on {step.expect_error}, but it was answered")
        passed = False
    scope = result.executed_scope
    ran_for = scope.municipality_id or scope.canton_code or scope.country_code
    if step.expect_scope is not None and ran_for != step.expect_scope:
        issues.append(f"{prefix}: expected the request to run for {step.expect_scope}, it ran for {ran_for}")
        passed = False
    if step.expect_not_recognised is not None and sorted(scope.not_recognised) != sorted(step.expect_not_recognised):
        issues.append(f"{prefix}: expected {sorted(step.expect_not_recognised) or 'no part'} not recognised, "
                      f"got {sorted(scope.not_recognised) or 'none'}")
        passed = False
    for concept_id, expected in step.expect_status.items():
        actual = per[concept_id].status
        if actual != expected:
            issues.append(f"{prefix}: {concept_id} expected {expected.value}, got {actual.value}")
            passed = False
    for concept_id, dimension in step.expect_gap.items():
        named = [gap.dimension for gap in per[concept_id].gaps]
        if dimension not in named:
            issues.append(f"{prefix}: {concept_id} expected a {dimension} gap, got {named or 'none'}")
            passed = False
    for concept_id, expected in step.expect_basis.items():
        labels = served_bases(per[concept_id])
        if not any(label.startswith(expected) for label in labels):
            issues.append(f"{prefix}: {concept_id} expected a fact resting on '{expected}', got {labels or 'no basis'}")
            passed = False
    for item in result.results:
        served.setdefault(item.concept_id, []).append(item)
    return dict(tool="resolve", concept_ids=step.concept_ids, as_of=request.as_of.isoformat(), scope=ran_for,
                not_recognised=dict(scope.not_recognised),
                statuses={item.concept_id: item.status.value for item in result.results},
                gaps={item.concept_id: [gap.dimension for gap in item.gaps] for item in result.results},
                facts={item.concept_id: [fact.fact_id for fact in item.facts] for item in result.results},
                bases={item.concept_id: served_bases(item) for item in result.results}, passed=passed)


def served_bases(item: ConceptResolution) -> list[str]:
    """The distinct basis labels of a concept's served facts, whether stated on the facts or once on the concept."""
    return sorted({fact.basis or item.basis for fact in item.facts if fact.basis or item.basis})


def check_claim(service: ReleaseService, claim: Claim, served: dict[str, list[ConceptResolution]], prefix: str,
                issues: list[str]) -> dict:
    facts = [fact for resolution in served.get(claim.concept, []) for fact in resolution.facts]
    if claim.fact_id:
        facts = [fact for fact in facts if fact.fact_id == claim.fact_id]
        if not facts:
            issues.append(f"{prefix}: fact {claim.fact_id} is no longer served for {claim.concept} ('{claim.claim}')")
            return dict(claim=claim.claim, concept=claim.concept, passed=False)
    stated = [fact for fact in facts if all(contains_phrase(fact.statement, phrase) for phrase in claim.statement_contains)]
    if not stated:
        missing = next((phrase for phrase in claim.statement_contains
                        if not any(contains_phrase(fact.statement, phrase) for fact in facts)), None)
        what = f"'{missing}'" if missing else "every phrase together"
        issues.append(f"{prefix}: {claim.concept} fact statements no longer state {what} ('{claim.claim}')")
        return dict(claim=claim.claim, concept=claim.concept, passed=False)
    if not claim.excerpt_contains:
        return dict(claim=claim.claim, concept=claim.concept, fact_ids=[fact.fact_id for fact in stated], passed=True)
    backed = [fact for fact in stated if any(
        all(contains_phrase(service.evidence[evidence_id].original_excerpt, phrase) for phrase in claim.excerpt_contains)
        for evidence_id in fact.evidence_ids)]
    if not backed:
        names = ", ".join(fact.fact_id for fact in stated)
        issues.append(f"{prefix}: {names} state(s) '{claim.claim}' but no cited excerpt contains "
                      + " and ".join(f"'{phrase}'" for phrase in claim.excerpt_contains))
        return dict(claim=claim.claim, concept=claim.concept, fact_ids=[fact.fact_id for fact in stated], passed=False)
    return dict(claim=claim.claim, concept=claim.concept, fact_ids=[fact.fact_id for fact in backed], passed=True)


def regression_run(report: dict) -> dict:
    """One retrieval mode's replay of a regression pack, compacted to what a reader compares between runs."""
    results = report["results"]
    cases = []
    for result in results:
        searches = [dict(query=step["query"], translated=step.get("translated", False), hits=step.get("hits", []),
                         match_strength=step.get("match_strength"), judged=step.get("judged", True),
                         **({"unjudged_issues": step["unjudged_issues"]} if step.get("unjudged_issues") else {}))
                    for step in result["steps"] if step["tool"] == "search"]
        cases.append(dict(case_id=result["case_id"], blocking=result["blocking"], passed=result["passed"],
                          issues=result["issues"], searches=searches))
    modes = {step["retrieval_mode"] for result in results for step in result["steps"] if step.get("retrieval_mode")}
    return dict(retrieval_mode=", ".join(sorted(modes)), cases=len(results),
                passed=sum(1 for r in results if r["passed"]), failed=report["failed"],
                quarantined=report["quarantined"], quarantined_failed=report["quarantined_failed"],
                quarantined_passing=[r["case_id"] for r in results if not r["blocking"] and r["passed"]],
                unjudged_steps=sum(1 for c in cases for s in c["searches"] if not s["judged"]), results=cases)


def regression_report(reports: dict[str, dict | str], acceptance_sha256: str, regression_sha256: str) -> dict:
    """`releases/<pack>/regression-report.json`: the regression pack replayed per retrieval mode.

    `reports` maps a mode (lexical, hybrid) to its check_acceptance report, or to the reason it was not run. `passed`
    is true when every mode ran and no blocking case failed in any of them."""
    first = next(r for r in reports.values() if isinstance(r, dict))
    runs = {mode: regression_run(r) if isinstance(r, dict) else dict(skipped=r) for mode, r in reports.items()}
    return dict(schema_version=REGRESSION_REPORT_SCHEMA_VERSION, pack=first["pack"], release_id=first["release_id"],
                content_sha256=first["content_sha256"], acceptance_sha256=acceptance_sha256,
                regression_sha256=regression_sha256, checked_at=datetime.now(UTC).isoformat(), as_of=first["as_of"],
                passed=all("skipped" not in run and not run["failed"] for run in runs.values()), runs=runs)
