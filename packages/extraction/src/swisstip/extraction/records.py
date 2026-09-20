"""Assemble a text record from a verified snapshot: dispatch, identity, offsets, hashes."""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from . import EXTRACTOR_VERSION, SCHEMA_VERSION
from .html_blocks import decode, extract_html
from .office_text import OfficeDependencyMissing, extract_ole_word, extract_openxml, extract_rtf
from .pdf_text import extract_pdf
from .run_reader import document_id

LANGUAGES = ("de", "fr", "it", "en", "rm")
EXCLUDING_REVIEW_FLAGS = {"javascript_application_shell", "possible_access_challenge"}
EXCLUDED_PAGE_KINDS = {"application_shell", "maintenance_page", "error_page"}
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HTML_SIGNATURE = re.compile(rb"<(?:!doctype\s+html|html|head|body)\b", re.I)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def representation_group(source_url: str) -> str:
    return "source-" + sha256_text(source_url)[:20]


def url_language(url: str) -> str | None:
    """A language code that is a whole path segment, or a `_de` / `-fr` file-name suffix."""
    path = urlsplit(url).path
    segments = [segment for segment in path.split("/") if segment]
    for segment in segments:
        if segment.lower() in LANGUAGES:
            return segment.lower()
    if segments:
        stem = PurePosixPath(segments[-1]).stem
        match = re.search(r"[-_](de|fr|it|en|rm)$", stem, re.I)
        if match:
            return match[1].lower()
    return None


def language_hint(item: dict) -> str | None:
    if item.get("language"):
        return item["language"]
    for entry in item.get("registry_entries", []):
        language = entry.get("definition", {}).get("language")
        if language:
            return language
    return url_language(item.get("document_url") or item["source_url"])


def title_for(html_title: str | None, references: list[dict], main_headings: list[str], source_url: str) -> str:
    if html_title and not html_title.startswith("input-"):
        return html_title
    if references and references[0].get("label"):
        return references[0]["label"]
    if main_headings:
        return main_headings[0]
    return source_url


def attach_offsets(document: dict) -> None:
    position, texts = 0, []
    for index, block in enumerate(document["blocks"], 1):
        block["block_id"] = f"{document['document_id']}:b{index:05d}"
        block["start"] = position
        block["end"] = position + len(block["text"])
        block["text_sha256"] = sha256_text(block["text"])
        texts.append(block["text"])
        position = block["end"] + 2
    document["content_text"] = "\n\n".join(texts)
    document["content_sha256"] = sha256_text(document["content_text"])
    for block in document["blocks"]:
        if document["content_text"][block["start"]:block["end"]] != block["text"]:
            raise AssertionError(f"Block offsets do not round-trip: {block['block_id']}")


def extract_bytes(raw: bytes, url: str, content_type: str, relative_path: str) -> tuple[dict, str]:
    """Pick the extractor from the bytes; return its result and the representation name."""
    head = raw[:1024].lstrip()
    if head.startswith(b"%PDF-"):
        return extract_pdf(raw), "pdf"
    if raw.startswith(b"PK\x03\x04"):
        value = extract_openxml(raw)
        return value, value["representation"]
    if head.startswith(b"{\\rtf"):
        return extract_rtf(raw), "rtf"
    if raw.startswith(OLE_SIGNATURE):
        return extract_ole_word(raw), "doc"
    if raw.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        return dict(blocks=[], html_title=None, main_headings=[], language_declared=None, page_kind="image_document",
                    decoding="Image retained as raw bytes; no pixels read", image_format="jpeg" if raw[0] == 0xFF else "png",
                    warnings=["image_requires_ocr"]), "image"
    suffix = PurePosixPath(relative_path).suffix.lower()
    # Without markup, a NUL byte outside UTF-16 means binary: an .html name alone does not make it a page. lxml
    # would decide otherwise per platform (libxml2 2.11 rejects such bytes, 2.14 parses them into a text block).
    binary = b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    if HTML_SIGNATURE.search(raw[:8192]) or (suffix in {".html", ".htm"} and not binary):
        return extract_html(raw, url, content_type), "html"
    declared = (content_type or "").lower()
    if declared.startswith("text/") or "json" in declared or "xml" in declared or suffix in {".txt", ".csv", ".json", ".xml"}:
        text, encoding, _, warnings = decode(raw, content_type)
        return dict(blocks=[dict(kind="plain_text", text=text, heading_path=[], source_locator={"file": relative_path}, links=[])]
                    if text.strip() else [],
                    html_title=None, main_headings=[], language_declared=None, page_kind="text_document",
                    decoding=encoding, warnings=warnings), "text"
    raise ValueError(f"Unsupported binary format ({content_type or 'no content type'}); raw bytes retained in the run")


