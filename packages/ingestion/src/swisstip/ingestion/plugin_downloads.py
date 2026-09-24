"""Shared source-plugin runner: bounded resolution, provenance and resumable snapshots."""

from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from .acquisition import (
    PLAN_SCHEMA, CurlOpener, now, read_json, saved_and_intact, snapshot, summary, url_id, write_json,
)
from .crawler import CrawlLimits, SafeCrawler, SourceDefinition
from .plugins import PluginRegistry, SourceRequest, validate_documents


class MetadataFetcher:
    """At most four cached, hash-checked JSON metadata requests per source."""

    def __init__(self, output: Path, plugin, transport: str = "urllib") -> None:
        self.output = output
        self.plugin = plugin
        self.transport = transport
        self.records = []
        self.calls = 0

    def __call__(self, url: str) -> dict:
        self.calls += 1
        if self.calls > 4:
            raise ValueError("Source resolution exceeds the four metadata requests per source limit")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.plugin.metadata_hosts or
                parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("Plugin metadata URL is outside its declared hosts")
        key = url_id(url)
        folder = self.output / "metadata"
        folder.mkdir(exist_ok=True)
        path, record_path = folder / f"{key}.json", folder / f"{key}.request.json"
        if path.exists() and record_path.exists():
            record = read_json(record_path)
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise ValueError("Cached plugin metadata failed its hash check")
        else:
            bodies = []
            source = SourceDefinition(key, url, allowed_hosts=self.plugin.metadata_hosts)
            limits = CrawlLimits(max_depth=0, max_pages=1, max_requests=6,
                                 max_total_bytes=3_000_000, max_response_bytes=2_000_000,
                                 max_duration_seconds=60, request_timeout_seconds=20, delay_seconds=2)
            report = SafeCrawler(source, limits, allow_query_strings=True,
                                 opener=CurlOpener() if self.transport == "curl" else None,
                                 document_content_types=("application/json", "application/sparql-results+json"),
                                 on_document=lambda page, body: bodies.append((page, body))).crawl()
            if not bodies:
                raise ValueError(f"Metadata unavailable: {report.stop_reason}; {report.robots_status}")
            page, body = bodies[0]
            # Validate JSON before caching a reusable response.
            json.loads(body)
            path.write_bytes(body)
            record = {**asdict(page), "sha256": hashlib.sha256(body).hexdigest()}
            write_json(record_path, record)
        self.records.append({"path": path.relative_to(self.output).as_posix(), "sha256": record["sha256"]})
        return json.loads(body)


def adopt_legacy_plan(plan: dict, identity: dict, catalogue_sha256: str) -> dict:
    """Give an imported archive the plugin identity, so new catalogue sources resolve next to its documents.

    The archived documents are recorded as resolutions of their source pages and are never fetched
    again; ``adopted_archive`` keeps the catalogue hash the archive was imported under.
    """
    resolutions: dict[str, list[str]] = {}
    for target in plan["targets"]:
        resolutions.setdefault(target["source_page_url"], []).append(target["url"])
    return {**plan, "source_plugin": identity, "catalogue_sha256": catalogue_sha256,
            "resolutions": resolutions, "resolution_errors": list(plan.get("resolution_errors", [])),
            "adopted_archive": {"at": now(), "catalogue_sha256": plan["catalogue_sha256"],
                                "documents": len(plan["targets"])}}


