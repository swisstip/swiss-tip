"""Separate, offline command for reviewed proposals to curation YAML."""

import argparse
import json
import sys
from pathlib import Path

import yaml

from swisstip.build.release_build import TextDataset

from .legacy import load_legacy_batch
from .package import _atomic_text, load_reports, package_candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--concepts", type=Path)
    inputs.add_argument("--legacy-batch", type=Path, action="append", help="Repeat to package multiple legacy batches")
    parser.add_argument("--curation", type=Path, required=True)
    parser.add_argument("--text", type=Path, required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--jurisdiction")
    parser.add_argument("--document-id", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--concept-type", action="append", default=[],
                        choices=["ENTITY", "PROCESS", "RULE", "SERVICE", "DOCUMENT", "OTHER"])
    parser.add_argument("--granularity", action="append", default=[], choices=["DOMAIN", "TOPIC", "ANSWERABLE", "DETAIL"])
    parser.add_argument("--job")
    parser.add_argument("--report", type=Path, help="Packaging report path; default is beside the curation")
    args = parser.parse_args(argv)
    try:
        legacy = []
        if args.legacy_batch:
            reports = []
            dataset = TextDataset(args.text)
            for path in args.legacy_batch:
                batch_reports, migration = load_legacy_batch(path, dataset)
                reports.extend(batch_reports)
                legacy.append(migration)
        else:
            reports = load_reports(args.concepts)
        result = package_candidates(
            reports, curation_path=args.curation, text_dir=args.text, topic=args.topic, write=args.write,
            report_path=args.report, jurisdiction=args.jurisdiction, document_ids=args.document_id,
            sources=args.source, min_confidence=args.min_confidence, concept_types=args.concept_type,
            granularities=args.granularity, job=args.job)
        if legacy:
            result["legacy"] = legacy
            _atomic_text(args.report or args.curation.parent / "packaging-report.json",
                         json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(yaml.safe_dump(result["entries"], allow_unicode=True, sort_keys=False, width=110), end="")
        print(f"{'Packaged' if args.write else 'Would package'} {result['summary']['packaged']} candidates; "
              f"skipped {result['summary']['skipped']}", file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"Packaging failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
