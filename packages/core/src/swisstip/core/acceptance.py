"""The acceptance suite of a pack: `releases/<pack>/acceptance.yaml`.

One case per user question, as the acceptance-test documents state it: the
question as typed, the expected answer and the trap a generic answer falls
into, for a reader; the tool steps a caller sends, validated against the tool
contracts so that a case cannot drift from the API; the claims of the
expected answer, each checked against the facts the release serves and the
excerpts they cite; and, for the answer-quality check, what a live caller's
final answer must and must not say. `swisstip.runtime.acceptance` replays a
suite against a release with no model involved; the OpenCode harness under
`scripts/test/mock-mcp/` runs the questions through a live model and its
grades are aggregated into `acceptance-answers.json`, which `answer_verdict`
reads as gate G5. docs/architecture/acceptance-gate.md describes the gate.
"""

import json
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import GapDimension, Jurisdiction, ResolveRequest, SearchRequest, Status
from .release import sha256_text

ACCEPTANCE_SCHEMA_VERSION = "swiss-tip-acceptance/v1"
ANSWERS_SCHEMA_VERSION = "swiss-tip-acceptance-answers/v1"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(Strict):
    as_of: date | Literal["snapshot", "today"] = Field(default="snapshot", description=(
        "Applicability date of every resolve step that names none: the release's snapshot date, today, or a date. "
        "The snapshot date makes a suite pass the same way on every day; staleness is a gate of its own."))
    min_runway_days: int = Field(default=14, ge=0, description=(
        "A release is ready only while its stale_from lies at least this many days after the attestation."))
    answer_check: Literal["required", "advisory", "off"] = Field(default="advisory", description=(
        "Whether gate G5, the graded live-caller runs of acceptance-answers.json, blocks readiness (required), is "
        "reported only (advisory) or is not evaluated (off)."))
    answer_repetitions: int = Field(default=3, ge=1, description="Graded sessions a case needs on the release.")
    answer_pass_rate: float = Field(default=0.8, ge=0, le=1, description=(
        "Share of a case's graded sessions that must pass every applicable criterion; the trap must hold in all."))


class AnswerCriteria(Strict):
    """What the answer-quality check expects of a live caller's final answer to the case's question."""

    criteria: list[str] = Field(default_factory=list, description="IDs of the general criteria the grader applies.")
    must_mention: dict[str, str] = Field(default_factory=dict, description=(
        "Name to regular expression the final answer must match, case-insensitive."))
    must_not: dict[str, str] = Field(default_factory=dict, description=(
        "Name to regular expression the final answer must not assert (a negated or quoted match does not count)."))
    cites: dict[str, str] = Field(default_factory=dict, description="Name to URL fragment the answer must contain.")
    resolved: list[str | list[str]] = Field(default_factory=list, description=(
        "Concepts the caller must have resolved; a nested list names alternatives."))

    @model_validator(mode="after")
    def patterns_compile(self) -> "AnswerCriteria":
        for group in (self.must_mention, self.must_not):
            for name, pattern in group.items():
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"pattern {name!r} does not compile: {exc}") from exc
        return self


