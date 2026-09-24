"""The two write targets of the console, and the audit log of every write.

Section 6 of docs/architecture/admin-console.md. `curation.yaml` is written
through the curation models and `save_curation`, `sources.json` through the
ingestion package's catalogue writer, so the console can never write a file
the pipeline would refuse. Before a curation file is saved the build is run
into memory against the text dataset: a fact that would be dropped refuses
the write, except where the relocation review drops it on purpose.

Every write is recorded in `.local/<pack>/console/audit.jsonl` with the actor,
the hashes before and after and the reason. The console never commits; the
lead commits from Git after reading the diff on the release screen.
"""

import json
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import ValidationError

from swisstip.build.curation import Curation, load_curation, save_curation
from swisstip.build.places import PlaceFileError, place_register_for
from swisstip.build.release_build import BuildError, build_release
from swisstip.builder.pipeline import next_release_id
from swisstip.builder.autopilot.store import PackWriteLock
from swisstip.ingestion.catalog import save_source_catalog, validate_source_catalog

from .checks import ChecksFile, save_checks
from .data import PackData, sha256_bytes

DRY_BUILD_BUDGET_SECONDS = 5.0


class WriteRefused(Exception):
    """The write was refused: the model, the dry build or the catalogue check said no."""

    def __init__(self, message: str, issues: list[str] | None = None, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.issues = issues or []
        self.fields = fields or {}


class WriteConflict(Exception):
    """The file changed since the form loaded it (section 5.2)."""

    def __init__(self, message: str, current_sha256: str | None, last_actor: str | None):
        super().__init__(message)
        self.message = message
        self.current_sha256 = current_sha256
        self.last_actor = last_actor


def field_errors(exc: ValidationError) -> dict[str, str]:
    return {".".join(str(part) for part in error["loc"]) or "request": error["msg"] for error in exc.errors()}


def audit_path(pack: PackData) -> Path:
    return pack.console_dir / "audit.jsonl"


def append_audit(pack: PackData, actor: str, target: Path, before: str | None, after: str, reason: str,
                 ids: list[str]) -> dict:
    entry = dict(at=datetime.now(UTC).isoformat(), actor=actor, file=str(target.relative_to(pack.root)),
                 sha256_before=before, sha256_after=after, reason=reason, ids=ids)
    path = audit_path(pack)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_audit(pack: PackData, limit: int = 200) -> list[dict]:
    path = audit_path(pack)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()][::-1]


def last_writer(pack: PackData, target: Path) -> str | None:
    relative = str(target.relative_to(pack.root))
    for entry in read_audit(pack):
        if entry.get("file") == relative:
            return f"{entry.get('actor')} at {entry.get('at', '')[:19]}"
    return None


