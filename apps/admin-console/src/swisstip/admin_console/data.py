"""File readers of the console: one class per file family, cached by hash.

Section 8 of docs/architecture/admin-console.md. A reader remembers the
SHA-256 of what it parsed and re-reads only when the file's size or mtime
changed and the hash differs, so a screen that is reloaded every few seconds
costs one `stat` per file. Text records under `documents/` are read on demand
and never retained: the full pack has 12,117 of them.
"""

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from swisstip.build.curation import Curation
from swisstip.builder.pipeline import default_run_dir
from swisstip.core.release import Release
from swisstip.ingestion.catalog import validate_source_catalog
from swisstip.ingestion.review_decisions import REVIEW_FILE, apply_review_decisions

from .checks import parse_checks

PAGE_SIZE = 100
CANTONS = ["CH-" + code for code in
           "AG AI AR BE BL BS FR GE GL GR JU LU NE NW OW SG SH SO SZ TG TI UR VD VS ZG ZH".split()]
MATRIX_LANGUAGES = ["de", "fr", "it", "rm", "en"]
MATRIX_STAGES = ["catalogued", "saved", "extracted", "curated", "reviewed"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_curation(text: str) -> Curation:
    return Curation.model_validate(yaml.safe_load(text))


def parse_catalogue(text: str) -> dict:
    """A catalogue the console may edit; an empty source list is a new pack, not an error."""
    return validate_source_catalog(json.loads(text), allow_empty=True)


class CachedFile:
    """A file the console reads but never writes behind the pipeline's back.

    `current()` returns the parsed value, None when the file is absent, and
    None with `error` set when it does not parse. A hand-edited file that no
    longer validates is shown as an error instead of stopping the screen.
    """

    def __init__(self, path: Path, parse=json.loads):
        self.path = Path(path)
        self.parse = parse
        self._signature: tuple | None = None
        self._sha256: str | None = None
        self._value = None
        self._error: str | None = None

    def current(self):
        if not self.path.is_file():
            self._signature, self._sha256, self._value, self._error = None, None, None, None
            return None
        stat = self.path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature != self._signature:
            data = self.path.read_bytes()
            digest = sha256_bytes(data)
            self._signature = signature
            if digest != self._sha256:
                self._sha256, self._error, self._value = digest, None, None
                try:
                    self._value = self.parse(data.decode("utf-8"))
                except Exception as exc:  # a file the pipeline would refuse must not crash a screen
                    self._error = f"{type(exc).__name__}: {exc}"
        return self._value

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def sha256(self) -> str | None:
        self.current()
        return self._sha256

    @property
    def error(self) -> str | None:
        self.current()
        return self._error


class TextDataset:
    """The text dataset of a run: the index in memory, records on demand."""

    def __init__(self, text_dir: Path):
        self.text_dir = Path(text_dir)
        self.index_file = CachedFile(self.text_dir / "index.json")
        self.summary = CachedFile(self.text_dir / "summary.json")
        self.unavailable = CachedFile(self.text_dir / "unavailable.json")
        self.errors = CachedFile(self.text_dir / "errors.json")
        self._indexed_sha: str | None = None
        self._by_id: dict[str, dict] = {}
        self._by_source: dict[str, list[dict]] = {}

    @property
    def entries(self) -> list[dict]:
        return self.index_file.current() or []

    def _reindex(self) -> None:
        entries = self.entries
        if self.index_file.sha256 == self._indexed_sha:
            return
        self._by_id = {entry["document_id"]: entry for entry in entries}
        self._by_source = {}
        for entry in entries:
            self._by_source.setdefault(entry["source_url"], []).append(entry)
        self._indexed_sha = self.index_file.sha256

    def entry(self, document_id: str) -> dict | None:
        self._reindex()
        return self._by_id.get(document_id)

    def for_source(self, source_url: str) -> list[dict]:
        self._reindex()
        return self._by_source.get(source_url, [])

    def record(self, document_id: str) -> dict | None:
        """Read one record from disk. Records are not retained; the index is."""
        path = self.text_dir / "documents" / f"{document_id}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def successor(self, entry: dict) -> dict | None:
        """The index entry that replaced a superseded record: same source URL, newer, not superseded."""
        candidates = [other for other in self.for_source(entry["source_url"])
                      if other["document_id"] != entry["document_id"] and not other.get("superseded")
                      and other.get("eligible_for_processing") and other.get("preferred_representation")]
        if not candidates:
            return None
        return max(candidates, key=lambda other: other.get("retrieved_at") or "")


class PackData:
    """Every file of one pack, plus the joins the screens share."""

    def __init__(self, root: Path, pack: str, run_dir: Path | None = None):
        self.root = Path(root).resolve()
        self.pack = pack
        self.pack_dir = self.root / "releases" / pack
        self.run_dir = Path(run_dir).resolve() if run_dir else default_run_dir(self.root, pack)
        self.text_dir = self.run_dir / "text"
        # Job logs, audit and call logs are working state; they stay out of Git with the run.
        self.console_dir = self.root / ".local" / pack / "console"
        self.catalogue_path = self.pack_dir / "sources.json"
        self.curation_path = self.pack_dir / "curation.yaml"
        self.release_path = self.pack_dir / "release.json"
        self.checks_path = self.pack_dir / "checks.yaml"
        self.catalogue = CachedFile(self.catalogue_path, parse_catalogue)
        self.curation = CachedFile(self.curation_path, parse_curation)
        self.release = CachedFile(self.release_path, Release.model_validate_json)
        self.checks = CachedFile(self.checks_path, parse_checks)
        self.build_report = CachedFile(self.pack_dir / "build-report.json")
        self.pipeline_report = CachedFile(self.pack_dir / "pipeline-report.json")
        self.plan = CachedFile(self.run_dir / "plan.json")
        self.run_summary = CachedFile(self.run_dir / "summary.json")
        self.gap_report = CachedFile(self.run_dir / "gap-report.json")
        self.review_decisions = CachedFile(self.run_dir / REVIEW_FILE)
        self._reviewed_gap_signature: tuple | None = None
        self._reviewed_gap_report: dict = {}
        self.dataset = TextDataset(self.text_dir)

    # --- joins the screens share -------------------------------------------

    def curated_facts(self) -> list[tuple]:
        """(concept, fact) pairs in curation order."""
        curation = self.curation.current()
        if curation is None:
            return []
        return [(concept, fact) for concept in curation.concepts for fact in concept.facts]

    def fact(self, fact_id: str) -> tuple:
        for concept, fact in self.curated_facts():
            if fact.fact_id == fact_id:
                return concept, fact
        return None, None

    def concept(self, concept_id: str):
        curation = self.curation.current()
        for concept in (curation.concepts if curation else []):
            if concept.concept_id == concept_id:
                return concept
        return None

    def facts_by_document(self) -> dict[str, list[str]]:
        cited: dict[str, list[str]] = {}
        for _, fact in self.curated_facts():
            for citation in fact.evidence:
                cited.setdefault(citation.document_id, []).append(fact.fact_id)
        return cited

    def facts_by_block(self, document_id: str) -> dict[int, list[str]]:
        """Block number to the facts citing it, for the reading view margin."""
        marks: dict[int, list[str]] = {}
        for _, fact in self.curated_facts():
            for citation in fact.evidence:
                if citation.document_id != document_id:
                    continue
                for number in range(citation.first_block, (citation.last_block or citation.first_block) + 1):
                    marks.setdefault(number, []).append(fact.fact_id)
        return marks

    def citation_outcomes(self) -> dict[str, list[dict]]:
        """fact_id to the citation outcomes of the last build report."""
        report = self.build_report.current() or {}
        outcomes = {entry["fact_id"]: entry.get("citations", []) for entry in report.get("facts_resolved", [])}
        for entry in report.get("dropped", []):
            if "fact_id" in entry:
                outcomes[entry["fact_id"]] = entry.get("citations", [])
        return outcomes

    def source_entries(self) -> list[dict]:
        catalogue = self.catalogue.current() or {}
        return catalogue.get("sources", [])

    def source(self, source_id: str) -> dict | None:
        for entry in self.source_entries():
            if entry["definition"]["source_id"] == source_id:
                return entry
        return None

    def gap_targets(self) -> list[dict]:
        return self.acquisition_report().get("targets", [])

    def acquisition_report(self) -> dict:
        """Reapply current scope decisions when either report or decision file changes."""
        report = self.gap_report.current()
        if report is None:
            return {}
        signature = (self.gap_report.sha256, self.review_decisions.sha256)
        if signature != self._reviewed_gap_signature:
            self._reviewed_gap_report = apply_review_decisions(report, self.run_dir)
            self._reviewed_gap_signature = signature
        return self._reviewed_gap_report

    def gap_by_source(self) -> dict[str, dict]:
        """The catalogue target of each source, by source_id."""
        verdicts: dict[str, dict] = {}
        for target in self.gap_targets():
            if target.get("kind") != "catalogue":
                continue
            for source_id in target.get("source_ids", []):
                verdicts.setdefault(source_id, target)
        return verdicts

    def catalogue_changed(self) -> bool:
        """A run planned from a different catalogue needs a new run directory."""
        plan = self.plan.current()
        return bool(plan) and self.catalogue.sha256 is not None and plan.get("catalogue_sha256") != self.catalogue.sha256

    def release_id(self) -> str | None:
        release = self.release.current()
        return release.manifest.release_id if release else None

    def archived_releases(self) -> list[Path]:
        folder = self.console_dir / "releases"
        return sorted(folder.glob("*.json")) if folder.is_dir() else []

    def stages(self) -> list[dict]:
        return (self.pipeline_report.current() or {}).get("stages", [])


class Console:
    """The console state: the repository root, the mode and one PackData per pack."""

    def __init__(self, root: Path, *, read_only: bool = False, actor: str = "operator",
                 run_dirs: dict[str, Path] | None = None, server_log: Path | None = None):
        self.root = Path(root).resolve()
        self.read_only = read_only
        self.actor = actor
        self.run_dirs = {name: Path(path) for name, path in (run_dirs or {}).items()}
        self.server_log = Path(server_log) if server_log else None
        self._packs: dict[str, PackData] = {}

    def pack_names(self) -> list[str]:
        releases = self.root / "releases"
        names = {child.name for child in releases.iterdir() if child.is_dir()} if releases.is_dir() else set()
        local = self.root / ".local"
        if local.is_dir():
            names.update(child.name for child in local.iterdir()
                         if child.is_dir() and (child / "autopilot" / "workflow.json").is_file())
        return sorted(names)

    def pack(self, name: str) -> PackData:
        if name not in self._packs:
            if name not in self.pack_names():
                raise KeyError(name)
            self._packs[name] = PackData(self.root, name, self.run_dirs.get(name))
        return self._packs[name]


def paginate(rows: list, page: int, size: int = PAGE_SIZE) -> dict:
    """Server-side paging; every list of the console goes through it (rule 7)."""
    total = len(rows)
    pages = max(1, -(-total // size))
    page = max(1, min(page, pages))
    start = (page - 1) * size
    return dict(rows=rows[start:start + size], page=page, pages=pages, total=total, size=size,
                first=start + 1 if total else 0, last=min(start + size, total))


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


def record_language(entry: dict) -> str:
    language = entry.get("language_declared") or entry.get("language_hint") or "und"
    return language.replace("_", "-").split("-")[0].lower()


def coverage_matrix(pack: PackData) -> dict:
    """Rows CH and the 26 cantons, columns de/fr/it/rm/en, the highest stage reached (section 4.2)."""
    counts: dict[tuple[str, str], dict[str, int]] = {}

    def bump(jurisdiction: str, language: str, stage: str) -> None:
        if jurisdiction not in ("CH", *CANTONS) or language not in MATRIX_LANGUAGES:
            return
        counts.setdefault((jurisdiction, language), dict.fromkeys(MATRIX_STAGES, 0))[stage] += 1

    by_source: dict[str, tuple[str, str]] = {}
    for entry in pack.source_entries():
        definition = entry["definition"]
        by_source[definition["source_id"]] = (definition["jurisdiction"], definition["language"])
        bump(definition["jurisdiction"], definition["language"], "catalogued")
    for target in pack.gap_targets():
        if target.get("kind") != "catalogue" or target.get("status") != "saved":
            continue
        for source_id in target.get("source_ids", []):
            if source_id in by_source:
                bump(by_source[source_id][0], by_source[source_id][1], "saved")
    for entry in pack.dataset.entries:
        if not entry.get("eligible_for_processing"):
            continue
        for source_id in entry.get("source_ids", []):
            if source_id in by_source:
                bump(by_source[source_id][0], record_language(entry), "extracted")
                break
    for _, fact in pack.curated_facts():
        languages = {record_language(pack.dataset.entry(citation.document_id) or {})
                     for citation in fact.evidence if pack.dataset.entry(citation.document_id)}
        for language in languages or {fact.language}:
            bump(fact.jurisdiction, language, "curated")
            if fact.provenance.review_status == "human-reviewed":
                bump(fact.jurisdiction, language, "reviewed")
    rows = []
    for jurisdiction in ["CH", *CANTONS]:
        cells = []
        for language in MATRIX_LANGUAGES:
            cell = counts.get((jurisdiction, language), dict.fromkeys(MATRIX_STAGES, 0))
            reached = [stage for stage in MATRIX_STAGES if cell[stage]]
            cells.append(dict(jurisdiction=jurisdiction, language=language, counts=cell,
                              stage=reached[-1] if reached else None, count=cell[reached[-1]] if reached else 0))
        rows.append(dict(jurisdiction=jurisdiction, cells=cells))
    return dict(rows=rows, languages=MATRIX_LANGUAGES, stages=MATRIX_STAGES)


def pack_card(pack: PackData) -> dict:
    """The four rows of a pack card on the home screen (section 4.1)."""
    sources = pack.source_entries()
    gap_counts = pack.acquisition_report().get("counts", {})
    summary = pack.dataset.summary.current() or {}
    curation = pack.curation.current()
    release = pack.release.current()
    report = pack.pipeline_report.current() or {}
    plan = pack.plan.current() or {}
    facts = [fact for _, fact in pack.curated_facts()]
    checks = pack.checks.current()
    expected = checks.expected_fact_ids() if checks else set()
    known = {fact.fact_id for fact in facts}
    return dict(
        pack=pack.pack, run_dir=str(pack.run_dir), catalogue_error=pack.catalogue.error,
        curation_error=pack.curation.error, release_error=pack.release.error,
        catalogue_changed=pack.catalogue_changed(),
        sources=dict(total=len(sources), by_level=dict(Counter(entry["authority_level"] for entry in sources)),
                     planned=len(plan.get("targets", [])),
                     saved=(gap_counts.get("status") or {}).get("saved", 0),
                     retriable=gap_counts.get("retriable", 0), not_retriable=gap_counts.get("not_retriable", 0),
                     approved=gap_counts.get("approved", 0)),
        text=dict(records=summary.get("records", 0), eligible=summary.get("eligible", 0),
                  blocks=summary.get("blocks", 0), generated_at=(summary.get("generated_at") or "")[:10],
                  superseded=summary.get("superseded", 0)),
        curation=dict(topics=len(curation.topics) if curation else 0,
                      concepts=len(curation.concepts) if curation else 0, facts=len(facts),
                      review=dict(Counter(fact.provenance.review_status for fact in facts)),
                      kinds=dict(Counter(fact.provenance.kind for fact in facts)),
                      reviewed=sum(1 for fact in facts if fact.provenance.review_status == "human-reviewed"),
                      expected_by_checks=sorted(expected),
                      expected_present=sorted(expected & known), expected_missing=sorted(expected - known),
                      expected_reviewed=sum(1 for fact in facts if fact.fact_id in expected
                                            and fact.provenance.review_status == "human-reviewed")),
        release=dict(release_id=release.manifest.release_id if release else None,
                     snapshot_date=release.manifest.freshness.snapshot_date.isoformat() if release else None,
                     days_to_stale=(release.manifest.freshness.stale_from - date.today()).days if release else None,
                     last_run=report.get("finished_at"), exit_code=report.get("exit_code"),
                     stages=[dict(stage=stage["stage"], status=stage.get("status"), seconds=stage.get("seconds"),
                                  reason=stage.get("reason"), error=stage.get("error"))
                             for stage in report.get("stages", [])],
                     last_check_run=checks.last_run.model_dump(mode="json") if checks and checks.last_run else None),
        checks_error=pack.checks.error, checks=checks)
