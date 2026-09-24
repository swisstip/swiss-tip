"""Screen 4.3: pipeline runs and the relocation review.

The stage strip, the streaming log of the running job and the run history
come from the pipeline report and the console's own job log. The relocation
review is the panel a refresh is judged on: every citation whose outcome is
not `same-snapshot`, grouped by outcome, with the action each one allows.
Picking a candidate or dropping a fact is a curation write.
"""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.build.curation import Anchor
from swisstip.builder.pipeline import STAGES, next_release_id
from swisstip.extraction.anchors import make_anchor

from ..app import get_pack, render, require_editor
from ..data import PackData
from ..jobs import download_budget, pipeline_job, run_history
from ..writes import WriteRefused, record_rejection, write_curation

read_router = APIRouter()
write_router = APIRouter()

# Warnings that send a relocated citation back to the review queue (section 4.3).
NEEDS_REVIEW = ("context-changed", "neighbourhood-changed", "blocks-restructured")
# The panels of the relocation review, worst first.
OUTCOME_ORDER = ("dropped", "ambiguous", "changed", "moved-with-warnings", "moved", "same-text")


def relocation_rows(pack: PackData) -> dict[str, list[dict]]:
    """Every citation the last build did not resolve inside its own snapshot."""
    report = pack.build_report.current() or {}
    grouped: dict[str, list[dict]] = {}
    entries = [(entry, False) for entry in report.get("facts_resolved", [])]
    entries += [(entry, True) for entry in report.get("dropped", []) if "fact_id" in entry]
    for entry, was_dropped in entries:
        concept, fact = pack.fact(entry["fact_id"])
        for number, citation in enumerate(entry.get("citations", []), 1):
            outcome = citation.get("outcome", "dropped")
            if outcome == "same-snapshot" and not citation.get("warnings"):
                continue
            reference = fact.evidence[number - 1] if fact and len(fact.evidence) >= number else None
            group = outcome if not citation.get("warnings") else \
                "moved-with-warnings" if outcome == "moved" else outcome
            grouped.setdefault(group, []).append(dict(
                fact_id=entry["fact_id"], concept_id=concept.concept_id if concept else None,
                statement=fact.statement if fact else "", number=number, citation=citation,
                anchor=reference.anchor if reference else None, dropped=was_dropped,
                document_id=citation.get("document_id"),
                entry=pack.dataset.entry(citation.get("document_id") or "") or {},
                warnings=citation.get("warnings", []), candidates=citation.get("candidates", []),
                reason=citation.get("reason", "")))
    return grouped


@read_router.get("/packs/{pack}/runs")
def index(request: Request, pack: PackData = Depends(get_pack)):
    report = pack.pipeline_report.current() or {}
    release = pack.release.current()
    return render(request, "runs/index.html", pack=pack, active="runs", report=report, stages=STAGES,
                  strip=pack.stages(), history=run_history(pack), jobs=request.app.state.jobs.history(pack),
                  job=request.app.state.jobs.active(pack.pack), budget=download_budget(pack),
                  next_release=next_release_id(release.manifest.release_id if release else None, pack.pack,
                                               date.today()),
                  catalogue_changed=pack.catalogue_changed())


@read_router.get("/packs/{pack}/runs/jobs/{job_id}")
def job_panel(request: Request, job_id: str, offset: int = 0, pack: PackData = Depends(get_pack)):
    """Polled by the page: the job state and the new bytes of its log."""
    runner = request.app.state.jobs
    job = runner.jobs.get(job_id)
    tail = runner.tail(pack, job_id, offset)
    return render(request, "runs/job.html", pack=pack, job=job, tail=tail)


@read_router.get("/packs/{pack}/runs/relocation")
def relocation(request: Request, pack: PackData = Depends(get_pack)):
    return render(request, "runs/relocation.html", pack=pack, active="runs", groups=relocation_rows(pack),
                  order=OUTCOME_ORDER, report=pack.build_report.current() or {})


