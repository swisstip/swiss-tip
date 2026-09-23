from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from swisstip.ingestion import SafeCrawler  # noqa: E402
from swisstip.ingestion import acquisition, download_cli  # noqa: E402
from swisstip.ingestion.acquisition import (  # noqa: E402
    NetworkBudgetExceeded, budget_usage, catalogue_targets, complete_network_attempt, markdown_targets,
    read_json, registry_targets, reserve_network_attempt, saved_and_intact, snapshot, summary, url_id,
)
from test_crawler import FakeOpener, FakeResponse, public_resolver  # noqa: E402




def registry(tmp: Path, entries: list[dict] | None = None) -> Path:
    data = {
        "schema_version": "source-catalog/v1", "artifact_id": "test-sources", "version": "draft-1",
        "knowledge_space_id": "hackathon", "title": "Test", "status": "SOURCES_ONLY",
        "scope": {"country_code": "CH", "canton_codes": ["CH-ZH"], "description": "", "exclusions": []},
        "planning_topics": [{"topic_id": "residence-permits", "label": "Residence permits"}],
        "language_discovery": {"preferred_seed_language": "de"},
        "crawl_profiles": {},
        "scan_sets": {"smoke": ["official-start"], "all": ["official-start", "official-review"]},
        "sources": entries or [
            source_entry("official-start", "https://official.example/allowed/start"),
            source_entry("official-review", "https://official.example/allowed/review", scan_status="needs_access_review"),
        ],
    }
    path = tmp / "sources.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def source_entry(source_id: str, url: str, *, scan_status: str = "ready") -> dict:
    return {
        "definition": {"source_id": source_id, "start_url": url, "allowed_hosts": ["official.example"],
                       "allowed_path_prefixes": ["/allowed/"], "canonical_authority": "Official Test Authority",
                       "jurisdiction": "CH", "language": "de"},
        "title": f"Title of {source_id}", "authority_level": "federal", "source_kind": "official_guidance",
        "priority": "P0", "topic_hints": ["residence-permits"],
        "discovery": {"method": "official_page_link", "reference_url": url, "located_on": "2026-09-06"},
        "scan_status": scan_status, "notes": "test",
    }


def fake_crawler(responses: dict[str, FakeResponse]):
    """Bind SafeCrawler to canned responses and a public DNS answer; no network."""
    opener = FakeOpener(responses)

    def factory(*args, **kwargs):
        kwargs.pop("opener", None)
        return SafeCrawler(*args, opener=opener, resolver=public_resolver, **kwargs)

    return factory, opener


class CatalogueTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def test_markdown_links_are_distinct_defragmented_and_ordered(self) -> None:
        path = self.tmp / "sources.md"
        path.write_text("Intro [A](https://a.example/x#top) and [B](https://b.example/y)\n"
                        "| [A again](https://a.example/x) | [C](https://a.example/x/) |\n", encoding="utf-8")
        targets = markdown_targets(path)
        self.assertEqual([t["url"] for t in targets], ["https://a.example/x", "https://b.example/y", "https://a.example/x/"])
        self.assertEqual([r["label"] for r in targets[0]["references"]], ["A", "A again"])
        self.assertEqual(targets[0]["references"][1]["catalogue_line"], 2)
        self.assertEqual(targets[0]["references"][0]["catalogue"], "sources.md")
        self.assertEqual(targets[0]["url_id"], hashlib.sha256(b"https://a.example/x").hexdigest())

    def test_registry_targets_carry_title_line_and_entry(self) -> None:
        path = registry(self.tmp)
        entries = read_json(path)["sources"]
        targets = registry_targets(path, entries)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0]["references"][0]["label"], "Title of official-start")
        self.assertEqual(targets[0]["references"][0]["catalogue"], "sources.json")
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertIn('"https://official.example/allowed/start"', lines[targets[0]["references"][0]["catalogue_line"] - 1])
        self.assertEqual(targets[0]["registry_entries"][0]["definition"]["source_id"], "official-start")

    def test_markdown_links_come_first_and_registry_metadata_is_attached_by_url(self) -> None:
        path = registry(self.tmp)
        markdown = self.tmp / "sources.md"
        markdown.write_text("[Extra](https://official.example/allowed/extra)\n[Seed](https://official.example/allowed/start)\n",
                            encoding="utf-8")
        targets = catalogue_targets(path, read_json(path)["sources"], markdown)
        self.assertEqual([t["url"].rsplit("/", 1)[1] for t in targets], ["extra", "start", "review"])
        self.assertEqual(targets[0]["registry_entries"][0]["definition"]["source_id"], "official-start")
        self.assertEqual([r["label"] for r in targets[1]["references"]], ["Seed", "Title of official-start"])
        self.assertEqual(targets[1]["registry_entries"][0]["definition"]["source_id"], "official-start")

    def test_markdown_links_outside_allowlists_or_without_https_are_refused(self) -> None:
        path = registry(self.tmp)
        entries = read_json(path)["sources"]
        for url in ("https://other.example/allowed/extra", "https://official.example/outside/extra",
                    "http://official.example/allowed/extra"):
            with self.subTest(url=url):
                markdown = self.tmp / "sources.md"
                markdown.write_text(f"[Unapproved]({url})\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    catalogue_targets(path, entries, markdown)


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = Path(directory.name)
        self.target = {"url": "https://official.example/allowed/start", "url_id": url_id("https://official.example/allowed/start"),
                       "references": [{"label": "Start"}], "registry_entries": []}

    def run_snapshot(self, responses: dict[str, FakeResponse]) -> dict:
        factory, _ = fake_crawler(responses)
        with patch.object(acquisition, "SafeCrawler", factory), patch("swisstip.ingestion.crawler.time.sleep"):
            return snapshot(self.target, self.output, ("official.example",))

    def test_saved_html_gets_manifest_latest_and_shell_flag(self) -> None:
        body = b"<html><title>Shell</title><body><app-root></app-root></body></html>"
        result = self.run_snapshot({
            "https://official.example/robots.txt": FakeResponse(404, b"", content_type="text/plain"),
            "https://official.example/allowed/start": FakeResponse(200, body),
        })
        self.assertEqual(result["status"], "saved")
        snapshot_record = result["snapshots"][0]
        self.assertEqual(snapshot_record["relative_path"], f"pages/{self.target['url_id']}/attempt-001/response.html")
        self.assertEqual(snapshot_record["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(snapshot_record["review_flags"], ["javascript_application_shell"])
        self.assertEqual(snapshot_record["processing_status"], "NOT_PROCESSED")
        self.assertEqual((self.output / snapshot_record["relative_path"]).read_bytes(), body)
        folder = self.output / "pages" / self.target["url_id"]
        self.assertEqual(read_json(folder / "attempt-001/manifest.json"), read_json(folder / "latest.json"))
        self.assertTrue(saved_and_intact(result, self.output))
        (self.output / snapshot_record["relative_path"]).write_bytes(b"tampered")
        self.assertFalse(saved_and_intact(result, self.output))

    def test_error_pages_served_with_success_are_flagged_by_title_or_main_heading(self) -> None:
        # The ch.ch shell of 10 and 11 September 2026: no <title>, the error in the heading.
        chch = b'<html><body><div><h1>Error Page (404) </h1></div><h2 class="title">Error Page (404) </h2></body></html>'
        self.assertEqual(acquisition.review_flags(chch, ".html"), ["error_page_title"])
        self.assertEqual(acquisition.review_flags(b"<title>Seite nicht gefunden</title>", ".html"), ["error_page_title"])
        self.assertEqual(acquisition.review_flags(b"<h1><span>404 - Not Found</span></h1>", ".html"), ["error_page_title"])
        ordinary = b"<title>Aufenthalt</title><h1>Was tun, wenn die Seite nicht gefunden wird?</h1>"
        self.assertEqual(acquisition.review_flags(ordinary, ".html"), [])
        self.assertEqual(acquisition.review_flags(chch, ".pdf"), [])

    def test_pdf_documents_are_saved_by_signature_and_failures_keep_a_manifest(self) -> None:
        pdf = b"%PDF-1.7 fake"
        result = self.run_snapshot({
            "https://official.example/robots.txt": FakeResponse(404, b"", content_type="text/plain"),
            "https://official.example/allowed/start": FakeResponse(200, pdf, content_type="application/pdf"),
        })
        self.assertEqual(result["snapshots"][0]["relative_path"].rsplit("/", 1)[1], "response.pdf")
        failed = self.run_snapshot({"https://official.example/robots.txt": FakeResponse(200, b"User-agent: *\nDisallow: /\n", content_type="text/plain")})
        self.assertEqual(failed["status"], "not_saved")
        self.assertEqual(failed["report"]["skipped"][0]["reason"], "robots-disallowed")
        self.assertTrue((self.output / "pages" / self.target["url_id"] / "attempt-002/manifest.json").is_file())
        self.assertEqual(read_json(self.output / "pages" / self.target["url_id"] / "latest.json")["status"], "not_saved")


class SummaryTests(unittest.TestCase):
    def test_summary_counts_pending_saved_and_supplements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            saved = {"url": "https://a.example/x", "url_id": url_id("https://a.example/x"), "references": [{"label": "A"}]}
            pending = {"url": "https://a.example/y", "url_id": url_id("https://a.example/y"), "references": []}
            page = output / "pages" / saved["url_id"] / "attempt-001"
            page.mkdir(parents=True)
            (page / "response.html").write_bytes(b"<html/>")
            acquisition.write_json(page.parent / "latest.json", {**saved, "status": "saved", "snapshots": [
                {"relative_path": f"pages/{saved['url_id']}/attempt-001/response.html", "bytes_downloaded": 7,
                 "sha256": hashlib.sha256(b"<html/>").hexdigest(), "review_flags": []}]})
            documents = output / "fedlex-documents"
            documents.mkdir()
            acquisition.write_json(documents / "summary.json", {"counts": {"saved": 2}, "saved_bytes": 10})
            value = summary(output, {"catalogue_sha256": "abc", "targets": [saved, pending]})
            self.assertEqual(value["counts"], {"saved": 1, "pending": 1})
            self.assertEqual(value["saved_bytes"], 7)
            self.assertEqual(value["supplements"][0]["relative_path"], "fedlex-documents/summary.json")
            readme = (output / "README.md").read_text(encoding="utf-8")
            self.assertIn("| [A](https://a.example/x) | saved |", readme)
            self.assertIn("| [https://a.example/y](https://a.example/y) | pending | pending |", readme)
            self.assertEqual(read_json(output / "summary.json")["schema_version"], "swisstip.catalogue-download/v1")

    def test_cumulative_budget_charges_crashed_and_retried_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            plan = {"catalogue_sha256": "a" * 64,
                    "network_budget": {"max_requests": 3, "max_bytes": 100},
                    "targets": []}
            target = {"url": "https://official.example/allowed/start"}
            crashed_id, crashed_limits = reserve_network_attempt(output, plan, target)
            self.assertEqual((crashed_limits.max_requests, crashed_limits.max_total_bytes), (3, 100))
            self.assertEqual(budget_usage(output, plan)["charged_requests"], 3)
            with self.assertRaisesRegex(NetworkBudgetExceeded, "exhausted"):
                reserve_network_attempt(output, plan, target)

            ledger = read_json(output / "network-budget.json")
            ledger["attempts"][0].update(actual_requests=1, actual_bytes=10, completed_at="2026-09-12T00:00:00+00:00")
            acquisition.write_json(output / "network-budget.json", ledger)
            retry_id, retry_limits = reserve_network_attempt(output, plan, target)
            self.assertEqual((retry_limits.max_requests, retry_limits.max_total_bytes), (2, 90))
            complete_network_attempt(output, plan, retry_id,
                                     {"report": {"requests_sent": 2, "bytes_downloaded": 20}})
            usage = budget_usage(output, plan)
            self.assertEqual((usage["charged_requests"], usage["charged_bytes"]), (3, 30))
            with self.assertRaisesRegex(NetworkBudgetExceeded, "exhausted"):
                reserve_network_attempt(output, plan, target)


class DownloadCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.catalogue = registry(self.tmp)
        self.output = self.tmp / "run"

    def run_cli(self, *extra: str) -> int:
        with redirect_stdout(io.StringIO()):
            return download_cli.main(["--catalogue", str(self.catalogue), "--output", str(self.output), *extra])

    def test_plan_only_writes_plan_inputs_and_summary_without_requests(self) -> None:
        with patch.object(download_cli, "snapshot") as fetch:
            self.assertEqual(self.run_cli("--set", "all"), 0)
            fetch.assert_not_called()
        plan = read_json(self.output / "plan.json")
        self.assertEqual(plan["schema_version"], "swisstip.catalogue-download-plan/v1")
        self.assertEqual([t["url"].rsplit("/", 1)[1] for t in plan["targets"]], ["start", "review"])
        self.assertEqual(plan["targets"][0]["allowed_redirect_hosts"],
                 ["official.example", "www.official.example"])
        self.assertEqual(plan["targets"][0]["allowed_path_prefixes"], ["/allowed/"])
        self.assertEqual(plan["selection"], {"scan_set": "all", "source_ids": None, "selected_source_count": 2})
        self.assertEqual(plan["catalogue_sha256"], hashlib.sha256(self.catalogue.read_bytes()).hexdigest())
        self.assertEqual((self.output / "catalogue.json").read_bytes(), self.catalogue.read_bytes())
        self.assertEqual(read_json(self.output / "plugin-plan.json")["enabled"][0]["id"], "fedlex")
        self.assertEqual(read_json(self.output / "summary.json")["counts"], {"pending": 2})
        self.assertEqual(self.run_cli("--source", "official-start"), 0, "an existing plan is reused as it is")
        self.assertEqual(len(read_json(self.output / "plan.json")["targets"]), 2)
        self.catalogue.write_text(self.catalogue.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self.run_cli()

    def test_download_skips_intact_pages_not_ready_seeds_and_retries_on_request(self) -> None:
        calls: list[str] = []

        def fake_snapshot(target, output, allowed_hosts=None, transport="urllib"):
            calls.append(target["url"])
            attempt = len(list((output / "pages" / target["url_id"]).glob("attempt-*"))) + 1
            path = output / "pages" / target["url_id"] / f"attempt-{attempt:03d}" / "response.html"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"<html/>")
            result = {**target, "status": "saved" if target["url"].endswith("start") else "not_saved", "snapshots": [] if
                      target["url"].endswith("review") else [{
                          "relative_path": path.relative_to(output).as_posix(), "review_flags": [],
                          "sha256": hashlib.sha256(b"<html/>").hexdigest(), "bytes_downloaded": 7,
                          "requested_url": target["url"], "final_url": target["url"],
                          "retrieved_at": "2026-09-12T00:00:00+00:00", "content_type": "text/html"}], "report": {}}
            acquisition.write_json(path.parent / "manifest.json", result)
            acquisition.write_json(path.parent.parent / "latest.json", result)
            return result

        with patch.object(download_cli, "snapshot", side_effect=fake_snapshot), \
                patch("swisstip.ingestion.download_cli.time.sleep"), \
                patch("swisstip.ingestion.plugin_downloads.run_source_plugins", return_value=[]):
            self.assertEqual(self.run_cli("--download"), 1, "the not-ready seed stays pending")
            self.assertEqual(calls, ["https://official.example/allowed/start"])
            self.assertEqual(self.run_cli("--download", "--include-not-ready"), 1)
            self.assertEqual(calls[1:], ["https://official.example/allowed/review"], "saved pages are not fetched again")
            self.assertEqual(self.run_cli("--download", "--include-not-ready"), 1)
            self.assertEqual(len(calls), 2, "failed pages are not retried without --retry-failed")
            self.assertEqual(self.run_cli("--download", "--include-not-ready", "--retry-failed"), 1)
            self.assertEqual(calls[2:], ["https://official.example/allowed/review"])
        self.assertEqual(read_json(self.output / "summary.json")["counts"], {"saved": 1, "not_saved": 1})

    def test_retry_failed_refetches_a_saved_error_page(self) -> None:
        self.run_cli("--source", "official-start")
        target = read_json(self.output / "plan.json")["targets"][0]
        body = b"<html><h1>Error Page (404)</h1></html>"
        path = self.output / "pages" / target["url_id"] / "attempt-001" / "response.html"
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
        # Imported manifests carry no review flags; the saved bytes decide.
        saved = {**target, "status": "saved", "report": {}, "snapshots": [{
            "relative_path": path.relative_to(self.output).as_posix(), "review_flags": [],
            "sha256": hashlib.sha256(body).hexdigest(), "bytes_downloaded": len(body)}]}
        acquisition.write_json(path.parent / "manifest.json", saved)
        acquisition.write_json(path.parent.parent / "latest.json", saved)
        with patch.object(download_cli, "snapshot", return_value=saved) as fetch, \
                patch("swisstip.ingestion.plugin_downloads.run_source_plugins", return_value=[]):
            self.run_cli("--download")
            fetch.assert_not_called()
            self.run_cli("--download", "--retry-failed")
            self.assertEqual([call.args[0]["url"] for call in fetch.call_args_list], [target["url"]])


if __name__ == "__main__":
    unittest.main()
