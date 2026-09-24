"""Build a pack: catalogue to validated release in one command.

    swisstip-build <pack> --packs-dir ../swiss-tip-mvp
                                                    run every stage on the existing run, build if curation changed
    swisstip-build <pack> --download                make a new download attempt first
    swisstip-build <pack> --until validate-text --workers 4
    swisstip-build <pack> --from build --release-id <pack>-2026-09-12-v3
    swisstip-build <pack> --from accept --until accept   just the acceptance gate, no rebuild
    swisstip-build <pack> --from accept --until ready --attested-by "A. Person"
                                                    the gate and the readiness record (readiness.json)

    swisstip-build ch --graph --packs-dir ../swiss-tip-mvp
                                                    build the knowledge graph graphs/ch: its catalogue, run,
                                                    Derive preview, compile and orientation checks
    swisstip-build ch --graph --from derive --until derive --apply-derive
                                                    write what Derive from packs adds into graph.yaml

The packs live in their own repository: `--packs-dir` (or SWISSTIP_PACKS)
names the folder that holds `releases/<pack>` and the runs under
`.local/<pack>`; this checkout holds no pack.

Stages: acquire, gaps, extract, validate-text, build, validate-release,
health, accept, ready; for a graph: acquire, gaps, extract, validate-text,
derive, compile, check. Every run writes releases/<pack>/pipeline-report.json.
Exit code 1 when a stage failed, the build dropped a fact, a blocking case of
the pack's acceptance suite failed, or a readiness gate failed; 2 on a usage
error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from .graph_pipeline import GRAPH_STAGES, GraphPipeline
from .pipeline import STAGES, Pipeline

PACKS_VARIABLE = "SWISSTIP_PACKS"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", help="pack name under <packs-dir>/releases/, or with --graph a graph under <packs-dir>/graphs/")
    parser.add_argument("--graph", action="store_true", help="build the knowledge graph graphs/<name> instead of a pack")
    parser.add_argument("--apply-derive", action="store_true", help="with --graph, write what Derive adds into graph.yaml")
    parser.add_argument("--packs-dir", type=Path, default=os.environ.get(PACKS_VARIABLE) or None,
                        help=f"the packs repository, holding releases/<pack> and .local/<pack>; default ${PACKS_VARIABLE}")
    parser.add_argument("--run-dir", type=Path, help="run directory; default releases/<pack> when it holds a run, else .local/<pack>")
    stages = list(dict.fromkeys([*STAGES, *GRAPH_STAGES]))
    parser.add_argument("--from", dest="start", choices=stages, default="acquire")
    parser.add_argument("--until", choices=stages, help="last stage; default accept for a pack, check for a graph")
    parser.add_argument("--download", action="store_true", help="make requests in the acquire stage")
    parser.add_argument("--retry-failed", action="store_true", help="with --download, retry failed targets")
    parser.add_argument("--obey-robots", action=argparse.BooleanOptionalAction, default=None,
                        help="respect robots.txt and its crawl delays in the acquire stage (default, or the value of "
                             "SWISSTIP_OBEY_ROBOTS); --no-obey-robots overrides it for hosts you are authorised to "
                             "access, and the run records the override")
    parser.add_argument("--no-source-plugins", action="store_true",
                        help="disable Fedlex and every other source plugin for this run")
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
        options = dict(run_dir=args.run_dir, download=args.download, retry_failed=args.retry_failed, workers=workers,
                       scope=args.scope, thorough=args.thorough, update_curation=args.update_curation,
                       source_plugins=not args.no_source_plugins, obey_robots=args.obey_robots)
        if args.graph:
            pipeline = GraphPipeline(args.packs_dir, args.pack, apply_derive=args.apply_derive, **options)
        else:
            pipeline = Pipeline(args.packs_dir, args.pack, release_id=args.release_id, attested_by=args.attested_by, **options)
        report = pipeline.run_stages(args.start, args.until or ("check" if args.graph else "accept"))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in report.items() if k != "stages"}, indent=2, ensure_ascii=False))
    print(f"report: {pipeline.report_path}")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
