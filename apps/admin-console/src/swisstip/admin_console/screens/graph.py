"""Screen 4.10: the knowledge graph - overview, explorer, items, review, Derive, compile and refresh.

Reads `graphs/<graph>/` and the graph's run; writes `graph.yaml` only, through `write_graph`. A person's edit takes
the item over from Derive (its author becomes the editor, so Derive never changes it again) and an edited statement
loses a confirmation it had, as a fact's does. Review sets `human-reviewed` with the reviewer's name; reject removes
the item (and the edges of a removed node) and keeps it in `.local/graph-<graph>/console/rejected.jsonl`. Derive is
previewed before it is applied; compile and refresh run as jobs with the graph pipeline's stages.
"""

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from swisstip.build.graph_cli import run_derive
from swisstip.build.graph_curation import CuratedEdge, GraphCitation, PackEvidenceRef
from swisstip.build.curation import CitationRef
from swisstip.builder.graph_pipeline import GraphPipeline
from swisstip.builder.pipeline import obey_robots_default
from swisstip.core.graph import RELATIONS
from swisstip.core.release import Provenance

from ..app import render, require_editor
from ..graph_data import UNREVIEWED, GraphData, record_rejection, write_graph
from ..writes import WriteRefused, read_audit

read_router = APIRouter()
write_router = APIRouter()


def get_graph(request: Request, graph: str) -> GraphData:
    try:
        return request.app.state.graphs.graph(graph)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown graph {graph!r}") from exc


def lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def pairs(text: str) -> dict[str, str]:
    return {key.strip(): value.strip() for line in lines(text) for key, _, value in [line.partition("=")] if value.strip()}


def status_of(item) -> str:
    return item.provenance.review_status


def item_id(item) -> str:
    return getattr(item, "node_id", None) or item.edge_id


def redirect(graph: GraphData, path: str = "", **query) -> RedirectResponse:
    suffix = "&".join(f"{key}={str(value).replace(' ', '+')}" for key, value in query.items())
    return RedirectResponse(f"/graphs/{graph.graph}{path}" + (f"?{suffix}" if suffix else ""), status_code=303)


# --- reading ----------------------------------------------------------------------------------------------------------


@read_router.get("/graphs/{graph}")
def overview(request: Request, graph: GraphData = Depends(get_graph)):
    jobs = request.app.state.jobs
    return render(request, "graph/overview.html", graph=graph, card=graph.card(), active="overview",
                  report=graph.report.current() or {}, checks=graph.checks_report.current() or {},
                  job=jobs.active(graph.pack), jobs=jobs.history(graph, limit=8), audit=read_audit(graph, limit=15),
                  robots_default=obey_robots_default(), derive=None)


@read_router.get("/graphs/{graph}/explore")
def explore(request: Request, graph: GraphData = Depends(get_graph)):
    return render(request, "graph/explore.html", graph=graph, active="explore", relations=sorted(RELATIONS))


@read_router.get("/graphs/{graph}/explore.json")
def explore_data(graph: GraphData = Depends(get_graph)):
    curation = graph.curation.current()
    if curation is None:
        return JSONResponse(dict(nodes=[], edges=[], error=graph.curation.error))
    bridges = graph.bridges()
    nodes = [dict(id=n.node_id, kind=n.kind, label=n.label, level=n.level, place=n.place, status=status_of(n),
                  author=n.provenance.author, summary=n.summary, names=n.names, topics=bridges.get(n.node_id, []))
             for n in curation.nodes]
    edges = [dict(id=e.edge_id, source=e.from_id, target=e.to_id, relation=e.relation, statement=e.statement,
                  place=e.place, status=status_of(e), author=e.provenance.author) for e in curation.edges]
    return JSONResponse(dict(graph=graph.graph, nodes=nodes, edges=edges))


@read_router.get("/graphs/{graph}/items")
def items(request: Request, graph: GraphData = Depends(get_graph), kind: str = "", status: str = "", q: str = ""):
    curation = graph.curation.current()
    rows = []
    for item in [*(curation.nodes if curation else []), *(curation.edges if curation else [])]:
        label = getattr(item, "label", None) or item.statement
        kind_of = getattr(item, "kind", None) or f"edge:{item.relation}"
        if kind and not kind_of.startswith(kind):
            continue
        if status and status_of(item) != status:
            continue
        if q and q.lower() not in f"{item_id(item)} {label}".lower():
            continue
        rows.append(dict(id=item_id(item), kind=kind_of, label=label, status=status_of(item), author=item.provenance.author))
    return render(request, "graph/items.html", graph=graph, active="items", rows=rows[:500], total=len(rows),
                  kind=kind, status=status, q=q)


