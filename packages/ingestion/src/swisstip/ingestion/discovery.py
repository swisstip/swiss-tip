"""Discovered pages: URLs found by following links from catalogue pages.

The package's own downloader fetches catalogue URLs only. Pages that an
earlier link-following crawl found (language variants, topic links,
attachments) can still join a run as *discovered targets*: they sit in the
same ``targets`` list of ``plan.json`` and get the same page folders, but
carry an ``attribution`` that says how they relate to the catalogue:

    in-scope          the URL lies inside the host and path allowlist of a
                      catalogue source; ``source_ids`` names the sources
    language-variant  reached through a published language link from an
                      in-scope or catalogue page, transitively
    out-of-scope      everything else the crawl saved (``scope="all"`` only)

The input is the crawl's state file, ``audit-state.json`` of the original
SwissTIP language audit: one record per URL with ``status``, ``url_id`` and
the list of ``discoveries`` (reason, discovered_on, advertised_language).
"""

from collections import Counter
import json
from pathlib import Path

from .acquisition import url_id
from .catalog import attribute_url


SCOPES = ("catalogue", "all")
LANGUAGE_LINK = "published-language-link"


def load_audit_state(path: Path) -> list[dict]:
    """Records of a crawl state file, without URL normalization aliases."""
    path = Path(path)
    if path.is_dir():
        path = path / "audit-state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    return [record for record in state["targets"].values() if record.get("status") != "url-normalization-alias"]


def discovered_target(record: dict, kind: str, source_ids: list[str]) -> dict:
    discoveries = record.get("discoveries", [])
    languages = sorted({d["advertised_language"] for d in discoveries if d.get("advertised_language")})
    origins = sorted({d["discovered_on"] for d in discoveries if d.get("discovered_on")})
    reasons = sorted({d["reason"] for d in discoveries if d.get("reason")})
    return {"url": record["url"], "url_id": record.get("url_id") or url_id(record["url"]),
            "references": [], "registry_entries": [], "discoveries": discoveries,
            "attribution": {"kind": kind, "source_ids": source_ids, "advertised_languages": languages,
                            "discovered_on": origins[:5], "reasons": reasons}}


def select_discovered(records: list[dict], entries: list[dict], *, scope: str = "catalogue",
                      exclude_urls: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """Discovered targets for a catalogue, in record order; catalogue URLs themselves are excluded."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown discovery scope: {scope}; expected one of {SCOPES}")
    seeds = {entry["definition"]["start_url"] for entry in entries}
    attributed: dict[str, tuple[str, list[str]]] = {}
    for url in seeds | set(exclude_urls):
        attributed[url] = ("catalogue", attribute_url(url, entries))
    for record in records:
        if record["url"] in attributed:
            continue
        source_ids = attribute_url(record["url"], entries)
        if source_ids:
            attributed[record["url"]] = ("in-scope", source_ids)
    # Language variants inherit the sources of the page that linked them, transitively.
    pending = [r for r in records if r["url"] not in attributed]
    changed = True
    while changed:
        changed = False
        remaining = []
        for record in pending:
            origin = next((d.get("discovered_on") for d in record.get("discoveries", [])
                           if d.get("reason") == LANGUAGE_LINK and d.get("discovered_on") in attributed), None)
            if origin is None:
                remaining.append(record)
                continue
            attributed[record["url"]] = ("language-variant", attributed[origin][1])
            changed = True
        pending = remaining
    targets = []
    for record in records:
        if record["url"] in seeds or record["url"] in exclude_urls:
            continue
        kind, source_ids = attributed.get(record["url"], ("out-of-scope", []))
        if kind == "out-of-scope" and scope != "all":
            continue
        targets.append(discovered_target(record, kind, source_ids))
    return targets


def describe(targets: list[dict]) -> dict:
    """Counts by attribution kind and by host, for summaries."""
    from urllib.parse import urlsplit

    discovered = [t for t in targets if t.get("attribution")]
    return {"count": len(discovered),
            "by_kind": dict(Counter(t["attribution"]["kind"] for t in discovered)),
            "by_host": dict(Counter(urlsplit(t["url"]).hostname for t in discovered).most_common()),
            "by_source": dict(Counter(s for t in discovered for s in t["attribution"]["source_ids"]).most_common())}
