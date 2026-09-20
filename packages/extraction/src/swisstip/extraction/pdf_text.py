"""PDF responses to text blocks: native embedded text per page, split into paragraphs.

PDFium supplies the page text; pypdf reads AcroForm fields. Pages without
text are reported, never recognised: there is no OCR here. Lines that repeat
at the top or bottom of most pages are labelled as page furniture and kept.
"""

import io
import math
import re
from collections import Counter

import pypdfium2 as pdfium
from pypdf import PdfReader

PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def page_texts(raw: bytes) -> list[str]:
    texts = []
    with pdfium.PdfDocument(raw) as pdf:
        for number in range(len(pdf)):
            page = pdf[number]
            try:
                textpage = page.get_textpage()
                try:
                    texts.append(textpage.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n").strip())
                finally:
                    textpage.close()
            finally:
                page.close()
    return texts


def paragraphs(text: str) -> list[str]:
    """Split page text at blank lines. A page without blank lines is one paragraph."""
    return [part.strip() for part in PARAGRAPH_BREAK.split(text) if part.strip()]


def normalize_line(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split())).casefold()


def repeated_lines(texts: list[str]) -> tuple[set[str], set[str]]:
    """First and last lines that recur on at least half of the pages (three pages minimum)."""
    pages = [t for t in texts if t]
    if len(pages) < 3:
        return set(), set()
    threshold = max(3, math.ceil(len(pages) / 2))
    firsts = Counter(normalize_line(t.splitlines()[0]) for t in pages)
    lasts = Counter(normalize_line(t.splitlines()[-1]) for t in pages)
    return ({line for line, count in firsts.items() if count >= threshold and line},
            {line for line, count in lasts.items() if count >= threshold and line})


def form_fields(raw: bytes) -> tuple[list[dict], list[str]]:
    try:
        reader = PdfReader(io.BytesIO(raw))
        fields = []
        for name, field in (reader.get_fields() or {}).items():
            fields.append(dict(name=name, field_type=str(field.get("/FT", "")), label=str(field.get("/TU", "")),
                               value=str(field.get("/V", "")), options=[str(option) for option in field.get("/Opt", [])]))
        return fields, []
    except Exception as exc:  # pypdf raises many exception types on damaged files
        return [], [f"pdf_form_fields_unreadable: {type(exc).__name__}"]


def extract_pdf(raw: bytes) -> dict:
    texts = page_texts(raw)
    headers, footers = repeated_lines(texts)
    blocks, blank = [], []
    for number, text in enumerate(texts, 1):
        if not text:
            blank.append(number)
            continue
        parts = paragraphs(text)
        kind = "pdf_paragraph" if len(parts) > 1 else "pdf_page"
        for position, part in enumerate(parts, 1):
            lines = part.splitlines()
            furniture = []
            if position == 1 and normalize_line(lines[0]) in headers:
                furniture.append("page-header")
            if position == len(parts) and normalize_line(lines[-1]) in footers:
                furniture.append("page-footer")
            blocks.append(dict(kind=kind, text=part, heading_path=[],
                               source_locator=dict(page=number, paragraph=position, furniture=furniture), links=[]))
    fields, warnings = form_fields(raw)
    warnings = ["pdf_layout_and_reading_order_unverified", *warnings]
    if blank:
        warnings.append("some_pdf_pages_have_no_text_no_ocr_performed")
    if headers or footers:
        warnings.append("pdf_repeated_page_lines_labelled")
    return dict(blocks=blocks, pdf_page_count=len(texts), pages_without_text=blank, form_fields=fields,
                html_title=None, main_headings=[], language_declared=None, page_kind="pdf_document",
                decoding="PDFium native embedded text", warnings=warnings)
