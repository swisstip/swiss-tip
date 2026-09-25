"""Offline validation and selection for an operator-authored source catalogue (``sources.json``)."""

from dataclasses import fields
from datetime import date
import json
import math
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urlsplit

from .crawler import CrawlLimits, SourceDefinition


SOURCE_SCHEMAS = {"source-catalog/v1", "swisstip.source-catalog/v1"}
SCAN_STATUSES = {"ready", "needs_access_review", "manual_adapter_required"}
LANGUAGES = {"en", "de", "fr", "it", "rm"}
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CANTONS = frozenset("AG AI AR BE BL BS FR GE GL GR JU LU NE NW OW SG SH SO SZ TG TI UR VD VS ZG ZH".split())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _https_url(value: str) -> None:
    parsed = urlsplit(value)
    _require(parsed.scheme == "https" and bool(parsed.hostname), "Source URLs must use absolute HTTPS")
    _require(not (parsed.username or parsed.password or parsed.query or parsed.fragment),
             "Source URLs cannot contain credentials, queries or fragments")
    _require(parsed.port in {None, 443}, "Source URLs must use the standard HTTPS port")


def _in_paths(path: str, prefixes: list[str]) -> bool:
    path = posixpath.normpath(unquote(path))
    return any(prefix == "/" or path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
               for prefix in prefixes)


def attribute_url(url: str, entries: list[dict]) -> list[str]:
    """IDs of the catalogue sources whose host and path allowlist contain the URL."""
    parsed = urlsplit(url)
    return [entry["definition"]["source_id"] for entry in entries
            if parsed.hostname in entry["definition"]["allowed_hosts"]
            and _in_paths(parsed.path or "/", list(entry["definition"]["allowed_path_prefixes"]))]


def source_definition(entry: dict) -> SourceDefinition:
    values = dict(entry["definition"])
    values["allowed_hosts"] = tuple(values["allowed_hosts"])
    values["allowed_path_prefixes"] = tuple(values["allowed_path_prefixes"])
    return SourceDefinition(**values)


