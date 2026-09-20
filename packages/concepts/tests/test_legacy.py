import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from swisstip.concepts.legacy import anchor_candidate, load_legacy_batch
from swisstip.concepts.package import package_candidates
from swisstip.concepts.package_cli import main
from swisstip.extraction.records import attach_offsets

from test_packaging import fixture, write_record


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.text, self.curation, self.record, self.report = fixture(self.root)
        self.candidate = copy.deepcopy(self.report["candidates"][0])
        self.candidate.pop("review")
        self.candidate.pop("heading_path")
        self.candidate["evidence"] = [dict(section_id="section-0001", start=0, end=40,
                                            quote="Anmeldung\nMelden Sie sich innert 14 Tagen an.")]
        self.batch = dict(schema_version="swisstip.concept-proposal-batch/v1", reports=[dict(
            document_id="page-predecessor", language="de-CH", provider="anthropic-assistant", model="claude-fable-5-1",
            generated_at="2026-09-11T12:00:00+00:00", provenance=dict(source_url=self.record["source_url"], sha256="a" * 64),
            effective_prompts=self.report["prompts"], candidates=[self.candidate], semantic_reviews=[dict(
                preferred_label=self.candidate["preferred_label"], primary_section_id="section-0001",
                decision="supported", issue="none", reason="Supported by the cited passage.")])])

    def tearDown(self):
        self.temporary.cleanup()

    def load(self):
        path = self.root / "batch-001" / "result.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.batch), encoding="utf-8")
        return load_legacy_batch(path, self.text)

    def test_heading_prefix_removed_and_fields_map(self):
        reports, summary = self.load()
        self.assertEqual(summary["anchored"], 1)
        self.assertEqual(summary["dropped"], 0)
        self.assertEqual(summary["heading_prefixes_stripped"], 1)
        self.assertEqual(summary["matches"], {"hash": 1})
        candidate = reports[0]["candidates"][0]
        self.assertEqual(candidate["evidence"][0]["block_number"], 2)
        self.assertEqual(candidate["evidence"][0]["quote"], self.record["blocks"][1]["text"])
        result = package_candidates(reports, curation_path=self.curation, text_dir=self.text, topic="residence")
        provenance = result["entries"][0]["facts"][0]["provenance"]
        self.assertEqual(provenance["author"], "anthropic-assistant:claude-fable-5-1")
        self.assertIn("legacy batch", provenance["source"])
        self.assertIn("report page-predecessor", provenance["source"])
        self.assertIn(self.candidate["candidate_id"], provenance["source"])
        self.assertEqual(result["build"]["citation_outcomes"], {"same-snapshot": 2})

    def test_plain_quote_and_whitespace_map_across_blocks(self):
        self.candidate["evidence"][0].update(start=50, quote="Melden  Sie sich innert 14 Tagen an.\n\tDie Anmeldung erfolgt vor dem Arbeitsbeginn.")
        reports, summary = self.load()
        self.assertEqual(summary["anchored"], 1)
        self.assertEqual(summary["heading_prefixes_stripped"], 0)
        spans = reports[0]["candidates"][0]["evidence"]
        self.assertEqual([span["block_number"] for span in spans], [2, 3])
        for span in spans:
            self.assertEqual(span["quote"], self.record["content_text"][span["start"]:span["end"]])

    def test_ambiguous_missing_and_partial_failures_dropped(self):
        self.record["blocks"].append(copy.deepcopy(self.record["blocks"][1]))
        attach_offsets(self.record)
        write_record(self.text, self.record)
        reports, summary = self.load()
        self.assertEqual(reports[0]["candidates"], [])
        self.assertEqual(summary["ambiguous"], 1)
        self.assertEqual(summary["dropped_candidates"][0]["reason"], "ambiguous_quote")
        self.assertEqual(summary["dropped_candidates"][0]["occurrences"], 2)
        self.candidate["evidence"] = [dict(quote="Bringen Sie Ihren Pass mit.", start=99),
                                        dict(quote="An invented quote.", start=150)]
        _, summary = self.load()
        self.assertEqual(summary["dropped_candidates"][0]["reason"], "quote_not_found")
        self.assertEqual(summary["dropped_candidates"][0]["evidence_number"], 2)

    def test_unrecognized_heading_is_not_discarded(self):
        self.candidate["evidence"][0]["quote"] = "Invented scope\nMelden Sie sich innert 14 Tagen an."
        _, result = anchor_candidate(self.candidate, self.record)
        self.assertEqual(result["reason"], "quote_not_found")

    def test_legacy_inline_link_spacing_in_prefix_does_not_change_quote(self):
        self.record["blocks"][0].update(text="Anmeldung.", heading_path=["Anmeldung."])
        for block in self.record["blocks"][1:]:
            block["heading_path"] = ["Anmeldung."]
        attach_offsets(self.record)
        self.candidate["evidence"][0]["quote"] = "Anmeldung .\nMelden Sie sich innert 14 Tagen an."
        converted, detail = anchor_candidate(self.candidate, self.record)
        self.assertEqual(detail["heading_prefixes_stripped"], 1)
        self.assertEqual(converted["evidence"][0]["quote"], self.record["blocks"][1]["text"])

    def test_merged_heading_tail_must_be_present_in_body(self):
        self.record["blocks"][0].update(text="Anmeldung: Melden", heading_path=["Anmeldung: Melden"])
        attach_offsets(self.record)
        self.candidate["evidence"][0]["quote"] = "Anmeldung:\nMelden Sie sich innert 14 Tagen an."
        converted, detail = anchor_candidate(self.candidate, self.record)
        self.assertIsNotNone(converted)
        self.assertEqual(detail["heading_prefixes_stripped"], 1)
        self.candidate["evidence"][0]["quote"] = "Anmeldung:\nDie Anmeldung erfolgt vor dem Arbeitsbeginn."
        converted, detail = anchor_candidate(self.candidate, self.record)
        self.assertIsNone(converted)
        self.assertEqual(detail["reason"], "quote_not_found")

    def test_hash_fallback_url_and_final_url(self):
        self.batch["reports"][0]["provenance"]["sha256"] = "c" * 64
        reports, summary = self.load()
        self.assertEqual(summary["matches"], {"url": 1})
        self.assertEqual(reports[0]["raw_sha256"], "a" * 64)
        provenance = self.batch["reports"][0]["provenance"]
        provenance["final_url"] = provenance.pop("source_url")
        _, summary = self.load()
        self.assertEqual(summary["anchored"], 1)
        provenance["final_url"] = "https://example.ch/missing"
        _, summary = self.load()
        self.assertEqual(summary["dropped_candidates"][0]["reason"], "record_not_found")

    def test_current_hash_preferred_over_newer_url_match(self):
        entries = json.loads((self.text / "index.json").read_text(encoding="utf-8"))
        other = copy.deepcopy(self.record)
        other["document_id"] = "doc-99999999999999999999"
        other["acquisition"].update(raw_sha256="b" * 64, retrieved_at="2026-09-12T12:00:00+00:00")
        attach_offsets(other)
        entries.append(dict(entries[0], document_id=other["document_id"], raw_sha256="b" * 64,
                            retrieved_at=other["acquisition"]["retrieved_at"]))
        write_record(self.text, other, entries)
        reports, _ = self.load()
        self.assertEqual(reports[0]["document_id"], self.record["document_id"])
        self.batch["reports"][0]["provenance"]["sha256"] = "c" * 64
        reports, _ = self.load()
        self.assertEqual(reports[0]["document_id"], other["document_id"])
        entries[-1]["superseded"] = True
        write_record(self.text, other, entries)
        reports, _ = self.load()
        self.assertEqual(reports[0]["document_id"], self.record["document_id"])

    def test_requires_supported_separate_review(self):
        self.batch["reports"][0]["semantic_reviews"][0]["decision"] = "unsupported"
        reports, summary = self.load()
        self.assertEqual(reports[0]["candidates"], [])
        self.assertEqual(summary["dropped_candidates"][0]["reason"], "missing_supported_review")

    def test_wrong_batch_schema_refused(self):
        self.batch["schema_version"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Not a"):
            self.load()

    def test_cli_accepts_multiple_batches_and_reports_duplicate_candidate(self):
        self.load()
        first = self.root / "batch-001" / "result.json"
        second = self.root / "second.json"
        second.write_bytes(first.read_bytes())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = main(["--legacy-batch", str(first), "--legacy-batch", str(second),
                           "--curation", str(self.curation), "--text", str(self.text), "--topic", "residence", "--write"])
        self.assertEqual(result, 0)
        report = json.loads((self.root / "packaging-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"], {"packaged": 1, "skipped": 1})
        self.assertEqual(len(report["legacy"]), 2)
        self.assertEqual(report["skipped"][0]["reason"], "already_present")