@read_router.get("/graphs/{graph}/items/{item}")
def item_page(request: Request, item: str, graph: GraphData = Depends(get_graph)):
    found = graph.items().get(item)
    if found is None:
        raise HTTPException(404, f"Unknown node or edge {item!r}")
    curation = graph.curation.current()
    edges = [e for e in curation.edges if item in (e.from_id, e.to_id)] if hasattr(found, "node_id") else []
    queue = [item_id(i) for i in [*curation.nodes, *curation.edges] if status_of(i) in UNREVIEWED]
    following = next((candidate for candidate in queue if candidate != item and queue.index(candidate) > queue.index(item)),
                     None) if item in queue else (queue[0] if queue else None)
    return render(request, "graph/item.html", graph=graph, active="items", item=found, is_node=hasattr(found, "node_id"),
                  excerpts=[graph.excerpt(c) for c in found.evidence], edges=edges, sha256=graph.curation.sha256,
                  following=following, relations=sorted(RELATIONS), node_ids=sorted(n.node_id for n in curation.nodes))


def queue_items(graph: GraphData, status: str = "", kind: str = "") -> list:
    """The unreviewed nodes and edges matching the review filter, in file order."""
    curation = graph.curation.current()
    result = []
    for item in [*(curation.nodes if curation else []), *(curation.edges if curation else [])]:
        if status_of(item) not in UNREVIEWED or (status and status_of(item) != status):
            continue
        kind_of = getattr(item, "kind", None) or f"edge:{item.relation}"
        if kind and not kind_of.startswith(kind):
            continue
        result.append(item)
    return result


@read_router.get("/graphs/{graph}/review")
def review(request: Request, graph: GraphData = Depends(get_graph), status: str = "", kind: str = ""):
    cards = []
    for item in queue_items(graph, status, kind):
        kind_of = getattr(item, "kind", None) or f"edge:{item.relation}"
        cards.append(dict(id=item_id(item), kind=kind_of, text=getattr(item, "summary", None) or item.statement,
                          label=getattr(item, "label", None) or f"{item.from_id} -{item.relation}-> {item.to_id}",
                          status=status_of(item), author=item.provenance.author))
    return render(request, "graph/review.html", graph=graph, active="review", cards=cards[:300], total=len(cards),
                  status=status, kind=kind, sha256=graph.curation.sha256)


# --- writing ----------------------------------------------------------------------------------------------------------


def take_over(item, actor: str, changed_text: bool) -> None:
    """A person's edit makes the item theirs: Derive leaves it alone, and a changed claim needs a new confirmation."""
    provenance = item.provenance
    update = dict(author=actor) if provenance.author in ("derive", "skeleton") or provenance.author.startswith("graph-") else {}
    if changed_text and provenance.review_status == "human-reviewed":
        update.update(review_status="assistant-authored-unreviewed", reviewed_by=None, reviewed_on=None)
    if update:
        item.provenance = provenance.model_copy(update=update)


@write_router.post("/graphs/{graph}/items/{item}/save")
def save_item(item: str, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor),
              sha256: str = Form(""), label: str = Form(""), summary: str = Form(""), statement: str = Form(""),
              names: str = Form(""), keywords: str = Form(""), level: str = Form(""), place: str = Form("")):
    def mutate(curation):
        target = next((i for i in [*curation.nodes, *curation.edges] if item_id(i) == item), None)
        if target is None:
            raise WriteRefused(f"{item} is no longer in the graph")
        if hasattr(target, "node_id"):
            changed = summary.strip() != target.summary or pairs(names) != target.names
            target.label, target.summary, target.names = label.strip() or target.label, summary.strip(), pairs(names)
            target.keywords, target.level, target.place = lines(keywords), level or None, place.strip() or None
        else:
            changed = statement.strip() != target.statement or (place.strip() or None) != target.place
            target.statement, target.place = statement.strip(), place.strip() or None
        take_over(target, actor, changed)
    write_graph(graph, mutate, actor, f"edit {item}", expected_sha256=sha256, ids=[item])
    return redirect(graph, f"/items/{item}", notice="saved")


