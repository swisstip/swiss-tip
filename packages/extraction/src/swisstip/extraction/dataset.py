"""The text dataset directory: records, reading views, index, summary, grouping, pruning."""

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import EXTRACTOR_VERSION, SCHEMA_VERSION
from .reading_view import render

SUMMARY_SCHEMA = "swisstip.source-text-dataset/v1"
GROUP_FIELDS = ("representation_group", "html_counterparts", "preferred_representation",
                "identical_raw_snapshots", "identical_text_records")
DEPENDENCIES = ("lxml", "pypdf", "pypdfium2", "olefile", "striprtf")


def write_json(path: Path, value: object, indent: int | None = 2) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def record_path(output: Path, document_id: str) -> Path:
    return output / "documents" / f"{document_id}.json"


def view_path(output: Path, document_id: str) -> Path:
    return output / "reading" / f"{document_id}.md"


def check_output_location(run: Path, output: Path) -> None:
    run, output = run.resolve(), output.resolve()
    if output == run:
        raise ValueError("The text dataset cannot be the run directory itself")
    if output.is_relative_to(run):
        relative = output.relative_to(run)
        if relative.parts and (relative.parts[0] == "pages" or relative.parts[0].endswith("-documents")):
            raise ValueError(f"Refusing to write the text dataset inside the saved responses: {output}")
    if "pages" in output.parts:
        raise ValueError(f"Refusing to write the text dataset inside a pages directory: {output}")


def reusable(existing: dict, raw_sha256: str, extractor_version: str = EXTRACTOR_VERSION) -> bool:
    """An existing record stands in for a new extraction when nothing that produced it changed."""
    if existing.get("acquisition", {}).get("raw_sha256") != raw_sha256:
        return False
    if existing.get("status") == "extraction_failed":
        return False
    return existing.get("extractor_version") == extractor_version or bool(existing.get("imported_text"))


def index_entry(record: dict, output: Path | None = None) -> dict:
    acquisition = record.get("acquisition", {})
    attribution = record.get("attribution", {})
    document_id = record["document_id"]
    entry = dict(
        document_id=document_id, title=record.get("title"), source_url=record.get("source_url"),
        document_url=record.get("document_url"), version_uri=record.get("version_uri"),
        representation=record.get("representation"), page_kind=record.get("page_kind"), status=record.get("status"),
        eligible_for_processing=record.get("eligible_for_processing", False),
        exclusion_reasons=record.get("exclusion_reasons", []),
        language_declared=record.get("language_declared"), language_hint=record.get("language_hint"),
        attribution_kind=attribution.get("kind"), source_ids=attribution.get("source_ids", []),
        plugin_id=attribution.get("plugin_id"), attempt=acquisition.get("attempt"),
        retrieved_at=acquisition.get("retrieved_at"), raw_sha256=acquisition.get("raw_sha256"),
        content_sha256=record.get("content_sha256"), blocks=len(record.get("blocks", [])),
        text_characters=len(record.get("content_text", "")),
        pdf_pages_without_text=record.get("pages_without_text", []), warnings=record.get("warnings", []),
        extractor_version=record.get("extractor_version"), imported=bool(record.get("imported_text")),
        file=f"documents/{document_id}.json",
        reading_file=f"reading/{document_id}.md" if output and view_path(output, document_id).exists() else None,
    )
    for field in GROUP_FIELDS:
        entry[field] = record.get(field)
    return entry


