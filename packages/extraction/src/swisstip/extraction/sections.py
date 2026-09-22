"""Sections of a text record, and which records a curator is expected to read.

A record's blocks fall into consecutive heading runs, the sections. A section is
*content* when it keeps at least one block that is neither page furniture
(navigation, header, footer, cookie notice, share bar) nor a footnote nor a
control, and when it is not a bare list of links. Everything here is a pure
derivation over the immutable blocks: no offsets change, nothing is
interpreted, no model is called.

Two consumers share these rules: the concept-extraction jobs, which read one
section at a time, and the curation coverage stage of the build, which asks
whether every content section of every candidate record is cited by a fact or
dispositioned by a curator. They must agree on what a section is, so the
rules live here and the concepts package imports them.

A *curation candidate* is a record a curator is expected to look at: an
extracted, eligible, preferred representation of a page that is neither
superseded nor a language variant of another page. The exclusions are named so
that a coverage report can say why a record is not asked for, and so that the
question "was this page ever read?" has one answer in the dataset instead of
one per consumer.

A *repeated section* is a content section whose text, whitespace and case
aside, also stands on another candidate page of the same host: the contact
card, the counter hours, the closure notice a site prints on every page of a
service. The dataset marks it on every page it appears on, with the number of
pages, and never drops it: whether it is boilerplate to set aside is the
build's decision under the pack's threshold, and which page cites it is the
coverage report's. Counting is per host because the same sentence on two
authorities' sites is two authorities saying it, not one site's furniture.
"""

import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit

FURNITURE_HEADINGS = frozenset({
    "kontakt", "contact", "contacts", "kontaktinformationen", "contact details",
    "zuständigkeit", "zuständiges amt", "suche", "search", "navigation",
    "inhaltsverzeichnis", "table of contents", "seite teilen", "share", "feedback",
    "war diese seite hilfreich?", "cookie-einstellungen", "auf dieser seite",
    "bitte geben sie uns feedback", "kontaktformular migrationsamt",
    "das könnte sie auch interessieren", "für dieses thema zuständig:",
    "breadcrumb", "seitenpfad (breadcrumb)",
})
FURNITURE_LABELS = frozenset({
    "navigation", "banner", "footer", "breadcrumb", "skiplinks", "share",
    "cookie-notice", "language-menu", "related-links", "anchor-navigation",
    "scroll-to-top", "chat-link",
})
NEWS_HEADINGS = {"news", "aktuell", "aktuelle meldungen", "neuigkeiten", "nachrichten"}
LINK_HEADINGS = {"links", "weiterführende links", "weitere links", "related links", "useful links", "external links",
                 "liens", "liens utiles", "collegamenti", "link"}

# Why an index entry is not a curation candidate, in the order the rules are tried.
CANDIDATE_EXCLUSIONS = ("not_extractable", "superseded", "secondary_representation", "language_variant",
                        "out_of_scope_page")


def terminology_key(value: str) -> str:
    # The distinction between, for example, ss and ß is deliberately preserved.
    return unicodedata.normalize("NFC", value).lower()


def heading_path(block: dict) -> list[str]:
    value = block.get("heading_path", [])
    return value.split(" > ") if isinstance(value, str) else list(value)


def block_exclusion(block: dict) -> str | None:
    locator = block.get("source_locator") or {}
    labels = set(locator.get("furniture") or [])
    if locator.get("explicit_hidden"):
        return "hidden"
    if locator.get("region") in {"nav", "footer", "header", "form"}:
        return "region"
    if labels & FURNITURE_LABELS:
        return "furniture"
    if block.get("kind") == "control":
        return "control"
    if labels & {"page-header", "page-footer"}:
        return "page-furniture"
    if locator.get("is_footnote") or block.get("is_footnote"):
        return "footnote"
    return None


def _link_only(blocks: list[dict], path: list[str]) -> bool:
    if not blocks:
        return False
    if all(block.get("kind") == "list_item" and "link-only" in
           (block.get("source_locator") or {}).get("furniture", []) for block in blocks):
        return True
    if not path or terminology_key(path[-1].strip()) not in LINK_HEADINGS:
        return False
    return all(block.get("links") and " ".join(block["text"].split()) ==
               " ".join(" ".join(link.get("text", "").split()) for link in block["links"])
               for block in blocks)


