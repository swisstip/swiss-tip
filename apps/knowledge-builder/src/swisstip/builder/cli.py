"""Build a pack: catalogue to validated release in one command.

    swisstip-build <pack> --packs-dir ../swiss-tip-mvp
                                                    run every stage on the existing run, build if curation changed
    swisstip-build <pack> --download                make a new download attempt first
    swisstip-build <pack> --until validate-text --workers 4
    swisstip-build <pack> --from build --release-id <pack>-2026-09-12-v3
    swisstip-build <pack> --from accept --until accept   just the acceptance gate, no rebuild
    swisstip-build <pack> --from accept --until ready --attested-by "A. Person"
                                                    the gate and the readiness record (readiness.json)

The packs live in their own repository: `--packs-dir` (or SWISSTIP_PACKS)
names the folder that holds `releases/<pack>` and the runs under
`.local/<pack>`; this checkout holds no pack.

Stages: acquire, gaps, extract, validate-text, build, validate-release,
health, accept, ready. Every run writes releases/<pack>/pipeline-report.json.
Exit code 1 when a stage failed, the build dropped a fact, a blocking case of
the pack's acceptance suite failed, or a readiness gate failed; 2 on a usage
error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .pipeline import STAGES, Pipeline

PACKS_VARIABLE = "SWISSTIP_PACKS"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", help="pack name under <packs-dir>/releases/")
    parser.add_argument("--packs-dir", type=Path, default=os.environ.get(PACKS_VARIABLE) or None,
                        help=f"the packs repository, holding releases/<pack> and .local/<pack>; default ${PACKS_VARIABLE}")
    parser.add_argument("--run-dir", type=Path, help="run directory; default releases/<pack> when it holds a run, else .local/<pack>")
    parser.add_argument("--from", dest="start", choices=STAGES, default="acquire")
    parser.add_argument("--until", choices=STAGES, default="accept")
    parser.add_argument("--download", action="store_true", help="make requests in the acquire stage")
    parser.add_argument("--retry-failed", action="store_true", help="with --download, retry failed targets")
    parser.add_argument("--workers", type=int, default=1, help="workers for download host groups and extraction")
    parser.add_argument("--scope", choices=("attributed", "all"), default="attributed", help="extraction scope")
    parser.add_argument("--thorough", action="store_true", help="re-hash every saved response in the validation stages")
    parser.add_argument("--update-curation", action="store_true", help="write relocated citations and anchors back into curation.yaml")
    parser.add_argument("--release-id", help="release identity; default: keep the current one, or bump it when inputs changed")
    parser.add_argument("--attested-by", help="the person attesting the release; required by the ready stage")
    args = parser.parse_args(argv)
    if args.packs_dir is None:
        print(f"error: no packs directory: pass --packs-dir DIR or set {PACKS_VARIABLE}", file=sys.stderr)
        return 2
    workers = max(1, min(args.workers, os.cpu_count() or 1))
    try:
        pipeline = Pipeline(args.packs_dir, args.pack, run_dir=args.run_dir, release_id=args.release_id, download=args.download,
                            retry_failed=args.retry_failed, workers=workers, scope=args.scope, thorough=args.thorough,
                            update_curation=args.update_curation, attested_by=args.attested_by)
        report = pipeline.run_stages(args.start, args.until)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in report.items() if k != "stages"}, indent=2, ensure_ascii=False))
    print(f"report: {pipeline.report_path}")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