class PackLocks:
    """One editor per pack (section 5.2): a form save and a writing job take the same lock."""

    def __init__(self):
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    def lock(self, pack: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(pack, threading.RLock())


LOCKS = PackLocks()


def pack_file_lock(pack: PackData) -> PackWriteLock:
    return PackWriteLock(pack.root, pack.pack)


def check_unchanged(pack: PackData, cached, target: Path, expected_sha256: str | None) -> None:
    current = cached.sha256
    if expected_sha256 and current != expected_sha256:
        raise WriteConflict(f"{target.name} changed since this form was loaded", current, last_writer(pack, target))


def dry_build(pack: PackData, curation: Curation, *, allow_drop: bool = False) -> dict:
    """Build the release into memory to catch what the pipeline would refuse (section 6.1 step 4)."""
    if not (pack.text_dir / "index.json").is_file():
        return dict(skipped="no text dataset; the build could not be tried")
    release_id = pack.release_id() or next_release_id(None, pack.pack, date.today())
    started = datetime.now(UTC)
    try:
        # With the place register the curation names: a fact for a jurisdiction the register does not list fails here.
        _, report = build_release(curation, pack.text_dir, release_id,
                                  place_register=place_register_for(curation, pack.curation_path))
    except (BuildError, PlaceFileError) as exc:
        raise WriteRefused(f"the build would fail: {exc}") from exc
    if report["dropped"] and not allow_drop:
        names = ", ".join(entry.get("fact_id") or entry.get("concept_id", "?") for entry in report["dropped"][:5])
        raise WriteRefused(f"the build would drop {len(report['dropped'])} fact(s): {names}",
                           [json.dumps(entry, ensure_ascii=False) for entry in report["dropped"][:5]])
    seconds = (datetime.now(UTC) - started).total_seconds()
    return dict(release_id=release_id, facts=report["facts"], concepts=report["concepts"],
                dropped=report["dropped"], citation_outcomes=report["citation_outcomes"], seconds=round(seconds, 2),
                slow=seconds > DRY_BUILD_BUDGET_SECONDS)


def write_curation(pack: PackData, mutate, actor: str, reason: str, *, expected_sha256: str | None = None,
                   ids: list[str] | None = None, allow_drop: bool = False) -> dict:
    """Load, mutate, validate, dry build, save, audit (section 6.1). Returns the dry-build report."""
    with pack_file_lock(pack), LOCKS.lock(pack.pack):
        if not pack.curation_path.is_file():
            raise WriteRefused(f"no curation file at {pack.curation_path}")
        if pack.curation.error:
            raise WriteRefused(f"{pack.curation_path.name} does not validate; fix it by hand first: {pack.curation.error}")
        check_unchanged(pack, pack.curation, pack.curation_path, expected_sha256)
        before = pack.curation.sha256
        curation = load_curation(pack.curation_path)
        mutate(curation)
        try:
            curation = Curation.model_validate(curation.model_dump(mode="python"))
        except ValidationError as exc:
            raise WriteRefused("the curation file would not validate", list(field_errors(exc).values()),
                               field_errors(exc)) from exc
        report = dry_build(pack, curation, allow_drop=allow_drop)
        save_curation(pack.curation_path, curation)
        after = sha256_bytes(pack.curation_path.read_bytes())
        append_audit(pack, actor, pack.curation_path, before, after, reason, ids or [])
        pack.curation.current()
        return report


def write_catalogue(pack: PackData, mutate, actor: str, reason: str, *, expected_sha256: str | None = None,
                    ids: list[str] | None = None) -> dict:
    """The same shape for `sources.json`, with the planning check instead of a dry build (section 6.2)."""
    with pack_file_lock(pack), LOCKS.lock(pack.pack):
        if not pack.catalogue_path.is_file():
            raise WriteRefused(f"no catalogue at {pack.catalogue_path}")
        if pack.catalogue.error:
            raise WriteRefused(f"{pack.catalogue_path.name} does not validate; fix it by hand first: {pack.catalogue.error}")
        check_unchanged(pack, pack.catalogue, pack.catalogue_path, expected_sha256)
        before = pack.catalogue.sha256
        data = json.loads(pack.catalogue_path.read_text(encoding="utf-8"))
        mutate(data)
        try:
            validate_source_catalog(data, allow_empty=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise WriteRefused(f"the catalogue would not validate: {exc}") from exc
        save_source_catalog(pack.catalogue_path, data)
        after = sha256_bytes(pack.catalogue_path.read_bytes())
        append_audit(pack, actor, pack.catalogue_path, before, after, reason, ids or [])
        pack.catalogue.current()
        return dict(sources=len(data["sources"]), needs_new_run=pack.catalogue_changed())


def write_checks(pack: PackData, checks: ChecksFile, actor: str, reason: str, ids: list[str] | None = None) -> None:
    with pack_file_lock(pack), LOCKS.lock(pack.pack):
        before = pack.checks.sha256
        pack.checks_path.parent.mkdir(parents=True, exist_ok=True)
        save_checks(pack.checks_path, checks)
        after = sha256_bytes(pack.checks_path.read_bytes())
        append_audit(pack, actor, pack.checks_path, before, after, reason, ids or [])
        pack.checks.current()


def record_rejection(pack: PackData, concept_id: str, fact, actor: str, note: str) -> None:
    """A rejected fact leaves the curation file and is kept, with its reason, outside Git (section 4.6)."""
    path = pack.console_dir / "rejected.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(at=datetime.now(UTC).isoformat(), reviewer=actor, concept_id=concept_id, note=note,
                 fact=fact.model_dump(mode="json", exclude_none=True))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_rejections(pack: PackData, limit: int = 100) -> list[dict]:
    path = pack.console_dir / "rejected.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()][::-1]
