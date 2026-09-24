"""Build the Swiss TIP container image from this checkout's source and one pack's release, and optionally push it.

    python scripts/container/build_image.py --pack-dir ../swiss-tip-mvp/releases/<pack>            build and tag locally
    python scripts/container/build_image.py --pack-dir <dir> --dry-run                            print the docker commands only
    python scripts/container/build_image.py --pack-dir <dir> --push                               build, then push every tag

The script needs only the standard library and Docker with BuildKit. This
repository holds no pack: --pack-dir (or SWISSTIP_PACK_DIR) names the pack
directory, a checkout's releases/<pack> in the packs repository or the same
files downloaded from a GitHub release. The script reads its release.json,
passes the directory to the root Dockerfile as the named build context "pack",
passes the release ID and content digest as build arguments and labels, and
tags the image

    <image>:<release_id>                   for example <pack>-2026-09-14-v1
    <image>:content-<first 12 hex digits>  of the release content digest
    <image>:<pack>                         the pack's moving tag

The default image name is ghcr.io/<owner>/swiss-tip, with the owner taken from
the origin remote. --push refuses a working tree with uncommitted changes, so a
published image always matches a commit of this repository; the pack's own
revision is the release ID. Log in first, for example with
"docker login ghcr.io -u <user>" and a token with the write:packages scope.

The repository URL goes into org.opencontainers.image.url, not into
org.opencontainers.image.source: GitHub links a package to the repository that
label names and then shows the repository README on the package page, which is
not the image's documentation. The package page shows the Dockerfile's
description label instead.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PACK_VARIABLE = "SWISSTIP_PACK_DIR"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def repository_url() -> str:
    """The origin remote as a public https URL, without credentials or the .git suffix."""
    remote = git("remote", "get-url", "origin")
    match = re.match(r"^(?:https?://(?:[^@/]+@)?|git@)([^/:]+)[/:](.+?)(?:\.git)?/?$", remote)
    return f"https://{match.group(1)}/{match.group(2)}" if match else ""


def default_image(source: str) -> str:
    match = re.match(r"^https://github\.com/([^/]+)/", source)
    return f"ghcr.io/{match.group(1).lower()}/swiss-tip" if match else "swiss-tip"


def image_plan(manifest: dict, image: str, revision: str, source: str, created: str) -> dict:
    release_id, digest, pack = manifest["release_id"], manifest["content_sha256"], manifest["pack"]
    tags = [f"{image}:{release_id}", f"{image}:content-{digest[:12]}", f"{image}:{pack}"]
    build_args = {"RELEASE_ID": release_id, "RELEASE_CONTENT_SHA256": digest, "REVISION": revision}
    labels = {"org.opencontainers.image.created": created}
    if source:
        labels["org.opencontainers.image.url"] = source
    return dict(release_id=release_id, content_sha256=digest, pack=pack, tags=tags, build_args=build_args, labels=labels)


def build_command(plan: dict, pack_dir: Path, platform: str | None) -> list[str]:
    command = ["docker", "build", "--file", str(ROOT / "Dockerfile"), "--build-context", f"pack={pack_dir}"]
    if platform:
        command += ["--platform", platform]
    for name, value in plan["build_args"].items():
        command += ["--build-arg", f"{name}={value}"]
    for name, value in plan["labels"].items():
        command += ["--label", f"{name}={value}"]
    for tag in plan["tags"]:
        command += ["--tag", tag]
    return [*command, str(ROOT)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack-dir", type=Path, default=os.environ.get(PACK_VARIABLE) or None,
                        help=f"the pack directory holding release.json and readiness.json; default ${PACK_VARIABLE}")
    parser.add_argument("--image", help="image name without tag; default ghcr.io/<origin owner>/swiss-tip")
    parser.add_argument("--platform", help="target platform, for example linux/amd64")
    parser.add_argument("--push", action="store_true", help="push every tag after the build")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    args = parser.parse_args(argv)
    if args.pack_dir is None:
        print(f"error: no pack directory: pass --pack-dir DIR or set {PACK_VARIABLE}", file=sys.stderr)
        return 2
    pack_dir = args.pack_dir.resolve()
    release = pack_dir / "release.json"
    if not release.is_file():
        print(f"error: {release} is not a file; --pack-dir names a directory with a pack's release.json", file=sys.stderr)
        return 2

    manifest = json.loads(release.read_text(encoding="utf-8"))["manifest"]
    # The Dockerfile refuses a candidate as well; failing here saves the build and names the reason.
    readiness = release.with_name("readiness.json")
    record = json.loads(readiness.read_text(encoding="utf-8")) if readiness.is_file() else {}
    if record.get("release_sha256") != hashlib.sha256(release.read_bytes()).hexdigest():
        print(f"error: {release} has no readiness record naming this file ({readiness}); "
              "run the knowledge builder's ready stage before building an image", file=sys.stderr)
        return 2
    source = repository_url()
    dirty = bool(git("status", "--porcelain"))
    revision = git("rev-parse", "HEAD") + ("-dirty" if dirty else "")
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    plan = image_plan(manifest, args.image or default_image(source), revision, source, created)
    if args.push and dirty and not args.dry_run:
        print("refusing to push: the working tree has uncommitted changes; commit them first", file=sys.stderr)
        return 2

    commands = [build_command(plan, pack_dir, args.platform)]
    if args.push:
        commands += [["docker", "push", tag] for tag in plan["tags"]]
    print(f"pack {plan['pack']} release {plan['release_id']} content {plan['content_sha256']} "
          f"revision {revision or 'unknown'}")
    for command in commands:
        print("+ " + subprocess.list2cmdline(command), flush=True)
        if not args.dry_run and subprocess.run(command).returncode != 0:
            return 1
    print("\n".join(["tags:", *plan["tags"]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
