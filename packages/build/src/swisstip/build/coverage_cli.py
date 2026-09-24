"""Report which content sections of a pack's text dataset no fact cites and no curator dispositioned.

    swisstip-coverage --release releases/<pack>/release.json --text .local/<pack>/text \
        --curation releases/<pack>/curation.yaml --dispositions releases/<pack>/curation-coverage.yaml \
        --catalogue .local/<pack>/catalogue.json --output releases/<pack>/curation-coverage.json

Writes the JSON report and a Markdown page next to it. Exit code 1 when the
report is not clean and the curation's `coverage_policy` is `enforce`; 0 under
the default `report` policy, whatever the report says. No request, no model.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.release import load_release

from .coverage import build_coverage, load_dispositions, sha256_file, write_report
from .curation import load_curation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--text", type=Path, required=True, help="text dataset of the pack's run")
    parser.add_argument("--curation", type=Path, help="curation.yaml, for coverage_policy and boilerplate_min_pages; defaults report and 5")
    parser.add_argument("--dispositions", type=Path, help="curation-coverage.yaml with the curator's dispositions")
    parser.add_argument("--catalogue", type=Path, help="the run's catalogue.json (or sources.json), for the source roll-up")
    parser.add_argument("--output", type=Path, required=True, help="curation-coverage.json to write; the .md goes next to it")
    args = parser.parse_args(argv)
    try:
        release = load_release(args.release)
        curation = load_curation(args.curation) if args.curation else None
        policy = curation.coverage_policy if curation else "report"
        dispositions = load_dispositions(args.dispositions) if args.dispositions and args.dispositions.is_file() else None
        catalogue = json.loads(args.catalogue.read_text(encoding="utf-8")) if args.catalogue and args.catalogue.is_file() else None
        report = build_coverage(release, args.text, policy=policy, dispositions=dispositions, catalogue=catalogue,
                                dispositions_sha256=sha256_file(args.dispositions) if dispositions else None,
                                min_pages=curation.boilerplate_min_pages if curation else None)
    except (ValidationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(report, args.output)
    print(json.dumps(dict(release_id=report["release_id"], policy=report["policy"], clean=report["clean"],
                          counts=report["counts"]), indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
