"""Read an ingestion run: plan, manifests, attribution, plugin documents, verified bytes.

A run directory is the output of `swisstip-download` (see the ingestion
package). Only `latest.json` of every page is considered; earlier attempts
are acquisition history. Nothing here writes to the run.
"""

import hashlib
import json
from pathlib import Path

SCOPES = ("attributed", "all")
ATTRIBUTED_KINDS = ("catalogue", "in-scope", "language-variant", "plugin-document")


class RunError(ValueError):
    pass


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document_id(source_url: str, raw_sha256: str) -> str:
    """Identity of a record: the source URL and the exact bytes, not the attempt folder."""
    return "doc-" + sha256(f"{source_url}\n{raw_sha256}".encode("utf-8"))[:20]


def load_plan(run: Path) -> dict:
    path = run / "plan.json"
    if not path.is_file():
        raise RunError(f"Not a run directory (no plan.json): {run}")
    return read_json(path)


def plan_targets(plan: dict) -> dict[str, dict]:
    return {target["url"]: target for target in plan.get("targets", [])}


def source_ids(target: dict | None) -> list[str]:
    if not target:
        return []
    ids = [entry["definition"]["source_id"] for entry in target.get("registry_entries", [])
           if entry.get("definition", {}).get("source_id")]
    if target.get("attribution"):
        ids.extend(target["attribution"].get("source_ids", []))
    return sorted(set(ids))


def attribution_for(target: dict | None, plugin_id: str | None, source_page_target: dict | None,
                    source_page_url: str | None) -> dict:
    if plugin_id:
        return dict(kind="plugin-document", plugin_id=plugin_id, source_ids=source_ids(source_page_target),
                    resolved_from=source_page_url)
    if target is None:
        return dict(kind="unplanned", source_ids=[])
    if target.get("attribution"):
        given = target["attribution"]
        return dict(kind=given.get("kind", "out-of-scope"), source_ids=sorted(given.get("source_ids", [])),
                    advertised_languages=given.get("advertised_languages", []), reasons=given.get("reasons", []))
    return dict(kind="catalogue", source_ids=source_ids(target))


def manifest_folders(run: Path) -> list[tuple[str | None, Path]]:
    """Plugin document folders first: when a crawl also saved a plugin document as an
    ordinary page with the same bytes, the two share a document ID and the first
    item wins, so the plugin attribution (source IDs of the catalogue page) is kept."""
    folders = []
    for folder in sorted(run.glob("*-documents")):
        if (folder / "pages").is_dir():
            folders.append((folder.name[: -len("-documents")], folder / "pages"))
    folders.append((None, run / "pages"))
    return folders


def iter_manifests(run: Path):
    """Yield (plugin_id, folder, pointer path, manifest) for every latest.json of the run."""
    for plugin_id, pages in manifest_folders(run):
        if not pages.is_dir():
            continue
        for pointer in sorted(pages.glob("*/latest.json")):
            yield plugin_id, pages.parent, pointer, read_json(pointer)


def snapshot_items(run: Path, plan: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Every intact saved snapshot of the run as a plain dict, plus the unavailable targets."""
    run = run.resolve()
    plan = plan or load_plan(run)
    targets = plan_targets(plan)
    items, unavailable = [], []
    for plugin_id, folder, pointer, manifest in iter_manifests(run):
        url = manifest["url"]
        target = targets.get(url)
        source_page_url = manifest.get("source_page_url")
        source_url = source_page_url or url
        snapshots = manifest.get("snapshots") or []
        if not snapshots:
            unavailable.append(dict(url=url, source_url=source_url, plugin_id=plugin_id, status=manifest.get("status"),
                                    error=manifest.get("error") or manifest.get("last_error"),
                                    manifest_path=pointer.relative_to(run).as_posix(),
                                    attribution=attribution_for(target, plugin_id, targets.get(source_page_url), source_page_url)))
            continue
        for snapshot in snapshots:
            relative = Path(snapshot["relative_path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise RunError(f"Unsafe snapshot path in {pointer}: {snapshot['relative_path']}")
            run_relative = (folder.relative_to(run) / relative).as_posix()
            attempt = next((part for part in relative.parts if part.startswith("attempt-")), None)
            items.append(dict(
                pointer=pointer.relative_to(run).as_posix(), plugin_id=plugin_id, url=url, source_url=source_url,
                document_url=snapshot.get("final_url") or url, requested_url=snapshot.get("requested_url") or url,
                version_uri=manifest.get("version_uri"), language=manifest.get("language"),
                registry_entries=manifest.get("registry_entries", []), references=manifest.get("references", []),
                discoveries=manifest.get("discoveries", []), imported_from=manifest.get("imported_from"),
                status=manifest.get("status"), http_status=manifest.get("http_status"),
                snapshot=dict(snapshot), relative_path=run_relative, attempt=attempt,
                raw_sha256=snapshot["sha256"], document_id=document_id(source_url, snapshot["sha256"]),
                attribution=attribution_for(target, plugin_id, targets.get(source_page_url), source_page_url)))
    return items, unavailable


def select_items(items: list[dict], *, scope: str = "attributed", kinds: list[str] | None = None,
                 sources: list[str] | None = None, document_ids: list[str] | None = None) -> list[dict]:
    if scope not in SCOPES:
        raise RunError(f"Unknown scope {scope!r}; expected one of {SCOPES}")
    selected = []
    for item in items:
        kind = item["attribution"]["kind"]
        if scope == "attributed" and kind not in ATTRIBUTED_KINDS and kind != "unplanned":
            continue
        if kinds and kind not in kinds:
            continue
        if sources and not set(sources) & set(item["attribution"].get("source_ids", [])):
            continue
        if document_ids and item.get("document_id") not in document_ids:
            continue
        selected.append(item)
    return selected


def read_verified(run: Path, item: dict) -> bytes:
    """Read the raw response and stop on any difference from the manifest."""
    run = run.resolve()
    path = (run / item["relative_path"]).resolve()
    if not path.is_relative_to(run):
        raise RunError(f"Snapshot escapes the run directory: {item['relative_path']}")
    if not path.is_file():
        raise RunError(f"Saved response is missing: {item['relative_path']}")
    raw = path.read_bytes()
    snapshot = item["snapshot"]
    if sha256(raw) != snapshot["sha256"] or len(raw) != snapshot["bytes_downloaded"]:
        raise RunError(f"Saved response differs from its manifest hash or size: {item['relative_path']}")
    return raw


def manifest_sha256(run: Path, item: dict) -> str:
    return sha256((run / item["pointer"]).read_bytes())