def review_items(curation, ids: set[str], action: str, actor: str, note: str) -> list:
    removed = []
    for item in [i for i in [*curation.nodes, *curation.edges] if item_id(i) in ids]:
        if action == "confirm":
            item.provenance = item.provenance.model_copy(update=dict(
                review_status="human-reviewed", reviewed_by=actor, reviewed_on=date.today(),
                notes=[*item.provenance.notes, *( [f"review: {note}"] if note else [])]))
        elif action == "flag":
            item.provenance = item.provenance.model_copy(update=dict(
                notes=[*item.provenance.notes, f"flag: {note or 'flagged'} ({actor}, {date.today().isoformat()})"]))
        elif action == "reject":
            removed.append(item)
    if removed:
        gone = {item_id(i) for i in removed}
        dangling = [e for e in curation.edges if e.from_id in gone or e.to_id in gone]
        removed.extend(e for e in dangling if e not in removed)
        gone |= {e.edge_id for e in dangling}
        curation.nodes = [n for n in curation.nodes if n.node_id not in gone]
        curation.edges = [e for e in curation.edges if e.edge_id not in gone]
        curation.role_rules = {role: rules for role, rules in curation.role_rules.items() if role not in gone}
    return removed


@write_router.post("/graphs/{graph}/items/{item}/review")
def review_item(item: str, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor),
                sha256: str = Form(""), action: str = Form(...), note: str = Form(""), following: str = Form("")):
    if action not in ("confirm", "flag", "reject"):
        raise WriteRefused(f"unknown review action {action!r}")
    if action in ("flag", "reject") and not note.strip():
        raise WriteRefused("flag and reject need a note")
    removed: list = []
    write_graph(graph, lambda curation: removed.extend(review_items(curation, {item}, action, actor, note.strip())),
                actor, f"{action} {item}", expected_sha256=sha256, ids=[item])
    for gone in removed:
        record_rejection(graph, gone, actor, note.strip())
    target = f"/items/{following}" if following else "/review"
    return redirect(graph, target, notice=f"{action}ed {item}".replace("confirmed", "confirmed").replace("flaged", "flagged"))


@write_router.post("/graphs/{graph}/review/bulk")
async def review_bulk(request: Request, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor)):
    form = await request.form()
    ids = set(form.getlist("ids"))
    if form.get("all_matching"):
        ids = {item_id(item) for item in queue_items(graph, form.get("filter_status") or "", form.get("filter_kind") or "")}
    action, note = form.get("action", ""), (form.get("note") or "").strip()
    if not ids or action not in ("confirm", "flag", "reject"):
        raise WriteRefused("select items and an action")
    if action in ("flag", "reject") and not note:
        raise WriteRefused("flag and reject need a note")
    bulk_note = f"{note}; " if note else ""
    removed: list = []
    write_graph(graph, lambda curation: removed.extend(review_items(
        curation, ids, action, actor, f"{bulk_note}confirmed in a bulk review of {len(ids)} items" if action == "confirm" else note)),
        actor, f"bulk {action} of {len(ids)} items", expected_sha256=form.get("sha256") or None, ids=sorted(ids))
    for gone in removed:
        record_rejection(graph, gone, actor, note)
    return redirect(graph, "/review", notice=f"{action} applied to {len(ids)} items")


