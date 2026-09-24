"""The readiness record of a release: `releases/<pack>/readiness.json`.

Written by the knowledge builder's `ready` stage when every gate of the
acceptance gate passed (docs/architecture/acceptance-gate.md, section 5),
and read by the server, which with `--require-ready` refuses to serve a
release without a matching record. The record is bound to the bytes of
`release.json` (`release_sha256`), so any change to the served file, a
manifest-only change the content digest would not show included, makes the
release a candidate again; `suite_sha256` binds it to the acceptance suite,
which the build side checks. Nothing in the record is a status a person
could forget to reset: readiness is the existence of a record that matches.
"""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

READINESS_SCHEMA_VERSION = "swiss-tip-readiness/v2"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Gate(Strict):
    gate: str = Field(description="G1 to G6 of docs/architecture/acceptance-gate.md.")
    title: str
    status: Literal["passed", "failed"]
    detail: str = ""


class CaseCounts(Strict):
    total: int = Field(ge=0)
    blocking: int = Field(ge=0)
    quarantined: list[str] = Field(default_factory=list)


class CoverageCounts(Strict):
    """What the curation coverage stage found on this release: informational, not a gate (see acceptance-gate.md, section 5)."""

    policy: Literal["report", "enforce"]
    candidates: int = Field(ge=0, description="Candidate records of the text dataset a curator is expected to read.")
    content_sections: int = Field(ge=0)
    cited_sections: int = Field(ge=0)
    dispositioned_sections: int = Field(ge=0)
    unclassified_sections: int = Field(ge=0)
    clean: bool


class SemanticIndexBinding(Strict):
    file_sha256: str
    content_sha256: str
    release_id: str
    release_content_sha256: str
    model: str
    model_digest: str
    dimension: int = Field(ge=1)
    input_version: str
    query_version: str
    query_prefix_sha256: str
    min_score: float = Field(ge=-1, le=1)
    candidate_limit: int = Field(ge=1)


class Readiness(Strict):
    schema_version: Literal["swiss-tip-readiness/v1", "swiss-tip-readiness/v2"] = READINESS_SCHEMA_VERSION
    pack: str
    release_id: str
    release_sha256: str = Field(description="SHA-256 of the bytes of release.json; the record is for exactly that file.")
    content_sha256: str = Field(description="The release's content digest, for the reader.")
    suite_sha256: str = Field(description="Digest of the acceptance suite the gates ran with.")
    suite_file_sha256: str | None = Field(default=None, exclude_if=lambda value: value is None, description=(
        "SHA-256 of acceptance.yaml bytes when the suite is distributed beside the release."))
    attested_at: datetime
    attested_by: str = Field(min_length=1, description="The person who ran the gates and stands behind the record.")
    gates: list[Gate] = Field(min_length=1)
    cases: CaseCounts
    review_statuses: dict[str, int]
    snapshot_date: date
    stale_from: date
    min_runway_days: int = Field(ge=0)
    coverage: CoverageCounts | None = Field(default=None, description=(
        "The curation coverage of this release's content digest, when curation-coverage.json was current at attestation."))
    semantic_index: SemanticIndexBinding | None = Field(default=None, exclude_if=lambda value: value is None, description=(
        "Exact semantic index bytes and retrieval settings validated before attestation."))

    @model_validator(mode="after")
    def all_six_gates_are_present_once(self) -> "Readiness":
        names = [gate.gate for gate in self.gates]
        expected = {f"G{number}" for number in range(1, 7)}
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("readiness must contain exactly one gate each for G1 through G6")
        if self.schema_version == "swiss-tip-readiness/v1" and (
                self.suite_file_sha256 is not None or self.semantic_index is not None):
            raise ValueError("suite-file and semantic-index bindings require swiss-tip-readiness/v2")
        return self


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def readiness_path(release_path: Path) -> Path:
    return Path(release_path).with_name("readiness.json")


def load_readiness(path: Path) -> Readiness:
    return Readiness.model_validate_json(Path(path).read_text(encoding="utf-8"))


def dump_readiness(record: Readiness) -> str:
    return json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def readiness_status(release_path: Path, *, semantic_index: dict | None = None) -> dict:
    """`ready` when a record next to the release names exactly this file with every gate passed, else `candidate` and why."""
    path = readiness_path(release_path)
    if not path.is_file():
        return dict(status="candidate", reason=f"no readiness record at {path}")
    try:
        record = load_readiness(path)
    except (OSError, ValueError, ValidationError) as exc:
        return dict(status="candidate", reason=f"the readiness record does not load: {exc}")
    actual = sha256_file(release_path)
    if record.release_sha256 != actual:
        return dict(status="candidate", reason=f"the readiness record is for another release file "
                                               f"({record.release_id}, {record.release_sha256[:12]}); this file is {actual[:12]}")
    try:
        release_data = json.loads(Path(release_path).read_text(encoding="utf-8"))
        suite = (release_data.get("manifest") or {}).get("acceptance_suite_sha256")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return dict(status="candidate", reason=f"the release does not load: {exc}")
    if suite is not None and record.suite_sha256 != suite:
        return dict(status="candidate", reason="the readiness record is for another acceptance suite")
    suite_path = Path(release_path).with_name("acceptance.yaml")
    if record.suite_file_sha256 is not None and suite_path.is_file() \
            and record.suite_file_sha256 != sha256_file(suite_path):
        return dict(status="candidate", reason="acceptance.yaml changed after readiness was attested")
    if semantic_index is not None:
        if record.schema_version != "swiss-tip-readiness/v2" or record.semantic_index is None:
            return dict(status="candidate", reason="the readiness record does not attest semantic retrieval")
        if record.semantic_index.model_dump(mode="json") != semantic_index:
            return dict(status="candidate", reason="the semantic index or retrieval settings changed after readiness was attested")
    failed = [gate.gate for gate in record.gates if gate.status != "passed"]
    if failed:
        return dict(status="candidate", reason=f"the readiness record has failed gates: {failed}")
    return dict(status="ready", release_id=record.release_id, release_sha256=record.release_sha256,
                attested_at=record.attested_at.isoformat(), attested_by=record.attested_by,
                gates=[gate.gate for gate in record.gates], stale_from=record.stale_from.isoformat())