class SearchStep(Strict):
    query: str = Field(min_length=1, description=(
        "The query as the caller sends it: a question in one of the release's query languages as asked, otherwise its "
        "key terms translated into the preferred query language (see `translated`)."))
    translated: bool = Field(default=False, description=(
        "The query is a translation, prepared by the suite's author, of the key terms of a question whose language "
        "the release does not advertise for search, as a caller following the server's language note would send it."))
    retrieval: Literal["any", "hybrid"] = Field(default="any", description=(
        "`hybrid`: the expectations hold only with semantic search; a lexical replay records the step and what it "
        "would have failed on without failing the case."))
    expect_concept: str | None = Field(default=None, description="The concept that must be among the first `within` hits.")
    within: int = Field(default=3, ge=1, le=10)
    expect_strength: Literal["strong", "weak", "none"] | None = Field(default=None, description=(
        "The match_strength the result must report: strong for a question the release covers, weak or none for one "
        "it does not, so a decline is pinned at the search step and not only at resolve."))
    jurisdiction: dict[str, str] = Field(default_factory=dict, description=(
        "Where the user lives, as a caller that knows it sends it with the search; empty for a search without a place."))
    expect_elsewhere: list[str] = Field(default_factory=list, description=(
        "Concepts the result must name in published_elsewhere and must not rank: those published for other places "
        "only, which a caller at `jurisdiction` must not resolve."))

    @model_validator(mode="after")
    def expects_something(self) -> "SearchStep":
        if self.expect_concept is None and self.expect_strength is None and not self.expect_elsewhere:
            raise ValueError("a search step expects a concept, a match strength, concepts published elsewhere or several of them")
        if self.expect_elsewhere and not self.jurisdiction:
            raise ValueError("expect_elsewhere needs the jurisdiction the search is sent with")
        return self

    def request(self) -> SearchRequest:
        return SearchRequest(query=self.query, limit=self.within, jurisdiction=self.jurisdiction)


class ResolveStep(Strict):
    concept_ids: list[str] = Field(min_length=1)
    jurisdiction: dict[str, str] = Field(default_factory=dict)
    context: dict[str, str] = Field(default_factory=dict)
    as_of: date | None = Field(default=None, description="Overrides the policy date for this step.")
    reviewed_only: bool = False
    expect_status: dict[str, Status] = Field(default_factory=dict, description="Expected status per requested concept.")
    expect_gap: dict[str, GapDimension] = Field(default_factory=dict, description=(
        "Per requested concept, a gap dimension its result must name: the reason a decline gives."))
    expect_basis: dict[str, str] = Field(default_factory=dict, description=(
        "Per requested concept, the start of a basis label that at least one served fact must carry, for example "
        "'Federal act: AIG, SR 142.20, Art. 12' or 'Cantonal authority guidance'."))
    expect_scope: str | None = Field(default=None, description=(
        "The most specific jurisdiction code the request must have run for, for example CH-ZH-69: what the place "
        "register made of a jurisdiction given in names."))
    expect_not_recognised: list[str] | None = Field(default=None, description=(
        "The jurisdiction parts (country, canton, city) the result must report as not recognised, and no others; an "
        "empty list requires that every part was recognised. Not checked when absent."))
    expect_error: str | None = Field(default=None, description=(
        "The request must be rejected as INVALID_ARGUMENT with an issue on this path, for example jurisdiction.city "
        "for a name several municipalities share. Such a step expects nothing else."))

    @model_validator(mode="after")
    def matches_the_contract(self) -> "ResolveStep":
        self.request(date.today())  # every contract rule applies: field names, the concept limit, the jurisdiction codes
        for field in ("expect_status", "expect_gap", "expect_basis"):
            unknown = [concept_id for concept_id in getattr(self, field) if concept_id not in self.concept_ids]
            if unknown:
                raise ValueError(f"{field} names concepts the step does not request: {unknown}")
        unknown = [part for part in self.expect_not_recognised or [] if part not in ("country", "canton", "city")]
        if unknown:
            raise ValueError(f"expect_not_recognised names no jurisdiction part: {unknown}")
        if self.expect_error and (self.expect_status or self.expect_gap or self.expect_basis or self.expect_scope
                                  or self.expect_not_recognised is not None):
            raise ValueError("a step that expects an error cannot expect a status, a gap, a basis or a scope")
        return self

    def request(self, as_of: date) -> ResolveRequest:
        return ResolveRequest(concept_ids=self.concept_ids, jurisdiction=Jurisdiction(**self.jurisdiction),
                              context=self.context, as_of=self.as_of or as_of, reviewed_only=self.reviewed_only)


class Step(Strict):
    """One tool call of the case: exactly one of `search` and `resolve`."""

    search: SearchStep | None = None
    resolve: ResolveStep | None = None

    @model_validator(mode="after")
    def exactly_one_tool(self) -> "Step":
        if (self.search is None) == (self.resolve is None):
            raise ValueError("a step is either a search or a resolve")
        return self


