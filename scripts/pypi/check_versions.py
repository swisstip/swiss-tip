"""Check that every component states one and the same version, and print it.

    ./.venv/Scripts/python.exe scripts/pypi/check_versions.py
    ./.venv/Scripts/python.exe scripts/pypi/check_versions.py --expect 0.3.0rc1

The three distributions take their version from the command line of
build_distributions.py, which the workflow fills from the tag that publishes
them, never from the component files. A component left at the previous
version would therefore be published under the tag's version without anything
noticing. This check is that guard: every packages/*/pyproject.toml and
apps/*/pyproject.toml must state the same version, and SERVER_VERSION of the
MCP server, which it reports to clients in the initialize result and on
/health, must be that version too.

With --expect the release part of the given version must be the committed one,
so a tag v0.3.0 on a checkout that still says 0.2.5 fails the run; a
pre-release or development suffix (0.3.0rc1, 0.3.0.dev0) is accepted for the
committed 0.3.0. The version alone goes to stdout, so a shell can read it;
everything else goes to stderr. The standard library is all it needs.
"""

import argparse
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SERVER_VERSION = ROOT / "apps" / "mcp-server" / "src" / "swisstip" / "mcp_server" / "__init__.py"


def stated_versions() -> dict[str, str]:
    """Every place in the repository that states the version, by the path that states it."""
    versions = {}
    for pattern in ("packages/*/pyproject.toml", "apps/*/pyproject.toml"):
        for path in sorted(ROOT.glob(pattern)):
            with path.open("rb") as handle:
                versions[path.relative_to(ROOT).as_posix()] = tomllib.load(handle)["project"]["version"]
    found = re.search(r'^SERVER_VERSION\s*=\s*"([^"]+)"', SERVER_VERSION.read_text(encoding="utf-8"), re.MULTILINE)
    if found is None:
        raise SystemExit(f"{SERVER_VERSION.relative_to(ROOT).as_posix()} states no SERVER_VERSION")
    versions[SERVER_VERSION.relative_to(ROOT).as_posix() + ":SERVER_VERSION"] = found.group(1)
    return versions


def release_part(version: str) -> str:
    """The release segment of a PEP 440 version: 0.3.0rc1 and 0.3.0.dev0 are both 0.3.0."""
    found = re.match(r"\d+(?:\.\d+)*", version)
    if found is None:
        raise SystemExit(f"{version} does not begin with a release segment; it is no PEP 440 version")
    return found.group(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect", help="the version being built; its release part must be the committed one")
    args = parser.parse_args(argv)

    versions = stated_versions()
    distinct = sorted(set(versions.values()))
    if len(distinct) != 1:
        for path, version in versions.items():
            print(f"  {version:12} {path}", file=sys.stderr)
        raise SystemExit(f"the components state {len(distinct)} different versions: {', '.join(distinct)}")
    committed = distinct[0]
    if args.expect and release_part(args.expect) != committed:
        raise SystemExit(f"the build names {args.expect} and the {len(versions)} component files say {committed}; "
                         "bump them together, or tag the version they state")
    print(f"{len(versions)} component files state {committed}"
          + (f", built as {args.expect}" if args.expect and args.expect != committed else ""), file=sys.stderr)
    print(committed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
