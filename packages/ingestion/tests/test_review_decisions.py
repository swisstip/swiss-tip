from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_gap_report import target, write_attempt
from swisstip.ingestion.acquisition import read_json, write_json
from swisstip.ingestion.gap_report import build_report, render_markdown
from swisstip.ingestion.review_decisions import (
    REVIEW_SCHEMA, apply_review_decisions, load_decisions,
    observation_fingerprint, text_fingerprint, text_review,
)


CATALOGUE_SHA256 = "a" * 64
REVIEW = {
    "review_status": "human-reviewed", "decision": "approved",
    "reviewed_by": "reviewer", "reviewed_on": "2026-09-14",
    "reason": "accepted demo scope",
}


class ReviewDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run = Path(self.temporary.name) / "run"
        self.run.mkdir()
        self.items = {name: target(f"https://example.test/{name}", name)
                      for name in ("clean", "blocked", "pending", "shell", "rendered")}
        write_json(self.run / "plan.json", {
            "catalogue": str(self.run / "catalogue.json"),
            "catalogue_sha256": CATALOGUE_SHA256,
            "targets": list(self.items.values()),
        })
        write_attempt(self.run, self.items["clean"], 1, status="saved", body=b"<html>Content</html>")
        write_attempt(self.run, self.items["blocked"], 1, status="not_saved", report={
            "stop_reason": "frontier-exhausted", "skipped": [{"reason": "robots-disallowed"}], "pages": [],
        })
        write_attempt(self.run, self.items["shell"], 1, status="saved", body=b"<html><app-root></app-root></html>")
        write_attempt(self.run, self.items["rendered"], 1, status="saved", body=b"<html>Rendered content</html>",
                      flags=["browser-rendered-dom-not-raw-http-response"])
        self.before = build_report(self.run)

    def row(self, name: str, report: dict | None = None) -> dict:
        return next(row for row in (report or self.before)["targets"] if row["url"] == self.items[name]["url"])

    def ledger(self, names=(), text_documents=()) -> dict:
        return {
            "schema_version": REVIEW_SCHEMA, "catalogue_sha256": CATALOGUE_SHA256,
            "acquisition": [{"url": self.row(name)["url"],
                             "observation_sha256": observation_fingerprint(self.row(name)), **REVIEW}
                            for name in names],
            "text_documents": [{"document_id": entry["document_id"],
                                "observation_sha256": text_fingerprint(entry), **REVIEW}
                               for entry in text_documents],
            "scope": {**REVIEW, "details": "Current demo corpus scope accepted by its owner."},
        }

    def persist(self, ledger: dict) -> None:
        write_json(self.run / "review-decisions.json", ledger)

    def test_without_decisions_existing_observations_remain_open(self) -> None:
        self.assertEqual(self.row("clean")["disposition"], "available")
        self.assertEqual(self.row("rendered")["disposition"], "informational")
        self.assertEqual(self.row("blocked")["disposition"], "open")
        self.assertTrue(self.row("blocked")["outstanding"])
        self.assertIsNone(self.row("blocked")["review"])
        self.assertEqual(self.before["counts"]["approved"], 0)
        self.assertEqual(self.before["counts"]["outstanding"], 3)

    def test_persisted_approval_closes_only_matching_observations(self) -> None:
        self.persist(self.ledger(["blocked", "shell"]))
        report = build_report(self.run)
        self.assertEqual(report["counts"]["approved"], 2)
        self.assertEqual(report["counts"]["outstanding"], 1)
        self.assertEqual(report["counts"]["retriable"], 1)
        self.assertEqual(report["counts"]["not_retriable"], 0)
        self.assertCountEqual(report["approved"], [self.items[name]["url"] for name in ("blocked", "shell")])
        self.assertEqual(report["retriable"], [self.items["pending"]["url"]])
        self.assertEqual(report["not_retriable"], [])
        for name in ("blocked", "shell"):
            row = self.row(name, report)
            self.assertEqual(row["disposition"], "approved")
            self.assertFalse(row["outstanding"])
            self.assertEqual(row["review"]["reviewed_by"], REVIEW["reviewed_by"])
            self.assertEqual(row["review"]["review_status"], "human-reviewed")
        self.assertEqual(build_report(self.run), report, "Rebuilding must reload the saved decisions.")

    def test_acceptance_does_not_invent_downloads_or_reviewed_evidence(self) -> None:
        original_manifests = {path: path.read_bytes() for path in self.run.glob("pages/**/*.json")}
        self.persist(self.ledger(["blocked", "shell"]))
        report = build_report(self.run)
        for name in self.items:
            row, before = self.row(name, report), self.row(name)
            for field in ("status", "gap", "latest_snapshots", "attempt_outcomes", "saved_attempts"):
                self.assertEqual(row[field], before[field], (name, field))
            self.assertNotIn("evidence_review_status", row)
        self.assertEqual(report["counts"]["status"], self.before["counts"]["status"])
        self.assertNotIn("access-denied", report["counts"]["gap"])
        self.assertNotIn("javascript-shell", report["counts"]["gap"])
        self.assertEqual(self.row("blocked", report)["latest_snapshots"], [])
        self.assertEqual({path: path.read_bytes() for path in original_manifests}, original_manifests)

    def test_rendering_uses_review_disposition_and_excludes_approved_from_open_section(self) -> None:
        self.persist(self.ledger(["blocked", "shell"]))
        markdown = render_markdown(build_report(self.run))
        self.assertIn("approved", markdown.lower())
        self.assertIn("human-reviewed", markdown.lower())
        catalogue = markdown.split("## All catalogue targets", 1)[1]
        blocked_line = next(line for line in catalogue.splitlines() if self.items["blocked"]["url"] in line)
        self.assertIn("approved", blocked_line.lower())
        self.assertNotIn("access-denied", blocked_line)
        open_sections = [section for section in markdown.split("\n## ")[1:]
                         if section.splitlines()[0].lower().startswith(("gaps", "outstanding"))]
        self.assertTrue(open_sections, "The pending target still needs an outstanding section.")
        for section in open_sections:
            self.assertNotIn(self.items["blocked"]["url"], section)
            self.assertNotIn(self.items["shell"]["url"], section)
        self.assertTrue(any(self.items["pending"]["url"] in section for section in open_sections))

    def test_changed_raw_bytes_at_same_url_reopen_the_item(self) -> None:
        self.persist(self.ledger(["shell"]))
        self.assertEqual(self.row("shell", build_report(self.run))["disposition"], "approved")
        write_attempt(self.run, self.items["shell"], 2, status="saved",
                      body=b"<html><app-root></app-root><script src='new.js'></script></html>")
        report = build_report(self.run)
        row = self.row("shell", report)
        self.assertEqual(row["gap"], "javascript-shell")
        self.assertEqual(row["disposition"], "open")
        self.assertTrue(row["outstanding"])
        self.assertIsNone(row["review"])
        self.assertEqual(report["counts"]["approved"], 0)

    def test_observation_changes_invalidate_acceptance(self) -> None:
        self.persist(self.ledger(["blocked"]))
        changes = {
            "status": "failed", "gap": "server-error", "kind": "in-scope",
            "source_ids": ["other-source"], "scan_statuses": ["needs_access_review"],
            "attempt_outcomes": [{"attempt": "attempt-002", "status": "not_saved", "detail": "HTTP 503"}],
            "latest_snapshots": [{"sha256": "b" * 64}],
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                report = deepcopy(self.before)
                self.row("blocked", report)[field] = value
                reviewed = apply_review_decisions(report, self.run)
                self.assertEqual(self.row("blocked", reviewed)["disposition"], "open")
                self.assertTrue(self.row("blocked", reviewed)["outstanding"])

    def test_changed_catalogue_rejects_stale_ledger(self) -> None:
        self.persist(self.ledger(["blocked"]))
        with self.assertRaises(ValueError):
            load_decisions(self.run, catalogue_sha256="b" * 64)
        plan = read_json(self.run / "plan.json")
        plan["catalogue_sha256"] = "b" * 64
        write_json(self.run / "plan.json", plan)
        with self.assertRaises(ValueError):
            build_report(self.run)

    def test_invalid_review_metadata_is_rejected(self) -> None:
        changes = {
            "reviewed_by": "  ", "reviewed_on": "2026-02-30",
            "review_status": "agent-reviewed", "decision": "pending",
        }
        for field, value in changes.items():
            for section in ("acquisition", "scope", "text_documents"):
                with self.subTest(field=field, section=section):
                    ledger = self.ledger(["blocked"], [self.text_entry()])
                    entry = ledger[section] if section == "scope" else ledger[section][0]
                    entry[field] = value
                    self.persist(ledger)
                    with self.assertRaises(ValueError):
                        load_decisions(self.run, catalogue_sha256=CATALOGUE_SHA256)

    def test_unknown_schema_and_invalid_fingerprint_are_rejected(self) -> None:
        ledger = self.ledger(["blocked"])
        ledger["schema_version"] = "unknown/v0"
        self.persist(ledger)
        with self.assertRaises(ValueError):
            load_decisions(self.run, catalogue_sha256=CATALOGUE_SHA256)
        ledger = self.ledger(["blocked"])
        ledger["acquisition"][0]["observation_sha256"] = "not-a-hash"
        self.persist(ledger)
        with self.assertRaises(ValueError):
            load_decisions(self.run, catalogue_sha256=CATALOGUE_SHA256)

    def test_scope_approval_does_not_approve_new_or_unlisted_observations(self) -> None:
        self.persist(self.ledger())
        report = build_report(self.run)
        self.assertEqual(report["counts"]["approved"], 0)
        self.assertEqual(report["counts"]["outstanding"], 3)
        self.assertIsNone(text_review(self.text_entry(), load_decisions(self.run)))

    @staticmethod
    def text_entry() -> dict:
        return {
            "document_id": "document-123", "raw_sha256": "c" * 64,
            "status": "excluded_source_response", "representation": "pdf",
            "pdf_pages_without_text": [2, 3], "exclusion_reasons": ["possible_access_challenge"],
            "eligible_for_processing": False,
        }

    def test_text_acceptance_preserves_extraction_status_and_eligibility(self) -> None:
        entry = self.text_entry()
        before = deepcopy(entry)
        self.persist(self.ledger(text_documents=[entry]))
        review = text_review(entry, load_decisions(self.run, catalogue_sha256=CATALOGUE_SHA256))
        self.assertIsNotNone(review)
        self.assertEqual(review["decision"], "approved")
        self.assertEqual(review["review_status"], "human-reviewed")
        self.assertEqual(entry, before)
        self.assertFalse(entry["eligible_for_processing"])
        self.assertEqual(entry["status"], "excluded_source_response")

    def test_text_observation_changes_require_a_new_decision(self) -> None:
        original = self.text_entry()
        self.persist(self.ledger(text_documents=[original]))
        decisions = load_decisions(self.run, catalogue_sha256=CATALOGUE_SHA256)
        changes = {
            "document_id": "document-456", "raw_sha256": "d" * 64, "status": "extracted",
            "representation": "html", "pdf_pages_without_text": [3], "exclusion_reasons": [],
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                entry = {**original, field: value}
                self.assertIsNone(text_review(entry, decisions))


if __name__ == "__main__":
    unittest.main()