@write_router.post("/packs/{pack}/runs")
def start_run(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
              start: str = Form("acquire"), until: str = Form("health"), workers: int = Form(1),
              scope: str = Form("attributed"), thorough: str = Form(""), update_curation: str = Form(""),
              release_id: str = Form(""), download: str = Form(""), retry_failed: str = Form(""),
              confirm_pack: str = Form("")):
    if download and confirm_pack.strip() != pack.pack:
        return RedirectResponse(f"/packs/{pack.pack}/runs?error=type+the+pack+name+to+confirm+a+download",
                                status_code=303)
    options = dict(start=start, until=until, workers=workers, scope=scope, thorough=bool(thorough),
                   update_curation=bool(update_curation), release_id=release_id.strip(), download=bool(download),
                   retry_failed=bool(retry_failed))
    try:
        job = request.app.state.jobs.start(pack, "pipeline", actor, options, pipeline_job(pack, options))
    except RuntimeError as exc:
        return RedirectResponse(f"/packs/{pack.pack}/runs?error={str(exc).replace(' ', '+')}", status_code=303)
    return RedirectResponse(f"/packs/{pack.pack}/runs?notice=job+{job.job_id}+started", status_code=303)


@write_router.post("/packs/{pack}/runs/relocation/pick")
def pick_candidate(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                   fact_id: str = Form(...), number: int = Form(...), document_id: str = Form(...),
                   first_block: int = Form(...), last_block: int = Form(...), sha256: str = Form("")):
    """An ambiguous or changed citation is re-pointed at the range the expert picked."""
    record = pack.dataset.record(document_id)
    if record is None:
        raise WriteRefused(f"no record {document_id} in the text dataset")

    def mutate(curation) -> None:
        for concept in curation.concepts:
            for fact in concept.facts:
                if fact.fact_id != fact_id:
                    continue
                citation = fact.evidence[number - 1]
                citation.document_id, citation.first_block, citation.last_block = document_id, first_block, last_block
                citation.anchor = Anchor(**make_anchor(record, first_block, last_block))

    write_curation(pack, mutate, actor, f"relocate citation {number} of fact {fact_id} onto {document_id}"
                   f" blocks {first_block}-{last_block}", expected_sha256=sha256 or None, ids=[fact_id])
    return RedirectResponse(f"/packs/{pack.pack}/runs/relocation?notice=citation+rewritten", status_code=303)


@write_router.post("/packs/{pack}/runs/relocation/drop")
def drop_fact(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
              fact_id: str = Form(...), note: str = Form(...), sha256: str = Form("")):
    """The one write that may drop a fact (section 6.1); the note says why."""
    if not note.strip():
        raise WriteRefused("dropping a fact needs a note")
    removed: dict = {}

    def mutate(curation) -> None:
        for concept in curation.concepts:
            for fact in list(concept.facts):
                if fact.fact_id != fact_id:
                    continue
                removed["concept_id"], removed["fact"] = concept.concept_id, fact
                concept.facts.remove(fact)
        curation.concepts = [concept for concept in curation.concepts if concept.facts]

    write_curation(pack, mutate, actor, f"drop fact {fact_id} after relocation: {note.strip()}",
                   expected_sha256=sha256 or None, ids=[fact_id], allow_drop=True)
    if removed:
        record_rejection(pack, removed["concept_id"], removed["fact"], actor, note.strip())
    return RedirectResponse(f"/packs/{pack.pack}/runs/relocation?notice=fact+{fact_id}+dropped", status_code=303)


@read_router.get("/packs/{pack}/runs/relocation/preview")
def preview_range(request: Request, document_id: str, first_block: int, last_block: int,
                  pack: PackData = Depends(get_pack)):
    """The excerpt a candidate range would cut, shown before the pick is taken."""
    record = pack.dataset.record(document_id)
    excerpt, heading = "", []
    if record and 1 <= first_block <= last_block <= len(record["blocks"]):
        blocks = record["blocks"][first_block - 1:last_block]
        excerpt = record["content_text"][blocks[0]["start"]:blocks[-1]["end"]]
        heading = blocks[0].get("heading_path", [])
    return render(request, "runs/preview.html", pack=pack, excerpt=excerpt, heading=heading,
                  document_id=document_id, first_block=first_block, last_block=last_block)
