"""Extract the text dataset of an ingestion run.

    swisstip-extract --run releases/<pack>
    swisstip-extract --run .local/<pack> --workers 4
    swisstip-extract --run .local/<pack> --scope all --workers 4

Reads `plan.json` and every `latest.json` under `pages/` and `*-documents/`,
verifies each saved response against its manifest, and writes one record per
response to `<run>/text/documents/` (or `--output`), with a reading view, an
index and a summary. Records whose bytes and extractor version did not change
are reused. No request is made and no model is called.
"""

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import EXTRACTOR_VERSION
from .dataset import (apply_groups, check_output_location, existing_ids, index_entry, load_entries, prune,
                      read_json, record_path, reusable, summarize, view_path, write_dataset_files, write_record)
from .reading_view import render
from .records import assemble
from .run_reader import (ATTRIBUTED_KINDS, SCOPES, RunError, load_plan, manifest_sha256, read_verified,
                         select_items, snapshot_items)


def extract_one(run: str, item: dict, output: str, catalogue_sha256: str | None, force: bool, reading_view: bool) -> dict:
    """Extract or reuse one snapshot; runs in a worker process when --workers is above one."""
    run_path, output_path = Path(run), Path(output)
    target = record_path(output_path, item["document_id"])
    if target.exists() and not force:
        existing = read_json(target)
        if reusable(existing, item["raw_sha256"], EXTRACTOR_VERSION):
            view = view_path(output_path, item["document_id"])
            if reading_view and existing.get("eligible_for_processing") and not view.exists():
                view.parent.mkdir(parents=True, exist_ok=True)
                view.write_text(render(existing), encoding="utf-8", newline="\n")
            return dict(entry=index_entry(existing, output_path), reused=True)
    raw = read_verified(run_path, item)
    record = assemble(item, raw, manifest_sha256(run_path, item), run_path.name, catalogue_sha256)
    write_record(output_path, record, reading_view)
    return dict(entry=index_entry(record, output_path), reused=False)


def run_extraction(run: Path, output: Path | None = None, *, scope: str = "attributed", kinds=None, sources=None,
                   document_ids=None, workers: int = 1, force: bool = False, prune_superseded: bool = False,
                   reading_view: bool = True, log=print) -> tuple[int, dict]:
    run = run.resolve()
    output = (output or run / "text").resolve()
    check_output_location(run, output)
    plan = load_plan(run)
    items, unavailable = snapshot_items(run, plan)
    selected = select_items(items, scope=scope, kinds=kinds, sources=sources, document_ids=document_ids)
    seen, unique = set(), []
    for item in selected:
        if item["document_id"] not in seen:
            seen.add(item["document_id"])
            unique.append(item)
    (output / "documents").mkdir(parents=True, exist_ok=True)
    catalogue_sha256 = plan.get("catalogue_sha256")
    touched, reused, failed = {}, 0, 0
    arguments = [(str(run), item, str(output), catalogue_sha256, force, reading_view) for item in unique]
    if workers > 1 and len(unique) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = pool.map(extract_one, *zip(*arguments), chunksize=8)
            for number, result in enumerate(results, 1):
                touched[result["entry"]["document_id"]] = result["entry"]
                reused += result["reused"]
                if number % 100 == 0:
                    log(json.dumps(dict(processed=number, total=len(unique), reused=reused)), flush=True)
    else:
        for number, args in enumerate(arguments, 1):
            result = extract_one(*args)
            touched[result["entry"]["document_id"]] = result["entry"]
            reused += result["reused"]
            if number % 100 == 0:
                log(json.dumps(dict(processed=number, total=len(unique), reused=reused)), flush=True)
    live_ids = {item["document_id"] for item in items}
    present = existing_ids(output)
    removed = prune(output, live_ids) if prune_superseded else []
    untouched = (present - set(touched)) - set(removed)
    entries = list(touched.values()) + load_entries(output, untouched)
    for entry in entries:
        entry["superseded"] = entry["document_id"] not in live_ids
    rewritten = apply_groups(output, [e for e in entries if not e["superseded"]])
    errors = [dict(document_id=e["document_id"], source_url=e["source_url"], warnings=e["warnings"])
              for e in entries if e["status"] == "extraction_failed"]
    failed = sum(1 for e in touched.values() if e["status"] == "extraction_failed")
    selection = dict(scope=scope, kinds=kinds, sources=sources, document_ids=document_ids, selected=len(unique),
                     snapshots_in_run=len(items), duplicates_skipped=len(selected) - len(unique))
    counts = dict(extracted_now=len(unique) - reused, reused=reused, failed_in_selection=failed,
                  superseded=sum(e["superseded"] for e in entries), pruned=len(removed), group_fields_rewritten=rewritten)
    summary = summarize(entries, run, output, plan, selection, counts, unavailable, errors)
    write_dataset_files(output, entries, summary, unavailable, errors)
    return (1 if failed else 0), summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="run directory of the ingestion package")
    parser.add_argument("--output", type=Path, help="text dataset directory; default <run>/text")
    parser.add_argument("--scope", choices=SCOPES, default="attributed",
                        help="attributed: catalogue, in-scope, language-variant and plugin documents; all: also out-of-scope")
    parser.add_argument("--kind", action="append", choices=[*ATTRIBUTED_KINDS, "out-of-scope", "unplanned"],
                        help="only targets of this attribution kind (repeatable)")
    parser.add_argument("--source", action="append", help="only targets attributed to this source ID (repeatable)")
    parser.add_argument("--document-id", action="append", help="only this record (repeatable)")
    parser.add_argument("--workers", type=int, default=1, help="process pool size, 1 to the CPU count")
    parser.add_argument("--force", action="store_true", help="re-extract records that could be reused")
    parser.add_argument("--prune", action="store_true", help="delete records of superseded attempts")
    parser.add_argument("--no-reading-view", action="store_true", help="skip the Markdown reading views")
    args = parser.parse_args(argv)
    workers = max(1, min(args.workers, os.cpu_count() or 1))
    try:
        code, summary = run_extraction(args.run, args.output, scope=args.scope, kinds=args.kind, sources=args.source,
                                       document_ids=args.document_id, workers=workers, force=args.force,
                                       prune_superseded=args.prune, reading_view=not args.no_reading_view)
    except (RunError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in summary.items() if k not in ("dependencies",)}, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
