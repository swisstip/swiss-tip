"""Screen 4.7: the release.

Manifest text, the build as one job (build, validate-release, health), the
diff against the previous release, the caller preview with the size of the
coverage root against the 2 KB target of the tool contracts, and the working
tree of the pack as `git status` and `git diff --stat` report it, run in the
pack's own repository (the packs directory), never in the console's checkout.
There is no commit button: the lead commits from Git after reading the diff.
"""

import json
import subprocess
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.builder.pipeline import next_release_id
from swisstip.core.contracts import GetCoverageRequest
from swisstip.core.release import Release, load_release
from swisstip.core.validation import validate_release
from swisstip.runtime.service import ReleaseService

from ..app import get_pack, render, require_editor
from ..data import PackData
from ..jobs import pipeline_job

read_router = APIRouter()
write_router = APIRouter()

COVERAGE_ROOT_TARGET_BYTES = 2048


def git(folder: Path, *arguments: str) -> str:
    """Read-only git calls only: status, diff, log; run inside `folder`, so they report the repository that holds it."""
    try:
        finished = subprocess.run(["git", *arguments], cwd=folder, capture_output=True, text=True, timeout=20,
                                  encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"({type(exc).__name__}: {exc})"
    return (finished.stdout or finished.stderr).strip()


def release_diff(old: Release | None, new: Release | None) -> dict:
    """What callers will see change, computed from the two release files (section 4.7)."""
    if old is None or new is None:
        return dict(available=False)
    old_concepts = {concept.concept_id: concept for concept in old.concepts}
    new_concepts = {concept.concept_id: concept for concept in new.concepts}
    old_facts = {fact.fact_id: fact for fact in old.facts}
    new_facts = {fact.fact_id: fact for fact in new.facts}
    old_evidence = {item.evidence_id: item for item in old.evidence}
    new_evidence = {item.evidence_id: item for item in new.evidence}

    def recited(fact_id: str) -> bool:
        before = [old_evidence[item] for item in old_facts[fact_id].evidence_ids if item in old_evidence]
        after = [new_evidence[item] for item in new_facts[fact_id].evidence_ids if item in new_evidence]
        key = [(item.document_id, item.start_offset, item.end_offset, item.excerpt_sha256) for item in before]
        return key != [(item.document_id, item.start_offset, item.end_offset, item.excerpt_sha256) for item in after]

    shared = sorted(set(old_facts) & set(new_facts))
    manifest_changes = []
    for field in ("title", "scope_statement", "out_of_scope_response", "out_of_scope", "limitations",
                  "jurisdictions", "languages"):
        before, after = getattr(old.manifest, field), getattr(new.manifest, field)
        if before != after:
            manifest_changes.append(dict(field=field, before=before, after=after))
    return dict(available=True, old_id=old.manifest.release_id, new_id=new.manifest.release_id,
                concepts_added=sorted(set(new_concepts) - set(old_concepts)),
                concepts_removed=sorted(set(old_concepts) - set(new_concepts)),
                facts_added=sorted(set(new_facts) - set(old_facts)),
                facts_removed=sorted(set(old_facts) - set(new_facts)),
                facts_reworded=[dict(fact_id=fact_id, before=old_facts[fact_id].statement,
                                     after=new_facts[fact_id].statement)
                                for fact_id in shared if old_facts[fact_id].statement != new_facts[fact_id].statement],
                facts_recited=[fact_id for fact_id in shared if recited(fact_id)],
                documents_added=sorted({d.document_id for d in new.documents} - {d.document_id for d in old.documents}),
                documents_removed=sorted({d.document_id for d in old.documents} - {d.document_id for d in new.documents}),
                manifest=manifest_changes,
                review_before=old.manifest.review_statuses, review_after=new.manifest.review_statuses)


def caller_preview(release: Release | None) -> dict:
    """The coverage root and each topic page with their sizes, as `get_coverage` returns them."""
    if release is None:
        return dict(available=False)
    service = ReleaseService(release)
    root = service.get_coverage(GetCoverageRequest())
    root_json = json.dumps(root.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)
    topics = []
    for topic in release.topics:
        page = service.get_coverage(GetCoverageRequest(parent_id=topic.topic_id))
        body = json.dumps(page.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2)
        topics.append(dict(topic_id=topic.topic_id, title=topic.title, bytes=len(body.encode("utf-8")),
                           concepts=len(page.concepts)))
    return dict(available=True, root=root_json, root_bytes=len(root_json.encode("utf-8")),
                target=COVERAGE_ROOT_TARGET_BYTES, over_target=len(root_json.encode("utf-8")) > COVERAGE_ROOT_TARGET_BYTES,
                topics=topics, coverage_md=coverage_markdown(release), limitations_md=limitations_markdown(release))


def coverage_markdown(release: Release) -> str:
    manifest = release.manifest
    lines = [f"# Coverage: {manifest.title}", "", f"Release `{manifest.release_id}`, snapshot "
             f"{manifest.freshness.snapshot_date.isoformat()}, stale from "
             f"{manifest.freshness.stale_from.isoformat()}.", "", manifest.scope_statement, "",
             f"Jurisdictions: {', '.join(manifest.jurisdictions)}. Languages: {', '.join(manifest.languages)}.", ""]
    if manifest.institution_levels:
        lines += ["Documents by level of their publisher: "
                  + ", ".join(f"{count} {level}" for level, count in sorted(manifest.institution_levels.items())) + ".", ""]
    if manifest.basis_kinds:
        lines += ["Facts by the kind of excerpt they rest on: "
                  + ", ".join(f"{count} {kind}" for kind, count in sorted(manifest.basis_kinds.items())) + ".", ""]
    lines += ["| Topic | Concepts | Jurisdictions |", "| --- | ---: | --- |"]
    for topic in release.topics:
        concepts = [concept for concept in release.concepts if concept.topic_id == topic.topic_id]
        lines.append(f"| {topic.title} | {len(concepts)} | "
                     f"{', '.join(sorted({j for c in concepts for j in c.jurisdictions}))} |")
    return "\n".join(lines) + "\n"


def limitations_markdown(release: Release) -> str:
    manifest = release.manifest
    lines = [f"# Limitations: {manifest.title}", "", f"Release `{manifest.release_id}`.", "", "## Out of scope", ""]
    lines += [f"- {item}" for item in manifest.out_of_scope]
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in manifest.limitations]
    lines += ["", "## Review status", ""]
    lines += [f"- {count} fact(s) {status}" for status, count in sorted(manifest.review_statuses.items())]
    return "\n".join(lines) + "\n"


