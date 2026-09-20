"""Screen 4.9: operations.

Health of the served release, traffic and latency per tool, the gap feed as
the curation backlog, and the freshness of the cited documents. Over stdio
the server runs inside the client and its stderr is not collected, so this
screen is empty until the streamable HTTP endpoint writes a log file; the
screen is specified and built now so that the log line format is not changed
without it in mind. Point the console at a log with `--server-log`.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..app import get_pack, render, require_editor
from ..calls import aggregate, append_calls, parse_log, read_calls
from ..data import PackData
from ..jobs import pipeline_job

read_router = APIRouter()
write_router = APIRouter()


def health(pack: PackData) -> dict:
    release = pack.release.current()
    if release is None:
        return dict(available=False)
    manifest = release.manifest
    return dict(available=True, release_id=manifest.release_id, pack=manifest.pack, topics=len(release.topics),
                concepts=len(release.concepts), facts=len(release.facts), evidence=len(release.evidence),
                documents=len(release.documents), jurisdictions=manifest.jurisdictions, languages=manifest.languages,
                snapshot_date=manifest.freshness.snapshot_date, stale_from=manifest.freshness.stale_from,
                days_to_stale=(manifest.freshness.stale_from - date.today()).days,
                review_statuses=manifest.review_statuses, provenance_kinds=manifest.provenance_kinds)


def freshness_rows(pack: PackData) -> list[dict]:
    release = pack.release.current()
    if release is None:
        return []
    by_date: dict[str, list[str]] = {}
    for document in release.documents:
        by_date.setdefault(document.accessed_on.isoformat(), []).append(document.document_id)
    return [dict(accessed_on=key, documents=len(value), age=(date.today() - date.fromisoformat(key)).days)
            for key, value in sorted(by_date.items())]


def calls_path(pack: PackData) -> Path:
    return pack.console_dir / "calls.jsonl"


@read_router.get("/packs/{pack}/operations")
def index(request: Request, pack: PackData = Depends(get_pack), by: str = "hour"):
    entries = read_calls(calls_path(pack)) + read_calls(pack.console_dir / "sandbox-calls.jsonl")
    served = health(pack)
    logged_releases = sorted({entry["release"] for entry in entries if entry.get("release")})
    return render(request, "operations/index.html", pack=pack, active="operations", health=served,
                  summary=aggregate(entries, by=by), by=by, calls=len(entries),
                  server_log=request.app.state.console.server_log,
                  mismatch=[item for item in logged_releases if served.get("release_id") and item != served["release_id"]
                            and not item.startswith("swiss-tip-sandbox")],
                  freshness=freshness_rows(pack), job=request.app.state.jobs.active(pack.pack),
                  concepts=[concept.concept_id for concept in (pack.release.current().concepts
                                                               if pack.release.current() else [])])


@write_router.post("/packs/{pack}/operations/import")
def import_log(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
               path: str = Form("")):
    """Parse a server log file into the append-only aggregate. Nothing else writes `calls.jsonl`."""
    source = Path(path.strip()) if path.strip() else request.app.state.console.server_log
    if source is None or not Path(source).is_file():
        return RedirectResponse(f"/packs/{pack.pack}/operations?error=no+log+file+at+{path or 'the+configured+path'}",
                                status_code=303)
    known = {(entry.get("at"), entry.get("tool"), entry.get("ms")) for entry in read_calls(calls_path(pack))}
    fresh = [entry for entry in parse_log(Path(source))
             if (entry.get("at"), entry.get("tool"), entry.get("ms")) not in known]
    written = append_calls(calls_path(pack), fresh)
    return RedirectResponse(f"/packs/{pack.pack}/operations?notice={written}+call(s)+imported", status_code=303)


@write_router.post("/packs/{pack}/operations/refresh")
def refresh(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
            confirm_pack: str = Form("")):
    """The freshness action: acquire with download, then extract, validate-text and build with update_curation."""
    if confirm_pack.strip() != pack.pack:
        return RedirectResponse(f"/packs/{pack.pack}/operations?error=type+the+pack+name+to+confirm+a+download",
                                status_code=303)
    options = dict(start="acquire", until="health", download=True, retry_failed=False, workers=1,
                   scope="attributed", thorough=False, update_curation=True, release_id="")
    try:
        job = request.app.state.jobs.start(pack, "refresh", actor, options, pipeline_job(pack, options))
    except RuntimeError as exc:
        return RedirectResponse(f"/packs/{pack.pack}/operations?error={str(exc).replace(' ', '+')}", status_code=303)
    return RedirectResponse(f"/packs/{pack.pack}/runs?notice=refresh+job+{job.job_id}+started", status_code=303)
