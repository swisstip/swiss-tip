from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from swisstip.ingestion.acquisition import read_json, url_id, write_json  # noqa: E402
from swisstip.ingestion.gap_report import build_report, classify_failure, main, render_markdown  # noqa: E402


def target(url: str, source_id: str | None = None, scan_status: str = "ready") -> dict:
    entries = [] if source_id is None else [{"definition": {"source_id": source_id, "start_url": url}, "scan_status": scan_status}]
    return {"url": url, "url_id": url_id(url), "references": [{"label": source_id or url}], "registry_entries": entries}


def write_attempt(run: Path, item: dict, number: int, *, status: str, body: bytes | None = None, flags=(), report=None, error=None) -> dict:
    folder = run / "pages" / item["url_id"] / f"attempt-{number:03d}"
    folder.mkdir(parents=True)
    snapshots = []
    if body is not None:
        (folder / "response.html").write_bytes(body)
        snapshots.append({"relative_path": f"pages/{item['url_id']}/attempt-{number:03d}/response.html",
                          "sha256": hashlib.sha256(body).hexdigest(), "bytes_downloaded": len(body),
                          "retrieved_at": f"2026-09-1{number}T00:00:00+00:00", "content_type": "text/html",
                          "review_flags": list(flags)})
    manifest = {**item, "status": status, "snapshots": snapshots}
    if report is not None:
        manifest["report"] = report
    if error is not None:
        manifest["error"] = error
    write_json(folder / "manifest.json", manifest)
    write_json(folder.parent / "latest.json", manifest)
    return manifest


