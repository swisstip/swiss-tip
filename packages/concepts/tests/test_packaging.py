import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from swisstip.build.curation import load_curation
from swisstip.build.release_build import build_release
from swisstip.concepts.package import PackagingError, candidate_citations, package_candidates
from swisstip.concepts.package_cli import main
from swisstip.extraction.anchors import make_anchor
from swisstip.extraction.records import attach_offsets


def fixture(root: Path) -> tuple[Path, Path, dict, dict]:
    text = root / "text"
    (text / "documents").mkdir(parents=True)
    record = dict(document_id="doc-0123456789abcdefabcd", source_url="https://example.ch/registration",
                  document_url="https://example.ch/registration", title="Anmeldung", language_declared="de-CH",
                  extractor_version="test-1", eligible_for_processing=True, preferred_representation=True,
                  source_registry=[dict(definition=dict(source_id="ag-residence", jurisdiction="CH-AG",
                                                         canonical_authority="Canton Aargau"))],
                  acquisition=dict(raw_sha256="a" * 64, retrieved_at="2026-09-11T12:00:00+00:00"),
                  blocks=[dict(kind="heading", text="Anmeldung", heading_path=["Anmeldung"]),
                          dict(kind="paragraph", text="Melden Sie sich innert 14 Tagen an.", heading_path=["Anmeldung"]),
                          dict(kind="paragraph", text="Die Anmeldung erfolgt vor dem Arbeitsbeginn.", heading_path=["Anmeldung"]),
                          dict(kind="paragraph", text="Bringen Sie Ihren Pass mit.", heading_path=["Anmeldung"]),
                          dict(kind="paragraph", text="Die Gemeinde hilft Ihnen bei Fragen.", heading_path=["Anmeldung"])])
    attach_offsets(record)
    write_record(text, record)
    curation = root / "curation.yaml"
    curation.write_text(yaml.safe_dump(dict(
        pack="test", title="Test", scope_statement="Residence", out_of_scope=["Fees"],
        out_of_scope_response="Not covered.", publishers={"example.ch": "Canton Aargau"},
        topics=[dict(topic_id="residence", title="Residence", description="Registration")],
        concepts=[dict(concept_id="existing", topic_id="residence", label="Existing", description="Existing statement",
                       facts=[dict(fact_id="existing-1", statement="Existing statement", language="de",
                                   jurisdiction="CH-AG", provenance=dict(kind="curated-statement",
                                   review_status="human-reviewed", author="Expert", reviewed_by="Expert"),
                                   evidence=[dict(document_id=record["document_id"], first_block=2)])])]),
        allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = dict(schema_version="swisstip.concept-candidates/v1", document_id=record["document_id"],
                  content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                  extractor_version=record["extractor_version"], language="de-CH", provider="fake", model="offline",
                  generated_at="2026-09-13T12:00:00+00:00", job_id="2026-09-13-fake-abc123",
                  prompts={kind: dict(sha256=character * 64) for kind, character in [("extraction", "b"), ("review", "c")]},
                  candidates=[dict(candidate_id="candidate-1234567890abcdef", preferred_label="Anmeldung innert 14 Tagen",
                                   alternative_labels=["Anmeldefrist"], concept_type="RULE", granularity="ANSWERABLE",
                                   description="Die Anmeldung muss innert 14 Tagen und vor dem Arbeitsbeginn erfolgen.",
                                   scope="Auslaendische Personen im Kanton Aargau", user_questions=["Wann melde ich mich an?"],
                                   confidence=0.8, relations=[dict(relation_type="RELATED", target_label="Gemeinde", confidence=0.7)],
                                   primary_section_id="section-0001", heading_path=["Anmeldung"],
                                   validation_state="CANDIDATE", review=dict(decision="supported", issue="none", reason="Belegt."),
                                   evidence=[span(record, 2), span(record, 3), span(record, 5)])])
    return text, curation, record, report


def span(record: dict, number: int, first: int = 0, last: int | None = None) -> dict:
    block = record["blocks"][number - 1]
    last = len(block["text"]) if last is None else last
    return dict(evidence_id=f"b{number:05d}:{first}:{last}", block_id=block["block_id"], block_number=number,
                start=block["start"] + first, end=block["start"] + last, quote=block["text"][first:last],
                text_sha256=block["text_sha256"])


def write_record(text: Path, record: dict, entries: list[dict] | None = None) -> None:
    (text / "documents" / f"{record['document_id']}.json").write_text(json.dumps(record), encoding="utf-8")
    if entries is None:
        entries = [dict(document_id=record["document_id"], source_url=record["source_url"],
                        content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"],
                        retrieved_at=record["acquisition"]["retrieved_at"], eligible_for_processing=True,
                        preferred_representation=True, superseded=False, source_ids=["ag-residence"])]
    (text / "index.json").write_text(json.dumps(entries), encoding="utf-8")


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.text, self.curation, self.record, self.report = fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def package(self, reports=None, **kwargs):
        return package_candidates([self.report] if reports is None else reports, curation_path=self.curation,
                                  text_dir=self.text, topic="residence", **kwargs)

    def test_mapping_and_full_dry_build(self):
        original = self.curation.read_bytes()
        result = self.package()
        self.assertEqual(self.curation.read_bytes(), original)
        self.assertEqual(result["build"]["citation_outcomes"], {"same-snapshot": 3})
        self.assertEqual(result["build"]["validation_issues"], [])
        entry = result["entries"][0]
        self.assertEqual(entry["concept_id"], "mc-01234567-anmeldung-innert-14-tagen")
        self.assertEqual(entry["topic_id"], "residence")
        candidate = self.report["candidates"][0]
        for field, source in [("label", "preferred_label"), ("description", "description"),
                              ("aliases", "alternative_labels"), ("questions", "user_questions")]:
            self.assertEqual(entry[field], candidate[source])
        for field in ("source_terms", "required_context", "required_user_facts"):
            self.assertEqual(entry[field], [])
        self.assertIsNone(entry["decision_rule"])
        self.assertIn("confidence: 0.8 (uncalibrated)", entry["notes"])
        self.assertIn("scope: Auslaendische Personen im Kanton Aargau", entry["notes"])
        self.assertIn("review: Belegt.", entry["notes"])
        self.assertIn("relations:", entry["notes"][3])
        self.assertEqual(entry["notes"][-1], "proposed by fake:offline on 2026-09-13, job 2026-09-13-fake-abc123")
        fact = entry["facts"][0]
        self.assertEqual(fact["statement"], candidate["description"])
        self.assertEqual(fact["fact_id"], entry["concept_id"] + "-1")
        self.assertEqual(fact["language"], "de")
        self.assertEqual(fact["jurisdiction"], "CH-AG")
        self.assertEqual(fact["provenance"]["kind"], "model-candidate")
        self.assertEqual(fact["provenance"]["review_status"], "model-candidate-automated-review")
        self.assertEqual(fact["provenance"]["author"], "fake:offline")
        self.assertEqual(fact["provenance"]["notes"], ["Belegt."])
        self.assertIn("prompts bbbbbbbb/cccccccc", fact["provenance"]["source"])
        self.assertEqual([(item["first_block"], item["last_block"]) for item in fact["evidence"]], [(2, 3), (5, 5)])
        self.assertEqual(fact["evidence"][0]["anchor"], make_anchor(self.record, 2, 3))
        self.assertTrue((self.root / "packaging-report.json").is_file())

    def test_a_classified_basis_becomes_the_citation_basis_and_a_note(self):
        self.report["candidates"][0]["basis"] = dict(kind="guidance", level="cantonal", norm=None, refers_to="Art. 9 BüG",
                                                     reason="The page explains the procedure in its own words.")
        result = self.package(write=True)
        self.assertTrue(result["written"])
        self.assertEqual(result["build"]["validation_issues"], [])
        curation = load_curation(self.curation)
        fact = curation.concepts[-1].facts[0]
        self.assertEqual({(c.basis.kind, c.basis.level, c.basis.norm, c.basis.refers_to) for c in fact.evidence},
                         {("guidance", "cantonal", None, "Art. 9 BüG")})
        self.assertIn("basis: guidance (The page explains the procedure in its own words.)", curation.concepts[-1].notes)
        release, _ = build_release(curation, self.text, "test-v1")
        self.assertEqual({e.basis.label for e in release.evidence if e.basis},
                         {"Cantonal authority guidance", "Cantonal authority guidance, referring to Art. 9 BüG"})

    def test_idempotent_and_accepted_fact_untouched(self):
        old_concept = yaml.safe_load(self.curation.read_text(encoding="utf-8"))["concepts"][0]
        self.package(write=True)
        first = self.curation.read_bytes()
        result = self.package(write=True)
        self.assertEqual(result["skipped"][0]["reason"], "already_present")
        self.assertEqual(self.curation.read_bytes(), first)
        data = yaml.safe_load(self.curation.read_text(encoding="utf-8"))
        self.assertEqual(data["concepts"][0], old_concept)
        fact = data["concepts"][-1]["facts"][0]
        fact["provenance"].update(kind="curated-statement", review_status="human-reviewed", reviewed_by="Expert")
        self.curation.write_text(yaml.safe_dump(data), encoding="utf-8")
        accepted = self.curation.read_bytes()
        self.package(write=True)
        self.assertEqual(self.curation.read_bytes(), accepted)
        release, _ = build_release(load_curation(self.curation), self.text, "test")
        self.assertEqual(release.manifest.review_statuses, {"human-reviewed": 2})

    def test_short_spans_pin_whole_blocks_and_deduplicate(self):
        candidate = copy.deepcopy(self.report["candidates"][0])
        candidate["evidence"] = [span(self.record, 2, 1, 10), span(self.record, 2, 12, 15), span(self.record, 3)]
        self.assertEqual(candidate_citations(candidate, self.record), [dict(
            document_id=self.record["document_id"], first_block=2, last_block=3, anchor=make_anchor(self.record, 2, 3))])

    def test_collision_suffix_and_default_domain_filter(self):
        self.report["candidates"].extend([dict(self.report["candidates"][0], candidate_id="candidate-second"),
                                           dict(self.report["candidates"][0], candidate_id="candidate-domain", granularity="DOMAIN")])
        result = self.package()
        self.assertTrue(result["entries"][1]["concept_id"].endswith("-2"))
        self.assertEqual(result["skipped"][0]["reason"], "granularity_filter")
        self.assertEqual(len(self.package(granularities=["DOMAIN"])["entries"]), 1)

    def test_missing_jurisdiction_and_override(self):
        self.record["source_registry"] = []
        write_record(self.text, self.record)
        self.assertEqual(self.package()["skipped"][0]["reason"], "missing_jurisdiction")
        self.assertEqual(self.package(jurisdiction="CH-ZH")["entries"][0]["facts"][0]["jurisdiction"], "CH-ZH")

    def test_stale_and_selection_filters(self):
        for field in ("content_sha256", "raw_sha256", "extractor_version"):
            with self.subTest(field=field):
                report = dict(self.report, **{field: "changed"})
                self.assertEqual(self.package([report])["skipped"][0]["reason"], "stale")
        for options, reason in [({"document_ids": ["doc-other"]}, "document_filter"),
                                ({"sources": ["zh-residence"]}, "source_filter"),
                                ({"min_confidence": 0.9}, "confidence_filter"),
                                ({"concept_types": ["SERVICE"]}, "concept_type_filter"),
                                ({"job": "other"}, "job_filter")]:
            with self.subTest(options=options):
                self.assertEqual(self.package(**options)["skipped"][0]["reason"], reason)

    def test_bad_evidence_or_review_never_writes(self):
        original = self.curation.read_bytes()
        for key, value in [("quote", "Invented"), ("start", -1), ("text_sha256", "x" * 64),
                           ("block_number", 999), ("evidence_id", "unknown")]:
            report = copy.deepcopy(self.report)
            report["candidates"][0]["evidence"][0][key] = value
            with self.subTest(key=key), self.assertRaises(PackagingError):
                self.package([report], write=True)
        report = copy.deepcopy(self.report)
        report["candidates"][0]["review"]["decision"] = "uncertain"
        with self.assertRaisesRegex(PackagingError, "supported separate review"):
            self.package([report], write=True)
        self.assertEqual(self.curation.read_bytes(), original)

    def test_non_same_snapshot_existing_citation_stops_write(self):
        real = build_release

        def moved(*args, **kwargs):
            release, report = real(*args, **kwargs)
            report["facts_resolved"][0]["citations"][0]["outcome"] = "moved"
            return release, report

        original = self.curation.read_bytes()
        with patch("swisstip.concepts.package.build_release", side_effect=moved):
            with self.assertRaisesRegex(PackagingError, "existing-1.*moved"):
                self.package(write=True)
        self.assertEqual(self.curation.read_bytes(), original)

    def test_report_cannot_overwrite_curation(self):
        original = self.curation.read_bytes()
        with self.assertRaisesRegex(PackagingError, "overwrite"):
            self.package(write=True, report_path=self.curation)
        self.assertEqual(self.curation.read_bytes(), original)

    def test_packaging_report_cannot_overwrite_text_index(self):
        original = (self.text / "index.json").read_bytes()
        with self.assertRaisesRegex(PackagingError, "source text dataset"):
            self.package(write=True, report_path=self.text / "index.json")
        self.assertEqual((self.text / "index.json").read_bytes(), original)

    def test_cli_preview_and_write(self):
        directory = self.root / "concepts" / "documents"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text(json.dumps(self.report), encoding="utf-8")
        arguments = ["--concepts", str(directory.parent), "--curation", str(self.curation), "--text", str(self.text),
                     "--topic", "residence"]
        original = self.curation.read_bytes()
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            self.assertIn("model-candidate-automated-review", output.getvalue())
            self.assertEqual(self.curation.read_bytes(), original)
            self.assertEqual(main(arguments + ["--write"]), 0)
        self.assertEqual(len(load_curation(self.curation).concepts), 2)
