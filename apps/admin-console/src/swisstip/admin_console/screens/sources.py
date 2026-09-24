"""Screen 4.2: sources and the coverage matrix.

The catalogue table joins `sources.json` with the run's gap verdicts and the
text index, so a source that saved but extracted nothing, or saved a soft
error page the gap report cannot see, shows in one row. The discovered pages
of the run are listed below it and can be promoted into the catalogue with a
documented discovery reference. The matrix is the KB2 progress board.
"""

import posixpath
from collections import Counter
from datetime import date
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.ingestion.catalog import CANTONS as CANTON_CODES, LANGUAGES, SCAN_STATUSES
from swisstip.ingestion.review_decisions import load_decisions, text_review

from ..app import get_pack, render, require_editor
from ..data import PackData, coverage_matrix, host_of, paginate, record_language
from ..writes import WriteRefused, write_catalogue

read_router = APIRouter()
write_router = APIRouter()

AUTHORITY_LEVELS = ("federal", "cantonal", "municipal")
PRIORITIES = ("P0", "P1", "P2")
DISCOVERY_METHODS = ("official_search_result", "official_page_link", "official_directory_link")
JURISDICTIONS = ["CH", *sorted("CH-" + code for code in CANTON_CODES)]


def catalogue_rows(pack: PackData) -> list[dict]:
    """One row per source with the three derived columns of section 4.2."""
    gaps = pack.gap_by_source()
    decisions = load_decisions(pack.run_dir, catalogue_sha256=(pack.plan.current() or {}).get("catalogue_sha256"))
    citing = Counter()
    for _, fact in pack.curated_facts():
        for citation in fact.evidence:
            entry = pack.dataset.entry(citation.document_id)
            for source_id in (entry or {}).get("source_ids", []):
                citing[source_id] += 1
    records: dict[str, list[dict]] = {}
    for entry in pack.dataset.entries:
        for source_id in entry.get("source_ids", []):
            records.setdefault(source_id, []).append(entry)
    rows = []
    for entry in pack.source_entries():
        definition = entry["definition"]
        source_id = definition["source_id"]
        target = gaps.get(source_id)
        seeds = [record for record in records.get(source_id, [])
                 if record["source_url"] == definition["start_url"] and not record.get("superseded")]
        seed = seeds[0] if seeds else None
        rows.append(dict(
            source_id=source_id, title=entry["title"], authority_level=entry["authority_level"],
            jurisdiction=definition["jurisdiction"], language=definition["language"], priority=entry["priority"],
            scan_status=entry["scan_status"], canonical_authority=definition["canonical_authority"],
            start_url=definition["start_url"], source_kind=entry.get("source_kind", ""),
            gap=target.get("gap") if target else None, retriable=target.get("retriable") if target else None,
            disposition=(target or {}).get("disposition"), review=(target or {}).get("review"),
            text_review=text_review(seed, decisions) if seed else None,
            action=target.get("action") if target else "", target_status=target.get("status") if target else None,
            record_status=record_status(seed), page_kind=(seed or {}).get("page_kind"),
            document_id=(seed or {}).get("document_id"), records=len(records.get(source_id, [])),
            facts=citing.get(source_id, 0)))
    return rows


def record_status(entry: dict | None) -> str:
    """What the text index says about the source's own page; `error_page` closes the ch.ch gap."""
    if entry is None:
        return "no record"
    if entry.get("page_kind") in ("error_page", "soft_404"):
        return "error_page"
    if entry.get("status") == "excluded_source_response":
        return f"excluded ({entry.get('page_kind') or 'unknown'})"
    if not entry.get("eligible_for_processing"):
        return "no_extractable_text"
    return entry.get("status") or "extracted"


