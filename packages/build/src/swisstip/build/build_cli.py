"""Build and validate a pack's knowledge release.

    swisstip-build-release --curation releases/<pack>/curation.yaml --text .local/<pack>/text \
        --release-id <release-id> --output releases/<pack>/release.json

Writes the release and, next to it, `build-report.json` with every citation
outcome and every dropped fact. `--update-curation` writes relocated
citations and fresh anchors back into the curation file. The place files the
curation names (`place_register`, `place_aliases`) are embedded as the
release's place register.
"""

import argparse
import json
import sys
from pathlib import Path

from swisstip.core.release import dump_release

from .curation import load_curation, save_curation
from .graph_embed import graph_for
from .places import place_register_for
from .release_build import BuildError, build_release


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--curation", type=Path, required=True)
    parser.add_argument("--text", type=Path, required=True, help="text dataset of the pack's run")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", type=Path, required=True, help="release.json to write")
    parser.add_argument("--update-curation", action="store_true",
                        help="write relocated citations and anchors back into the curation file")
    args = parser.parse_args(argv)
    try:
        curation = load_curation(args.curation)
        release, report = build_release(curation, args.text, args.release_id, update_citations=args.update_curation,
                                        place_register=place_register_for(curation, args.curation),
                                        knowledge_graph=graph_for(curation, args.curation))
    except (BuildError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dump_release(release), encoding="utf-8", newline="\n")
    report_path = args.output.with_name("build-report.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    if args.update_curation:
        save_curation(args.curation, curation)
    print(json.dumps({k: v for k, v in report.items() if k != "facts_resolved"}, indent=2, ensure_ascii=False))
    return 1 if report["dropped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
