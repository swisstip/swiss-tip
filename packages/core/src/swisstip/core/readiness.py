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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

READINESS_SCHEMA_VERSION = "swiss-tip-readiness/v1"


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


class Readiness(Strict):
    schema_version: Literal["swiss-tip-readiness/v1"] = READINESS_SCHEMA_VERSION
    pack: str
    release_id: str
    release_sha256: str = Field(description="SHA-256 of the bytes of release.json; the record is for exactly that file.")
    content_sha256: str = Field(description="The release's content digest, for the reader.")
    suite_sha256: str = Field(description="Digest of the acceptance suite the gates ran with.")
    attested_at: datetime
    attested_by: str = Field(min_length=1, description="The person who ran the gates and stands behind the record.")
    gates: list[Gate] = Field(min_length=1)
    cases: CaseCounts
    review_statuses: dict[str, int]
    snapshot_date: date
    stale_from: date
    min_runway_days: int = Field(ge=0)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def readiness_path(release_path: Path) -> Path:
    return Path(release_path).with_name("readiness.json")


def load_readiness(path: Path) -> Readiness:
    return Readiness.model_validate_json(Path(path).read_text(encoding="utf-8"))


def dump_readiness(record: Readiness) -> str:
    return json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def readiness_status(release_path: Path) -> dict:
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
    failed = [gate.gate for gate in record.gates if gate.status != "passed"]
    if failed:
        return dict(status="candidate", reason=f"the readiness record has failed gates: {failed}")
    return dict(status="ready", release_id=record.release_id, release_sha256=record.release_sha256,
                attested_at=record.attested_at.isoformat(), attested_by=record.attested_by,
                gates=[gate.gate for gate in record.gates], stale_from=record.stale_from.isoformat())
