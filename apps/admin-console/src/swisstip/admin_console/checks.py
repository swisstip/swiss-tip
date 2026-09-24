"""Stored tool checks of a pack: `releases/<pack>/checks.yaml`.

Section 6.4 of docs/architecture/admin-console.md. A check is a name, a tool,
a request and expectations. The request is validated against the contract
model of its tool, so a check can never drift from the contract; the runner
answers it with the same `ReleaseService` the server uses, so a passing check
means the served release answers it.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from swisstip.core.contracts import TOOL_CONTRACTS
from swisstip.runtime.service import ReleaseService

from . import CHECKS_SCHEMA_VERSION

TOOLS = ("get_knowledge_graph", "get_coverage", "search", "resolve", "get_evidence")

# The two standing cases of the specification, as scripts/test/mcp/check_server.py asserts them.
STANDING_CASES = [
    dict(check_id="czech-registration",
         title="Czech citizen starting work in Zurich, registration deadline",
         tool="resolve",
         request=dict(concept_ids=["eu-employment-registration-deadline", "zh-eu-registration"],
                      jurisdiction=dict(country_code="CH", canton_code="CH-ZH"),
                      as_of="2026-09-12", context=dict(population="eu_efta")),
         expect=dict(status={"eu-employment-registration-deadline": "SUPPORTED", "zh-eu-registration": "SUPPORTED"})),
    dict(check_id="third-country-work-permit",
         title="Third-country national taking up employment, work admission and permit procedure",
         tool="resolve",
         request=dict(concept_ids=["third-country-work", "aig-work-permit", "permit-authority"],
                      jurisdiction=dict(country_code="CH"), as_of="2026-09-12",
                      context=dict(population="third_country")),
         expect=dict(status={"third-country-work": "SUPPORTED", "aig-work-permit": "SUPPORTED",
                             "permit-authority": "SUPPORTED"})),
    dict(check_id="search-registration-zh",
         title="Search finds the Zurich registration concept",
         tool="search",
         request=dict(query="register stay municipal authority Zurich", limit=5),
         expect=dict(concept_ids_in_top=["zh-eu-registration"])),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expect(Strict):
    """What the answer must contain. Every field is optional; an empty expectation set passes."""

    status: dict[str, str] = Field(default_factory=dict, description="Expected status per concept_id (resolve).")
    fact_ids: list[str] = Field(default_factory=list, description="Fact IDs that must be present (resolve).")
    concept_ids_in_top: list[str] = Field(default_factory=list, description="Concept IDs in the ranked hits (search).")


class Check(Strict):
    check_id: str
    title: str = ""
    tool: Literal["get_knowledge_graph", "get_coverage", "search", "resolve", "get_evidence"]
    request: dict = Field(default_factory=dict)
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def request_matches_the_contract(self) -> "Check":
        TOOL_CONTRACTS[self.tool][0].model_validate(self.request)
        return self

    def request_model(self):
        return TOOL_CONTRACTS[self.tool][0].model_validate(self.request)


class LastRun(Strict):
    release_id: str
    at: datetime
    results: dict[str, str] = Field(default_factory=dict)


class Sample(Strict):
    """A spot-check verdict on one document, for packs whose facts are derived in the tens of thousands."""

    reviewed_by: str
    on: date
    blocks_checked: int = Field(ge=0)
    verdict: str


class ChecksFile(Strict):
    schema_version: Literal["swiss-tip-checks/v1"] = CHECKS_SCHEMA_VERSION
    pack: str
    checks: list[Check] = Field(default_factory=list)
    last_run: LastRun | None = None
    samples: dict[str, Sample] = Field(default_factory=dict)

    def check(self, check_id: str) -> Check | None:
        return next((item for item in self.checks if item.check_id == check_id), None)

    def expected_fact_ids(self) -> set[str]:
        return {fact_id for item in self.checks for fact_id in item.expect.fact_ids}

    def expected_concept_ids(self) -> set[str]:
        return {concept_id for item in self.checks
                for concept_id in (*item.expect.status, *item.expect.concept_ids_in_top)}


def parse_checks(text: str) -> ChecksFile:
    return ChecksFile.model_validate(yaml.safe_load(text))


def load_checks(path: Path) -> ChecksFile:
    return parse_checks(Path(path).read_text(encoding="utf-8"))


def dump_checks(checks: ChecksFile) -> str:
    return yaml.safe_dump(checks.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False,
                          width=110)


def save_checks(path: Path, checks: ChecksFile) -> None:
    Path(path).write_text(dump_checks(checks), encoding="utf-8", newline="\n")


def seed_checks(pack: str, service: ReleaseService | None = None) -> ChecksFile:
    """The standing cases, kept to the concepts the release publishes."""
    known = set(service.concepts) if service else None
    seeded = []
    for case in STANDING_CASES:
        check = Check.model_validate(case)
        if known is not None:
            if check.tool == "resolve":
                concept_ids = [item for item in check.request.get("concept_ids", []) if item in known]
                if not concept_ids:
                    continue
                check.request = {**check.request, "concept_ids": concept_ids}
                check.expect.status = {key: value for key, value in check.expect.status.items() if key in known}
            check.expect.concept_ids_in_top = [item for item in check.expect.concept_ids_in_top if item in known]
            if check.tool == "search" and not check.expect.concept_ids_in_top:
                continue  # an expectation with no published concept would assert nothing
        seeded.append(Check.model_validate(check.model_dump()))  # the narrowed request must still fit the contract
    return ChecksFile(pack=pack, checks=seeded)


def expectations(check: Check, answer) -> list[dict]:
    """One row per expectation with its verdict, in the order the check states them."""
    payload = answer.model_dump(mode="json")
    rows: list[dict] = []
    if "error" in payload:
        return [dict(description="the tool answers without an error", passed=False,
                     detail=payload["error"]["code"] + ": " + "; ".join(i["message"] for i in payload["error"]["issues"]))]
    by_concept = {item["concept_id"]: item for item in payload.get("results", [])}
    for concept_id, status in check.expect.status.items():
        actual = by_concept.get(concept_id, {}).get("status")
        rows.append(dict(description=f"{concept_id} resolves {status}", passed=actual == status,
                         detail=f"got {actual or 'no result'}"))
    if check.expect.fact_ids:
        served = {fact["fact_id"] for item in payload.get("results", []) for fact in item.get("facts", [])}
        for fact_id in check.expect.fact_ids:
            rows.append(dict(description=f"fact {fact_id} is served", passed=fact_id in served,
                             detail="present" if fact_id in served else "missing"))
    if check.expect.concept_ids_in_top:
        hits = [hit["concept_id"] for hit in payload.get("results", [])]
        for concept_id in check.expect.concept_ids_in_top:
            rank = hits.index(concept_id) + 1 if concept_id in hits else None
            rows.append(dict(description=f"{concept_id} is in the ranked hits", passed=rank is not None,
                             detail=f"rank {rank}" if rank else "not in the hits"))
    if not rows:
        rows.append(dict(description="the tool answers without an error", passed=True, detail="no expectation stated"))
    return rows


def run_check(check: Check, service: ReleaseService) -> dict:
    answer = service.dispatch(check.tool, check.request)
    rows = expectations(check, answer)
    return dict(check_id=check.check_id, title=check.title, tool=check.tool, passed=all(row["passed"] for row in rows),
                expectations=rows, answer=answer.model_dump(mode="json", exclude_none=True))


def run_checks(checks: ChecksFile, service: ReleaseService) -> tuple[ChecksFile, list[dict]]:
    """Run every check and record the run in the file; returns the updated file and the results."""
    results = [run_check(check, service) for check in checks.checks]
    updated = checks.model_copy(deep=True)
    updated.last_run = LastRun(release_id=service.release_id, at=datetime.now(UTC),
                               results={row["check_id"]: ("pass" if row["passed"] else "fail") for row in results})
    return updated, results