def assemble(item: dict, raw: bytes, manifest_digest: str, run_name: str, catalogue_sha256: str | None,
             extractor_version: str = EXTRACTOR_VERSION) -> dict:
    snapshot = item["snapshot"]
    record = dict(
        schema_version=SCHEMA_VERSION, document_id=document_id(item["source_url"], snapshot["sha256"]),
        corpus_id=run_name, catalogue_sha256=catalogue_sha256,
        source_url=item["source_url"], document_url=item["document_url"], requested_url=item["requested_url"],
        version_uri=item.get("version_uri"), attribution=item["attribution"],
        source_registry=item.get("registry_entries", []), catalogue_references=item.get("references", []),
        discovery_provenance=item.get("discoveries", []),
        acquisition=dict(path=item["relative_path"], attempt=item.get("attempt"), manifest_path=item["pointer"],
                         manifest_sha256=manifest_digest, raw_sha256=snapshot["sha256"], bytes=len(raw),
                         retrieved_at=snapshot.get("retrieved_at"), requested_url=item["requested_url"],
                         declared_content_type=snapshot.get("content_type"), review_flags=snapshot.get("review_flags", []),
                         http_status=snapshot.get("status") or item.get("http_status"), imported_from=item.get("imported_from")),
        extractor_version=extractor_version, extracted_at=datetime.now(UTC).isoformat(),
        representation_group=representation_group(item["source_url"]),
    )
    flags = list(snapshot.get("review_flags", []))
    try:
        extracted, representation = extract_bytes(raw, item["document_url"], snapshot.get("content_type") or "",
                                                  item["relative_path"])
        extracted.pop("representation", None)
        record.update(extracted, representation=representation)
        excluded_flags = sorted(set(flags) & EXCLUDING_REVIEW_FLAGS)
        excluded = record["page_kind"] in EXCLUDED_PAGE_KINDS or bool(excluded_flags)
        record["eligible_for_processing"] = bool(record["blocks"]) and not excluded
        record["status"] = ("excluded_source_response" if excluded else
                            "extracted" if record["blocks"] else "no_extractable_text")
        record["exclusion_reasons"] = ([record["page_kind"]] if record["page_kind"] in EXCLUDED_PAGE_KINDS else []) + excluded_flags
        record.setdefault("warnings", [])
        record["warnings"].extend(flag for flag in flags if flag not in EXCLUDING_REVIEW_FLAGS)
    except OfficeDependencyMissing as exc:
        record.update(blocks=[], representation="unknown", page_kind="office_document", warnings=[str(exc)],
                      status="extraction_failed", eligible_for_processing=False, exclusion_reasons=[],
                      html_title=None, main_headings=[], language_declared=None)
    except Exception as exc:  # a damaged file must become a recorded failure, not a crash of the run
        record.update(blocks=[], representation="unknown", page_kind="unknown", warnings=[f"{type(exc).__name__}: {exc}"],
                      status="extraction_failed", eligible_for_processing=False, exclusion_reasons=[],
                      html_title=None, main_headings=[], language_declared=None)
    record["language_hint"] = language_hint(item)
    record["title"] = title_for(record.get("html_title"), record["catalogue_references"], record["main_headings"],
                                item["source_url"])
    attach_offsets(record)
    record.update(html_counterparts=[], preferred_representation=record["eligible_for_processing"],
                  identical_raw_snapshots=[], identical_text_records=[])
    return record