def discovered_groups(pack: PackData) -> list[dict]:
    """The run's discovered pages by attribution kind and host, as the gap report renders them."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for target in pack.gap_targets():
        if target.get("kind") == "catalogue":
            continue
        groups.setdefault((host_of(target["url"]), target["kind"]), []).append(target)
    rows = []
    for (host, kind), items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        rows.append(dict(host=host, kind=kind, pages=len(items),
                         saved=sum(1 for item in items if item["status"] == "saved"),
                         gaps=dict(Counter(item["gap"] for item in items
                                           if item.get("outstanding", item["gap"] != "none"))),
                         approved=sum(1 for item in items if item.get("disposition") == "approved"),
                         targets=items[:50]))
    return rows


def matching(rows: list[dict], filters: dict) -> list[dict]:
    """Filters on every column; an empty value matches everything."""
    for key, value in filters.items():
        if not value:
            continue
        needle = value.casefold()
        rows = [row for row in rows if needle in str(row.get(key, "")).casefold()]
    return rows


@read_router.get("/packs/{pack}/sources")
def table(request: Request, pack: PackData = Depends(get_pack), tab: str = "catalogue", page: int = 1,
          source_id: str = "", authority_level: str = "", jurisdiction: str = "", language: str = "",
          priority: str = "", scan_status: str = "", gap: str = "", record_status_filter: str = ""):
    filters = dict(source_id=source_id, authority_level=authority_level, jurisdiction=jurisdiction,
                   language=language, priority=priority, scan_status=scan_status, gap=gap,
                   record_status=record_status_filter)
    rows = matching(catalogue_rows(pack), filters)
    return render(request, "sources/index.html", pack=pack, active="sources", tab=tab,
                  listing=paginate(rows, page), filters=filters, discovered=discovered_groups(pack),
                  matrix=coverage_matrix(pack) if tab == "matrix" else None,
                  levels=AUTHORITY_LEVELS, languages=sorted(LANGUAGES), jurisdictions=JURISDICTIONS)


@read_router.get("/packs/{pack}/sources/new")
def new_source(request: Request, pack: PackData = Depends(get_pack), url: str = "", label: str = ""):
    """The promote action: the form prefilled from a discovered page (section 4.2)."""
    prefilled = prefill_from_page(pack, url, label) if url else {}
    return render(request, "sources/form.html", pack=pack, active="sources", source=prefilled, creating=True,
                  levels=AUTHORITY_LEVELS, priorities=PRIORITIES, methods=DISCOVERY_METHODS,
                  scan_statuses=sorted(SCAN_STATUSES), languages=sorted(LANGUAGES), jurisdictions=JURISDICTIONS,
                  topics=[topic["topic_id"] for topic in (pack.catalogue.current() or {}).get("planning_topics", [])],
                  sha256=pack.catalogue.sha256, today=date.today().isoformat())


@read_router.get("/packs/{pack}/sources/{source_id}")
def source_form(request: Request, source_id: str, pack: PackData = Depends(get_pack)):
    entry = pack.source(source_id)
    if entry is None:
        return RedirectResponse(f"/packs/{pack.pack}/sources?error=unknown+source+{source_id}", status_code=303)
    definition = entry["definition"]
    source = dict(entry, **definition, allowed_hosts="\n".join(definition["allowed_hosts"]),
                  allowed_path_prefixes="\n".join(definition["allowed_path_prefixes"]),
                  topic_hints="\n".join(entry.get("topic_hints", [])),
                  discovery_method=entry["discovery"]["method"], reference_url=entry["discovery"]["reference_url"],
                  located_on=entry["discovery"]["located_on"],
                  municipality_name=(entry.get("municipality") or {}).get("name", ""),
                  bfs_code=(entry.get("municipality") or {}).get("bfs_code", ""))
    return render(request, "sources/form.html", pack=pack, active="sources", source=source, creating=False,
                  levels=AUTHORITY_LEVELS, priorities=PRIORITIES, methods=DISCOVERY_METHODS,
                  scan_statuses=sorted(SCAN_STATUSES), languages=sorted(LANGUAGES), jurisdictions=JURISDICTIONS,
                  topics=[topic["topic_id"] for topic in (pack.catalogue.current() or {}).get("planning_topics", [])],
                  sha256=pack.catalogue.sha256, today=date.today().isoformat())


def prefill_from_page(pack: PackData, url: str, label: str) -> dict:
    """URL, host, path prefix, language hint and the attributing source's authority and jurisdiction."""
    parsed = urlsplit(url)
    target = next((item for item in pack.gap_targets() if item["url"] == url), {})
    attributing = next((pack.source(source_id) for source_id in target.get("source_ids", [])
                        if pack.source(source_id)), None)
    entry = next((item for item in pack.dataset.entries if item["source_url"] == url), None)
    prefix = posixpath.dirname(unquote(parsed.path or "/")) or "/"
    definition = (attributing or {}).get("definition", {})
    return dict(source_id="", title=label or target.get("label") or url, start_url=url,
                allowed_hosts=parsed.hostname or "", allowed_path_prefixes=prefix,
                canonical_authority=definition.get("canonical_authority", ""),
                jurisdiction=definition.get("jurisdiction", "CH"),
                language=(record_language(entry) if entry else definition.get("language", "de")),
                authority_level=(attributing or {}).get("authority_level", "federal"),
                source_kind=(attributing or {}).get("source_kind", "official_guidance"), priority="P1",
                topic_hints="\n".join((attributing or {}).get("topic_hints", [])), scan_status="ready",
                discovery_method="official_page_link",
                reference_url=definition.get("start_url") or url, located_on=date.today().isoformat(),
                notes=f"Promoted from a page the run discovered under {definition.get('source_id', 'the crawl')}.",
                municipality_name=(attributing or {}).get("municipality", {}).get("name", ""),
                bfs_code=(attributing or {}).get("municipality", {}).get("bfs_code", ""))