def write_record(output: Path, record: dict, reading_view: bool = True) -> None:
    path = record_path(output, record["document_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    # Records are read by tools and are large; the reading view is the human copy.
    write_json(path, record, indent=None)
    view = view_path(output, record["document_id"])
    if reading_view and record.get("eligible_for_processing"):
        view.parent.mkdir(parents=True, exist_ok=True)
        view.write_text(render(record), encoding="utf-8", newline="\n")
    elif view.exists():
        view.unlink()


def existing_ids(output: Path) -> set[str]:
    documents = output / "documents"
    return {path.stem for path in documents.glob("doc-*.json")} if documents.is_dir() else set()


def load_entries(output: Path, document_ids) -> list[dict]:
    return [index_entry(read_json(record_path(output, document_id)), output) for document_id in sorted(document_ids)]


def group_fields(entries: list[dict]) -> dict[str, dict]:
    """Representation groups, PDF to HTML counterparts, identical raw bytes, identical normalized text."""
    by_source, by_raw, by_text = defaultdict(list), defaultdict(list), defaultdict(list)
    for entry in entries:
        by_source[entry["source_url"]].append(entry)
        if entry.get("raw_sha256"):
            by_raw[entry["raw_sha256"]].append(entry["document_id"])
        if entry.get("content_sha256") and entry.get("text_characters"):
            by_text[entry["content_sha256"]].append(entry["document_id"])
    result = {}
    for entry in entries:
        group = by_source[entry["source_url"]]
        html_ids = sorted(e["document_id"] for e in group if e["representation"] == "html" and e["eligible_for_processing"])
        is_pdf = entry["representation"] == "pdf"
        result[entry["document_id"]] = dict(
            representation_group=entry.get("representation_group"),
            html_counterparts=html_ids if is_pdf else [],
            preferred_representation=bool(entry["eligible_for_processing"] and (not is_pdf or not html_ids)),
            identical_raw_snapshots=sorted(i for i in by_raw.get(entry.get("raw_sha256"), []) if i != entry["document_id"]),
            identical_text_records=sorted(i for i in by_text.get(entry.get("content_sha256"), []) if i != entry["document_id"]),
        )
    return result


def apply_groups(output: Path, entries: list[dict]) -> int:
    """Write the group fields into every record whose values changed; return the number rewritten."""
    rewritten = 0
    computed = group_fields(entries)
    for entry in entries:
        fields = computed[entry["document_id"]]
        if all(entry.get(name) == value for name, value in fields.items()):
            continue
        record = read_json(record_path(output, entry["document_id"]))
        record.update(fields)
        write_json(record_path(output, entry["document_id"]), record, indent=None)
        entry.update(fields)
        rewritten += 1
    return rewritten


def prune(output: Path, keep_ids: set[str]) -> list[str]:
    removed = []
    for document_id in sorted(existing_ids(output) - keep_ids):
        record_path(output, document_id).unlink()
        view = view_path(output, document_id)
        if view.exists():
            view.unlink()
        removed.append(document_id)
    return removed


def dependency_versions() -> dict[str, str | None]:
    versions = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def index_markdown(entries: list[dict], run_name: str) -> str:
    lines = [f"# Text dataset: {run_name}", "",
             f"{len(entries)} records. Statuses: {dict(Counter(e['status'] for e in entries))}.",
             "Each record is one saved response; `reading/` holds a Markdown view of every eligible record.", ""]
    by_kind = defaultdict(list)
    for entry in entries:
        by_kind[entry.get("attribution_kind") or "unknown"].append(entry)
    for kind in sorted(by_kind):
        lines.extend([f"## {kind} ({len(by_kind[kind])})", "", "| Title | Sources | Language | Status | Blocks | Record | Reading |",
                      "| --- | --- | --- | --- | ---: | --- | --- |"])
        for entry in sorted(by_kind[kind], key=lambda e: (",".join(e["source_ids"]), e["source_url"])):
            title = (entry.get("title") or entry["source_url"]).replace("|", "/")
            reading = f"[view]({entry['reading_file']})" if entry.get("reading_file") else ""
            lines.append(f"| [{title}]({entry['source_url']}) | {', '.join(entry['source_ids'])} | "
                         f"{entry.get('language_declared') or entry.get('language_hint') or ''} | {entry['status']} | "
                         f"{entry['blocks']} | [{entry['document_id']}]({entry['file']}) | {reading} |")
        lines.append("")
    return "\n".join(lines)


def summarize(entries: list[dict], run: Path, output: Path, plan: dict, selection: dict, counts: dict,
              unavailable: list[dict], errors: list[dict]) -> dict:
    return dict(
        schema_version=SUMMARY_SCHEMA, record_schema=SCHEMA_VERSION, run=run.name, run_path=str(run),
        output_path=str(output), generated_at=datetime.now(UTC).isoformat(), extractor_version=EXTRACTOR_VERSION,
        dependencies=dependency_versions(), catalogue_sha256=plan.get("catalogue_sha256"), selection=selection,
        records=len(entries), statuses=dict(Counter(e["status"] for e in entries)),
        representations=dict(Counter(e["representation"] for e in entries)),
        attribution_kinds=dict(Counter(e["attribution_kind"] for e in entries)),
        language_hints=dict(Counter(e.get("language_declared") or e.get("language_hint") or "undeclared" for e in entries)),
        eligible=sum(bool(e["eligible_for_processing"]) for e in entries),
        preferred=sum(bool(e.get("preferred_representation")) for e in entries),
        blocks=sum(e["blocks"] for e in entries), text_characters=sum(e["text_characters"] for e in entries),
        pdf_records_with_pages_without_text=sum(bool(e["pdf_pages_without_text"]) for e in entries),
        html_text_sequence_differs=sum("html_text_sequence_differs_review_required" in e["warnings"] for e in entries),
        imported_records=sum(bool(e["imported"]) for e in entries),
        extractor_versions=dict(Counter(e["extractor_version"] for e in entries)),
        unavailable=len(unavailable), errors=len(errors), network_requests=0, model_calls=0, ocr_performed=False,
        **counts,
    )


def write_dataset_files(output: Path, entries: list[dict], summary: dict, unavailable: list[dict], errors: list[dict]) -> None:
    entries = sorted(entries, key=lambda e: e["document_id"])
    write_json(output / "index.json", entries)
    (output / "index.md").write_text(index_markdown(entries, summary["run"]), encoding="utf-8", newline="\n")
    write_json(output / "unavailable.json", unavailable)
    write_json(output / "errors.json", errors)
    write_json(output / "summary.json", summary)