def build_sections(record: dict) -> list[dict]:
    """Return all consecutive heading runs, including explicit exclusion reasons.

    ``span_blocks`` and ``context_blocks`` retain the original blocks and their
    document numbers for chunk construction. ``section_summary`` removes them.
    """
    sections = []
    all_headings = {terminology_key(part.strip()) for block in record.get("blocks", [])
                    for part in heading_path(block)}
    contact_page = len(all_headings) == 1 and bool(all_headings & {"kontakt", "contact", "contacts"})
    for number, original in enumerate(record.get("blocks", []), 1):
        path = heading_path(original)
        if not sections or sections[-1]["heading_path"] != path:
            sections.append(dict(section_id=f"section-{len(sections) + 1:04d}", first_block=number,
                                 last_block=number, heading_path=path, characters=0,
                                 span_blocks=[], context_blocks=[], excluded_blocks=[]))
        section = sections[-1]
        section["last_block"] = number
        block = dict(original, block_number=number)
        keys = [terminology_key(part.strip()) for part in path]
        reason = block_exclusion(block)
        # The root of a path is the page's own label and says nothing about a section under it: the City of Zurich
        # pages hang every block under a stray "Navigation" heading, which used to make whole pages furniture.
        # Below the root, and for a path of one, a furniture heading excludes the block.
        furniture_keys = keys[1:] if len(keys) > 1 else keys
        if any(part in NEWS_HEADINGS for part in keys[1:]):
            reason = "embedded_news"
        elif not contact_page and any(part in FURNITURE_HEADINGS for part in furniture_keys):
            reason = "page_furniture_heading"
        if reason:
            section["excluded_blocks"].append(dict(block_number=number, reason=reason))
            if reason == "footnote":
                section["context_blocks"].append(block)
        elif block.get("kind") != "heading" and block.get("text", "").strip():
            section["span_blocks"].append(block)
            section["context_blocks"].append(block)
            section["characters"] += len(block["text"])
    for section in sections:
        blocks = section["span_blocks"]
        section["kept"] = bool(blocks)
        section["link_only"] = _link_only(blocks, section["heading_path"])
        if not blocks:
            excluded = section["excluded_blocks"]
            section["reason"] = excluded[0]["reason"] if excluded else "no_spans"
        elif section["link_only"]:
            section["reason"] = "link_only"
    return sections


def section_summary(section: dict) -> dict:
    return {key: value for key, value in section.items() if key not in {"span_blocks", "context_blocks", "link_only"}}


def is_content(section: dict) -> bool:
    """A section a curator could cite: it keeps text and is not a bare list of links."""
    return bool(section.get("kept")) and not section.get("link_only")


def normalise(text: str) -> str:
    """The text of a block as repetition sees it: one space between words, lower case."""
    return " ".join(text.split()).lower()


def section_text(section: dict) -> str:
    """The normalised text of a section's kept blocks; two sections repeat each other when this is equal."""
    return " ".join(normalise(block["text"]) for block in section["span_blocks"])


def host_of(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def repeated_sections(candidates: list[tuple[dict, dict]]) -> dict[str, list[dict]]:
    """Per candidate record, the content sections whose text also stands on another candidate page of the same host.

    `candidates` pairs an index entry with its record. The result maps `document_id` to a list of
    `{section_id, heading_path, first_block, last_block, pages}`, `pages` being the number of candidate pages of the
    host that carry the text, this one included; records without a repeated section are absent."""
    pages: dict[tuple[str, str], set[str]] = defaultdict(set)
    sections_of: dict[str, list[tuple[dict, str]]] = {}
    for entry, record in candidates:
        host = host_of(entry.get("source_url") or record.get("source_url"))
        kept = [(s, section_text(s)) for s in build_sections(record) if is_content(s)]
        sections_of[entry["document_id"]] = [(s, text) for s, text in kept if text]
        for _, text in sections_of[entry["document_id"]]:
            pages[(host, text)].add(entry["document_id"])
    result: dict[str, list[dict]] = {}
    for entry, record in candidates:
        host = host_of(entry.get("source_url") or record.get("source_url"))
        repeated = [dict(section_id=s["section_id"], heading_path=s["heading_path"], first_block=s["first_block"],
                         last_block=s["last_block"], pages=len(pages[(host, text)]))
                    for s, text in sections_of[entry["document_id"]] if len(pages[(host, text)]) > 1]
        if repeated:
            result[entry["document_id"]] = repeated
    return result


def content_sections(record: dict) -> list[dict]:
    """The sections of a record a curator is expected to read, with the block range of each."""
    return [dict(section_id=s["section_id"], heading_path=s["heading_path"], first_block=s["first_block"],
                 last_block=s["last_block"], characters=s["characters"])
            for s in build_sections(record) if is_content(s)]


def section_fields(record: dict) -> dict:
    """The section counts of a record for the index: how many heading runs, how many carry citable text."""
    sections = build_sections(record)
    content = [s for s in sections if is_content(s)]
    return dict(sections=len(sections), content_sections=len(content),
                content_characters=sum(s["characters"] for s in content))


def candidate_status(entry: dict) -> tuple[bool, str | None]:
    """Whether an index entry is a curation candidate, and if not, the first rule that excludes it.

    The entry needs the group fields (`preferred_representation`) and `superseded`, which the extractor sets after
    grouping; call this on the finished entries, not on a bare record.
    """
    if not entry.get("eligible_for_processing"):
        return False, "not_extractable"
    if entry.get("superseded"):
        return False, "superseded"
    if not entry.get("preferred_representation"):
        return False, "secondary_representation"
    kind = entry.get("attribution_kind") or (entry.get("attribution") or {}).get("kind")
    if kind == "language-variant":
        return False, "language_variant"
    if kind == "out-of-scope":
        return False, "out_of_scope_page"
    return True, None


def apply_candidates(entries: list[dict]) -> dict[str, int]:
    """Set `curation_candidate` and `candidate_exclusion` on every entry; return the counts for the summary."""
    exclusions: dict[str, int] = {}
    candidates = 0
    for entry in entries:
        candidate, exclusion = candidate_status(entry)
        entry["curation_candidate"] = candidate
        entry["candidate_exclusion"] = exclusion
        if candidate:
            candidates += 1
        else:
            exclusions[exclusion] = exclusions.get(exclusion, 0) + 1
    return dict(curation_candidates=candidates, candidate_exclusions=exclusions)
