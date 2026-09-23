"""Build and validate one dataset bundle of a pack.

    swisstip-build-dataset --packs-dir ../swiss-tip-mvp --pack <pack> --dataset <dataset_id>

Reads `datasets/<pack>/<dataset_id>/dataset.yaml` under the packs directory,
downloads the sources it does not find under `.local/<pack>/datasets/<dataset_id>/`
(the one step of the pipeline with network), writes `dataset.json` and
`build-report.json` next to the curation file, and writes the pins of a
first download back into it. A source whose bytes differ from the pinned
hash stops the build; `--refresh` accepts the publisher's new file and
re-pins it. `--offline` fails instead of downloading.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from swisstip.core.datasets import dump_dataset

from .datasets import BuildError, build_dataset, fetch_url, load_dataset_curation, save_dataset_curation

PACKS_VARIABLE = "SWISSTIP_PACKS"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--packs-dir", type=Path, default=os.environ.get(PACKS_VARIABLE) or None,
                        help=f"the packs repository checkout; default ${PACKS_VARIABLE}")
    parser.add_argument("--pack", required=True, help="pack name under <packs-dir>/datasets/")
    parser.add_argument("--dataset", required=True, help="dataset_id, the directory under <packs-dir>/datasets/<pack>/")
    parser.add_argument("--sources-dir", type=Path, help="where the downloaded files are kept; default .local/<pack>/datasets/<dataset>")
    parser.add_argument("--version", type=int, default=1, help="the v<n> of the dataset version; raise it for a rebuild of the same download")
    parser.add_argument("--refresh", action="store_true", help="accept a source whose bytes differ from the pinned hash and re-pin it")
    parser.add_argument("--offline", action="store_true", help="never download; fail when a source file is not at hand")
    args = parser.parse_args(argv)
    if args.packs_dir is None:
        print(f"error: no packs directory: pass --packs-dir DIR or set {PACKS_VARIABLE}", file=sys.stderr)
        return 2
    folder = args.packs_dir / "datasets" / args.pack / args.dataset
    curation_path = folder / "dataset.yaml"
    sources_dir = args.sources_dir or args.packs_dir / ".local" / args.pack / "datasets" / args.dataset
    try:
        curation = load_dataset_curation(curation_path)
        if curation.dataset_id != args.dataset or curation.pack != args.pack:
            raise BuildError(f"{curation_path} is the curation of {curation.pack}/{curation.dataset_id}, not of {args.pack}/{args.dataset}")
        dataset, report = build_dataset(curation, sources_dir, fetch=None if args.offline else fetch_url,
                                        refresh=args.refresh, version=args.version)
    except (BuildError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    (folder / "dataset.json").write_text(dump_dataset(dataset), encoding="utf-8", newline="\n")
    (folder / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    save_dataset_curation(curation_path, curation)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