class GapReportTests(unittest.TestCase):
    def test_failure_classes_follow_the_recorded_cause(self) -> None:
        cases = {
            "mailto-redirect": {"error": "HTTPError: HTTP Error 302:  - Redirection to url 'mailto:office@tg.ch' is not allowed"},
            "access-denied": {"report": {"stop_reason": "frontier-exhausted", "skipped": [{"reason": "robots-disallowed"}], "pages": []}},
            "dns-failure": {"error": "URLError: <urlopen error [Errno 11001] getaddrinfo failed>"},
            "tls-certificate": {"error": "URLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch"},
            "not-found": {"report": {"stop_reason": "frontier-exhausted", "skipped": [], "pages": [{"status": 404, "outcome": "http-error"}]}},
            "rate-limited": {"report": {"stop_reason": "server-rate-limit", "skipped": [], "pages": [{"status": 429, "outcome": "rate-limited"}]}},
            "server-error": {"report": {"stop_reason": "server-unavailable", "skipped": [], "pages": [{"status": 503, "outcome": "server-unavailable"}]}},
            "transient-network": {"report": {"stop_reason": "frontier-exhausted", "skipped": [{"reason": "request-failed: OSError"}], "pages": []}},
            "robots-unavailable": {"report": {"stop_reason": "robots-unavailable", "skipped": [], "pages": []}},
            "not-attempted": {},
        }
        for expected, manifest in cases.items():
            self.assertEqual(classify_failure({"status": "x", **manifest}), expected, expected)

    def test_report_classifies_every_target_and_renders_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            clean = target("https://a.example/clean", "a-clean")
            shell = target("https://www.fedlex.admin.ch/eli/cc/2007/758/de", "ch-fedlex-aig", "manual_adapter_required")
            blocked = target("https://b.example/blocked", "b-blocked")
            recovered = target("https://c.example/late", "c-late")
            review = target("https://d.example/tls", "d-tls", "needs_access_review")
            pending = target("https://e.example/pending")
            soft = target("https://www.ch.ch/de/auslander-in-der-schweiz/in-der-schweiz-arbeiten/", "ch-chch-work")
            variant = {**target("https://a.example/en/clean"), "attribution": {"kind": "language-variant", "source_ids": ["a-clean"],
                                                                                 "advertised_languages": ["en"], "discovered_on": [], "reasons": []}}
            plan = {"catalogue": str(run / "missing.json"), "catalogue_sha256": "abc",
                    "targets": [clean, shell, blocked, recovered, review, pending, variant, soft]}
            write_json(run / "plan.json", plan)
            write_attempt(run, clean, 1, status="saved", body=b"<html>ok</html>")
            # No flag recorded: the shell must be re-detected from the saved bytes.
            write_attempt(run, shell, 1, status="saved", body=b"<html><app-root></app-root></html>")
            write_attempt(run, blocked, 1, status="not_saved",
                          report={"stop_reason": "frontier-exhausted", "skipped": [{"reason": "robots-disallowed"}], "pages": []})
            write_attempt(run, recovered, 1, status="not_saved",
                          report={"stop_reason": "frontier-exhausted", "skipped": [{"reason": "request-failed: OSError"}], "pages": []})
            write_attempt(run, recovered, 2, status="saved", body=b"<html>later</html>")
            write_attempt(run, review, 1, status="not_saved", report={"stop_reason": "robots-unavailable", "skipped": [], "pages": []})
            write_attempt(run, review, 2, status="failed", error="URLError: certificate verify failed: Hostname mismatch")
            write_attempt(run, variant, 1, status="failed", error="URLError: timed out")
            write_attempt(run, soft, 1, status="saved", body=b"<html><body><h1>Error Page (404) </h1></body></html>")
            documents = run / "fedlex-documents"
            documents.mkdir()
            write_json(documents / "summary.json", {"counts": {"saved": 1}, "saved_bytes": 1, "results": [
                {"status": "saved", "source_page_url": shell["url"], "url": "https://fedlex.data.admin.ch/x.html",
                 "version_uri": "v1", "snapshots": [{"relative_path": "pages/x/attempt-001/response.html"}]}]})

            report = build_report(run)
            by_url = {row["url"]: row for row in report["targets"]}
            self.assertEqual(by_url[clean["url"]]["gap"], "none")
            self.assertEqual(by_url[shell["url"]]["gap"], "javascript-shell-resolved")
            self.assertFalse(by_url[shell["url"]]["retriable"])
            self.assertEqual(by_url[shell["url"]]["plugin_documents"][0]["version_uri"], "v1")
            self.assertEqual((by_url[blocked["url"]]["gap"], by_url[blocked["url"]]["retriable"]), ("access-denied", False))
            self.assertEqual(by_url[recovered["url"]]["gap"], "none")
            self.assertIn("recovered on attempt-002 after: transient-network", by_url[recovered["url"]]["action"])
            self.assertEqual(by_url[review["url"]]["gap"], "tls-certificate", "the most specific cause across attempts wins")
            self.assertFalse(by_url[review["url"]]["retriable"], "needs_access_review blocks automatic retries")
            self.assertEqual((by_url[pending["url"]]["gap"], by_url[pending["url"]]["retriable"]), ("not-attempted", True))
            self.assertEqual((by_url[variant["url"]]["kind"], by_url[variant["url"]]["source_ids"], by_url[variant["url"]]["gap"]),
                             ("language-variant", ["a-clean"], "transient-network"))
            self.assertEqual((by_url[soft["url"]]["gap"], by_url[soft["url"]]["retriable"]), ("soft-error-page", True),
                             "an error page served with HTTP 200 is a gap, not a saved source")
            self.assertEqual(by_url[soft["url"]]["latest_snapshots"][0]["review_flags"], ["error_page_title"])
            self.assertEqual(report["counts"]["retriable"], 3)
            self.assertEqual(report["counts"]["not_retriable"], 2)
            self.assertEqual(report["counts"]["informational"], 1)
            self.assertEqual(report["counts"]["kind"], {"catalogue": 7, "language-variant": 1})
            self.assertEqual(report["counts"]["gap_by_kind"]["language-variant"], {"transient-network": 1})
            self.assertEqual(report["informational"], [shell["url"]])
            markdown = render_markdown(report)
            self.assertIn("| `b-blocked` | catalogue | [b-blocked](https://b.example/blocked) | not_saved | access-denied | no |", markdown)
            self.assertIn("(needs_access_review)", markdown)
            self.assertIn("## Recovered by a later attempt", markdown)
            self.assertIn("| a.example | language-variant | 1 | 0 | 1 | transient-network 1 |", markdown)
            self.assertNotIn("[https://a.example/en/clean]", markdown.split("## Discovered pages")[0].split("## All catalogue")[1],
                             "discovered pages are summarised, not listed one by one")

            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--run", str(run)]), 0)
            self.assertEqual(read_json(run / "gap-report.json")["target_count"], 8)
            self.assertTrue((run / "gap-report.md").is_file())


if __name__ == "__main__":
    unittest.main()
