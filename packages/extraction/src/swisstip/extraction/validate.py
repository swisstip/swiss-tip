"""Validate a text dataset and write validation.json into it.

    swisstip-extract-validate --text .local/<pack>/text

Checks every record's content hash, block offsets and block hashes, verifies
that the raw response each record describes still exists in the run with the
same hash, and compares the index with the records on disk. Nonzero exit on
any failure. No request, no model.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from . import SCHEMA_VERSION
from .dataset import read_json, write_json

VALIDATION_SCHEMA = "swisstip.source-text-validation/v1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_record(record: dict) -> list[str]:
    problems = []
    if record.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version {record.get('schema_version')!r}")
    content = record.get("content_text", "")
    if sha256_text(content) != record.get("content_sha256"):
        problems.append("content_sha256 mismatch")
    for block in record.get("blocks", []):
        if content[block["start"]:block["end"]] != block["text"]:
            problems.append(f"offsets of {block['block_id']}")
        if sha256_text(block["text"]) != block.get("text_sha256"):
            problems.append(f"text_sha256 of {block['block_id']}")
        if not block["block_id"].startswith(record["document_id"] + ":b"):
            problems.append(f"block id {block['block_id']} outside document")
    return problems


def check_snapshot(run: Path, record: dict) -> str | None:
    acquisition = record.get("acquisition", {})
    path = (run / acquisition.get("path", "")).resolve()
    if not path.is_relative_to(run.resolve()) or not path.is_file():
        return "saved response missing"
    if hashlib.sha256(path.read_bytes()).hexdigest() != acquisition.get("raw_sha256"):
        return "saved response hash differs"
    return None


def validate_dataset(text: Path, run: Path | None = None, check_raw: bool = True) -> dict:
    """Validate the dataset; with check_raw the saved responses are re-hashed against the run."""
    text = text.resolve()
    if not check_raw:
        run = None
    elif run is None and (text.parent / "plan.json").is_file():
        run = text.parent
    documents = sorted((text / "documents").glob("doc-*.json"))
    index = read_json(text / "index.json") if (text / "index.json").is_file() else []
    indexed = {entry["document_id"] for entry in index}
    failures, snapshot_failures, unreadable = [], [], []
    statuses, representations, kinds, languages = Counter(), Counter(), Counter(), Counter()
    blocks = characters = pages_without_text = sequence_differs = missing_views = 0
    reading_dir = text / "reading"
    for path in documents:
        try:
            record = read_json(path)
        except (OSError, ValueError) as exc:
            unreadable.append(dict(file=path.name, error=str(exc)))
            continue
        problems = check_record(record)
        if path.stem != record.get("document_id"):
            problems.append("file name differs from document_id")
        if problems:
            failures.append(dict(document_id=record.get("document_id"), problems=problems))
        if run is not None:
            problem = check_snapshot(run, record)
            if problem:
                snapshot_failures.append(dict(document_id=record.get("document_id"), problem=problem,
                                              path=record.get("acquisition", {}).get("path")))
        statuses[record.get("status")] += 1
        representations[record.get("representation")] += 1
        kinds[record.get("attribution", {}).get("kind")] += 1
        languages[record.get("language_declared") or record.get("language_hint") or "undeclared"] += 1
        blocks += len(record.get("blocks", []))
        characters += len(record.get("content_text", ""))
        pages_without_text += len(record.get("pages_without_text", []) or [])
        sequence_differs += "html_text_sequence_differs_review_required" in record.get("warnings", [])
        if reading_dir.is_dir() and record.get("eligible_for_processing") and not (reading_dir / f"{path.stem}.md").exists():
            missing_views += 1
    on_disk = {path.stem for path in documents}
    index_problems = dict(in_index_not_on_disk=sorted(indexed - on_disk), on_disk_not_in_index=sorted(on_disk - indexed))
    checks = [
        dict(name="records readable", passed=not unreadable, detail=unreadable[:20]),
        dict(name="content hash, block offsets and block hashes", passed=not failures, detail=failures[:20]),
        dict(name="saved responses present with the recorded hash",
             passed=run is not None and not snapshot_failures, skipped=run is None, detail=snapshot_failures[:20]),
        dict(name="index matches the records on disk", passed=not any(index_problems.values()), detail=index_problems),
        dict(name="eligible records have a reading view", passed=missing_views == 0, skipped=not reading_dir.is_dir(),
             detail=dict(missing=missing_views)),
    ]
    report = dict(schema_version=VALIDATION_SCHEMA, validated_at=datetime.now(UTC).isoformat(), text_path=str(text),
                  run_path=str(run) if run else None, records=len(documents), checks=checks,
                  passed=all(c["passed"] or c.get("skipped") for c in checks),
                  statuses=dict(statuses), representations=dict(representations), attribution_kinds=dict(kinds),
                  languages=dict(languages), blocks=blocks, text_characters=characters,
                  pdf_pages_without_text=pages_without_text, html_text_sequence_differs=sequence_differs,
                  network_requests=0, model_calls=0)
    write_json(text / "validation.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", type=Path, required=True, help="text dataset directory")
    parser.add_argument("--run", type=Path, help="run directory; default: the parent of --text when it holds plan.json")
    args = parser.parse_args(argv)
    try:
        report = validate_dataset(args.text, args.run)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
