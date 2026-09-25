"""Fetch a knowledge pack from the packs repository, verify it against its readiness record and run the first tests.

    swisstip-quickstart <pack>                            fetch the pack's current release into .local/packs/<pack>,
                                                          check it and run the tests on it
    swisstip-quickstart <pack> --ref <commit|tag|branch>  the release as of that ref of the packs repository; default main
    swisstip-quickstart <pack> --serve                    the above, then serve the pack on http://127.0.0.1:8000/mcp
    swisstip-quickstart <pack> --no-fetch                 the checks and tests on the files fetched before, offline
    swisstip-quickstart --url http://127.0.0.1:8000/mcp   the round trip against a running server, such as a container

Published as swisstip-quickstart, so "uvx swisstip-quickstart <pack>" runs it without an installation and without a
clone; in a checkout, "uv run swisstip-quickstart <pack>" runs it on the checkout's own code.

The packs are published in a public GitHub repository (swisstip/swiss-tip-mvp; --repository names another) under
releases/<pack>/: the release, its readiness record, its semantic index and its acceptance suite. The command
resolves the ref to one commit, reads the pack's readiness record at that commit and fetches what the record
attests: release.json must hash to the record's release_sha256, semantic-index.json to its index binding and
acceptance.yaml to its suite binding; regression.yaml and the pack's README.md come along when the pack has them.
The files land in <packs-dir>/<pack>/ (default .local/packs/<pack>/, outside Git) with a source.json that names the
repository and commit they came from; a file whose hash already matches is not downloaded again.

The tests need no model and no network. The release validates and its readiness record names exactly this file,
the check the server's --require-ready makes; the attested semantic index, when there is one, is bound to exactly
this release; the pack's acceptance suite and its regression pack are replayed against the release with the code
of the pipeline's accept stage; and a client round trip over MCP stdio runs against the server started from this
environment. The command ends with the commands that serve the pack and connect a client. This package knows no
pack by name: the pack is the argument, or SWISSTIP_PACK, the name compose.yaml takes as well.
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml

from swisstip.core.acceptance import AcceptanceFile
from swisstip.core.readiness import Readiness, load_readiness, readiness_path, readiness_status, sha256_file
from swisstip.core.validation import ReleaseInvalid
from swisstip.mcp_server.roundtrip import roundtrip
from swisstip.mcp_server.server import MCP_PATH
from swisstip.mcp_server.server import main as serve_main
from swisstip.runtime.acceptance import check_acceptance, issues_of
from swisstip.runtime.semantic import SemanticError, load_index, semantic_index_binding
from swisstip.runtime.service import ReleaseService

DEFAULT_REPOSITORY = "swisstip/swiss-tip-mvp"
PACK_VARIABLE = "SWISSTIP_PACK"
DEFAULT_PACKS_DIR = Path(".local") / "packs"
SOURCE_FILE = "source.json"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
Fetch = Callable[[str], bytes]


class NotFound(Exception):
    """The repository has no such file at that ref."""


class QuickstartError(Exception):
    """A fetch or a verification that failed, with the reason for the user."""


def package_version() -> str:
    try:
        return version("swisstip-quickstart")
    except PackageNotFoundError:
        return "0"


def http_get(url: str, timeout: float = 120) -> bytes:
    """GET a URL; a 404 is NotFound, any other failure a QuickstartError with the URL in it."""
    headers = {"User-Agent": f"swisstip-quickstart/{package_version()}"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers),
            timeout=timeout,
            context=ssl._create_unverified_context(),
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise QuickstartError(f"{url}: HTTP {exc.code} {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QuickstartError(f"{url}: {exc}") from exc


def resolve_ref(repository: str, ref: str, fetch: Fetch) -> tuple[str, str | None]:
    """The commit the ref names, asked of the GitHub API, and a note when the API could not answer.

    A full commit ID needs no request. The API allows 60 unauthenticated requests an hour per address (GITHUB_TOKEN
    raises the limit); when it does not answer, the files are fetched at the ref itself, which is exact for a tag or
    a commit and, for a branch, holds unless something is pushed while they are fetched: the hash checks catch that."""
    if COMMIT_PATTERN.fullmatch(ref):
        return ref, None
    try:
        commit = json.loads(fetch(f"https://api.github.com/repos/{repository}/commits/{ref}"))["sha"]
    except (QuickstartError, NotFound, ValueError, KeyError, TypeError) as exc:
        return ref, f"the GitHub API did not resolve {ref} ({exc}); fetching at {ref} itself"
    if not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit):
        return ref, f"the GitHub API named no commit for {ref}; fetching at {ref} itself"
    return commit, None


def raw_url(repository: str, commit: str, pack: str, name: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{commit}/releases/{pack}/{name}"


@dataclass
class FetchedPack:
    pack: str
    repository: str
    ref: str
    commit: str
    directory: Path
    record: Readiness
    files: dict[str, str] = field(default_factory=dict)  # file name: downloaded, kept or absent
    notes: list[str] = field(default_factory=list)


def fetch_pack(pack: str, *, repository: str = DEFAULT_REPOSITORY, ref: str = "main",
               packs_dir: Path = DEFAULT_PACKS_DIR, fetch: Fetch | None = None) -> FetchedPack:
    """Fetch the files the pack's readiness record attests, at one commit, into <packs_dir>/<pack>."""
    fetch = fetch or http_get
    commit, note = resolve_ref(repository, ref, fetch)
    try:
        record_bytes = fetch(raw_url(repository, commit, pack, "readiness.json"))
    except NotFound:
        raise QuickstartError(f"{repository} at {commit} has no releases/{pack}/readiness.json: "
                              "no such pack, or a pack without an attested release") from None
    try:
        record = Readiness.model_validate_json(record_bytes)
    except ValueError as exc:
        raise QuickstartError(f"the readiness record of {pack} at {commit} does not load: {exc}") from exc
    if record.pack != pack:
        raise QuickstartError(f"the readiness record under releases/{pack} is for the pack {record.pack}")
    directory = Path(packs_dir) / pack
    directory.mkdir(parents=True, exist_ok=True)
    result = FetchedPack(pack, repository, ref, commit, directory, record)
    if note:
        result.notes.append(note)
    (directory / "readiness.json").write_bytes(record_bytes)
    # What the record attests is required and checked against its hash; the rest is taken when the pack has it.
    wanted: list[tuple[str, str | None, bool]] = [("release.json", record.release_sha256, True)]
    if record.semantic_index is not None:
        wanted.append(("semantic-index.json", record.semantic_index.file_sha256, True))
    wanted.extend([("acceptance.yaml", record.suite_file_sha256, False), ("regression.yaml", None, False),
                   ("README.md", None, False)])
    for name, expected, required in wanted:
        target = directory / name
        if expected is not None and target.is_file() and sha256_file(target) == expected:
            result.files[name] = "kept"
            continue
        try:
            data = fetch(raw_url(repository, commit, pack, name))
        except NotFound:
            if required:
                raise QuickstartError(f"{repository} at {commit} has no releases/{pack}/{name}, "
                                      "which the readiness record attests") from None
            result.files[name] = "absent"
            if target.is_file():
                target.unlink()  # from an earlier fetch of another release; it would be checked as this one's
            continue
        if expected is not None:
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise QuickstartError(f"releases/{pack}/{name} at {commit} hashes to {actual[:12]}, the readiness "
                                      f"record attests {expected[:12]}: the files were not fetched at one commit, "
                                      "or the record is stale; run the command again")
        target.write_bytes(data)
        result.files[name] = "downloaded"
    (directory / SOURCE_FILE).write_text(json.dumps(dict(
        repository=repository, ref=ref, commit=commit, pack=pack, release_id=record.release_id,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"), files=result.files), indent=2) + "\n",
        encoding="utf-8")
    return result


class Checks:
    """One line per check, as the round trip prints them; the failed labels decide the exit code."""

    def __init__(self, out=None):
        self.failures: list[str] = []
        self.out = out or sys.stdout

    def line(self, status: str, label: str) -> None:
        print(f"{status:<5}{label}", file=self.out, flush=True)

    def check(self, label: str, ok: bool) -> bool:
        self.line("ok" if ok else "FAIL", label)
        if not ok:
            self.failures.append(label)
        return ok

    def skip(self, label: str) -> None:
        self.line("skip", label)

    def info(self, label: str) -> None:
        self.line("info", label)


def check_release(directory: Path, checks: Checks) -> tuple[ReleaseService | None, Readiness | None]:
    """The release validates, its readiness record attests exactly this file, and the attested index is bound to it."""
    release_path = directory / "release.json"
    try:
        service = ReleaseService.from_file(release_path)
    except (OSError, ValueError, ReleaseInvalid) as exc:
        checks.check(f"release {release_path} validates: {exc}", False)
        return None, None
    manifest = service.release.manifest
    checks.check(f"release {manifest.release_id} validates: {len(service.topics)} topics, {len(service.concepts)} concepts, "
                 f"{len(service.facts)} facts, {len(service.evidence)} excerpts of {len(service.release.documents)} documents",
                 True)
    checks.info("facts by review status: " + ", ".join(f"{status} {count}" for status, count in manifest.review_statuses.items())
                + f"; snapshot {manifest.freshness.snapshot_date}, stale from {manifest.freshness.stale_from}")
    status = readiness_status(release_path)
    if status["status"] == "ready":
        checks.check(f"readiness record attests exactly this file: {status['attested_by']}, {status['attested_at'][:10]}, "
                     f"gates {' '.join(status['gates'])} passed", True)
    else:
        checks.check(f"readiness record attests exactly this file: {status['reason']}", False)
    record = load_readiness(readiness_path(release_path)) if readiness_path(release_path).is_file() else None
    if record is not None and record.semantic_index is not None:
        index_path = directory / "semantic-index.json"
        if not index_path.is_file():
            checks.check("semantic index: the record attests one, but the pack directory has none", False)
        else:
            try:
                index = load_index(index_path, service.release)
                binding = semantic_index_binding(index_path, index, min_score=record.semantic_index.min_score,
                                                 candidate_limit=record.semantic_index.candidate_limit)
                bound = readiness_status(release_path, semantic_index=binding)
                if bound["status"] == "ready":
                    checks.check(f"semantic index bound to this release: {index.model}, {len(index.concepts)} concepts", True)
                else:
                    checks.check(f"semantic index: {bound['reason']}", False)
            except (OSError, ValueError, SemanticError) as exc:
                checks.check(f"semantic index loads for this release: {exc}", False)
    return service, record


def describe(name: str, report: dict) -> str:
    text = (f"{name}: {report['cases']} cases replayed, "
            f"{report['blocking'] - len(report['failed'])} of {report['blocking']} blocking pass")
    if report["quarantined"]:
        text += f", {len(report['quarantined'])} quarantined ({len(report['quarantined_failed'])} failing)"
    return text


def load_suite(path: Path) -> AcceptanceFile:
    return AcceptanceFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def replay_suites(directory: Path, service: ReleaseService, record: Readiness | None, checks: Checks) -> None:
    """The acceptance suite and, when the pack has one, the regression pack, replayed lexically with no model."""
    suite_path = directory / "acceptance.yaml"
    if not suite_path.is_file():
        checks.skip("acceptance suite: the pack publishes none")
        return
    try:
        suite = load_suite(suite_path)
    except (OSError, ValueError) as exc:
        checks.check(f"acceptance suite loads: {exc}", False)
        return
    if record is not None:
        checks.check("acceptance suite is the one the readiness record attests" if suite.digest() == record.suite_sha256
                     else "acceptance suite: its digest is not the one the readiness record attests",
                     suite.digest() == record.suite_sha256)
    report = check_acceptance(service, suite)
    checks.check(describe("acceptance suite", report), report["passed"])
    for issue in issues_of(report)[:5]:
        checks.info("  " + issue)
    regression_path = directory / "regression.yaml"
    if not regression_path.is_file():
        return
    try:
        regression = load_suite(regression_path)
        # The pack replays the acceptance cases first and the regression cases after them, under the acceptance
        # suite's policy, as swisstip.build.acceptance.load_regression combines them; the hybrid-only search steps
        # are recorded without being judged, because no model is involved here.
        combined = AcceptanceFile(pack=suite.pack, policy=suite.policy, cases=suite.cases + regression.cases)
    except (OSError, ValueError) as exc:
        checks.check(f"regression pack loads: {exc}", False)
        return
    report = check_acceptance(service, combined)
    checks.check(describe("regression pack, lexical", report), report["passed"])
    for issue in issues_of(report)[:5]:
        checks.info("  " + issue)


def check_endpoint(url: str, checks: Checks, fetch: Fetch) -> None:
    """/health beside a running endpoint, then the round trip over Streamable HTTP."""
    base = url[:-len(MCP_PATH)] if url.endswith(MCP_PATH) else url.rstrip("/")
    try:
        payload = json.loads(fetch(base + "/health"))
        readiness = payload.get("readiness") or {}
        search = payload.get("search") or {}
        checks.check(f"/health: release {payload.get('release_id')}, readiness {readiness.get('status')}, "
                     f"search {search.get('configured_mode')}", payload.get("status") == "ok")
    except (QuickstartError, NotFound, ValueError, AttributeError) as exc:
        checks.check(f"/health beside {url}: {exc}", False)
    try:
        roundtrip(url=url, report=lambda label, ok: checks.check(f"round trip over Streamable HTTP: {label}", ok))
    except Exception as exc:  # noqa: BLE001 - a transport failure is one failed check, not a traceback
        checks.check(f"round trip over Streamable HTTP against {url}: {exc}", False)


def shown(path: Path) -> str:
    """A path as the user would type it: relative to the working directory when it lies under it, else absolute."""
    try:
        relative = os.path.relpath(path)
    except ValueError:  # another drive on Windows
        return str(path)
    return str(path) if relative.startswith("..") else relative


def command(name: str) -> str:
    """The console script as the user runs it: through uv run in a uv project, through uvx from the published
    package when uv made this environment for a run without a project, else by its path when it is not on PATH."""
    if os.environ.get("UV"):
        prefix = Path(sys.prefix).resolve()
        project_environment = os.environ.get("UV_PROJECT_ENVIRONMENT")
        if prefix.is_relative_to(Path.cwd().resolve()) or (
                project_environment and prefix == Path(project_environment).resolve()):
            return f"uv run {name}"
        return f"uvx {name}"
    binary = Path(sys.executable).with_name(name + (".exe" if os.name == "nt" else ""))
    if binary.is_file():
        return shown(binary.with_suffix("") if os.name == "nt" else binary)
    return name


def next_steps(pack: str, directory: Path, record: Readiness | None, repository: str) -> str:
    release = shown(directory / "release.json")
    serve = f"{command('swisstip-mcp')} --release {release} --require-ready"
    # A client chooses its own working directory, so its command names the release by its absolute path.
    client = f"{command('swisstip-mcp')} --release {(directory / 'release.json').resolve()} --require-ready"
    lines = ["Next:",
             f"  serve:   {serve} --transport streamable-http",
             f"           MCP on http://127.0.0.1:8000{MCP_PATH} with /health beside it; the same: "
             f"{command('swisstip-quickstart')} {pack} --serve",
             f"  client:  {serve} --print-client-config opencode",
             "           prints the stdio configuration of a client; Claude Code: "
             f"claude mcp add swiss-tip -- {client}"]
    if record is not None and record.semantic_index is not None:
        owner = repository.split("/")[0]
        model = record.semantic_index.model.replace(":", "-")
        lines.extend([f"  hybrid:  docker run -d --rm --name swiss-tip-embeddings -p 127.0.0.1:11434:11434 "
                      f"-e OLLAMA_HOST=0.0.0.0:11434 ghcr.io/{owner}/swiss-tip-ollama:{model}",
                      f"           then --serve --hybrid, or --semantic-index {shown(directory / 'semantic-index.json')} "
                      "on the serve command"])
    lines.append(f"  docker:  SWISSTIP_PACK={pack} docker compose up -d --wait, then "
                 f"{command('swisstip-quickstart')} --url http://127.0.0.1:8000{MCP_PATH}")
    return "\n".join(lines)


def serve(directory: Path, port: int, hybrid: bool, ollama_url: str) -> int:
    argv = ["--release", str(directory / "release.json"), "--require-ready", "--transport", "streamable-http",
            "--port", str(port)]
    if hybrid:
        argv.extend(["--semantic-index", str(directory / "semantic-index.json"), "--ollama-url", ollama_url])
    print(f"\nserving http://127.0.0.1:{port}{MCP_PATH} with /health beside it; Ctrl-C stops the server", flush=True)
    return serve_main(argv)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="swisstip-quickstart", description=__doc__.splitlines()[0])
    parser.add_argument("pack", nargs="?", default=os.environ.get(PACK_VARIABLE) or None,
                        help=f"the pack, releases/<pack> of the packs repository; default ${PACK_VARIABLE}")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, help="GitHub repository of the packs, owner/name")
    parser.add_argument("--ref", default="main", help="branch, tag or commit of that repository; default main")
    parser.add_argument("--packs-dir", type=Path, default=DEFAULT_PACKS_DIR,
                        help="where the pack lands, under <packs-dir>/<pack>; default .local/packs")
    parser.add_argument("--no-fetch", action="store_true", help="no request: check and test the files fetched before")
    parser.add_argument("--url", help="the round trip against a running Streamable HTTP endpoint, nothing else")
    parser.add_argument("--serve", action="store_true", help="after the checks, serve the pack over Streamable HTTP")
    parser.add_argument("--port", type=int, default=8000, help="with --serve; default 8000")
    parser.add_argument("--hybrid", action="store_true",
                        help="with --serve: hybrid search with the attested index and a local Ollama that holds its model")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="with --hybrid; default http://127.0.0.1:11434")
    args = parser.parse_args(argv)
    checks = Checks()
    if args.url:
        check_endpoint(args.url, checks, http_get)
        print(f"\n{len(checks.failures)} failure(s)")
        return 1 if checks.failures else 0
    if not args.pack:
        parser.error(f"name the pack to fetch, or set {PACK_VARIABLE}")
    directory = args.packs_dir / args.pack
    if args.no_fetch:
        if not (directory / "release.json").is_file():
            print(f"error: {directory} holds no release.json; run without --no-fetch first")
            return 2
        checks.info(f"{shown(directory)}: the files fetched before, no request made")
    else:
        try:
            fetched = fetch_pack(args.pack, repository=args.repository, ref=args.ref, packs_dir=args.packs_dir,
                                 fetch=http_get)
        except QuickstartError as exc:
            print(f"error: {exc}")
            return 2
        for note in fetched.notes:
            checks.info(note)
        checks.info(f"{fetched.repository} at {fetched.commit[:12]} ({fetched.ref}): releases/{args.pack}, "
                    f"release {fetched.record.release_id}, into {shown(directory)}")
        checks.info("files: " + ", ".join(
            f"{name} {state}" + (f" ({(directory / name).stat().st_size / 1e6:.1f} MB)"
                                 if state != "absent" and (directory / name).stat().st_size >= 1e6 else "")
            for name, state in fetched.files.items()))
    service, record = check_release(directory, checks)
    if service is not None:
        replay_suites(directory, service, record, checks)
        try:
            roundtrip(directory / "release.json",
                      report=lambda label, ok: checks.check(f"round trip over MCP stdio: {label}", ok))
        except Exception as exc:  # noqa: BLE001 - a transport failure is one failed check, not a traceback
            checks.check(f"round trip over MCP stdio: {exc}", False)
    print(f"\n{len(checks.failures)} failure(s)")
    if service is None:
        return 1
    print(next_steps(args.pack, directory, record, args.repository))
    if args.serve and not checks.failures:
        return serve(directory, args.port, args.hybrid, args.ollama_url)
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