def load_source_catalog(path: Path) -> dict:
    """Reject malformed scopes and dangling selectors without DNS, HTTP or inference."""
    return validate_source_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def dump_source_catalog(data: dict) -> str:
    """The catalogue as it is written to disk; validates first, so an edited catalogue
    can never be saved in a shape the planner would refuse. An empty source list is
    allowed here and refused by the planner: a new catalogue starts empty."""
    validate_source_catalog(data, allow_empty=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_source_catalog(path: Path, data: dict) -> None:
    Path(path).write_text(dump_source_catalog(data), encoding="utf-8", newline="\n")


def validate_source_catalog(data: dict, *, allow_empty: bool = False) -> dict:
    _require(data["schema_version"] in SOURCE_SCHEMAS, "Unsupported source catalog schema")
    _require(data["status"] == "SOURCES_ONLY", "Expected a source-only catalog")
    preferred_language = data["language_discovery"].get("preferred_seed_language", "de")
    _require(preferred_language in LANGUAGES, "Invalid preferred seed language")
    _require(_identifier(data["knowledge_space_id"]) and _identifier(data["artifact_id"]), "Invalid catalog ID")
    topics = [item["topic_id"] for item in data["planning_topics"]]
    _require(all(_identifier(topic) for topic in topics) and len(set(topics)) == len(topics),
             "Planning topic IDs must be valid and unique")
    _require(data["scope"]["country_code"] == "CH", "Expected Swiss jurisdiction")
    cantons = data["scope"]["canton_codes"]
    _require(len(cantons) == len(set(cantons)) and set(cantons) <= {f"CH-{code}" for code in CANTONS},
             "Invalid or duplicate cantonal scope")
    for values in data["crawl_profiles"].values():
        _require(set(values) == {field.name for field in fields(CrawlLimits)}, "Specify every crawl limit explicitly")
        for key, value in values.items():
            _require(type(value) in {int, float} and math.isfinite(value), f"Invalid numeric limit: {key}")
            if not key.endswith("seconds"):
                _require(type(value) is int, f"Expected integer limit: {key}")
        CrawlLimits(**values)
    ids: set[str] = set()
    urls: set[str] = set()
    _require(allow_empty or bool(data["sources"]), "Source list cannot be empty")
    for entry in data["sources"]:
        source = source_definition(entry)
        _require(_identifier(source.source_id) and source.source_id not in ids, "Invalid or duplicate source ID")
        _https_url(source.start_url)
        _require(source.start_url not in urls, "Duplicate seed URL")
        _require(bool(source.canonical_authority), "Missing canonical authority")
        _require(source.jurisdiction in {"CH", *cantons}, "Source jurisdiction outside catalog scope")
        _require(source.language in LANGUAGES, "Invalid seed language hint")
        _require(entry["authority_level"] in {"federal", "cantonal", "municipal"}, "Invalid authority level")
        _require((entry["authority_level"] == "federal") == (source.jurisdiction == "CH"), "Authority/jurisdiction mismatch")
        if entry["authority_level"] == "municipal":
            _require(bool(entry["municipality"]["name"]) and re.fullmatch(r"[0-9]{4}", entry["municipality"]["bfs_code"]) is not None,
                     "Municipal sources require an explicit municipality and BFS code")
        _require(entry["priority"] in {"P0", "P1", "P2"}, "Invalid source priority")
        _require(entry["scan_status"] in SCAN_STATUSES, "Invalid scan status")
        _require(entry.get("user_agent") in {None, "browser"}, "user_agent must be absent or 'browser'")
        _require(bool(entry["title"]) and bool(entry["notes"]), "Missing source title or scan notes")
        _require(bool(entry["topic_hints"]) and set(entry["topic_hints"]) <= set(topics), "Unknown planning topic")
        _require(bool(source.allowed_hosts) and urlsplit(source.start_url).hostname in source.allowed_hosts,
                 "Seed host must be explicitly allowlisted")
        for prefix in source.allowed_path_prefixes:
            _require((unquote(prefix) == prefix and posixpath.normpath(prefix) == prefix.rstrip("/")) or prefix == "/",
                     "Path prefixes must be canonical and decoded")
            _require("?" not in prefix and "#" not in prefix and "\\" not in prefix,
                     "Path prefixes cannot contain queries, fragments or backslashes")
        _require(_in_paths(urlsplit(source.start_url).path or "/", list(source.allowed_path_prefixes)),
                 "Seed path must be inside its allowlist")
        _https_url(entry["discovery"]["reference_url"])
        date.fromisoformat(entry["discovery"]["located_on"])
        _require(entry["discovery"]["method"] in {"official_search_result", "official_page_link", "official_directory_link"},
                 "Unknown discovery method")
        ids.add(source.source_id)
        urls.add(source.start_url)
    by_id = {entry["definition"]["source_id"]: entry for entry in data["sources"]}
    group_ids: set[str] = set()
    grouped_sources: set[str] = set()
    for group in data.get("parallel_page_groups", []):
        group_id, members = group["group_id"], group["source_ids"]
        _require(_identifier(group_id) and group_id not in group_ids, "Invalid or duplicate parallel page group ID")
        _require(len(members) >= 2 and len(set(members)) == len(members) and set(members) <= ids,
                 "Parallel page groups require at least two unique, known source IDs")
        _require(not grouped_sources.intersection(members), "A seed can belong to only one parallel page group")
        _require(group["alignment_status"] == "NOT_EVALUATED" and bool(group["notes"]),
                 "Source-only page groups cannot assert evaluated content equivalence")
        entries = [by_id[member] for member in members]
        first = entries[0]
        _require(len({entry["definition"]["language"] for entry in entries}) == len(entries),
                 "Parallel page group seeds must use distinct language hints")
        _require(all(all(entry["definition"][key] == first["definition"][key]
                         for key in ("jurisdiction", "canonical_authority"))
                     and entry["authority_level"] == first["authority_level"]
                     and entry.get("municipality") == first.get("municipality") for entry in entries),
                 "Parallel page groups must preserve authority and jurisdiction")
        group_ids.add(group_id)
        grouped_sources.update(members)
    for name, members in data["scan_sets"].items():
        _require(_identifier(name) and bool(members) and len(set(members)) == len(members) and set(members) <= ids,
                 "Scan sets require unique, known source IDs")
    return data


def select_sources(data: dict, *, scan_set: str | None = None, source_ids: list[str] | None = None) -> list[dict]:
    """Entries of a named scan set or an explicit ID list, in catalogue order; default: every source."""
    _require(scan_set is None or source_ids is None, "Select a scan set or explicit source IDs, not both")
    known = {entry["definition"]["source_id"]: entry for entry in data["sources"]}
    if source_ids is not None:
        missing = sorted(set(source_ids) - known.keys())
        _require(not missing, f"Unknown source IDs: {missing}")
        selected = set(source_ids)
    elif scan_set is not None:
        _require(scan_set in data["scan_sets"], f"Unknown scan set: {scan_set}")
        selected = set(data["scan_sets"][scan_set])
    else:
        selected = set(known)
    return [entry for entry in data["sources"] if entry["definition"]["source_id"] in selected]
