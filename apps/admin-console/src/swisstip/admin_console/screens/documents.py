"""Screen 4.4: the document list, the reading view and the diff view.

The list is the text index, filtered and paged; a record is read only when
it is opened. The reading view is the Markdown reading view as a page: one
line per block with its number in the margin, tables as tables, marks for
region, furniture, hidden text and footnotes, and the fact IDs of every
cited block. Selecting a range of blocks shows the excerpt as the build
would cut it and offers to cite it. Nothing here writes.
"""

import difflib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from swisstip.extraction.reading_view import SKIP_REGIONS
from swisstip.ingestion.review_decisions import load_decisions, text_review

from ..app import get_pack, render
from ..data import PackData, paginate

read_router = APIRouter()

FURNITURE_TOGGLES = ("navigation", "banner", "footer", "breadcrumb", "share", "cookie-notice", "language-menu",
                     "related-links", "anchor-navigation", "scroll-to-top", "chat-link", "link-only")


def is_furniture(block: dict) -> bool:
    locator = block.get("source_locator", {})
    return bool(locator.get("furniture")) or locator.get("region") in SKIP_REGIONS


def marks_of(block: dict) -> list[str]:
    locator = block.get("source_locator", {})
    marks = []
    if locator.get("region") in SKIP_REGIONS:
        marks.append(locator["region"])
    marks.extend(locator.get("furniture", []))
    if locator.get("explicit_hidden"):
        marks.append("hidden")
    if locator.get("is_footnote"):
        marks.append("footnote")
    if locator.get("page") is not None:
        marks.append(f"page {locator['page']}")
    return marks


def block_rows(record: dict, cited: dict[int, list[str]]) -> list[dict]:
    rows = []
    for number, block in enumerate(record.get("blocks", []), 1):
        locator = block.get("source_locator", {})
        rows.append(dict(number=number, block=block, kind=block["kind"],
                         depth=min(len(block.get("heading_path", [])), 6), marks=marks_of(block),
                         furniture=is_furniture(block), hidden=bool(locator.get("explicit_hidden")),
                         dom_path=locator.get("dom_path"), links=block.get("links", []),
                         facts=cited.get(number, [])))
    return rows


def index_row(pack: PackData, entry: dict, cited: dict[str, list[str]]) -> dict:
    return dict(entry, facts=len(cited.get(entry["document_id"], [])),
                fact_ids=cited.get(entry["document_id"], []),
                exclusions=", ".join(entry.get("exclusion_reasons", [])),
                sources=", ".join(entry.get("source_ids", [])))


def document_worklists(pack: PackData, rows: list[dict]) -> dict:
    """Keep approved scope decisions out of the pending acquisition and extraction lists."""
    approved_urls = {target["url"]: target.get("review") for target in pack.gap_targets()
                     if target.get("disposition") == "approved"}
    approved_records = {row["document_id"]: row for row in rows if row.get("scope_review")}
    unavailable, errors, accepted = [], [], []
    for item in pack.dataset.unavailable.current() or []:
        if item["url"] in approved_urls:
            accepted.append(dict(url=item["url"], document_id=None, status=item.get("status"),
                                 review=approved_urls[item["url"]]))
        else:
            unavailable.append(item)
    for item in pack.dataset.errors.current() or []:
        if item.get("document_id") not in approved_records:
            errors.append(item)
    for row in approved_records.values():
        accepted.append(dict(url=row["source_url"], document_id=row["document_id"], status=row.get("status"),
                             review=row["scope_review"]))
    return dict(unavailable=unavailable, errors=errors, accepted=accepted)


@read_router.get("/packs/{pack}/documents")
def index(request: Request, pack: PackData = Depends(get_pack), tab: str = "records", page: int = 1, q: str = "",
          page_kind: str = "", status: str = "", language: str = "", attribution_kind: str = "",
          representation: str = "", eligible: str = "", superseded: str = "", cited: str = ""):
    citing = pack.facts_by_document()
    rows = [index_row(pack, entry, citing) for entry in pack.dataset.entries]
    decisions = load_decisions(pack.run_dir, catalogue_sha256=(pack.plan.current() or {}).get("catalogue_sha256"))
    for row in rows:
        row["scope_review"] = text_review(row, decisions)
    worklists = document_worklists(pack, rows)
    if q:
        needle = q.casefold()
        rows = [row for row in rows if needle in (row.get("title") or "").casefold()
                or needle in row["source_url"].casefold()]
    for key, value in (("page_kind", page_kind), ("status", status), ("attribution_kind", attribution_kind),
                       ("representation", representation)):
        if value:
            rows = [row for row in rows if str(row.get(key, "")) == value]
    if language:
        rows = [row for row in rows if language in (row.get("language_declared") or row.get("language_hint") or "")]
    if eligible:
        rows = [row for row in rows if bool(row.get("eligible_for_processing")) == (eligible == "yes")]
    if superseded:
        rows = [row for row in rows if bool(row.get("superseded")) == (superseded == "yes")]
    if cited:
        rows = [row for row in rows if bool(row["facts"]) == (cited == "yes")]
    facets = {key: sorted({str(entry.get(key, "")) for entry in pack.dataset.entries if entry.get(key) is not None})
              for key in ("page_kind", "status", "attribution_kind", "representation")}
    return render(request, "documents/index.html", pack=pack, active="documents", tab=tab,
                  listing=paginate(rows, page), facets=facets,
                  filters=dict(q=q, page_kind=page_kind, status=status, language=language,
                               attribution_kind=attribution_kind, representation=representation,
                               eligible=eligible, superseded=superseded, cited=cited),
                  **worklists)