def run_source_plugins(corpus: Path, registry: PluginRegistry, *, transport: str = "urllib",
                       retry_failed: bool = False) -> list[dict]:
    original = read_json(corpus / "plan.json")
    as_of = date.fromisoformat(original["created_at"][:10])
    summaries = []
    for plugin in registry.plugins:
        selected = [t for t in original["targets"] if registry.select(t["url"]) is plugin]
        if not selected:
            continue
        output = corpus / f"{plugin.plugin_id}-documents"
        output.mkdir(exist_ok=True)
        plan_path = output / "plan.json"
        identity = {"id": plugin.plugin_id, "version": plugin.version, "api_version": plugin.api_version}
        if plan_path.exists():
            plan = read_json(plan_path)
            if "source_plugin" not in plan and plan.get("targets"):
                # A completed archive resolved before the plugin existed, or imported
                # from an earlier run, is kept as it is with its original provenance.
                legacy = summary(output, plan, title=f"{plugin.plugin_id} documents: {corpus.name}")
                if not all(saved_and_intact(r, output) for r in legacy["results"]):
                    raise ValueError(f"Incomplete legacy archive at {output}; use a new corpus directory")
                archived = {t.get("source_page_url") for t in plan["targets"]}
                if all(item["url"] in archived for item in selected):
                    summaries.append(legacy)
                    continue
                # The catalogue gained sources this plugin resolves: the archive's documents keep
                # their provenance, the plan takes the plugin identity so the new sources resolve.
                plan = adopt_legacy_plan(plan, identity, original["catalogue_sha256"])
                write_json(plan_path, plan)
            if plan.get("source_plugin") != identity:
                raise ValueError(f"{output} uses a different or legacy resolver; use a new corpus directory")
            if plan["catalogue_sha256"] != original["catalogue_sha256"]:
                raise ValueError("Plugin plan catalogue hash mismatch")
        else:
            plan = {"schema_version": PLAN_SCHEMA, "created_at": now(),
                    "catalogue_sha256": original["catalogue_sha256"], "source_plugin": identity,
                    "as_of": as_of.isoformat(), "targets": [], "resolutions": {}, "resolution_errors": [],
                    "allowed_redirect_hosts": list(plugin.document_hosts)}
        for item in selected:
            if item["url"] in plan["resolutions"]:
                continue
            prior_error = any(e["source_url"] == item["url"] for e in plan["resolution_errors"])
            if prior_error and not retry_failed:
                continue
            plan["resolution_errors"] = [e for e in plan["resolution_errors"] if e["source_url"] != item["url"]]
            fetcher = MetadataFetcher(output, plugin, transport)
            try:
                documents = plugin.resolve(SourceRequest(item["url"], as_of), fetcher)
                validate_documents(plugin, documents)
                targets = []
                for document in documents:
                    targets.append({"url": document.url, "url_id": url_id(document.url),
                                    "references": item["references"], "registry_entries": item.get("registry_entries", []),
                                    "source_page_url": item["url"], "source_plugin": identity,
                                    "version_uri": document.version_uri, "language": document.language,
                                    "expected_media_type": document.media_type,
                                    "preferred_for_extraction": document.preferred_for_extraction,
                                    "document_metadata": document.metadata, "resolution_metadata": fetcher.records})
                known = {t["url"] for t in plan["targets"]}
                if any(t["url"] in known for t in targets):
                    raise ValueError("Two source pages resolved to the same document; explicit alignment is required")
                plan["targets"].extend(targets)
                plan["resolutions"][item["url"]] = [t["url"] for t in targets]
            except (ValueError, KeyError, TypeError, OSError) as exc:
                plan["resolution_errors"].append({"source_url": item["url"], "error": str(exc)})
            write_json(plan_path, plan)
        for target in plan["targets"]:
            latest = output / "pages" / target["url_id"] / "latest.json"
            if latest.exists():
                previous = read_json(latest)
                if saved_and_intact(previous, output):
                    continue
                if previous["status"] != "saved" and not retry_failed:
                    continue
            result = snapshot(target, output, tuple(plugin.document_hosts), transport)
            print(f"{plugin.plugin_id} {result['status']}: {target['url']}", flush=True)
            time.sleep(max(2, result.get("report", {}).get("effective_delay_seconds", 2)))
        summaries.append(summary(output, plan, title=f"{plugin.plugin_id} documents: {corpus.name}"))
    return summaries