class Claim(Strict):
    """One assertion of the expected answer, checked against a served fact and its cited excerpt."""

    claim: str = Field(min_length=1, description="The assertion in the reader's words.")
    concept: str = Field(description="The concept whose served facts must carry it.")
    fact_id: str | None = Field(default=None, description="Pins the fact; otherwise any served fact of the concept may hold it.")
    statement_contains: list[str] = Field(default_factory=list, description="Phrases the fact's statement contains.")
    excerpt_contains: list[str] = Field(default_factory=list, description=(
        "Phrases one cited excerpt of that fact contains, verbatim in the language of the source."))

    @model_validator(mode="after")
    def states_something_checkable(self) -> "Claim":
        if not (self.fact_id or self.statement_contains or self.excerpt_contains):
            raise ValueError(f"claim {self.claim!r} names neither a fact nor a phrase")
        return self


class Case(Strict):
    case_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    spec: str | None = Field(default=None, description="Section of the acceptance-test document the case restates.")
    question: str = Field(min_length=1, description="The user's question, verbatim.")
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$", description=(
        "Language of the question as typed (ISO 639 code, `gsw` for Swiss German); required when a search step is "
        "translated."))
    expected_answer: str = Field(min_length=1)
    trap: str | None = Field(default=None, description="The mistake a generic answer makes.")
    blocking: bool = True
    quarantine_reason: str | None = Field(default=None, description="Why a non-blocking case is kept in the suite.")
    steps: list[Step] = Field(default_factory=list, description=(
        "Tool calls to replay; a case without steps is checked only by the answer-quality check."))
    claims: list[Claim] = Field(default_factory=list)
    not_served: dict[str, list[str]] = Field(default_factory=dict, description=(
        "Per concept, phrases its not_served list must keep naming."))
    must_not_serve: list[str] = Field(default_factory=list, description="Fact IDs no step of the case may return.")
    answer: AnswerCriteria | None = Field(default=None, description=(
        "What the live caller's answer must and must not say; a case without it is not part of gate G5."))

    @model_validator(mode="after")
    def consistent(self) -> "Case":
        if self.blocking and self.quarantine_reason:
            raise ValueError(f"case {self.case_id} is blocking and has a quarantine reason")
        if not self.blocking and not (self.quarantine_reason or "").strip():
            raise ValueError(f"case {self.case_id} is not blocking and gives no quarantine reason")
        if any(step.search and step.search.translated for step in self.steps) and not self.language:
            raise ValueError(f"case {self.case_id} translates a search query but names no question language")
        resolved = {concept_id for step in self.steps if step.resolve for concept_id in step.resolve.concept_ids}
        for claim in self.claims:
            if claim.concept not in resolved:
                raise ValueError(f"case {self.case_id}: claim {claim.claim!r} names {claim.concept}, which no resolve step requests")
        for concept_id in self.not_served:
            if concept_id not in resolved:
                raise ValueError(f"case {self.case_id}: not_served names {concept_id}, which no resolve step requests")
        return self


