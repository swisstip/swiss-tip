"""Synthetic run directories and documents for the extraction tests. No network."""

import hashlib
import io
import json
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

CATALOGUE_URL = "https://www.zh.example/permit/overview.html"
IN_SCOPE_URL = "https://www.zh.example/permit/deadline.html"
OUT_OF_SCOPE_URL = "https://www.other.example/leaflet.pdf"
FAILED_URL = "https://www.zh.example/permit/missing.html"
FEDLEX_URL = "https://www.fedlex.example/eli/cc/2007/758/de"
FEDLEX_HTML_URL = "https://data.fedlex.example/eli/cc/2007/758/20260101/de/html/act.html"
FEDLEX_PDF_URL = "https://data.fedlex.example/eli/cc/2007/758/20260101/de/pdf/act.pdf"
CATALOGUE_SHA256 = "c" * 64

OLD_PAGE = b"""<html lang="de"><head><title>Aufenthalt</title></head><body>
<header><nav class="mod-mainnavigation"><a href="/de">Home</a></nav></header>
<main><h1>Anmeldung</h1><p>Sie melden sich innert 14 Tagen an.</p>
<h2>Fristen</h2><p>Vor Arbeitsbeginn.</p></main>
<footer><p>Kanton Zuerich</p></footer></body></html>"""
NEW_PAGE = OLD_PAGE.replace(b"<h1>Anmeldung</h1>", b"<h1>Anmeldung</h1><p>Neu: Online-Schalter.</p>")
DEADLINE_PAGE = b"""<html><head><title>Frist</title></head><body><main><h1>Frist</h1>
<p>Innert 14 Tagen nach der Einreise.</p></main></body></html>"""
SHELL_PAGE = b"<html><head><title>Fedlex</title></head><body><app-root></app-root></body></html>"
ACT_PAGE = b"""<html lang="de"><head><title>input-de</title></head><body><main><h1>AIG</h1>
<article id="art_12"><h2>Art. 12</h2><p>Anmeldung vor Ablauf der Frist.</p></article></main></body></html>"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_pdf(pages: list[str | None]) -> bytes:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text is None:
            continue
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        content = b"BT /F1 12 Tf 10 250 Td " + b" ".join(
            b"(" + line.encode("latin-1") + b") Tj 0 -20 Td" for line in text.splitlines()) + b" ET"
        stream.set_data(content)
        page[NameObject("/Contents")] = writer._add_object(stream)
    raw = io.BytesIO()
    writer.write(raw)
    return raw.getvalue()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_page(pages: Path, url: str, attempts: list[tuple[bytes, str, str, list[str]]], **extra) -> dict:
    """Write attempt folders and latest.json for one URL; return the manifest."""
    folder = pages / sha256(url.encode())
    snapshots = []
    for number, (body, extension, content_type, flags) in enumerate(attempts, 1):
        attempt = folder / f"attempt-{number:03d}"
        attempt.mkdir(parents=True, exist_ok=True)
        (attempt / f"response.{extension}").write_bytes(body)
        snapshot = dict(relative_path=f"pages/{folder.name}/attempt-{number:03d}/response.{extension}",
                        requested_url=url, final_url=url, content_type=content_type, sha256=sha256(body),
                        bytes_downloaded=len(body), retrieved_at=f"2026-09-1{number}T06:00:00+00:00", review_flags=flags)
        snapshots.append(snapshot)
        write_json(attempt / "manifest.json", dict(url=url, url_id=folder.name, snapshots=[snapshot], status="saved",
                                                   http_status=200, references=[], registry_entries=[]))
    manifest = dict(url=url, url_id=folder.name, references=[], registry_entries=[],
                    snapshots=snapshots[-1:] if snapshots else [], status="saved" if snapshots else "failed",
                    http_status=200 if snapshots else None)
    manifest.update(extra)
    write_json(folder / "latest.json", manifest)
    return manifest


def registry(source_id: str, url: str, language: str = "de") -> dict:
    return dict(definition=dict(source_id=source_id, start_url=url, allowed_hosts=["www.zh.example"],
                                allowed_path_prefixes=["/permit"], canonical_authority="Canton", jurisdiction="CH-ZH",
                                language=language), title=source_id, authority_level="cantonal")


def make_run(root: Path, *, new_attempt: bool = True) -> Path:
    run = root / "run"
    pages = run / "pages"
    plan = dict(schema_version="swisstip.catalogue-download-plan/v1", catalogue_sha256=CATALOGUE_SHA256, targets=[
        dict(url=CATALOGUE_URL, url_id=sha256(CATALOGUE_URL.encode()),
             references=[dict(label="Permit overview", catalogue_line=1, catalogue="sources.json")],
             registry_entries=[registry("zh-permit", CATALOGUE_URL)]),
        dict(url=IN_SCOPE_URL, url_id=sha256(IN_SCOPE_URL.encode()), references=[], registry_entries=[],
             attribution=dict(kind="in-scope", source_ids=["zh-permit"], advertised_languages=["de"], reasons=["topic-link"])),
        dict(url=OUT_OF_SCOPE_URL, url_id=sha256(OUT_OF_SCOPE_URL.encode()), references=[], registry_entries=[],
             attribution=dict(kind="out-of-scope", source_ids=[], advertised_languages=[], reasons=["topic-link"])),
        dict(url=FAILED_URL, url_id=sha256(FAILED_URL.encode()), references=[], registry_entries=[registry("zh-missing", FAILED_URL)]),
        dict(url=FEDLEX_URL, url_id=sha256(FEDLEX_URL.encode()),
             references=[dict(label="AIG", catalogue_line=2, catalogue="sources.json")],
             registry_entries=[registry("ch-fedlex-aig", FEDLEX_URL)]),
    ])
    write_json(run / "plan.json", plan)
    attempts = [(OLD_PAGE, "html", "text/html;charset=utf-8", [])]
    if new_attempt:
        attempts.append((NEW_PAGE, "html", "text/html;charset=utf-8", []))
    save_page(pages, CATALOGUE_URL, attempts, references=plan["targets"][0]["references"],
              registry_entries=plan["targets"][0]["registry_entries"])
    save_page(pages, IN_SCOPE_URL, [(DEADLINE_PAGE, "html", "text/html", [])], language="de",
              discoveries=[dict(label="Frist", discovered_on=CATALOGUE_URL, reason="topic-link")])
    save_page(pages, OUT_OF_SCOPE_URL, [(make_pdf(["Leaflet text", None]), "pdf", "application/pdf", [])])
    save_page(pages, FAILED_URL, [], error="HTTP Error 404: Not Found")
    save_page(pages, FEDLEX_URL, [(SHELL_PAGE, "html", "text/html", ["javascript_application_shell"])],
              registry_entries=plan["targets"][4]["registry_entries"])
    documents = run / "fedlex-documents" / "pages"
    version = "https://data.fedlex.example/eli/cc/2007/758/20260101"
    save_page(documents, FEDLEX_HTML_URL, [(ACT_PAGE, "html", "text/html", [])], source_page_url=FEDLEX_URL,
              version_uri=version, references=plan["targets"][4]["references"])
    save_page(documents, FEDLEX_PDF_URL, [(make_pdf(["AIG Art. 12 Anmeldung vor Ablauf der Frist."]), "pdf", "application/pdf", [])],
              source_page_url=FEDLEX_URL, version_uri=version)
    write_json(run / "fedlex-documents" / "summary.json", dict(schema_version="swisstip.catalogue-download/v1", results=[]))
    return run