def previous_release(pack: PackData) -> Release | None:
    """The newest archived release that is not the current one."""
    current = pack.release_id()
    archives = [path for path in pack.archived_releases() if path.stem != current]
    if not archives:
        return None
    return load_release(max(archives, key=lambda path: path.stat().st_mtime))


@read_router.get("/packs/{pack}/release")
def index(request: Request, pack: PackData = Depends(get_pack)):
    release = pack.release.current()
    curation = pack.curation.current()
    issues = validate_release(release, pack.text_dir) if release else []
    return render(request, "release/index.html", pack=pack, active="release", release=release, curation=curation,
                  issues=issues, diff=release_diff(previous_release(pack), release),
                  preview=caller_preview(release), report=pack.build_report.current() or {},
                  next_release=next_release_id(release.manifest.release_id if release else None, pack.pack,
                                               date.today()),
                  job=request.app.state.jobs.active(pack.pack), sha256=pack.curation.sha256,
                  git_status=git(pack.pack_dir, "status", "--short", "--", "."),
                  git_diff=git(pack.pack_dir, "diff", "--stat", "--", "."),
                  git_log=git(pack.pack_dir, "log", "-10", "--pretty=format:%h %s"))


@write_router.post("/packs/{pack}/release/build")
def build(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
          release_id: str = Form(""), update_curation: str = Form("")):
    options = dict(start="build", until="health", release_id=release_id.strip(),
                   update_curation=bool(update_curation), workers=1, scope="attributed")
    try:
        job = request.app.state.jobs.start(pack, "build", actor, options, pipeline_job(pack, options))
    except RuntimeError as exc:
        return RedirectResponse(f"/packs/{pack.pack}/release?error={str(exc).replace(' ', '+')}", status_code=303)
    return RedirectResponse(f"/packs/{pack.pack}/release?notice=build+job+{job.job_id}+started", status_code=303)
