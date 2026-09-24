"""The build pipeline of a pack: catalogue to validated, accepted and ready release in ten stages.

| Stage | Does | Skipped when |
| --- | --- | --- |
| acquire | Plans the run from `releases/<pack>/sources.json` (and `sources.md`); downloads with `download=True` | The run exists for this catalogue and no download was asked |
| gaps | Writes `gap-report.json` and `.md` into the run | The report is newer than the run summary and review decisions are unchanged |
| extract | Builds the text dataset `<run>/text` (reuses unchanged records itself) | never |
| validate-text | Checks hashes, offsets, index and views; re-hashes saved responses with `thorough=True` | never |
| build | Builds `release.json` from `curation.yaml`, relocating citations and embedding the place files it names | Curation, text index, place files and release ID unchanged since the last build; or no curation file yet |
| validate-release | Validates the release against the text dataset (and the run with `thorough=True`) | never |
| coverage | Joins the text dataset with the release: every content section of every candidate record is cited by a fact or dispositioned in `curation-coverage.yaml`; writes `curation-coverage.json` and `.md`; fails when not clean and the curation says `coverage_policy: enforce` | no release or no text index |
| health | Loads the release the way the server does and reports its counts | never |
| accept | Replays the pack's acceptance suite (`acceptance.yaml`) against the release, writes `acceptance-report.json` and fails on any blocking case | The pack has no acceptance suite |
| ready | Runs the gates of the acceptance gate on the current files (validation, no dropped fact, the suite, the runway, the graded live-caller answers, the committed report) and writes `readiness.json` bound to the bytes of `release.json`; needs `attested_by` | never; only runs when asked (`--until ready`) |

A failed stage stops the pipeline. Every run writes `releases/<pack>/pipeline-report.json`.
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from swisstip.build.acceptance import load_acceptance
from swisstip.build.coverage import build_coverage, coverage_counts, load_dispositions, write_report
from swisstip.build.curation import load_curation, save_curation
from swisstip.build.places import PlaceFileError, place_files, place_register_for
from swisstip.build.release_build import BuildError, build_release
from swisstip.core.acceptance import AcceptanceAnswers, answer_verdict
from swisstip.core.readiness import CaseCounts, CoverageCounts, Gate, Readiness, dump_readiness, readiness_path
from swisstip.core.release import dump_release, load_release
from swisstip.core.validation import validate_release
from swisstip.extraction.extract_cli import run_extraction
from swisstip.extraction.validate import validate_dataset
from swisstip.ingestion import download_cli, gap_report
from swisstip.ingestion.acquisition import write_json as write_run_json
from swisstip.runtime.acceptance import check_acceptance, issues_of, policy_date
from swisstip.runtime.service import ReleaseService

from . import REPORT_SCHEMA_VERSION

STAGES = ("acquire", "gaps", "extract", "validate-text", "build", "validate-release", "coverage", "health", "accept", "ready")


class StageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def next_release_id(existing: str | None, pack: str, today: date) -> str:
    """Same day: bump the version; otherwise start at v1 for today."""
    match = re.fullmatch(rf"{re.escape(pack)}-(\d{{4}}-\d{{2}}-\d{{2}})-v(\d+)", existing or "")
    if match and match[1] == today.isoformat():
        return f"{pack}-{today.isoformat()}-v{int(match[2]) + 1}"
    return f"{pack}-{today.isoformat()}-v1"


def default_run_dir(root: Path, pack: str) -> Path:
    """`.local/<pack>`, where every run lives outside Git; the pack folder only if a run was placed there (`plan.json`)."""
    pack_dir = Path(root) / "releases" / pack
    return pack_dir if (pack_dir / "plan.json").is_file() else Path(root) / ".local" / pack


class Pipeline:
    def __init__(self, root: Path, pack: str, *, run_dir: Path | None = None, release_id: str | None = None,
                 download: bool = False, retry_failed: bool = False, workers: int = 1, scope: str = "attributed",
                 thorough: bool = False, update_curation: bool = False, attested_by: str | None = None, log=print):
        self.root = Path(root).resolve()  # the packs repository: releases/<pack> and .local/<pack> live under it
        self.pack = pack
        self.pack_dir = self.root / "releases" / pack
        self.catalogue = self.pack_dir / "sources.json"
        self.markdown = self.pack_dir / "sources.md"
        self.curation = self.pack_dir / "curation.yaml"
        self.acceptance = self.pack_dir / "acceptance.yaml"
        self.release = self.pack_dir / "release.json"
        self.report_path = self.pack_dir / "pipeline-report.json"
        self.acceptance_report_path = self.pack_dir / "acceptance-report.json"
        self.dispositions = self.pack_dir / "curation-coverage.yaml"
        self.coverage_report_path = self.pack_dir / "curation-coverage.json"
        self.run = (run_dir or default_run_dir(self.root, pack)).resolve()
        self.text = self.run / "text"
        self.release_id = release_id
        self.download, self.retry_failed, self.workers, self.scope = download, retry_failed, workers, scope
        self.thorough, self.update_curation, self.log = thorough, update_curation, log
        self.attested_by = (attested_by or "").strip() or None
        self.previous = json.loads(self.report_path.read_text(encoding="utf-8")) if self.report_path.is_file() else {}
        self.stages: list[dict] = []
        self._curation = None

    # --- stages -------------------------------------------------------------

    def acquire(self) -> tuple[str, dict]:
        if not self.catalogue.is_file():
            raise StageError(f"No catalogue at {self.catalogue}")
        catalogue_sha = sha256_file(self.catalogue)
        plan_path = self.run / "plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan.get("catalogue_sha256") != catalogue_sha:
                raise StageError(f"The catalogue changed since the run in {self.run} was planned; "
                                 "a changed catalogue needs a new run directory (--run-dir)")
            if not self.download:
                summary = self.run_summary()
                return "skipped", dict(reason="run exists for this catalogue; pass --download to fetch", **summary)
        arguments = ["--catalogue", str(self.catalogue), "--output", str(self.run), "--workers", str(min(max(self.workers, 1), 4))]
        if self.markdown.is_file():
            arguments += ["--markdown", str(self.markdown)]
        if self.download:
            arguments.append("--download")
            if self.retry_failed:
                arguments.append("--retry-failed")
        code = download_cli.main(arguments)
        summary = self.run_summary()
        if code and not self.download:
            raise StageError(f"Planning the run failed with exit code {code}")
        return "ran", dict(downloaded=self.download, download_exit_code=code, **summary)

    def run_summary(self) -> dict:
        path = self.run / "summary.json"
        if not path.is_file():
            return {}
        summary = json.loads(path.read_text(encoding="utf-8"))
        return dict(targets=summary.get("target_count"), counts=summary.get("counts"))

    def gaps(self) -> tuple[str, dict]:
        report_file, summary_file = self.run / "gap-report.json", self.run / "summary.json"
        decisions_file = self.run / "review-decisions.json"
        decisions_sha256 = sha256_file(decisions_file) if decisions_file.is_file() else None
        if report_file.is_file() and summary_file.is_file() and report_file.stat().st_mtime >= summary_file.stat().st_mtime:
            report = json.loads(report_file.read_text(encoding="utf-8"))
            if report.get("review_decisions_sha256") == decisions_sha256:
                return "skipped", dict(reason="report is newer than the run summary and review decisions are unchanged",
                                       counts=report.get("counts"))
        report = gap_report.build_report(self.run)
        report["review_decisions_sha256"] = decisions_sha256
        write_run_json(report_file, report)
        (self.run / "gap-report.md").write_text(gap_report.render_markdown(report), encoding="utf-8")
        return "ran", dict(targets=report.get("target_count"), counts=report.get("counts"))

    def extract(self) -> tuple[str, dict]:
        code, summary = run_extraction(self.run, self.text, scope=self.scope, workers=self.workers, log=self.log)
        details = {key: summary.get(key) for key in ("records", "eligible", "blocks", "extracted_now", "reused",
                                                     "failed_in_selection", "superseded", "unavailable")}
        if code:
            raise StageError(f"{summary.get('failed_in_selection')} record(s) failed to extract; see {self.text / 'errors.json'}")
        return "ran", details

    def validate_text(self) -> tuple[str, dict]:
        report = validate_dataset(self.text, self.run if self.thorough else None, check_raw=self.thorough)
        details = dict(records=report["records"], blocks=report["blocks"], raw_responses_rehashed=self.thorough,
                       checks={c["name"]: ("skipped" if c.get("skipped") else "passed" if c["passed"] else "failed") for c in report["checks"]})
        if not report["passed"]:
            raise StageError("the text dataset does not validate; see validation.json")
        return "ran", details

    def loaded_curation(self):
        """The curation file, read once per run: the build needs it, and so does the question whether to build."""
        if self._curation is None:
            self._curation = load_curation(self.curation)
        return self._curation

    def build_inputs(self, release_id: str) -> str:
        """The curation, the text index, the release ID and the place files the curation names: a register fetched
        anew or an edited alias rebuilds the release like an edited fact does."""
        try:
            places = [sha256_file(path).encode() for path in place_files(self.loaded_curation(), self.curation)]
        except OSError as exc:
            raise StageError(f"a place file the curation names cannot be read: {exc}") from exc
        return hashlib.sha256(b"".join([sha256_file(self.curation).encode(), sha256_file(self.text / "index.json").encode(),
                                        release_id.encode(), *places])).hexdigest()

    def build(self) -> tuple[str, dict]:
        if not self.curation.is_file():
            return "skipped", dict(reason=f"no curation file at {self.curation}; nothing to build yet")
        if not (self.text / "index.json").is_file():
            raise StageError("the text dataset has no index; run the extract stage first")
        # The last build record, whether it ran or was skipped, carries the input hash of the current release.
        previous = next((s for s in self.previous.get("stages", []) if s["stage"] == "build" and s.get("inputs_sha256")), None)
        existing_id = load_release(self.release).manifest.release_id if self.release.is_file() else None
        release_id = self.release_id or existing_id or next_release_id(None, self.pack, date.today())
        if previous and self.release.is_file() and previous.get("inputs_sha256") == self.build_inputs(release_id):
            return "skipped", dict(reason="curation, text index and release ID unchanged since the last build",
                                   release_id=release_id, inputs_sha256=previous["inputs_sha256"])
        if self.release_id is None and existing_id and previous and previous.get("inputs_sha256") != self.build_inputs(existing_id):
            release_id = next_release_id(existing_id, self.pack, date.today())
        curation = self.loaded_curation()
        try:
            release, report = build_release(curation, self.text, release_id, update_citations=self.update_curation,
                                            place_register=place_register_for(curation, self.curation))
        except (BuildError, PlaceFileError) as exc:
            raise StageError(str(exc)) from exc
        self.release.write_text(dump_release(release), encoding="utf-8", newline="\n")
        (self.pack_dir / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8", newline="\n")
        if self.update_curation:
            save_curation(self.curation, curation)
        return "ran", dict(release_id=release_id, concepts=report["concepts"], facts=report["facts"],
                           evidence=report["evidence"], documents=report["documents"],
                           citation_outcomes=report["citation_outcomes"], dropped=len(report["dropped"]),
                           curation_updated=self.update_curation, inputs_sha256=self.build_inputs(release_id))

    def validate_release_stage(self) -> tuple[str, dict]:
        if not self.release.is_file():
            return "skipped", dict(reason="no release file")
        release = load_release(self.release)
        issues = validate_release(release, self.text, self.run if self.thorough else None)
        if issues:
            raise StageError(f"{len(issues)} issue(s): " + "; ".join(issues[:5]))
        return "ran", dict(release_id=release.manifest.release_id, checked_against_text=True, saved_responses_rehashed=self.thorough)

    def coverage(self) -> tuple[str, dict]:
        """What the text dataset holds that no fact cites: the report is always written; only `enforce` fails on it."""
        if not self.release.is_file():
            return "skipped", dict(reason="no release file")
        if not (self.text / "index.json").is_file():
            return "skipped", dict(reason="the text dataset has no index; run the extract stage first")
        curation = self.loaded_curation() if self.curation.is_file() else None
        policy = curation.coverage_policy if curation else "report"
        try:
            dispositions = load_dispositions(self.dispositions) if self.dispositions.is_file() else None
        except (ValidationError, ValueError) as exc:
            raise StageError(f"curation-coverage.yaml does not load: {exc}") from exc
        if dispositions is not None and dispositions.pack != self.pack:
            raise StageError(f"curation-coverage.yaml is for pack {dispositions.pack!r}, not {self.pack!r}")
        catalogue = self.read_json(self.run / "catalogue.json") or self.read_json(self.catalogue)
        report = build_coverage(load_release(self.release), self.text, policy=policy, dispositions=dispositions, catalogue=catalogue,
                                dispositions_sha256=sha256_file(self.dispositions) if dispositions is not None else None,
                                min_pages=curation.boilerplate_min_pages if curation else None)
        write_report(report, self.coverage_report_path)
        details = dict(release_id=report["release_id"], policy=policy, clean=report["clean"], **report["counts"])
        if not report["passed"]:
            findings = {k: v for k, v in report["counts"]["findings"].items() if v}
            raise StageError(f"{report['counts']['unclassified_sections']} content section(s) neither cited nor dispositioned"
                             + (f", findings {findings}" if findings else "") + f"; see {self.coverage_report_path.with_suffix('.md')}")
        return "ran", details

    def health(self) -> tuple[str, dict]:
        if not self.release.is_file():
            return "skipped", dict(reason="no release file")
        service = ReleaseService.from_file(self.release)
        manifest = service.release.manifest
        return "ran", dict(release_id=manifest.release_id, topics=len(service.topics), concepts=len(service.concepts),
                           facts=len(service.facts), evidence=len(service.evidence), jurisdictions=len(manifest.jurisdictions),
                           languages=manifest.languages, stale_from=manifest.freshness.stale_from.isoformat(),
                           review_statuses=manifest.review_statuses, institution_levels=manifest.institution_levels,
                           basis_kinds=manifest.basis_kinds)

    def accept(self) -> tuple[str, dict]:
        if not self.acceptance.is_file():
            return "skipped", dict(reason=f"no acceptance suite at {self.acceptance}")
        if not self.release.is_file():
            return "skipped", dict(reason="no release file")
        try:
            suite = load_acceptance(self.acceptance)
        except (ValidationError, ValueError) as exc:
            raise StageError(f"the acceptance suite does not load: {exc}") from exc
        if suite.pack != self.pack:
            raise StageError(f"the acceptance suite is for pack {suite.pack!r}, not {self.pack!r}")
        report = check_acceptance(ReleaseService.from_file(self.release), suite)
        self.acceptance_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8", newline="\n")
        details = dict(release_id=report["release_id"], suite_sha256=report["suite_sha256"], as_of=report["as_of"],
                       cases=report["cases"], blocking=report["blocking"], failed=report["failed"],
                       quarantined=report["quarantined"], quarantined_failed=report["quarantined_failed"])
        if not report["passed"]:
            raise StageError(f"{len(report['failed'])} of {report['blocking']} blocking acceptance case(s) failed: "
                             + "; ".join(issues_of(report)))
        return "ran", details

    def ready(self) -> tuple[str, dict]:
        """Every gate on the current files, never on an inherited record; the record is written only when all pass."""
        if not self.attested_by:
            raise StageError("the ready stage needs the name of the person attesting the release (--attested-by)")
        if not self.release.is_file():
            raise StageError("no release file")
        if not self.acceptance.is_file():
            raise StageError(f"a release without an acceptance suite cannot be ready (no {self.acceptance})")
        attested_at = datetime.now(UTC)
        release = load_release(self.release)
        manifest = release.manifest
        gates: list[Gate] = []

        def gate(name: str, title: str, passed: bool, detail: str) -> None:
            gates.append(Gate(gate=name, title=title, status="passed" if passed else "failed", detail=detail))

        issues = validate_release(release, self.text, self.run)
        gate("G1", "the release validates against the text dataset and the re-hashed saved responses", not issues,
             "; ".join(issues[:5]) if issues else f"{len(release.documents)} saved responses re-hashed")
        build_report = self.read_json(self.pack_dir / "build-report.json")
        if build_report is None:
            gate("G2", "the build dropped no fact", False, "no build-report.json")
        elif build_report.get("release_id") != manifest.release_id:
            gate("G2", "the build dropped no fact", False, f"build-report.json is for {build_report.get('release_id')}")
        else:
            dropped = build_report.get("dropped") or []
            gate("G2", "the build dropped no fact", not dropped, f"{len(dropped)} dropped" if dropped else "0 dropped")
        try:
            suite = load_acceptance(self.acceptance)
        except (ValidationError, ValueError) as exc:
            raise StageError(f"the acceptance suite does not load: {exc}") from exc
        if suite.pack != self.pack:
            raise StageError(f"the acceptance suite is for pack {suite.pack!r}, not {self.pack!r}")
        service = ReleaseService(release)
        report = check_acceptance(service, suite)
        gate("G3", "every blocking acceptance case passes", report["passed"],
             ("failed: " + ", ".join(report["failed"]) + "; " + "; ".join(issues_of(report))) if not report["passed"]
             else f"{report['blocking']} blocking case(s) pass" + (f", quarantined: {report['quarantined']}" if report["quarantined"] else ""))
        runway = attested_at.date() + timedelta(days=suite.policy.min_runway_days)
        as_of = policy_date(service, suite)
        gate("G4", f"stale_from lies at least {suite.policy.min_runway_days} days after the attestation and after the policy date",
             manifest.freshness.stale_from >= runway and as_of < manifest.freshness.stale_from,
             f"stale from {manifest.freshness.stale_from.isoformat()}, runway until {runway.isoformat()}, policy date {as_of.isoformat()}")
        answers_path = self.pack_dir / "acceptance-answers.json"
        answers = None
        if answers_path.is_file():
            try:
                answers = AcceptanceAnswers.model_validate_json(answers_path.read_text(encoding="utf-8"))
            except (ValidationError, ValueError) as exc:
                raise StageError(f"acceptance-answers.json does not load: {exc}") from exc
        verdict = answer_verdict(suite, answers, manifest.content_sha256)
        gate("G5", f"the graded live-caller sessions meet the answer policy (answer_check {verdict['mode']})",
             verdict["passed"], verdict["detail"])
        committed = self.read_json(self.acceptance_report_path)
        current = (committed is not None and committed.get("content_sha256") == manifest.content_sha256
                   and committed.get("suite_sha256") == suite.digest() and committed.get("passed") is True)
        gate("G6", "the committed acceptance report is the report of this release and this suite", current,
             "acceptance-report.json matches" if current else
             "no acceptance-report.json" if committed is None else "acceptance-report.json is for another release or suite; run the accept stage")
        failed = [g for g in gates if g.status != "passed"]
        if failed:
            raise StageError(f"{len(failed)} of {len(gates)} gate(s) failed: " + "; ".join(f"{g.gate} {g.detail}" for g in failed))
        # The coverage counts are carried, not gated: they say what the curator was asked to read and what stays open.
        coverage_report = self.read_json(self.coverage_report_path)
        coverage = (CoverageCounts(**coverage_counts(coverage_report))
                    if coverage_report and coverage_report.get("content_sha256") == manifest.content_sha256 else None)
        record = Readiness(pack=self.pack, release_id=manifest.release_id, release_sha256=sha256_file(self.release),
                           content_sha256=manifest.content_sha256, suite_sha256=suite.digest(), attested_at=attested_at,
                           attested_by=self.attested_by, gates=gates,
                           cases=CaseCounts(total=report["cases"], blocking=report["blocking"], quarantined=report["quarantined"]),
                           review_statuses=manifest.review_statuses, snapshot_date=manifest.freshness.snapshot_date,
                           stale_from=manifest.freshness.stale_from, min_runway_days=suite.policy.min_runway_days,
                           coverage=coverage)
        readiness_path(self.release).write_text(dump_readiness(record), encoding="utf-8", newline="\n")
        return "ran", dict(release_id=manifest.release_id, release_sha256=record.release_sha256, attested_by=self.attested_by,
                           attested_at=attested_at.isoformat(), gates={g.gate: g.status for g in gates},
                           cases=record.cases.model_dump(), stale_from=manifest.freshness.stale_from.isoformat())

    @staticmethod
    def read_json(path: Path) -> dict | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    # --- orchestration -----------------------------------------------------

    def run_stages(self, start: str = "acquire", until: str = "accept") -> dict:
        if start not in STAGES or until not in STAGES or STAGES.index(start) > STAGES.index(until):
            raise ValueError(f"stages must be in order from {STAGES}")
        handlers = {"acquire": self.acquire, "gaps": self.gaps, "extract": self.extract, "validate-text": self.validate_text,
                    "build": self.build, "validate-release": self.validate_release_stage, "coverage": self.coverage,
                    "health": self.health, "accept": self.accept, "ready": self.ready}
        started = datetime.now(UTC)
        failed = False
        dropped = 0
        for stage in STAGES[STAGES.index(start):STAGES.index(until) + 1]:
            begun = datetime.now(UTC)
            entry = dict(stage=stage, started_at=begun.isoformat())
            try:
                status, details = handlers[stage]()
                entry.update(status=status, **details)
            except StageError as exc:
                entry.update(status="failed", error=str(exc))
                failed = True
            except Exception as exc:  # any other failure is still recorded before it stops the pipeline
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                failed = True
            entry["seconds"] = round((datetime.now(UTC) - begun).total_seconds(), 1)
            self.stages.append(entry)
            self.log(json.dumps({k: entry[k] for k in ("stage", "status", "seconds")} | ({"error": entry["error"]} if "error" in entry else {})))
            if stage == "build" and entry.get("status") == "ran":
                dropped = entry.get("dropped", 0)
            if failed:
                break
        report = dict(schema_version=REPORT_SCHEMA_VERSION, pack=self.pack, root=str(self.root), run=str(self.run),
                      started_at=started.isoformat(), finished_at=datetime.now(UTC).isoformat(),
                      options=dict(download=self.download, retry_failed=self.retry_failed, workers=self.workers, scope=self.scope,
                                   thorough=self.thorough, update_curation=self.update_curation, release_id=self.release_id,
                                   attested_by=self.attested_by, stages=f"{start}..{until}"),
                      stages=self.merge_previous(self.stages), exit_code=1 if failed or dropped else 0,
                      facts_dropped=dropped)
        self.pack_dir.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return report

    def merge_previous(self, stages: list[dict]) -> list[dict]:
        """Keep the last record of every stage that did not run this time, so skip decisions survive partial runs.

        A record of a stage the pipeline no longer has (`ground`, before the acceptance gate) is dropped."""
        current = {s["stage"] for s in stages}
        kept = [s for s in self.previous.get("stages", []) if s["stage"] in STAGES and s["stage"] not in current]
        return sorted(kept + stages, key=lambda s: STAGES.index(s["stage"]))
