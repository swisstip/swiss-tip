"""Pipeline stages and check runs as jobs (section 7).

A job is a thread in the console process whose `log` is redirected to
`.local/<pack>/console/jobs/<job_id>.log`; the page polls a status endpoint
and tails that file. One job per pack at a time, and a job that writes takes
the same lock as a form save. A job survives a page reload but not a console
restart: a restart marks jobs that were still running as `interrupted`.

The download flag is the only job that touches the network. It is confirmed
on every start (section 4.3); there is no "always allow".
"""

import json
import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from swisstip.builder.pipeline import STAGES, Pipeline
from swisstip.core.release import load_release

from .data import PackData
from .writes import LOCKS, pack_file_lock


@dataclass
class Job:
    job_id: str
    pack: str
    kind: str
    actor: str
    options: dict = field(default_factory=dict)
    status: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    error: str | None = None
    result: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def job_dir(pack: PackData) -> Path:
    return pack.console_dir / "jobs"


def log_path(pack: PackData, job_id: str) -> Path:
    return job_dir(pack) / f"{job_id}.log"


def state_path(pack: PackData, job_id: str) -> Path:
    return job_dir(pack) / f"{job_id}.json"


def archive_run(pack: PackData, report: dict) -> Path | None:
    """Keep the pipeline report a later run would overwrite (section 4.3)."""
    folder = pack.console_dir / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (report.get("finished_at") or datetime.now(UTC).isoformat()).replace(":", "-")
    path = folder / f"{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def archive_release(pack: PackData) -> Path | None:
    """Keep each built release, since Git holds only the current one (section 4.7)."""
    if not pack.release_path.is_file():
        return None
    release = load_release(pack.release_path)
    folder = pack.console_dir / "releases"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{release.manifest.release_id}.json"
    path.write_bytes(pack.release_path.read_bytes())
    return path


class JobRunner:
    """One job per pack, its log on disk, its state in memory and on disk."""

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._guard = threading.Lock()

    # --- lifecycle ---------------------------------------------------------

    def mark_interrupted(self, pack: PackData) -> list[str]:
        """At start-up, a job state file that still says `running` outlived its console."""
        interrupted = []
        folder = job_dir(pack)
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("status") == "running":
                state.update(status="interrupted", finished_at=datetime.now(UTC).isoformat(),
                             error="the console restarted while this job was running")
                path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
                interrupted.append(state["job_id"])
        return interrupted

    def active(self, pack: str) -> Job | None:
        return next((job for job in self.jobs.values() if job.pack == pack and job.status == "running"), None)

    def for_pack(self, pack: str) -> list[Job]:
        return sorted((job for job in self.jobs.values() if job.pack == pack),
                      key=lambda job: job.started_at, reverse=True)

    def start(self, pack: PackData, kind: str, actor: str, options: dict, target) -> Job:
        """`target(log)` runs in a thread; whatever it returns becomes the job result."""
        with self._guard:
            running = self.active(pack.pack)
            if running is not None:
                raise RuntimeError(f"job {running.job_id} ({running.kind}) is already running for {pack.pack}")
            job = Job(job_id=uuid.uuid4().hex[:12], pack=pack.pack, kind=kind, actor=actor, options=options,
                      started_at=datetime.now(UTC).isoformat())
            self.jobs[job.job_id] = job
        job_dir(pack).mkdir(parents=True, exist_ok=True)
        log_path(pack, job.job_id).write_text("", encoding="utf-8", newline="\n")
        self._save(pack, job)

        def log(message) -> None:
            with log_path(pack, job.job_id).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{datetime.now(UTC).isoformat()[11:19]} {message}\n")

        def run() -> None:
            try:
                with pack_file_lock(pack), LOCKS.lock(pack.pack):
                    job.result = target(log)
                job.status = "finished"
            except Exception as exc:
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
                log(job.error)
                log(traceback.format_exc())
            finally:
                job.finished_at = datetime.now(UTC).isoformat()
                self._save(pack, job)

        thread = threading.Thread(target=run, name=f"job-{job.job_id}", daemon=True)
        self._threads[job.job_id] = thread
        thread.start()
        return job

    def _save(self, pack: PackData, job: Job) -> None:
        state_path(pack, job.job_id).write_text(json.dumps(job.as_dict(), indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8", newline="\n")

    def wait(self, job: Job, timeout: float = 60.0) -> Job:
        """Used by the tests and by the check run, which is fast enough to answer in the request."""
        thread = self._threads.get(job.job_id)
        if thread is not None:
            thread.join(timeout)
        return job

    def tail(self, pack: PackData, job_id: str, offset: int = 0) -> dict:
        path = log_path(pack, job_id)
        if not path.is_file():
            return dict(text="", offset=offset)
        data = path.read_bytes()
        return dict(text=data[offset:].decode("utf-8", errors="replace"), offset=len(data))

    def history(self, pack: PackData, limit: int = 25) -> list[dict]:
        folder = job_dir(pack)
        states = []
        for path in sorted(folder.glob("*.json"), reverse=True)[:limit] if folder.is_dir() else []:
            states.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(states, key=lambda state: state.get("started_at", ""), reverse=True)


def run_history(pack: PackData, limit: int = 25) -> list[dict]:
    """Past pipeline reports kept by the console, newest first."""
    folder = pack.console_dir / "runs"
    reports = []
    for path in sorted(folder.glob("*.json"), reverse=True)[:limit] if folder.is_dir() else []:
        report = json.loads(path.read_text(encoding="utf-8"))
        reports.append(dict(file=path.name, started_at=report.get("started_at"), finished_at=report.get("finished_at"),
                            exit_code=report.get("exit_code"), options=report.get("options", {}),
                            stages=[dict(stage=stage["stage"], status=stage.get("status"), seconds=stage.get("seconds"))
                                    for stage in report.get("stages", [])]))
    return reports


def pipeline_job(pack: PackData, options: dict):
    """The target of a pipeline job: run the stages, then archive the report and the release."""

    def target(log):
        pipeline = Pipeline(pack.root, pack.pack, run_dir=pack.run_dir, release_id=options.get("release_id") or None,
                            download=bool(options.get("download")), retry_failed=bool(options.get("retry_failed")),
                            workers=int(options.get("workers") or 1), scope=options.get("scope") or "attributed",
                            thorough=bool(options.get("thorough")),
                            update_curation=bool(options.get("update_curation")),
                            source_plugins=not bool(options.get("no_source_plugins")),
                            lock_pack=False, log=log)
        report = pipeline.run_stages(options.get("start") or STAGES[0], options.get("until") or STAGES[-1])
        archive_run(pack, report)
        archive_release(pack)
        log(f"exit code {report['exit_code']}, {report['facts_dropped']} fact(s) dropped")
        return dict(exit_code=report["exit_code"], facts_dropped=report["facts_dropped"],
                    stages=[dict(stage=stage["stage"], status=stage.get("status")) for stage in report["stages"]])

    return target


def download_budget(pack: PackData) -> dict:
    """What a download run would request, shown in the confirmation of section 4.3."""
    plan = pack.plan.current() or {}
    catalogue = pack.catalogue.current() or {}
    targets = plan.get("targets", [])
    hosts = sorted({host for target in targets
                    for host in [target["url"].split("/")[2] if "//" in target["url"] else ""] if host})
    profiles = catalogue.get("crawl_profiles", {})
    return dict(targets=len(targets), hosts=hosts, profiles=profiles,
                pack=pack.pack, catalogue_changed=pack.catalogue_changed())