def lines(value: str) -> list[str]:
    return [item.strip() for item in (value or "").replace(",", "\n").splitlines() if item.strip()]


def entry_from_form(form: dict) -> dict:
    entry = {
        "definition": {
            "source_id": form["source_id"].strip(), "start_url": form["start_url"].strip(),
            "allowed_hosts": lines(form["allowed_hosts"]), "allowed_path_prefixes": lines(form["allowed_path_prefixes"]),
            "canonical_authority": form["canonical_authority"].strip(), "jurisdiction": form["jurisdiction"].strip(),
            "language": form["language"].strip()},
        "title": form["title"].strip(), "authority_level": form["authority_level"],
        "source_kind": form["source_kind"].strip() or "official_guidance", "priority": form["priority"],
        "topic_hints": lines(form["topic_hints"]),
        "discovery": {"method": form["discovery_method"], "reference_url": form["reference_url"].strip(),
                      "located_on": form["located_on"].strip()},
        "scan_status": form["scan_status"], "notes": form["notes"].strip()}
    if form["authority_level"] == "municipal":
        entry["municipality"] = {"name": form["municipality_name"].strip(), "bfs_code": form["bfs_code"].strip()}
    return entry


@write_router.post("/packs/{pack}/sources")
def save_source(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                source_id: str = Form(...), original_id: str = Form(""), title: str = Form(...),
                start_url: str = Form(...), allowed_hosts: str = Form(...), allowed_path_prefixes: str = Form(...),
                canonical_authority: str = Form(...), jurisdiction: str = Form(...), language: str = Form(...),
                authority_level: str = Form(...), source_kind: str = Form("official_guidance"),
                priority: str = Form("P1"), topic_hints: str = Form(""), discovery_method: str = Form(...),
                reference_url: str = Form(...), located_on: str = Form(...), scan_status: str = Form("ready"),
                notes: str = Form(""), municipality_name: str = Form(""), bfs_code: str = Form(""),
                sha256: str = Form("")):
    entry = entry_from_form(dict(source_id=source_id, title=title, start_url=start_url, allowed_hosts=allowed_hosts,
                                 allowed_path_prefixes=allowed_path_prefixes, canonical_authority=canonical_authority,
                                 jurisdiction=jurisdiction, language=language, authority_level=authority_level,
                                 source_kind=source_kind, priority=priority, topic_hints=topic_hints,
                                 discovery_method=discovery_method, reference_url=reference_url,
                                 located_on=located_on, scan_status=scan_status, notes=notes,
                                 municipality_name=municipality_name, bfs_code=bfs_code))
    new_id = entry["definition"]["source_id"]

    def mutate(data: dict) -> None:
        existing = [item for item in data["sources"] if item["definition"]["source_id"] == (original_id or new_id)]
        if original_id and not existing:
            raise WriteRefused(f"unknown source {original_id}")
        if not original_id and any(item["definition"]["source_id"] == new_id for item in data["sources"]):
            raise WriteRefused(f"source_id {new_id} is already in the catalogue")
        if existing:
            data["sources"][data["sources"].index(existing[0])] = entry
        else:
            data["sources"].append(entry)
        scope = data["scope"]
        if jurisdiction != "CH" and jurisdiction not in scope["canton_codes"]:
            scope["canton_codes"] = sorted([*scope["canton_codes"], jurisdiction])

    reason = f"{'edit' if original_id else 'add'} source {new_id}"
    result = write_catalogue(pack, mutate, actor, reason, expected_sha256=sha256 or None, ids=[new_id])
    notice = f"saved+{new_id}" + ("+-+the+catalogue+changed,+plan+a+new+run" if result["needs_new_run"] else "")
    return RedirectResponse(f"/packs/{pack.pack}/sources?notice={notice}", status_code=303)
