"""HTML responses to labelled text blocks with DOM locators.

A port of the SwissTIP intermediate extractor's block walker. Text is kept in
document order; navigation, banners, footers and hidden text stay in the
result with region, visibility and furniture labels. Only non-text elements
(scripts, styles, SVG, canvas, templates, the head) are left out.
"""

import codecs
import hashlib
import json
import re
from collections import Counter
from urllib.parse import urljoin

from lxml import html

IGNORED = {"script", "style", "noscript", "svg", "canvas", "template", "head"}
STRUCTURAL = {"address", "article", "aside", "blockquote", "button", "caption", "dd", "details",
              "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "header",
              "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "nav", "ol", "p", "pre",
              "section", "summary", "table", "ul"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
REGION_TAGS = {"nav", "header", "footer", "aside", "form", "main"}
BLOCK_KINDS = {"p": "paragraph", "li": "list_item", "dt": "definition_term", "dd": "definition",
               "pre": "preformatted", "blockquote": "quotation", "summary": "disclosure_title",
               "button": "control", "figcaption": "caption", "address": "address"}

# Exact class tokens of page furniture seen on SEM (admin.ch) and Canton of
# Zurich pages. Labels are added to blocks; no text is removed because of them.
FURNITURE_CLASSES = {
    "mod-mainnavigation": "navigation", "nav-main": "navigation", "navbar-nav": "navigation",
    "nav-tabs": "navigation", "mod-leftnavigation": "navigation", "site-map": "navigation",
    "mod-breadcrumb": "breadcrumb", "breadcrumb": "breadcrumb", "mdl-page-header__breadcrumb": "breadcrumb",
    "mdl-skiplinks": "skiplinks", "mdl-anchornav": "anchor-navigation",
    "mdl-page-header__logo-container": "banner",
    "mdl-footer__menu": "footer", "mdl-footer__submenu": "footer", "mdl-footer__social-media": "share",
    "mod-socialshare": "share", "mdl-scroll2top": "scroll-to-top", "mdl-backtochat": "chat-link",
    "mdl-related-content": "related-links", "mdl-content_nav": "related-links",
    "mdl-content_nav__list": "related-links",
}
FURNITURE_PATTERNS = (
    (re.compile(r"breadcrumb"), "breadcrumb"),
    (re.compile(r"cookie|consent"), "cookie-notice"),
    (re.compile(r"skip-?links?"), "skiplinks"),
    (re.compile(r"social-?share|socialshare|sharing"), "share"),
    (re.compile(r"lang(?:uage)?-?(?:switch|menu|nav|select)"), "language-menu"),
)
ROLE_LABELS = {"navigation": "navigation", "banner": "banner", "contentinfo": "footer",
               "search": "search", "dialog": "dialog"}
# Web components of the City of Zurich design system (stadt-zuerich.ch) that a
# browser renders from attributes: a contact card's name, address and phone
# numbers, the headings of its accordion ("Öffnungszeiten") and its link list
# ("Fahrplan nach Stadthausquai 17"). Their text is not DOM text; it becomes
# blocks marked inline_data, which stay out of the sequence comparison.
CONTACT = "stzh-contact"
DATATABLE = "stzh-datatable"
COMPONENTS = {CONTACT, DATATABLE, "stzh-accordion-item", "stzh-datalist-item"}
MAINTENANCE = re.compile(r"wartungsarbeiten|maintenance|lavurs da mantegniment|lavori di manutenzione", re.I)
ERROR_TITLE = re.compile(r"\s*(?:Error Page\s*\(404\)|404(?:\s*[-:]?\s*(?:Not Found|Page not found))?|"
                         r"Page not found|Not Found|Seite nicht gefunden|Page introuvable|Pagina non trovata|"
                         r"Access Denied|Forbidden)\s*", re.I)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fold(text: str) -> str:
    return clean(text).casefold()


def tag(node) -> str:
    return node.tag.lower() if isinstance(node.tag, str) else ""


def inline_text(node) -> str:
    if not tag(node) or tag(node) in IGNORED:
        return ""
    if tag(node) == "br":
        return "\n"
    return (node.text or "") + "".join(("\n" if tag(child) in STRUCTURAL else "") +
                                      inline_text(child) + ("\n" if tag(child) in STRUCTURAL else "") +
                                      (child.tail or "") for child in node)


def inline_data_rows(table, dom_path: str) -> list[list[dict]]:
    """Rows a client-side data table carries as JSON instead of in its body.

    Some CMS tables (seen on Stadt Wallisellen pages) ship an empty <tbody> and
    the rows in a data-entities attribute, {"data": [{column key: HTML}]}, with
    each header cell naming its key in data-data. A browser renders the rows;
    without this the saved page's emergency numbers or collection dates would be
    lost. Only the columns named by header cells are read, in header order.
    """
    raw = table.get("data-entities")
    keys = [cell.get("data-data") for cell in table.xpath(".//thead//th")]
    if not raw or not keys or not all(keys):
        return []
    try:
        entities = json.loads(raw)
    except ValueError:
        return []
    data = entities.get("data") if isinstance(entities, dict) else None
    if not isinstance(data, list):
        return []
    rows = []
    for entity in data:
        if not isinstance(entity, dict):
            continue
        cells = []
        for key in keys:
            value = entity.get(key)
            if value is None:
                value = ""
            fragment = str(value)
            if "<" in fragment:
                fragment = html.fragment_fromstring(fragment, create_parent="div").text_content()
            cells.append(dict(text=clean(fragment), header=False, rowspan="1", colspan="1",
                              dom_path=f"{dom_path}/@data-entities", source="data-entities"))
        if any(cell["text"] for cell in cells):
            rows.append(cells)
    return rows


def json_attribute(node, name: str) -> list:
    try:
        value = json.loads(node.get(name) or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def contact_lines(node) -> list[str]:
    """The address and numbers a stzh-contact card renders, one line each, in the card's order."""
    lines = [clean(str(v)) for v in json_attribute(node, "street-info") + json_attribute(node, "street")]
    place = clean(f"{node.get('postal-code') or ''} {node.get('location') or ''}")
    lines.append(place)
    for key in ("numbers", "emails", "websites"):
        for entry in json_attribute(node, key):
            if isinstance(entry, dict):
                value = entry.get("number") or entry.get("email") or entry.get("href") or entry.get("url") or ""
                label = entry.get("label") or ""
                lines.append(clean(f"{label} {value}" if label and label != value else str(value)))
            else:
                lines.append(clean(str(entry)))
    return [line for line in lines if line]


def cell_text(value) -> str:
    fragment = str(value if value is not None else "")
    if "<" in fragment:
        fragment = html.fragment_fromstring(fragment, create_parent="div").text_content()
    return clean(fragment)


def datatable_rows(node, dom_path: str) -> list[list[dict]]:
    """The header and body rows a stzh-datatable renders from its columns and rows attributes (JSON)."""
    columns, rows = json_attribute(node, "columns"), json_attribute(node, "rows")
    result = []
    header = [cell_text(c.get("text")) if isinstance(c, dict) else cell_text(c) for c in columns]
    if any(header):
        result.append([dict(text=text, header=True, rowspan="1", colspan="1", dom_path=f"{dom_path}/@columns",
                            source="stzh-datatable") for text in header])
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [dict(text=cell_text(c.get("value") if isinstance(c, dict) else c), header=False, rowspan="1",
                      colspan="1", dom_path=f"{dom_path}/@rows", source="stzh-datatable") for c in row]
        if any(cell["text"] for cell in cells):
            result.append(cells)
    return result


def declared_charset(header: str | None) -> str | None:
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", header or "", re.I)
    return match[1] if match else None


def meta_charset(raw: bytes) -> str | None:
    match = re.search(rb"charset\s*=\s*[\"\x27]?([a-zA-Z0-9_.:-]+)", raw[:8192])
    return match[1].decode("ascii") if match else None


def decode(raw: bytes, content_type: str | None = None) -> tuple[str, str, dict, list[str]]:
    """Decode HTML bytes: BOM, then strict UTF-8, then the HTTP charset, then the meta charset.

    Returns the text, the encoding used, the declared charsets and warnings. A
    byte sequence that is valid UTF-8 is taken as UTF-8 whatever the headers
    say; invalid bytes are never replaced silently.
    """
    declared = {"http": declared_charset(content_type), "meta": meta_charset(raw)}
    warnings: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig", declared, warnings
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16", declared, warnings
    try:
        return raw.decode("utf-8"), "utf-8", declared, warnings
    except UnicodeDecodeError:
        pass
    for source in ("http", "meta"):
        name = declared[source]
        if not name:
            continue
        try:
            codecs.lookup(name)
            return raw.decode(name), codecs.lookup(name).name, declared, warnings
        except (LookupError, UnicodeDecodeError):
            warnings.append(f"declared_charset_failed:{source}:{name}")
    warnings.append("character_encoding_fallback")
    try:
        return raw.decode("windows-1252"), "windows-1252", declared, warnings
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1", declared, warnings


def furniture_labels(ancestors) -> list[str]:
    labels: set[str] = set()
    in_content = any(tag(n) in {"main", "article"} or n.get("role") == "main" for n in ancestors)
    for node in ancestors:
        name, role = tag(node), (node.get("role") or "").lower()
        if name == "nav":
            labels.add("navigation")
        if role in ROLE_LABELS:
            labels.add(ROLE_LABELS[role])
        if name == "footer" and not in_content:
            labels.add("footer")
        if name == "header" and not in_content:
            labels.add("banner")
        classes = (node.get("class") or "").lower()
        identifier = (node.get("id") or "").lower()
        for token in classes.split():
            if token in FURNITURE_CLASSES:
                labels.add(FURNITURE_CLASSES[token])
        for pattern, label in FURNITURE_PATTERNS:
            if pattern.search(classes) or pattern.search(identifier):
                labels.add(label)
    return sorted(labels)


class HtmlBlocks:
    def __init__(self, raw: bytes, url: str, content_type: str | None = None):
        self.text, self.encoding, self.declared_charsets, self.warnings = decode(raw, content_type)
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", self.text, count=1)
        self.root = html.fromstring(text, parser=html.HTMLParser(no_network=True))
        self.tree = self.root.getroottree()
        base = self.root.xpath("//base/@href")
        self.url = urljoin(url, base[0]) if base else url
        self.blocks: list[dict] = []
        self.headings: list[tuple[int, str]] = []
        self.contact_levels: dict[str, int] = {}
        self.ignored = Counter(tag(n) for n in self.root.iter() if tag(n) in IGNORED)

    def links(self, node) -> list[dict]:
        links = []
        for anchor in node.iter():
            if tag(anchor) == "a" and anchor.get("href"):
                links.append(dict(text=clean(inline_text(anchor)), href=anchor.get("href"),
                                  resolved_url=urljoin(self.url, anchor.get("href"))))
        return links

    def context(self, node) -> dict:
        ancestors = [node, *node.iterancestors()]
        region = next((tag(n) for n in ancestors if tag(n) in REGION_TAGS), "body")
        roles = [n.get("role") for n in ancestors if n.get("role")]
        anchor = next((n.get("id") for n in ancestors if n.get("id")), None)
        article_id = next((n.get("id") for n in ancestors if tag(n) == "article" and n.get("id")), None)
        footnote = any("footnote" in n.get("class", "").lower() or n.get("id", "").startswith("fn-") for n in ancestors)
        lists = []
        for n in reversed(ancestors):
            if tag(n) == "li":
                parent = n.getparent()
                lists.append(dict(ordered=tag(parent) == "ol", item_position=len(n.xpath("preceding-sibling::li")) + 1,
                                  start=parent.get("start"), value=n.get("value")))
        return dict(dom_path=self.tree.getpath(node), anchor=anchor, article_id=article_id, region=region,
                    aria_roles=roles,
                    in_main=any(tag(n) == "main" or n.get("role") == "main" for n in ancestors),
                    explicit_hidden=any("hidden" in n.attrib or n.get("aria-hidden") == "true" or
                                        re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", n.get("style", ""))
                                        for n in ancestors),
                    is_footnote=footnote, list_context=lists, furniture=furniture_labels(ancestors))

    def emit(self, node, text: str, kind: str | None = None, links=None, **extra) -> None:
        if not text.strip():
            return
        kind = kind or BLOCK_KINDS.get(tag(node), "text")
        links = list(links or [])
        for ancestor in node.iterancestors():
            if tag(ancestor) == "a" and ancestor.get("href"):
                links.append(dict(text=clean(inline_text(ancestor)), href=ancestor.get("href"),
                                  resolved_url=urljoin(self.url, ancestor.get("href"))))
        locator = self.context(node)
        if kind == "list_item" and links and fold(" ".join(link["text"] for link in links)) == fold(text):
            locator["furniture"] = sorted(set(locator["furniture"]) | {"link-only"})
        self.blocks.append(dict(kind=kind, text=text, heading_path=[h[1] for h in self.headings],
                                source_locator=locator, links=links, **extra))

    def attribute_heading(self, node, text: str, level: int, source: str) -> None:
        text = clean(text)
        if not text:
            return
        level = min(max(level, 1), 6)
        self.headings = [h for h in self.headings if h[0] < level]
        self.headings.append((level, text))
        self.emit(node, text, "heading", level=level, inline_data=dict(source=source, text=text))

    def component(self, node, name: str) -> bool:
        """Emit the attribute text of a design-system component; True when its children need no walk."""
        if name == DATATABLE:
            # The table's own heading is a slotted child with a level; it precedes the rows it names.
            for child in node:
                level = child.get("level") or ""
                if tag(child) == "stzh-heading" and level.isdigit():
                    text = clean(inline_text(child))
                    if text:
                        self.headings = [h for h in self.headings if h[0] < int(level)]
                        self.headings.append((int(level), text))
                        self.emit(child, text, "heading", level=int(level))
                else:
                    self.walk(child)
            rows = datatable_rows(node, self.tree.getpath(node))
            if rows:
                text = "\n".join("\t".join(c["text"] for c in row) for row in rows)
                self.emit(node, text, "table", rows=rows, caption="", nested_table_count=0,
                          inline_data=dict(source="stzh-datatable", rows=len(rows), text=text))
            return True
        contact = next((a for a in node.iterancestors() if tag(a) == CONTACT), None)
        if name == CONTACT:
            level = (self.headings[-1][0] + 1) if self.headings else 2
            if node.get("main-heading"):
                raw_level = node.get("main-heading-level") or ""
                main_level = int(raw_level) if raw_level.isdigit() else 2
                self.attribute_heading(node, node.get("main-heading"), main_level, "stzh-contact@main-heading")
                level = main_level + 1
            self.attribute_heading(node, node.get("heading") or "", level, "stzh-contact@heading")
            self.contact_levels[self.tree.getpath(node)] = min(level + 1, 6)
            text = "\n".join(contact_lines(node))
            if text:
                self.emit(node, text, "address", inline_data=dict(source="stzh-contact", text=text))
            return False
        if contact is None:
            return False
        if name == "stzh-accordion-item":
            level = self.contact_levels.get(self.tree.getpath(contact), 4)
            self.attribute_heading(node, node.get("heading") or "", level, "stzh-accordion-item@heading")
            return False
        text = clean(node.get("value") or "")
        if text:
            links = [dict(text=text, href=node.get("href"), resolved_url=urljoin(self.url, node.get("href")))] \
                if node.get("href") else []
            self.emit(node, text, "list_item", links, inline_data=dict(source="stzh-datalist-item", text=text))
        return True

    def walk(self, node) -> None:
        name = tag(node)
        if not name or name in IGNORED:
            return
        if name in COMPONENTS and self.component(node, name):
            return
        if name in HEADING_TAGS:
            text = clean(inline_text(node))
            level = int(name[1])
            self.headings = [h for h in self.headings if h[0] < level]
            if text:
                self.headings.append((level, text))
                self.emit(node, text, "heading", self.links(node), level=level)
            return
        if name == "table":
            rows = []
            for row in node.xpath(".//tr"):
                if row.xpath("ancestor::table[1]")[0] is not node:
                    continue
                cells = [dict(text=clean(inline_text(cell)), header=tag(cell) == "th",
                              rowspan=cell.get("rowspan", "1"), colspan=cell.get("colspan", "1"),
                              dom_path=self.tree.getpath(cell)) for cell in row if tag(cell) in {"td", "th"}]
                if cells:
                    rows.append(cells)
            caption = clean(" ".join(inline_text(c) for c in node.xpath("./caption")))
            text = "\n".join(([caption] if caption else []) + ["\t".join(c["text"] for c in row) for row in rows])
            extra = {}
            data_rows = inline_data_rows(node, self.tree.getpath(node))
            if data_rows:
                data_text = "\n".join("\t".join(c["text"] for c in row) for row in data_rows)
                rows.extend(data_rows)
                text = f"{text}\n{data_text}" if text else data_text
                extra["inline_data"] = dict(source="data-entities", rows=len(data_rows), text=data_text)
            self.emit(node, text, "table", self.links(node), rows=rows, caption=caption,
                      nested_table_count=len(node.xpath(".//table")), **extra)
            return
        pieces, links = [node.text or ""], []

        def flush():
            value = "".join(pieces)
            self.emit(node, value.strip() if name == "pre" else clean(value), links=list(links))
            pieces.clear()
            links.clear()

        for child in node:
            child_tag = tag(child)
            if child_tag in IGNORED or not child_tag:
                pass
            elif (child_tag in STRUCTURAL or child_tag in COMPONENTS or
                  any(tag(n) in STRUCTURAL or tag(n) in COMPONENTS for n in child.iterdescendants())):
                flush()
                self.walk(child)
            else:
                pieces.append(inline_text(child))
                links.extend(self.links(child))
            pieces.append(child.tail or "")
        flush()

    def extract(self) -> list[dict]:
        bodies = self.root.xpath("//body")
        self.walk(bodies[0] if bodies else self.root)
        return self.blocks


def page_kind(raw: bytes, root, title: str, headings: list[str], text_length: int) -> str:
    kind = "ordinary_page"
    if title.lower() == "fedlex" or root.xpath('//*[local-name()="fedlex-app" or local-name()="app-root"]'):
        kind = "application_shell"
    if b"window.CONTENT_ID" in raw and b"window.IS_FRONTEND" in raw and text_length < 300:
        kind = "application_shell"
    if MAINTENANCE.search(title):
        kind = "maintenance_page"
    if any(ERROR_TITLE.fullmatch(candidate) for candidate in [title, *headings]):
        kind = "error_page"
    return kind


def main_headings(blocks: list[dict]) -> list[str]:
    """Level-1 headings of the content, not the ones a site puts in its banner or footer."""
    h1 = [b for b in blocks if b["kind"] == "heading" and b.get("level") == 1]
    content = [b for b in h1 if b["source_locator"].get("in_main") and not b["source_locator"].get("furniture")]
    if not content:
        content = [b for b in h1 if b["source_locator"].get("region") not in {"header", "footer", "nav"}
                   and not b["source_locator"].get("furniture")]
    return [b["text"] for b in (content or h1)]


def empty_result(decoding: str, warnings: list[str], declared: dict) -> dict:
    return dict(blocks=[], html_title="", main_headings=[], language_declared=None, decoding=decoding,
                charset_declared=declared, page_kind="ordinary_page", ignored_nontext_elements={},
                warnings=warnings,
                text_integrity=dict(method="DOM text sequence excluding nontext elements and whitespace",
                                    sequence_preserved=True, source_sequence_sha256=digest(b""),
                                    extracted_sequence_sha256=digest(b"")))


def extract_html(raw: bytes, url: str, content_type: str | None = None) -> dict:
    text, encoding, declared, warnings = decode(raw, content_type)
    if not text.strip():
        return empty_result(encoding, warnings, declared)
    parser = HtmlBlocks(raw, url, content_type)
    blocks = parser.extract()
    title = clean(parser.root.xpath("string(//title)"))
    headings = main_headings(blocks)
    all_h1 = [b["text"] for b in blocks if b["kind"] == "heading" and b.get("level") == 1]
    kind = page_kind(raw, parser.root, title, all_h1, sum(len(b["text"]) for b in blocks))
    bodies = parser.root.xpath("//body")
    source_sequence = re.sub(r"\s+", "", inline_text(bodies[0] if bodies else parser.root))
    # Rows recovered from a table's data-entities attribute and the text of design-system components are
    # not DOM text; they stay out of the comparison.
    extracted_sequence = re.sub(r"\s+", "", "".join(
        b["text"].removesuffix(b["inline_data"]["text"]) if b.get("inline_data") else b["text"] for b in blocks))
    warnings = list(parser.warnings)
    if source_sequence != extracted_sequence:
        warnings.append("html_text_sequence_differs_review_required")
    return dict(blocks=blocks, html_title=title, main_headings=headings,
                language_declared=parser.root.get("lang") or parser.root.get("{http://www.w3.org/XML/1998/namespace}lang"),
                decoding=parser.encoding, charset_declared=parser.declared_charsets, page_kind=kind,
                ignored_nontext_elements=dict(parser.ignored), warnings=warnings,
                text_integrity=dict(method="DOM text sequence excluding nontext elements and whitespace",
                                    sequence_preserved=source_sequence == extracted_sequence,
                                    source_sequence_sha256=digest(source_sequence.encode("utf-8")),
                                    extracted_sequence_sha256=digest(extracted_sequence.encode("utf-8"))))
