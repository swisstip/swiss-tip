"""Select sections from immutable text-record blocks without changing offsets."""

import unicodedata


FURNITURE_HEADINGS = frozenset({
    "kontakt", "contact", "contacts", "kontaktinformationen", "contact details",
    "zuständigkeit", "zuständiges amt", "suche", "search", "navigation",
    "inhaltsverzeichnis", "table of contents", "seite teilen", "share", "feedback",
    "war diese seite hilfreich?", "cookie-einstellungen", "auf dieser seite",
    "bitte geben sie uns feedback", "kontaktformular migrationsamt",
    "das könnte sie auch interessieren", "für dieses thema zuständig:",
})
FURNITURE_LABELS = frozenset({
    "navigation", "banner", "footer", "breadcrumb", "skiplinks", "share",
    "cookie-notice", "language-menu", "related-links", "anchor-navigation",
    "scroll-to-top", "chat-link",
})
NEWS_HEADINGS = {"news", "aktuell", "aktuelle meldungen", "neuigkeiten", "nachrichten"}
LINK_HEADINGS = {"links", "weiterführende links", "weitere links", "related links", "useful links", "external links",
                 "liens", "liens utiles", "collegamenti", "link"}


def terminology_key(value: str) -> str:
    # V3 deliberately preserves the distinction between, for example, ss and ß.
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
        if any(part in NEWS_HEADINGS for part in keys[1:]):
            reason = "embedded_news"
        elif not contact_page and any(part in FURNITURE_HEADINGS for part in keys):
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
