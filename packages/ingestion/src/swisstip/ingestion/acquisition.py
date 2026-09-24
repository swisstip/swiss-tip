"""Snapshot the exact URLs of a source catalogue, without link traversal.

Layout of a run directory (unchanged from the original SwissTIP downloader):

    plan.json                      target list, catalogue hash, redirect allowlist
    catalogue.json / catalogue.md  the input files as they were read
    pages/<url-sha256>/attempt-NNN/response.<ext>   raw response bytes
    pages/<url-sha256>/attempt-NNN/manifest.json    outcome of that attempt
    pages/<url-sha256>/latest.json                  outcome of the last attempt
    summary.json, README.md        current outcome per target
"""

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from email.parser import BytesParser
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.parse import urldefrag, urlsplit

from .crawler import CrawlLimits, SafeCrawler, SourceDefinition


DOCUMENT_TYPES = ("application/pdf", "application/octet-stream", "text/plain", "application/xml", "text/xml")
PRINT_LOCK = threading.Lock()
SUMMARY_SCHEMA = "swisstip.catalogue-download/v1"
PLAN_SCHEMA = "swisstip.catalogue-download-plan/v1"
# Titles of error pages served with HTTP 200; the same pattern as the extraction's
# ERROR_TITLE, which this standard-library package cannot import.
ERROR_TITLE = re.compile(r"\s*(?:Error Page\s*\(404\)|404(?:\s*[-:]?\s*(?:Not Found|Page not found))?|"
                         r"Page not found|Not Found|Seite nicht gefunden|Page introuvable|Pagina non trovata|"
                         r"Access Denied|Forbidden)\s*", re.I)


class CurlResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: bytes) -> None:
        super().__init__(body)
        # Native curl may expose an initial proxy CONNECT header block.
        block = headers.strip().split(b"\r\n\r\n")[-1]
        status_line, _, fields = block.partition(b"\r\n")
        self.status = int(status_line.split()[1])
        self.headers = BytesParser().parsebytes(fields)

    def getcode(self) -> int:
        return self.status