@read_router.get("/packs/{pack}/documents/{document_id}")
def reading(request: Request, document_id: str, pack: PackData = Depends(get_pack), furniture: str = "",
            hidden: str = "", raw: str = "", first: int = 0, last: int = 0, block: int = 0):
    record = pack.dataset.record(document_id)
    if record is None:
        return RedirectResponse(f"/packs/{pack.pack}/documents?error=unknown+record+{document_id}", status_code=303)
    entry = pack.dataset.entry(document_id) or {}
    rows = block_rows(record, pack.facts_by_block(document_id))
    selection = excerpt_of(record, first, last) if first else None
    curation = pack.curation.current()
    return render(request, "documents/reading.html", pack=pack, active="documents", record=record, entry=entry,
                  rows=rows, show_furniture=bool(furniture), show_hidden=bool(hidden), show_raw=bool(raw),
                  selection=selection, first=first, last=last, focus=block or first,
                  facts=[(fact.fact_id, fact.statement[:80]) for _, fact in pack.curated_facts()],
                  concepts=[(concept.concept_id, concept.label) for concept in (curation.concepts if curation else [])],
                  successor=pack.dataset.successor(entry) if entry.get("superseded") else None)


def excerpt_of(record: dict, first: int, last: int) -> dict | None:
    """The excerpt exactly as `build_release` would cut it: the first block's start to the last block's end."""
    last = last or first
    blocks = record.get("blocks", [])
    if not (1 <= first <= last <= len(blocks)):
        return None
    chosen = blocks[first - 1:last]
    start, end = chosen[0]["start"], chosen[-1]["end"]
    return dict(first=first, last=last, start=start, end=end, text=record["content_text"][start:end],
                heading_path=chosen[0].get("heading_path", []),
                block_ids=[block["block_id"] for block in chosen], blocks=len(chosen))


@read_router.get("/packs/{pack}/documents/{document_id}/excerpt")
def excerpt(request: Request, document_id: str, first: int = 1, last: int = 0,
            pack: PackData = Depends(get_pack)):
    """The selection panel, refreshed while the expert picks blocks."""
    record = pack.dataset.record(document_id)
    selection = excerpt_of(record, first, last or first) if record else None
    curation = pack.curation.current()
    return render(request, "documents/excerpt.html", pack=pack, document_id=document_id, selection=selection,
                  concepts=[(concept.concept_id, concept.label) for concept in (curation.concepts if curation else [])],
                  facts=[(fact.fact_id, fact.statement[:80]) for _, fact in pack.curated_facts()])


@read_router.get("/packs/{pack}/documents/{document_id}/diff")
def diff(request: Request, document_id: str, pack: PackData = Depends(get_pack)):
    """A superseded record beside the record that replaced it, blocks aligned by text hash."""
    entry = pack.dataset.entry(document_id)
    if entry is None:
        return RedirectResponse(f"/packs/{pack.pack}/documents?error=unknown+record+{document_id}", status_code=303)
    successor_entry = pack.dataset.successor(entry)
    old = pack.dataset.record(document_id)
    new = pack.dataset.record(successor_entry["document_id"]) if successor_entry else None
    rows = align_blocks(old, new) if old and new else []
    outcomes = pack.citation_outcomes()
    citations = [dict(fact_id=fact.fact_id, concept_id=concept.concept_id, number=number,
                      first_block=citation.first_block, last_block=citation.last_block,
                      outcome=next((item for item in outcomes.get(fact.fact_id, [])
                                    if item.get("document_id") == document_id), {}))
                 for concept, fact in pack.curated_facts()
                 for number, citation in enumerate(fact.evidence, 1) if citation.document_id == document_id]
    return render(request, "documents/diff.html", pack=pack, active="documents", entry=entry,
                  successor=successor_entry, rows=rows, citations=citations,
                  changed=sum(1 for row in rows if row["state"] != "same"))


def align_blocks(old: dict, new: dict) -> list[dict]:
    """Align by `text_sha256`; unchanged runs collapse, added and removed blocks stand out."""
    old_hashes = [block["text_sha256"] for block in old["blocks"]]
    new_hashes = [block["text_sha256"] for block in new["blocks"]]
    rows = []
    matcher = difflib.SequenceMatcher(None, old_hashes, new_hashes, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows.append(dict(state="same", count=i2 - i1, old_from=i1 + 1, old_to=i2, new_from=j1 + 1, new_to=j2))
            continue
        for offset in range(max(i2 - i1, j2 - j1)):
            old_block = old["blocks"][i1 + offset] if i1 + offset < i2 else None
            new_block = new["blocks"][j1 + offset] if j1 + offset < j2 else None
            rows.append(dict(state="changed" if old_block and new_block else "removed" if old_block else "added",
                             old=old_block, new=new_block,
                             old_number=i1 + offset + 1 if old_block else None,
                             new_number=j1 + offset + 1 if new_block else None))
    return rows
