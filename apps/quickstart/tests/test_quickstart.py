"""The quickstart on the synthetic release: the packs repository is a dictionary of URLs in memory, so no test
needs the network or a knowledge base."""

import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import yaml

from swisstip.core.acceptance import AcceptanceFile
from swisstip.core.datasets import dump_dataset, load_dataset
from swisstip.core.readiness import Gate, Readiness, dump_readiness
from swisstip.core.release import load_release
from swisstip.quickstart import cli as quickstart
from swisstip.quickstart.cli import NotFound, QuickstartError, fetch_pack, resolve_ref

# The synthetic release of the server's tests, copied next to these: five concepts on example.gov pages, no pack.
FIXTURE = Path(__file__).parent / "fixtures" / "release.json"
REPOSITORY = "example/packs"
COMMIT = "a" * 40
RAW = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/releases/test/"
API = f"https://api.github.com/repos/{REPOSITORY}/commits/main"
DATASETS_API = f"https://api.github.com/repos/{REPOSITORY}/contents/datasets/test?ref={COMMIT}"
DATASETS_RAW = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/datasets/test/"
# The calendar connector's synthetic bundle, bound here to a concept of the synthetic release; the content hash
# covers the rows only, so the bundle stays valid.
BUNDLE = Path(__file__).parents[2] / "calendar-connector" / "tests" / "fixtures" / "test-waste-bioabfall" / "dataset.json"
# One case on the first concept of the synthetic release, in the suite format of docs/architecture/acceptance-gate.md.
SUITE = dict(schema_version="swiss-tip-acceptance/v1", pack="test", cases=[dict(
    case_id="T-1", label="Registration deadline", question="How soon after arriving must I register?",
    expected_answer="Within the deadline the municipality sets.",
    steps=[dict(search=dict(query="Municipal registration deadline after arrival", expect_concept="deadline")),
           dict(resolve=dict(concept_ids=["deadline"], jurisdiction={"canton": "Zurich"}, context={"population": "eu_efta"},
                             expect_status={"deadline": "SUPPORTED"}))])])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_for(release_bytes: bytes, suite_bytes: bytes | None = None) -> Readiness:
    """A readiness record with every gate passed for exactly these bytes, as the tests of the server write one."""
    manifest = load_release(FIXTURE).manifest
    suite = AcceptanceFile.model_validate(SUITE)
    return Readiness(pack="test", release_id=manifest.release_id, release_sha256=sha256(release_bytes),
                     content_sha256=manifest.content_sha256, suite_sha256=suite.digest(),
                     suite_file_sha256=sha256(suite_bytes) if suite_bytes is not None else None,
                     attested_at="2026-09-12T12:00:00Z", attested_by="Test",
                     gates=[Gate(gate=f"G{number}", title=f"test {number}", status="passed") for number in range(1, 7)],
                     cases=dict(total=1, blocking=1), review_statuses=manifest.review_statuses,
                     snapshot_date=manifest.freshness.snapshot_date, stale_from=manifest.freshness.stale_from,
                     min_runway_days=0)


def repository(with_suite: bool = True) -> dict[str, bytes]:
    """The URLs the quickstart asks for, with their bytes: the API answer for main and the pack files at the commit."""
    release_bytes = FIXTURE.read_bytes()
    files = {API: json.dumps(dict(sha=COMMIT)).encode(), RAW + "release.json": release_bytes}
    suite_bytes = None
    if with_suite:
        suite_bytes = yaml.safe_dump(SUITE, sort_keys=False).encode()
        files[RAW + "acceptance.yaml"] = suite_bytes
    files[RAW + "readiness.json"] = dump_readiness(record_for(release_bytes, suite_bytes)).encode()
    return files


def bundle(pack: str = "test", concept_id: str = "city-arrival") -> bytes:
    dataset = load_dataset(BUNDLE)
    manifest = dataset.manifest.model_copy(update=dict(pack=pack, concept_id=concept_id))
    return dump_dataset(dataset.model_copy(update=dict(manifest=manifest))).encode("utf-8")


def with_datasets(files: dict[str, bytes], data: bytes | None = None) -> dict[str, bytes]:
    """The repository with one dataset bundle under datasets/test/, as the GitHub API lists it."""
    files[DATASETS_API] = json.dumps([dict(name="test-waste-bioabfall", type="dir"), dict(name="README.md", type="file")]).encode()
    files[DATASETS_RAW + "test-waste-bioabfall/dataset.json"] = data if data is not None else bundle()
    return files


