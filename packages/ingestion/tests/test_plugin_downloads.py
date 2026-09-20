from contextlib import redirect_stdout
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from swisstip.ingestion.acquisition import read_json, url_id, write_json  # noqa: E402
from swisstip.ingestion.fedlex import FedlexPlugin  # noqa: E402
from swisstip.ingestion.plugin_downloads import MetadataFetcher, run_source_plugins  # noqa: E402
from swisstip.ingestion.plugins import (  # noqa: E402
    PluginRegistry, SourceDocument, SourcePlugin, SourceRequest, load_source_plugins, validate_documents,
)


class ExamplePlugin(SourcePlugin):
    plugin_id = "example"
    version = "1"
    metadata_hosts = ("example.gov",)
    document_hosts = ("example.gov",)

    def matches(self, url):
        return url == "https://example.gov/law"

    def resolve(self, request, fetch_json):
        metadata = fetch_json("https://example.gov/metadata")
        return [SourceDocument(metadata["url"], "text/html", "en", version_uri="2026-09-10")]


class PluginDownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        write_json(self.root / "plan.json", {"created_at": "2026-09-10T00:00:00Z",
                   "catalogue_sha256": "abc", "targets": [{"url": "https://example.gov/law",
                   "references": [{"label": "Law"}]}]})

    def cache_metadata(self, body=b'{"url":"https://example.gov/law.html"}'):
        output = self.root / "example-documents"
        folder = output / "metadata"
        folder.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(b"https://example.gov/metadata").hexdigest()
        (folder / f"{key}.json").write_bytes(body)
        write_json(folder / f"{key}.request.json", {"sha256": hashlib.sha256(body).hexdigest()})
        return output, folder / f"{key}.json"

    def fake_snapshot(self, target, output, hosts, transport):
        path = output / "pages" / target["url_id"] / "attempt-001" / "response.html"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"<html><title>Law</title><p>A residence permit is required.</p></html>")
        result = {**target, "status": "saved", "snapshots": [{
            "relative_path": path.relative_to(output).as_posix(), "review_flags": [],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes_downloaded": path.stat().st_size,
            "requested_url": target["url"], "final_url": target["url"],
            "retrieved_at": "2026-09-10T00:00:00Z", "content_type": "text/html"}]}
        write_json(path.parent / "manifest.json", result)
        write_json(path.parent.parent / "latest.json", result)
        return result

    def test_second_run_resumes_and_targets_carry_provenance(self):
        self.cache_metadata()
        registry = PluginRegistry([ExamplePlugin()])
        with patch("swisstip.ingestion.plugin_downloads.snapshot", side_effect=self.fake_snapshot) as save, \
                patch("swisstip.ingestion.plugin_downloads.time.sleep"), redirect_stdout(io.StringIO()):
            reports = run_source_plugins(self.root, registry)
            self.assertEqual(reports[0]["counts"], {"saved": 1})
            run_source_plugins(self.root, registry)
            save.assert_called_once()
        plan = read_json(self.root / "example-documents/plan.json")
        self.assertEqual(plan["source_plugin"], {"id": "example", "version": "1", "api_version": 1})
        self.assertEqual(plan["resolutions"], {"https://example.gov/law": ["https://example.gov/law.html"]})
        target = plan["targets"][0]
        self.assertEqual(target["source_page_url"], "https://example.gov/law")
        self.assertEqual(target["version_uri"], "2026-09-10")
        self.assertEqual(len(target["resolution_metadata"]), 1)
        self.assertTrue((self.root / "example-documents/README.md").is_file())

    def test_resolution_errors_are_persisted_and_explicit_retry_recovers(self):
        self.cache_metadata(b"{}")
        registry = PluginRegistry([ExamplePlugin()])
        report = run_source_plugins(self.root, registry)[0]
        self.assertEqual(len(report["resolution_errors"]), 1)
        self.cache_metadata()
        self.assertTrue(run_source_plugins(self.root, registry)[0]["resolution_errors"])
        with patch("swisstip.ingestion.plugin_downloads.snapshot", side_effect=self.fake_snapshot), \
                patch("swisstip.ingestion.plugin_downloads.time.sleep"), redirect_stdout(io.StringIO()):
            report = run_source_plugins(self.root, registry, retry_failed=True)[0]
        self.assertEqual(report["resolution_errors"], [])
        self.assertEqual(report["counts"], {"saved": 1})

    def test_legacy_archive_without_plugin_identity_is_kept_when_intact(self):
        output = self.root / "example-documents"
        target = {"url": "https://example.gov/law.html", "url_id": url_id("https://example.gov/law.html"),
                  "references": [{"label": "Law"}], "source_page_url": "https://example.gov/law"}
        self.fake_snapshot(target, output, (), "urllib")
        write_json(output / "plan.json", {"created_at": "2026-09-10T00:00:00Z", "catalogue_sha256": "old",
                                          "targets": [target]})
        registry = PluginRegistry([ExamplePlugin()])
        with patch("swisstip.ingestion.plugin_downloads.snapshot") as save:
            report = run_source_plugins(self.root, registry)[0]
            save.assert_not_called()
        self.assertEqual(report["counts"], {"saved": 1})
        (output / "pages" / target["url_id"] / "attempt-001" / "response.html").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Incomplete legacy archive"):
            run_source_plugins(self.root, registry)

    def test_legacy_archive_adopts_plugin_identity_when_the_catalogue_gains_sources(self):
        output = self.root / "example-documents"
        archived = {"url": "https://example.gov/old-law.html", "url_id": url_id("https://example.gov/old-law.html"),
                    "references": [{"label": "Old law"}], "source_page_url": "https://example.gov/old-law"}
        self.fake_snapshot(archived, output, (), "urllib")
        write_json(output / "plan.json", {"created_at": "2026-09-10T00:00:00Z", "catalogue_sha256": "old",
                                          "targets": [archived], "imported_from": ["earlier-run"]})
        self.cache_metadata()
        registry = PluginRegistry([ExamplePlugin()])
        with patch("swisstip.ingestion.plugin_downloads.snapshot", side_effect=self.fake_snapshot) as save, \
                patch("swisstip.ingestion.plugin_downloads.time.sleep"), redirect_stdout(io.StringIO()):
            report = run_source_plugins(self.root, registry)[0]
            self.assertEqual([call.args[0]["url"] for call in save.call_args_list], ["https://example.gov/law.html"])
        self.assertEqual(report["counts"], {"saved": 2})
        plan = read_json(output / "plan.json")
        self.assertEqual(plan["source_plugin"], {"id": "example", "version": "1", "api_version": 1})
        self.assertEqual(plan["catalogue_sha256"], "abc")
        self.assertEqual(plan["resolutions"], {"https://example.gov/old-law": ["https://example.gov/old-law.html"],
                                               "https://example.gov/law": ["https://example.gov/law.html"]})
        self.assertEqual(plan["adopted_archive"]["catalogue_sha256"], "old")
        self.assertEqual(plan["imported_from"], ["earlier-run"])
        self.assertEqual([t["url"] for t in plan["targets"]],
                         ["https://example.gov/old-law.html", "https://example.gov/law.html"])
        with patch("swisstip.ingestion.plugin_downloads.snapshot") as save:
            run_source_plugins(self.root, registry)
            save.assert_not_called()

    def test_metadata_rejects_unknown_hosts_tampered_cache_and_excess_calls(self):
        output, path = self.cache_metadata()
        fetch = MetadataFetcher(output, ExamplePlugin())
        with self.assertRaisesRegex(ValueError, "declared hosts"):
            fetch("https://other.gov/metadata")
        path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash"):
            fetch("https://example.gov/metadata")
        self.cache_metadata()
        fetch("https://example.gov/metadata")
        fetch("https://example.gov/metadata")
        with self.assertRaisesRegex(ValueError, "four metadata"):
            fetch("https://example.gov/metadata")

    def test_registry_rejects_unknown_and_malformed_plugins(self):
        with self.assertRaisesRegex(ValueError, "Unknown source plugins"):
            load_source_plugins(["other"])
        self.assertEqual(load_source_plugins().describe()[0]["id"], "fedlex")
        self.assertEqual(load_source_plugins([]).plugins, ())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            PluginRegistry([ExamplePlugin(), ExamplePlugin()])
        plugin = ExamplePlugin()
        with self.assertRaisesRegex(ValueError, "outside its declared hosts"):
            validate_documents(plugin, [SourceDocument("https://other.gov/x", "text/html", "en")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_documents(plugin, [SourceDocument("https://example.gov/x", "text/html", "en")] * 2)


class FedlexPluginTests(unittest.TestCase):
    def test_matches_only_undated_eli_pages(self):
        plugin = FedlexPlugin()
        self.assertTrue(plugin.matches("https://www.fedlex.admin.ch/eli/cc/2007/758/de"))
        self.assertTrue(plugin.matches("https://www.fedlex.admin.ch/eli/cc/2006/14_fga/de"))
        self.assertFalse(plugin.matches("https://www.fedlex.admin.ch/eli/cc/2006/14_xyz/de"))
        self.assertFalse(plugin.matches("https://www.fedlex.admin.ch/eli/cc/2007/758/20260612/de"))
        self.assertFalse(plugin.matches("https://www.fedlex.admin.ch/"))
        self.assertFalse(plugin.matches("http://www.fedlex.admin.ch/eli/cc/2007/758/de"))

    def test_resolution_selects_dated_html_and_pdf_of_the_requested_language(self):
        plugin = FedlexPlugin()
        version = "https://fedlex.data.admin.ch/eli/cc/2007/758/20260612"
        base = "https://fedlex.data.admin.ch/filestore/fedlex.data.admin.ch/eli/cc/2007/758/20260612/de"
        rows = [{"version": {"value": version}, "file": {"value": f"{base}/html/fedlex-de-html.html"}},
                {"version": {"value": version}, "file": {"value": f"{base}/pdf-a/fedlex-de-pdf-a.pdf"}}]
        requested = []

        def fetch_json(url):
            requested.append(url)
            return {"results": {"bindings": rows}}

        documents = plugin.resolve(SourceRequest("https://www.fedlex.admin.ch/eli/cc/2007/758/de", date(2026, 9, 10)), fetch_json)
        validate_documents(plugin, documents)
        self.assertTrue(requested[0].startswith("https://fedlex.data.admin.ch/sparqlendpoint?"))
        self.assertIn("20260910", requested[0])
        self.assertEqual([(d.media_type, d.preferred_for_extraction) for d in documents],
                         [("text/html", True), ("application/pdf", False)])
        self.assertEqual(documents[0].metadata["consolidation_date"], "20260612")
        rows[0]["file"]["value"] = base.replace("/de", "/fr") + "/html/x.html"
        with self.assertRaisesRegex(ValueError, "different work, version or language"):
            plugin.resolve(SourceRequest("https://www.fedlex.admin.ch/eli/cc/2007/758/de", date(2026, 9, 10)), fetch_json)
        with self.assertRaisesRegex(ValueError, "no HTML representation"):
            plugin.resolve(SourceRequest("https://www.fedlex.admin.ch/eli/cc/2007/758/de", date(2026, 9, 10)),
                           lambda url: {"results": {"bindings": rows[1:]}})


if __name__ == "__main__":
    unittest.main()