@write_router.post("/graphs/{graph}/edges/new")
def new_edge(graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor), sha256: str = Form(""),
             from_id: str = Form(...), relation: str = Form(...), to_id: str = Form(...), statement: str = Form(...),
             place: str = Form(""), evidence: str = Form(...)):
    citations = []
    for reference in lines(evidence):
        if reference.startswith("text:"):
            _, document_id, blocks = reference.split(":", 2)
            first, _, last = blocks.partition("-")
            citations.append(GraphCitation(text=CitationRef(document_id=document_id, first_block=int(first),
                                                            last_block=int(last) if last else None)))
        else:
            pack, _, evidence_id = reference.partition(":")
            record = next((e for e in graph.packs.release(pack).evidence if e.evidence_id == evidence_id), None)
            if record is None:
                raise WriteRefused(f"{reference} is not an evidence record of {pack}")
            citations.append(GraphCitation(pack=PackEvidenceRef(pack=pack, evidence_id=evidence_id,
                                                                excerpt_sha256=record.excerpt_sha256)))
    edge_id = f"{from_id}.{relation}.{to_id}" + (f".{place.strip().lower()}" if place.strip() else "")

    def mutate(curation):
        if any(e.edge_id == edge_id for e in curation.edges):
            raise WriteRefused(f"the edge {edge_id} exists already")
        curation.edges.append(CuratedEdge(edge_id=edge_id, from_id=from_id, relation=relation, to_id=to_id,
                                          statement=statement.strip(), place=place.strip() or None, evidence=citations,
                                          provenance=Provenance(kind="curated-statement",
                                                                review_status="assistant-authored-unreviewed", author=actor)))
    write_graph(graph, mutate, actor, f"add {edge_id}", expected_sha256=sha256, ids=[edge_id])
    return redirect(graph, f"/items/{edge_id}", notice="edge added")


@write_router.post("/graphs/{graph}/derive")
def derive(request: Request, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor),
           apply: str = Form("")):
    if not apply:
        preview = run_derive(graph.curation_path, graph.root, apply=False)
        jobs = request.app.state.jobs
        return render(request, "graph/overview.html", graph=graph, card=graph.card(), active="overview",
                      report=graph.report.current() or {}, checks=graph.checks_report.current() or {},
                      job=jobs.active(graph.pack), jobs=jobs.history(graph, limit=8), audit=read_audit(graph, limit=15),
                      robots_default=obey_robots_default(), derive=preview)
    applied: dict = {}

    def mutate(curation):
        from swisstip.build.graph_derive import apply_derivation, derive as derived
        from swisstip.build.places import load_place_register
        register = (load_place_register((graph.curation_path.parent / curation.place_register).resolve())
                    if curation.place_register else None)
        applied.update(apply_derivation(curation, derived(curation, graph.root, register)).as_dict())
    write_graph(graph, mutate, actor, "apply Derive from packs")
    return redirect(graph, notice=f"Derive applied: {len(applied.get('added', []))} added, "
                                  f"{len(applied.get('updated', []))} updated")


def graph_job(graph: GraphData, options: dict):
    def target(log):
        pipeline = GraphPipeline(graph.root, graph.graph, download=bool(options.get("download")),
                                 update_curation=bool(options.get("update_curation")), obey_robots=options.get("obey_robots"),
                                 lock_pack=False, log=log)
        report = pipeline.run_stages(options["start"], options["until"])
        log(f"exit code {report['exit_code']}")
        return dict(exit_code=report["exit_code"], stages=[dict(stage=s["stage"], status=s.get("status"), error=s.get("error"))
                                                           for s in report["stages"]])
    return target


@write_router.post("/graphs/{graph}/compile")
def compile_graph(request: Request, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor)):
    options = dict(start="compile", until="check")
    try:
        job = request.app.state.jobs.start(graph, "compile", actor, options, graph_job(graph, options))
    except RuntimeError as exc:
        return redirect(graph, error=str(exc))
    return redirect(graph, notice=f"compile job {job.job_id} started")


@write_router.post("/graphs/{graph}/refresh")
def refresh_graph(request: Request, graph: GraphData = Depends(get_graph), actor: str = Depends(require_editor),
                  confirm_graph: str = Form(""), override_robots: str = Form("")):
    if confirm_graph.strip() != graph.graph:
        return redirect(graph, error="type the graph name to confirm a refresh, which downloads its sources again")
    options = dict(start="acquire", until="check", download=True, update_curation=True,
                   obey_robots=False if override_robots else None)
    try:
        job = request.app.state.jobs.start(graph, "refresh", actor, options, graph_job(graph, options))
    except RuntimeError as exc:
        return redirect(graph, error=str(exc))
    return redirect(graph, notice=f"refresh job {job.job_id} started")


@read_router.get("/graphs/{graph}/jobs/{job_id}")
def job_log(request: Request, job_id: str, graph: GraphData = Depends(get_graph)):
    tail = request.app.state.jobs.tail(graph, job_id)
    state = next((j for j in request.app.state.jobs.history(graph, limit=50) if j.get("job_id") == job_id), {})
    return render(request, "graph/job.html", graph=graph, active="overview", state=state, log=tail["text"])