class CurlOpener:
    """Use native certificate validation; SafeCrawler still controls every redirect."""

    def open(self, request, timeout=None):
        with tempfile.TemporaryDirectory() as directory:
            headers = Path(directory) / "headers"
            body = Path(directory) / "body"
            command = ["curl.exe" if os.name == "nt" else "curl", "--disable", "--silent", "--show-error",
                       "--proto", "=http,https", "--max-time", str(timeout or 20),
                       "--max-filesize", "25000000", "--dump-header", str(headers), "--output", str(body)]
            for key, value in request.header_items():
                command.extend(["--header", f"{key}: {value}"])
            command.append(request.full_url)
            completed = subprocess.run(command, capture_output=True, timeout=(timeout or 20) + 5,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if completed.returncode:
                raise OSError(completed.stderr.decode(errors="replace").strip())
            return CurlResponse(body.read_bytes(), headers.read_bytes())


def now() -> str:
    return datetime.now(UTC).isoformat()


def url_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def saved_and_intact(result: dict, output: Path) -> bool:
    if result["status"] != "saved" or not result["snapshots"]:
        return False
    for item in result["snapshots"]:
        path = output / item["relative_path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            return False
    return True


def markdown_targets(path: Path) -> list[dict]:
    """Every distinct explicit HTTP(S) link of a Markdown catalogue, in document order."""
    content = path.read_text(encoding="utf-8")
    targets: dict[str, dict] = {}
    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", content):
        url = urldefrag(match.group(2))[0]
        entry = targets.setdefault(url, {"url": url, "url_id": url_id(url), "references": []})
        entry["references"].append({
            "label": " ".join(match.group(1).split()),
            "catalogue_line": content.count("\n", 0, match.start()) + 1,
            "catalogue": path.name,
        })
    return list(targets.values())


def registry_targets(path: Path, entries: list[dict]) -> list[dict]:
    """One target per selected registry entry; the reference points at the entry's line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    targets: dict[str, dict] = {}
    for entry in entries:
        url = entry["definition"]["start_url"]
        line = next((number for number, text in enumerate(lines, 1) if json.dumps(url) in text), None)
        target = targets.setdefault(url, {"url": url, "url_id": url_id(url), "references": [], "registry_entries": []})
        target["references"].append({"label": entry["title"], "catalogue_line": line, "catalogue": path.name})
        target["registry_entries"].append(entry)
    return list(targets.values())


def catalogue_targets(registry: Path, entries: list[dict], markdown: Path | None = None) -> list[dict]:
    """Markdown links first (when given), then registry seeds; registry metadata is attached by URL."""
    by_url: dict[str, list[dict]] = {}
    for entry in entries:
        by_url.setdefault(entry["definition"]["start_url"], []).append(entry)
    targets: dict[str, dict] = {}
    if markdown is not None:
        for target in markdown_targets(markdown):
            targets[target["url"]] = {**target, "registry_entries": by_url.get(target["url"], [])}
    for target in registry_targets(registry, entries):
        if target["url"] in targets:
            targets[target["url"]]["references"].extend(target["references"])
        else:
            targets[target["url"]] = target
    return list(targets.values())


def review_flags(body: bytes, suffix: str) -> list[str]:
    """Cheap content checks that mark a saved HTML response for human review."""
    flags = []
    if suffix == ".html":
        text = body.decode("utf-8", errors="replace").lower()
        if "<app-root" in text or "<fedlex" in text:
            flags.append("javascript_application_shell")
        if any(term in text for term in ("just a moment...", "checking your browser", "access denied")):
            flags.append("possible_access_challenge")
        # Like the extraction, the title and every level-1 heading count.
        titles = re.findall(r"<(title|h1)\b[^>]*>(.*?)</\1\s*>", text, re.S)
        if any(ERROR_TITLE.fullmatch(html.unescape(re.sub(r"<[^>]*>", "", value))) for _, value in titles):
            flags.append("error_page_title")
    return flags


def error_page(result: dict, output: Path) -> bool:
    """A saved response whose title says it is an error page, although the server answered with success.

    Copies made by other tools carry no flags, so the saved bytes are checked too.
    """
    for item in result.get("snapshots", []):
        path = output / item["relative_path"]
        flags = set(item.get("review_flags", []))
        if path.is_file():
            flags.update(review_flags(path.read_bytes(), path.suffix.lower()))
        if "error_page_title" in flags:
            return True
    return False


def snapshot(target: dict, output: Path, allowed_hosts: tuple[str, ...], transport: str = "urllib") -> dict:
    folder = output / "pages" / target["url_id"]
    folder.mkdir(parents=True, exist_ok=True)
    attempt_number = 1
    while (folder / f"attempt-{attempt_number:03d}").exists():
        attempt_number += 1
    attempt = folder / f"attempt-{attempt_number:03d}"
    attempt.mkdir()
    captured = []

    def save(page, body: bytes) -> None:
        suffix = ".pdf" if body.startswith(b"%PDF-") else {
            "text/html": ".html", "application/xhtml+xml": ".html",
            "text/plain": ".txt", "application/xml": ".xml", "text/xml": ".xml",
        }.get(page.content_type, ".bin")
        destination = attempt / ("response" + suffix)
        destination.write_bytes(body)
        flags = review_flags(body, suffix)
        expected = target.get("expected_media_type")
        if expected and page.content_type != expected:
            flags.append("unexpected_media_type")
        captured.append({**asdict(page), "relative_path": destination.relative_to(output).as_posix(),
                         "review_flags": flags, "processing_status": "NOT_PROCESSED"})

    limits = CrawlLimits(max_depth=0, max_pages=1, max_requests=16,
                         max_total_bytes=30_000_000, max_response_bytes=25_000_000,
                         max_duration_seconds=120, request_timeout_seconds=20,
                         delay_seconds=2, max_redirects=6, max_links_per_page=100,
                         max_queued_urls=1, max_failures=2)
    source = SourceDefinition(source_id=target["url_id"], start_url=target["url"],
                              allowed_hosts=allowed_hosts, allowed_path_prefixes=("/",))
    started = now()
    try:
        report = SafeCrawler(source, limits, allow_query_strings=True,
                             opener=CurlOpener() if transport == "curl" else None,
                             document_content_types=DOCUMENT_TYPES,
                             on_page=save, on_document=save).crawl().to_dict()
        result = {**target, "started_at": started, "finished_at": now(),
                  "status": "saved" if captured else "not_saved", "snapshots": captured,
                  "report": report, "transport": transport}
    except Exception as exc:
        result = {**target, "started_at": started, "finished_at": now(),
                  "status": "error", "snapshots": captured,
                  "error": f"{type(exc).__name__}: {exc}"}
    write_json(attempt / "manifest.json", result)
    write_json(folder / "latest.json", result)
    return result


def failure_detail(item: dict) -> str:
    """One line that explains why a target has no saved response."""
    report = item.get("report", {})
    parts = [item.get("error")] if item.get("error") else [report.get("stop_reason", "pending")]
    parts.extend(skipped["reason"] for skipped in report.get("skipped", []))
    parts.extend(f"HTTP {page['status']}: {page['outcome']}" for page in report.get("pages", []))
    return "; ".join(part for part in parts if part)


def summary(output: Path, plan: dict, title: str | None = None) -> dict:
    results = []
    for target in plan["targets"]:
        path = output / "pages" / target["url_id"] / "latest.json"
        item = read_json(path) if path.exists() else {**target, "status": "pending", "snapshots": []}
        if target.get("attribution"):
            # Imported manifests predate the attribution; the plan is its home.
            item["attribution"] = target["attribution"]
        results.append(item)
    value = {"schema_version": SUMMARY_SCHEMA, "updated_at": now(),
             "catalogue_sha256": plan["catalogue_sha256"], "target_count": len(results),
             "counts": dict(Counter(item["status"] for item in results)),
             "saved_bytes": sum(s["bytes_downloaded"] for item in results for s in item["snapshots"]),
             "processing_status": "NOT_RUN", "results": results}
    value["resolution_errors"] = plan.get("resolution_errors", [])
    supplements = []
    for path in sorted(output.glob("*-documents/summary.json")):
        supplement = read_json(path)
        supplements.append({"relative_path": path.relative_to(output).as_posix(),
                            "counts": supplement["counts"], "saved_bytes": supplement["saved_bytes"]})
    value["supplements"] = supplements
    catalogue = [item for item in results if not item.get("attribution")]
    discovered = [item for item in results if item.get("attribution")]
    value["catalogue_counts"] = dict(Counter(item["status"] for item in catalogue))
    value["discovered_counts"] = dict(Counter(item["status"] for item in discovered))
    write_json(output / "summary.json", value)
    heading = title or f"Source download: {output.name}"
    lines = [f"# {heading}", "", f"Updated: {value['updated_at']}", "",
             f"Targets: {len(results)}. Status counts: {value['counts']}.",
             f"Saved response bytes: {value['saved_bytes']}. No extraction or publication run.", "",
             "Only listed URLs were requested; links, assets and attachments were not recursively followed.",
             "Saved HTML can be a navigation page or JavaScript shell. See review flags and raw files.", ""]
    if discovered:
        lines.extend([f"Catalogue targets: {len(catalogue)} ({value['catalogue_counts']}). "
                      f"Discovered pages imported from a link-following crawl: {len(discovered)} "
                      f"({value['discovered_counts']}); they are listed by kind and host below and in full in summary.json.", ""])
    for supplement in supplements:
        folder = Path(supplement["relative_path"]).parent.as_posix()
        lines.extend([f"[Source documents: {folder}]({folder}/README.md)", ""])
    for error in value["resolution_errors"]:
        lines.extend([f"Resolution failed: {error['source_url']}: {error['error']}", ""])
    lines.extend(["| Source | Status | Local response / reason |", "| --- | --- | --- |"])
    for item in catalogue:
        if item["snapshots"]:
            detail = "; ".join(f"[response]({s['relative_path']}) " + ", ".join(s["review_flags"])
                               for s in item["snapshots"])
        else:
            detail = failure_detail(item)
        label = item["references"][0]["label"] if item.get("references") else item["url"]
        lines.append(f"| [{label}]({item['url']}) | {item['status']} | {detail.replace('|', '/')} |")
    if discovered:
        lines.extend(["", "## Discovered pages", "", "| Kind | Pages | Saved |", "| --- | ---: | ---: |"])
        kinds = Counter(item["attribution"]["kind"] for item in discovered)
        for kind, count in kinds.most_common():
            saved = sum(1 for item in discovered if item["attribution"]["kind"] == kind and item["status"] == "saved")
            lines.append(f"| {kind} | {count} | {saved} |")
        lines.extend(["", "| Host | Pages | Saved | Attributed sources |", "| --- | ---: | ---: | --- |"])
        hosts = Counter(urlsplit(item["url"]).hostname for item in discovered)
        for host, count in hosts.most_common():
            items = [item for item in discovered if urlsplit(item["url"]).hostname == host]
            sources = sorted({s for item in items for s in item["attribution"]["source_ids"]})
            lines.append(f"| {host} | {count} | {sum(1 for item in items if item['status'] == 'saved')} | "
                         f"{', '.join(f'`{s}`' for s in sources) or '-'} |")
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return value
