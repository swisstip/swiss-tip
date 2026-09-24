"""Screen 4.1: packs overview, the home screen.

One card per pack with four rows (sources, text, curation, release), read
from the files the pipeline writes. Writes nothing except when a pack is
added, which creates `releases/<pack>/sources.json` from the catalogue
template with an empty source list.
"""

import json
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.ingestion.catalog import save_source_catalog

from ..app import get_pack, render, require_editor
from ..data import PackData, pack_card
from ..writes import append_audit, read_audit

read_router = APIRouter()
write_router = APIRouter()

CATALOGUE_TEMPLATE = {
    "schema_version": "source-catalog/v1",
    "artifact_id": "",
    "version": "draft-1",
    "knowledge_space_id": "hackathon",
    "title": "",
    "status": "SOURCES_ONLY",
    "scope": {"country_code": "CH", "canton_codes": [], "description": "", "exclusions": []},
    "planning_topics": [{"topic_id": "residence-permits", "label": "Residence permits and documents"}],
    "language_discovery": {"preferred_seed_language": "de", "seed_languages_are_hints": True},
    "crawl_profiles": {"smoke": {"max_depth": 0, "max_pages": 1, "max_requests": 5, "max_total_bytes": 3000000,
                                 "max_response_bytes": 2000000, "max_duration_seconds": 60,
                                 "request_timeout_seconds": 15, "delay_seconds": 2, "max_redirects": 2,
                                 "max_links_per_page": 200, "max_queued_urls": 50, "max_failures": 2}},
    "scan_sets": {},
    "build_notes": [],
    "sources": [],
    "parallel_page_groups": [],
    "evidence_selection_policy": {"prefer": "official_guidance", "notes": ""},
}


@read_router.get("/")
def overview(request: Request):
    console = request.app.state.console
    cards = []
    for name in console.pack_names():
        pack = console.pack(name)
        cards.append(dict(card=pack_card(pack), job=request.app.state.jobs.active(name)))
    graphs = request.app.state.graphs
    graph_cards = [dict(card=graphs.graph(name).card(), job=request.app.state.jobs.active(f"graph-{name}"))
                   for name in graphs.names()]
    return render(request, "packs/index.html", cards=cards, graph_cards=graph_cards, active="packs", today=date.today())


@read_router.get("/packs/{pack}/audit")
def audit(request: Request, pack: PackData = Depends(get_pack)):
    return render(request, "packs/audit.html", pack=pack, entries=read_audit(pack), active="packs")


@write_router.post("/packs")
def add_pack(request: Request, name: str = Form(...), title: str = Form(""), actor: str = Depends(require_editor)):
    console = request.app.state.console
    folder = console.root / "releases" / name.strip()
    if not name.strip() or folder.exists():
        return RedirectResponse(f"/?error=pack+{name}+exists+or+has+no+name", status_code=303)
    folder.mkdir(parents=True)
    catalogue = json.loads(json.dumps(CATALOGUE_TEMPLATE))
    catalogue["artifact_id"] = f"{name.strip()}-sources"
    catalogue["title"] = title.strip() or f"Sources of {name.strip()}"
    path = folder / "sources.json"
    save_source_catalog(path, catalogue)  # the template is validated before it reaches disk
    pack = console.pack(name.strip())
    append_audit(pack, actor, path, None, pack.catalogue.sha256 or "", f"create pack {name.strip()}", [name.strip()])
    return RedirectResponse(f"/packs/{name.strip()}/sources?notice=pack+created", status_code=303)