class AcceptanceFile(Strict):
    schema_version: Literal["swiss-tip-acceptance/v1"] = ACCEPTANCE_SCHEMA_VERSION
    pack: str
    policy: Policy = Field(default_factory=Policy)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> "AcceptanceFile":
        ids = [case.case_id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate case IDs: {duplicates}")
        return self

    def case(self, case_id: str) -> Case | None:
        return next((case for case in self.cases if case.case_id == case_id), None)

    def digest(self) -> str:
        """SHA-256 over the canonical JSON of the suite: a reformatted or commented YAML keeps it, a changed expectation does not.

        Every field counts with its default, except the fields added after the first attested suites
        (OPTIONAL_DIGEST_FIELDS), which count only when set, so adding such a field does not invalidate a readiness
        record whose suite does not use it."""
        data = self.model_dump(mode="json")
        for case in data["cases"]:
            drop_defaults(case, OPTIONAL_DIGEST_FIELDS["case"])
            for step in case["steps"]:
                if step.get("search"):
                    drop_defaults(step["search"], OPTIONAL_DIGEST_FIELDS["search"])
                if step.get("resolve"):
                    drop_defaults(step["resolve"], OPTIONAL_DIGEST_FIELDS["resolve"])
        return sha256_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


# Fields added after the first attested suites, with their defaults: absent from the digest while unset.
OPTIONAL_DIGEST_FIELDS = {
    "case": {"language": None},
    "search": {"translated": False, "retrieval": "any", "jurisdiction": {}, "expect_elsewhere": []},
    "resolve": {"expect_scope": None, "expect_not_recognised": None, "expect_error": None},
}


def drop_defaults(item: dict, defaults: dict) -> None:
    for name, default in defaults.items():
        if item.get(name) == default:
            item.pop(name, None)


class AnswerRecord(Strict):
    """The graded live-caller sessions of one case on one release, as the harness aggregated them."""

    runs: int = Field(ge=0, description="Answered sessions with the server on this release.")
    graded: int = Field(ge=0)
    trap_held: int = Field(ge=0, description="Graded sessions whose answer avoided the case's trap.")
    criteria_pass: int = Field(ge=0, description="Graded sessions that met every applicable criterion.")
    models: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list, description="Specifics the graders found unsupported.")
    run_folders: list[str] = Field(default_factory=list)


class AcceptanceAnswers(Strict):
    """`releases/<pack>/acceptance-answers.json`: the grades of the live-caller runs, bound to a release and a suite."""

    schema_version: Literal["swiss-tip-acceptance-answers/v1"] = ANSWERS_SCHEMA_VERSION
    pack: str
    release_id: str
    content_sha256: str
    suite_sha256: str
    aggregated_at: datetime
    cases: dict[str, AnswerRecord] = Field(default_factory=dict)


def answer_verdict(suite: AcceptanceFile, answers: AcceptanceAnswers | None, content_sha256: str) -> dict:
    """Gate G5: whether the graded sessions on this release meet the policy for every blocking case with an answer block.

    `passed` is true when no case falls short, or when the policy is advisory or off; `detail` says what was found."""
    policy = suite.policy
    checked = [case for case in suite.cases if case.blocking and case.answer is not None]
    if policy.answer_check == "off":
        return dict(passed=True, mode="off", detail="answer check off", cases={})
    if answers is None:
        return dict(passed=policy.answer_check != "required", mode=policy.answer_check,
                    detail="no acceptance-answers.json", cases={})
    if answers.content_sha256 != content_sha256 or answers.suite_sha256 != suite.digest():
        return dict(passed=policy.answer_check != "required", mode=policy.answer_check,
                    detail="acceptance-answers.json is for another release or suite", cases={})
    cases: dict[str, dict] = {}
    for case in checked:
        record = answers.cases.get(case.case_id)
        if record is None or record.graded == 0:
            cases[case.case_id] = dict(passed=False, reason="no graded session")
            continue
        rate = record.criteria_pass / record.graded
        reasons = []
        if record.graded < policy.answer_repetitions:
            reasons.append(f"{record.graded} of {policy.answer_repetitions} graded sessions")
        if record.trap_held != record.graded:
            reasons.append(f"trap held in {record.trap_held} of {record.graded}")
        if rate < policy.answer_pass_rate:
            reasons.append(f"criteria passed in {record.criteria_pass} of {record.graded}, below {policy.answer_pass_rate:.0%}")
        cases[case.case_id] = dict(passed=not reasons, graded=record.graded, trap_held=record.trap_held,
                                   criteria_pass=record.criteria_pass, reason="; ".join(reasons))
    failing = [case_id for case_id, verdict in cases.items() if not verdict["passed"]]
    detail = (f"{len(checked) - len(failing)} of {len(checked)} answer-checked case(s) meet the policy"
              + ("; short: " + ", ".join(f"{case_id} ({cases[case_id]['reason']})" for case_id in failing) if failing else ""))
    return dict(passed=not failing or policy.answer_check == "advisory", mode=policy.answer_check, detail=detail, cases=cases)