def fetcher(files: dict[str, bytes], log: list[str] | None = None):
    def fetch(url: str) -> bytes:
        if log is not None:
            log.append(url)
        if url not in files:
            raise NotFound(url)
        return files[url]
    return fetch


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp()
        self.packs = Path(self.temporary) / "packs"
        self.addCleanup(shutil.rmtree, self.temporary, True)

    def test_the_attested_files_are_fetched_at_one_commit_and_verified(self):
        files = repository()
        (self.packs / "test").mkdir(parents=True)
        (self.packs / "test" / "regression.yaml").write_text("stale: true\n", encoding="utf-8")
        log: list[str] = []
        fetched = fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs, fetch=fetcher(files, log))
        self.assertEqual((fetched.commit, fetched.notes), (COMMIT, []))
        self.assertEqual(fetched.files["release.json"], "downloaded")
        self.assertEqual(fetched.files["acceptance.yaml"], "downloaded")
        self.assertEqual(fetched.files["README.md"], "absent")
        self.assertEqual(fetched.files["regression.yaml"], "absent")
        self.assertNotIn("semantic-index.json", fetched.files, "the record attests no index, so none is asked for")
        self.assertFalse((self.packs / "test" / "regression.yaml").exists(), "a file of an earlier fetch is not kept")
        self.assertEqual((self.packs / "test" / "release.json").read_bytes(), FIXTURE.read_bytes())
        self.assertEqual((self.packs / "test" / "readiness.json").read_bytes(), files[RAW + "readiness.json"])
        source = json.loads((self.packs / "test" / "source.json").read_text(encoding="utf-8"))
        self.assertEqual((source["repository"], source["commit"], source["release_id"]), (REPOSITORY, COMMIT, "test-v1"))
        self.assertEqual(log[:2], [API, RAW + "readiness.json"])
        again = fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs, fetch=fetcher(files, log))
        self.assertEqual((again.files["release.json"], again.files["acceptance.yaml"]), ("kept", "kept"))
        self.assertEqual(log.count(RAW + "release.json"), 1, "a file whose hash matches is not downloaded again")
        self.assertEqual(again.datasets, [], "a pack without datasets/<pack> has none")

    def test_the_dataset_bundles_are_fetched_at_the_same_commit(self):
        stale = self.packs / "test" / "datasets" / "gone"
        stale.mkdir(parents=True)
        (stale / "dataset.json").write_text("{}", encoding="utf-8")
        fetched = fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs,
                             fetch=fetcher(with_datasets(repository())))
        self.assertEqual(fetched.datasets, ["test-waste-bioabfall"])
        self.assertEqual((self.packs / "test" / "datasets" / "test-waste-bioabfall" / "dataset.json").read_bytes(), bundle())
        self.assertFalse(stale.exists(), "a bundle the pack no longer has is not kept")
        source = json.loads((self.packs / "test" / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(source["datasets"], ["test-waste-bioabfall"])

        def refused(url: str) -> bytes:
            if url == DATASETS_API:
                raise QuickstartError(f"{url}: HTTP 403 rate limit exceeded")
            return fetcher(repository())(url)

        again = fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs, fetch=refused)
        self.assertIn("the datasets fetched before are kept", again.notes[-1])
        self.assertTrue((self.packs / "test" / "datasets" / "test-waste-bioabfall" / "dataset.json").is_file())

    def test_a_release_that_does_not_match_its_record_is_refused(self):
        files = repository(with_suite=False)
        files[RAW + "release.json"] = files[RAW + "release.json"] + b"\n"
        with self.assertRaises(QuickstartError) as raised:
            fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs, fetch=fetcher(files))
        self.assertIn("readiness record attests", str(raised.exception))
        self.assertFalse((self.packs / "test" / "release.json").exists())

    def test_a_pack_without_a_readiness_record_is_refused(self):
        files = {API: json.dumps(dict(sha=COMMIT)).encode()}
        with self.assertRaises(QuickstartError) as raised:
            fetch_pack("test", repository=REPOSITORY, ref="main", packs_dir=self.packs, fetch=fetcher(files))
        self.assertIn("readiness.json", str(raised.exception))

    def test_a_ref_resolves_through_the_api_or_is_used_as_it_is(self):
        log: list[str] = []
        self.assertEqual(resolve_ref(REPOSITORY, "main", fetcher(repository(False), log)), (COMMIT, None))
        self.assertEqual(resolve_ref(REPOSITORY, COMMIT, fetcher({}, log)), (COMMIT, None))
        self.assertEqual(log, [API], "a full commit ID needs no request")

        def refused(url: str) -> bytes:
            raise QuickstartError(f"{url}: HTTP 403 rate limit exceeded")

        commit, note = resolve_ref(REPOSITORY, "v1", refused)
        self.assertEqual(commit, "v1")
        self.assertIn("fetching at v1 itself", note)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp()
        self.packs = Path(self.temporary) / "packs"
        self.addCleanup(shutil.rmtree, self.temporary, True)

    def run_main(self, argv: list[str], files: dict[str, bytes] | None) -> tuple[int, str]:
        def unexpected(url: str) -> bytes:
            raise AssertionError(f"no request expected, got {url}")

        output = StringIO()
        with patch.object(quickstart, "http_get", fetcher(files) if files is not None else unexpected):
            with redirect_stdout(output):
                code = quickstart.main(argv)
        return code, output.getvalue()

    def test_the_command_fetches_checks_and_tests_a_pack(self):
        code, output = self.run_main(["test", "--repository", REPOSITORY, "--packs-dir", str(self.packs)], repository())
        self.assertEqual(code, 0, output)
        self.assertIn(f"info {REPOSITORY} at {COMMIT[:12]} (main): releases/test, release test-v1", output)
        self.assertIn("ok   release test-v1 validates: 2 topics, 5 concepts, 7 facts", output)
        self.assertIn("ok   readiness record attests exactly this file: Test, 2026-09-12, gates G1 G2 G3 G4 G5 G6 passed", output)
        self.assertIn("ok   acceptance suite is the one the readiness record attests", output)
        self.assertIn("ok   acceptance suite: 1 cases replayed, 1 of 1 blocking pass", output)
        self.assertIn("ok   round trip over MCP stdio: server swiss-tip", output)
        self.assertIn("ok   round trip over MCP stdio: an empty evidence request is a typed error", output)
        self.assertIn("\n0 failure(s)\n", output)
        self.assertIn("Next:", output)
        self.assertIn("--require-ready --transport streamable-http", output)
        self.assertNotIn("hybrid:", output, "no index is attested, so no sidecar is suggested")
        # The same files again, offline: nothing is requested.
        code, output = self.run_main(["test", "--no-fetch", "--packs-dir", str(self.packs)], None)
        self.assertEqual(code, 0, output)
        self.assertIn("the files fetched before, no request made", output)
        self.assertIn("\n0 failure(s)\n", output)

    def test_the_datasets_are_checked_and_served_beside_the_server(self):
        code, output = self.run_main(["test", "--repository", REPOSITORY, "--packs-dir", str(self.packs)],
                                     with_datasets(repository()))
        self.assertEqual(code, 0, output)
        self.assertIn("info datasets: 1 bundles into", output)
        self.assertIn("ok   datasets: 1 of 1 bundles validate and bind to concepts of the release", output)
        started: list[tuple[Path, int]] = []
        served: list[list[str]] = []

        class Process:
            def terminate(self):
                started.append(("terminated", 0))

            def wait(self, timeout=None):
                return 0

        def start(datasets: Path, port: int):
            started.append((datasets, port))
            return Process()

        with patch.object(quickstart, "start_connector", start),                 patch.object(quickstart, "serve_main", lambda argv: served.append(argv) or 0):
            code, output = self.run_main(["test", "--no-fetch", "--serve", "--packs-dir", str(self.packs)], None)
        self.assertEqual(code, 0, output)
        self.assertEqual(started, [(self.packs / "test" / "datasets", 8100), ("terminated", 0)])
        self.assertEqual(served[0][-2:], ["--connector", "http://127.0.0.1:8100"])
        self.assertIn("calendar connector on http://127.0.0.1:8100 with 1 datasets", output)
        with patch.object(quickstart, "start_connector", start),                 patch.object(quickstart, "serve_main", lambda argv: served.append(argv) or 0):
            self.run_main(["test", "--no-fetch", "--serve", "--no-calendar", "--packs-dir", str(self.packs)], None)
        self.assertNotIn("--connector", served[1])

    def test_a_bundle_of_another_pack_fails_its_check(self):
        code, output = self.run_main(["test", "--repository", REPOSITORY, "--packs-dir", str(self.packs)],
                                     with_datasets(repository(), bundle(pack="other")))
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL datasets: 0 of 1 bundles validate and bind to concepts of the release", output)
        self.assertIn("test-waste-bioabfall: bound to pack 'other', this server serves 'test'", output)

    def test_a_pack_without_a_suite_skips_the_replay(self):
        code, output = self.run_main(["test", "--repository", REPOSITORY, "--packs-dir", str(self.packs)],
                                     repository(with_suite=False))
        self.assertEqual(code, 0, output)
        self.assertIn("skip acceptance suite: the pack publishes none", output)
        self.assertIn("\n0 failure(s)\n", output)

    def test_the_command_reports_a_pack_it_cannot_fetch(self):
        code, output = self.run_main(["nothing", "--repository", REPOSITORY, "--packs-dir", str(self.packs)],
                                     {API: json.dumps(dict(sha=COMMIT)).encode()})
        self.assertEqual(code, 2)
        self.assertIn("error: example/packs at", output)
        self.assertIn("no releases/nothing/readiness.json", output)
        code, output = self.run_main(["nothing", "--no-fetch", "--packs-dir", str(self.packs)], None)
        self.assertEqual(code, 2)
        self.assertIn("run without --no-fetch first", output)

    def test_a_tampered_release_fails_the_readiness_check(self):
        code, output = self.run_main(["test", "--repository", REPOSITORY, "--packs-dir", str(self.packs)],
                                     repository(with_suite=False))
        self.assertEqual(code, 0, output)
        release = self.packs / "test" / "release.json"
        release.write_bytes(release.read_bytes() + b"\n")
        code, output = self.run_main(["test", "--no-fetch", "--packs-dir", str(self.packs)], None)
        self.assertEqual(code, 1)
        self.assertIn("FAIL readiness record attests exactly this file: the readiness record is for another release file", output)


if __name__ == "__main__":
    unittest.main()
