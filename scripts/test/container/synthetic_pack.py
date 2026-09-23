"""Write a pack directory with the synthetic test release, for the image tests: the container images of this
repository are tested on it, so that their build depends on no knowledge base.

    ./.venv/Scripts/python.exe scripts/test/container/synthetic_pack.py --out <dir>

The directory gets the release of apps/mcp-server/tests/fixtures, copied byte for byte, and a readiness record that
names exactly that file, because the images serve with --require-ready. The record is a test record: it says so in
attested_by, its six synthetic gates attest nothing about a published pack. The
directory is mounted on /srv/swiss-tip of the slim MCP image. There is no semantic index in it, so search is lexical.
"""

import argparse
import shutil
from pathlib import Path

from swisstip.core.readiness import Gate, Readiness, dump_readiness, readiness_path, sha256_file
from swisstip.core.release import load_release

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "apps" / "mcp-server" / "tests" / "fixtures" / "release.json"


def write_pack(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    release = out / "release.json"
    shutil.copyfile(FIXTURE, release)
    manifest = load_release(release).manifest
    record = Readiness(pack=manifest.pack, release_id=manifest.release_id, release_sha256=sha256_file(release),
                       content_sha256=manifest.content_sha256, suite_sha256="0" * 64, attested_at="2026-09-12T12:00:00Z",
                       attested_by="Synthetic test record (scripts/test/container/synthetic_pack.py)",
                      gates=[Gate(gate=f"G{number}", title="synthetic release image check", status="passed")
                          for number in range(1, 7)],
                       cases=dict(total=0, blocking=0), review_statuses=manifest.review_statuses,
                       snapshot_date=manifest.freshness.snapshot_date, stale_from=manifest.freshness.stale_from,
                       min_runway_days=0)
    readiness_path(release).write_text(dump_readiness(record), encoding="utf-8")
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory to write release.json and readiness.json into")
    args = parser.parse_args()
    release = write_pack(args.out.resolve())
    print(f"{release.parent}: {load_release(release).manifest.release_id} with a test readiness record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
