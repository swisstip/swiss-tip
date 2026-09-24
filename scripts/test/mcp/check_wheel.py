"""Round trip against any release, with no knowledge of its content: what the package build runs on the installed
wheel, and the image build on the slim MCP image, with a synthetic release, so that neither depends on a knowledge base.

    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py
    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py --release releases/<pack>/release.json
    ./.venv/Scripts/python.exe scripts/test/mcp/check_wheel.py --url http://127.0.0.1:8000/mcp

Without --url the server is started over stdio with the interpreter that runs this script, so it tests the packages
installed there; with --url the round trip runs against a running Streamable HTTP endpoint, a container. The round
trip itself is swisstip.mcp_server.roundtrip, which the quickstart runs on a fetched pack as well: it takes the first
concept of the first topic from get_coverage, finds it again with search by its label, resolves it for its own
jurisdiction with the first allowed value of every required context field, reads the evidence of a served fact and
provokes a typed error. The checks of the published packs live with the packs, in swiss-tip-mvp.
"""

import argparse
from pathlib import Path

from swisstip.mcp_server.roundtrip import roundtrip

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "apps" / "mcp-server" / "tests" / "fixtures" / "release.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", type=Path, default=FIXTURE, help="release.json to serve; default: the synthetic test release")
    parser.add_argument("--url", help="Streamable HTTP endpoint of a running server, instead of starting one over stdio")
    args = parser.parse_args()
    failures = roundtrip(args.release, args.url)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
