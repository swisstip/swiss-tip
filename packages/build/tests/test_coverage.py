import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import ValidationError

from swisstip.build.coverage import (CoverageDispositions, Disposition, build_coverage, coverage_counts,
                                     dump_dispositions, load_dispositions, render_markdown, write_report)
from swisstip.build.coverage_cli import main
from swisstip.build.curation import Curation, load_curation
from swisstip.build.release_build import build_release
from swisstip.core.release import dump_release
from swisstip.extraction.extract_cli import run_extraction

URL = "https://www.sem.example/faq.html"
# Two content sections: Registration is cited by the curation below, Fees never is.
PAGE = (b"<html lang=\"en\"><head><title>FAQ</title></head><body><main><h1>FAQ</h1>"
        b"<h2>Registration</h2><p>Register within 14 days of arrival.</p><p>Register before starting work.</p>"
        b"<h2>Fees</h2><p>The permit costs 65 francs.</p></main></body></html>")
# A telephone card every page of the site repeats. Its heading is not one of the furniture headings on purpose: the
# boilerplate rule exists for exactly the repeated sections the section rules cannot know about.
CONTACT = b"<h2>Telefon</h2><p>044 000 00 00, Montag bis Freitag 8 bis 12 Uhr.</p>"
CURATION = """
schema_version: swiss-tip-curation/v1
pack: test
title: Test pack
scope_statement: Registration of EU/EFTA nationals.
out_of_scope: [Fees and appointment availability for any permit.]
out_of_scope_response: Say that it is not covered.
limitations: [Assistant-authored, unreviewed.]
publishers:
  www.sem.example: State Secretariat for Migration SEM
topics:
  - {topic_id: residence, title: Residence, description: Permits and registration.}
concepts:
  - concept_id: registration-deadline
    topic_id: residence
    label: Registration deadline
    description: When EU/EFTA nationals register.
    facts:
      - fact_id: registration-deadline-1
        statement: Register within 14 days of arrival and before starting work.
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: test}
        evidence:
          - {document_id: DOC, first_block: 3, last_block: 4}
"""
CATALOGUE = dict(sources=[dict(definition=dict(source_id="sem-faq", start_url=URL), title="SEM FAQ"),
                          dict(definition=dict(source_id="sem-unused", start_url="https://www.sem.example/other.html"), title="Never fetched")])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run(root: Path, pages: dict[str, bytes]) -> Path:
    """A run with one saved attempt per URL; every URL is a catalogue target of the source sem-faq."""
    run = root / "run"
    targets = []
    for url, body in pages.items():
        folder = run / "pages" / sha256(url.encode())
        attempt = folder / "attempt-001"
        attempt.mkdir(parents=True)
        (attempt / "response.html").write_bytes(body)
        snapshot = dict(relative_path=f"pages/{folder.name}/attempt-001/response.html", requested_url=url, final_url=url,
                        content_type="text/html", sha256=sha256(body), bytes_downloaded=len(body),
                        retrieved_at="2026-09-11T06:00:00+00:00", review_flags=[])
        manifest = dict(url=url, url_id=folder.name, references=[], registry_entries=[], snapshots=[snapshot],
                        status="saved", http_status=200)
        (attempt / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        targets.append(dict(url=url, url_id=folder.name, references=[dict(label="FAQ", catalogue_line=1)],
                            registry_entries=[dict(definition=dict(source_id="sem-faq", start_url=url, allowed_hosts=["www.sem.example"],
                                                                   allowed_path_prefixes=["/"], canonical_authority="SEM",
                                                                   jurisdiction="CH", language="en"))]))
    (run / "plan.json").write_text(json.dumps(dict(catalogue_sha256="c" * 64, targets=targets)), encoding="utf-8")
    return run


def topic_page(number: int) -> bytes:
    body = f"<html lang=\"en\"><head><title>Page</title></head><body><main><h1>Topic {number}</h1><h2>Rule</h2>" \
           f"<p>Only this page says rule number {number}.</p>"
    return body.encode() + CONTACT + b"</main></body></html>"


def tariff_page(rows: int) -> bytes:
    table = "".join(f"<h2>Row {n}</h2><p>Tariff value {n}.</p>" for n in range(rows))
    return f"<html lang=\"en\"><head><title>Tariffs</title></head><body><main><h1>Tariffs</h1>{table}</main></body></html>".encode()


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = make_run(self.root, {URL: PAGE})
        run_extraction(self.run, log=lambda *a, **k: None)
        self.text = self.run / "text"
        index = json.loads((self.text / "index.json").read_text(encoding="utf-8"))
        self.document_id = index[0]["document_id"]
        self.curation_path = self.root / "curation.yaml"
        self.curation_path.write_text(CURATION.replace("DOC", self.document_id), encoding="utf-8")
        self.curation = load_curation(self.curation_path)
        self.release, _ = build_release(self.curation, self.text, "test-2026-09-22-v1")
        self.today = date(2026, 9, 22)

    def tearDown(self):
        self.temporary.cleanup()

    def dispositions(self, *items: dict) -> CoverageDispositions:
        return CoverageDispositions(pack="test", dispositions=[Disposition(author="tester", date=self.today, **item) for item in items])

    def test_an_uncited_section_is_unclassified_and_the_source_is_partly_covered(self):
        report = build_coverage(self.release, self.text, catalogue=CATALOGUE, today=self.today)
        self.assertEqual(report["counts"]["candidates"], 1)
        self.assertEqual(report["counts"]["content_sections"], 2)
        self.assertEqual((report["counts"]["cited_sections"], report["counts"]["unclassified_sections"]), (1, 1))
        self.assertEqual(report["counts"]["units"], {"section": 1})
        document = report["documents"][0]
        self.assertEqual(document["status"], "partly_cited")
        self.assertEqual(document["unclassified_sections"][0]["heading_path"], ["FAQ", "Fees"])
        self.assertFalse(report["clean"])
        self.assertTrue(report["passed"], "the default policy reports and does not fail")
        sources = {s["source_id"]: s for s in report["sources"]}
        self.assertEqual(sources["sem-faq"]["status"], "partly_covered")
        self.assertEqual(sources["sem-unused"]["status"], "no_documents")
        self.assertEqual(sources["sem-faq"]["title"], "SEM FAQ")
        self.assertIn("## Unclassified sections", render_markdown(report))
        self.assertFalse(build_coverage(self.release, self.text, policy="enforce", today=self.today)["passed"])

    def test_a_section_disposition_names_the_gap_and_out_of_scope_needs_a_manifest_entry(self):
        section_id = build_coverage(self.release, self.text, today=self.today)["documents"][0]["unclassified_sections"][0]["section_id"]
        good = self.dispositions(dict(kind="out_of_scope", reason="Fees are out of scope.", document_id=self.document_id,
                                      section_ids=[section_id], out_of_scope_entry="Fees and appointment availability"))
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=good, today=self.today)
        self.assertTrue(report["clean"] and report["passed"])
        self.assertEqual(report["documents"][0]["status"], "cited_and_dispositioned")
        self.assertEqual(report["documents"][0]["dispositions"], {"out_of_scope": 1})
        bad = self.dispositions(dict(kind="out_of_scope", reason="Fees are out of scope.", document_id=self.document_id,
                                     section_ids=[section_id], out_of_scope_entry="Quotas"))
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=bad, today=self.today)
        self.assertFalse(report["clean"])
        self.assertEqual(report["counts"]["findings"]["unknown_out_of_scope_entry"], 1)

    def test_a_deferral_expires_and_a_stale_disposition_is_reported(self):
        deferred = self.dispositions(dict(kind="deferred", reason="Fees next week.", document_id=self.document_id,
                                          reaffirm_by=self.today + timedelta(days=30)))
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=deferred, today=self.today)
        self.assertTrue(report["clean"])
        self.assertEqual(report["documents"][0]["dispositions"], {"deferred": 1})
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=deferred, today=self.today + timedelta(days=31))
        self.assertFalse(report["clean"])
        self.assertEqual(report["counts"]["findings"]["expired"], 1)
        stale = self.dispositions(dict(kind="duplicate", reason="Gone.", document_id="doc-0000000000000000dead"))
        report = build_coverage(self.release, self.text, dispositions=stale, today=self.today)
        self.assertEqual(report["counts"]["findings"]["stale"], 1)
        self.assertEqual(report["counts"]["unclassified_sections"], 1)

    def test_a_prefix_rule_covers_its_known_documents_and_flags_new_ones(self):
        unknown = self.dispositions(dict(kind="navigation", reason="Whole FAQ tree.", url_prefix="https://www.sem.example/"))
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=unknown, today=self.today)
        self.assertEqual(report["counts"]["unclassified_sections"], 0)
        self.assertEqual(report["counts"]["findings"]["new_under_rule"], 1)
        self.assertFalse(report["clean"])
        known = self.dispositions(dict(kind="navigation", reason="Whole FAQ tree.", url_prefix="https://www.sem.example/",
                                       known_documents=[self.document_id]))
        report = build_coverage(self.release, self.text, policy="enforce", dispositions=known, today=self.today)
        self.assertTrue(report["clean"])
        idle = self.dispositions(dict(kind="navigation", reason="Nothing here.", url_prefix="https://www.other.example/"))
        report = build_coverage(self.release, self.text, dispositions=idle, today=self.today)
        self.assertEqual(report["counts"]["findings"]["unused_rule"], 1)
        self.assertFalse(report["clean"], "an unused rule is only informational; the section is still open")

    def test_dispositions_validate_their_shape_and_round_trip(self):
        with self.assertRaises(ValidationError):
            Disposition(kind="duplicate", reason="x", author="a", date=self.today)  # no target
        with self.assertRaises(ValidationError):
            Disposition(kind="duplicate", reason="x", author="a", date=self.today, document_id="d", url_prefix="u")
        with self.assertRaises(ValidationError):
            Disposition(kind="out_of_scope", reason="x", author="a", date=self.today, document_id="d")
        with self.assertRaises(ValidationError):
            Disposition(kind="deferred", reason="x", author="a", date=self.today, document_id="d")
        with self.assertRaises(ValidationError):
            Disposition(kind="navigation", reason="x", author="a", date=self.today, url_prefix="u", section_ids=["s"])
        dispositions = self.dispositions(dict(kind="deferred", reason="Later.", document_id=self.document_id, reaffirm_by=self.today))
        path = self.root / "curation-coverage.yaml"
        path.write_text(dump_dispositions(dispositions), encoding="utf-8")
        loaded = load_dispositions(path)
        self.assertEqual(loaded, dispositions)
        self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8"))["schema_version"], "swiss-tip-curation-coverage/v1")

    def test_coverage_policy_is_a_curation_field_the_release_does_not_carry(self):
        self.assertEqual(self.curation.coverage_policy, "report")
        strict = Curation.model_validate(yaml.safe_load(CURATION.replace("DOC", self.document_id)) | dict(coverage_policy="enforce"))
        self.assertEqual(strict.coverage_policy, "enforce")
        self.assertNotIn("coverage_policy", self.release.manifest.model_dump())
        with self.assertRaises(ValidationError):
            Curation.model_validate(yaml.safe_load(CURATION.replace("DOC", self.document_id)) | dict(coverage_policy="never"))

    def test_cli_writes_json_and_markdown_and_exits_by_policy(self):
        release_path = self.root / "release.json"
        release_path.write_text(dump_release(self.release), encoding="utf-8")
        output = self.root / "curation-coverage.json"
        arguments = ["--release", str(release_path), "--text", str(self.text), "--curation", str(self.curation_path), "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["counts"]["unclassified_sections"], 1)
        self.assertTrue(output.with_suffix(".md").exists())
        self.assertEqual(coverage_counts(report)["clean"], False)
        self.curation_path.write_text(self.curation_path.read_text(encoding="utf-8") + "\ncoverage_policy: enforce\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 1)
        write_report(report, output)
        self.assertTrue(output.exists())


class AutomaticClassTests(unittest.TestCase):
    """Boilerplate, reference documents and oversized pages are settled without a curator."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        pages = {URL: PAGE.replace(b"</main>", CONTACT + b"</main>")}
        for number in range(5):
            pages[f"https://www.sem.example/topic-{number}.html"] = topic_page(number)
        pages["https://www.sem.example/tariffs.html"] = tariff_page(120)
        self.run = make_run(self.root, pages)
        run_extraction(self.run, log=lambda *a, **k: None)
        self.text = self.run / "text"
        self.index = json.loads((self.text / "index.json").read_text(encoding="utf-8"))
        self.by_url = {e["source_url"]: e for e in self.index}
        faq = self.by_url[URL]["document_id"]
        self.curation = Curation.model_validate(yaml.safe_load(CURATION.replace("DOC", faq)))
        self.release, _ = build_release(self.curation, self.text, "test-2026-09-22-v1")
        self.today = date(2026, 9, 22)

    def tearDown(self):
        self.temporary.cleanup()

    def documents(self, report) -> dict:
        return {d["source_url"]: d for d in report["documents"]}

    def test_a_telephone_card_repeated_on_five_pages_is_boilerplate_not_a_question_for_the_curator(self):
        report = build_coverage(self.release, self.text, today=self.today)
        documents = self.documents(report)
        topic = documents["https://www.sem.example/topic-0.html"]
        self.assertEqual((topic["boilerplate"], topic["unclassified"]), (1, 1), topic)
        self.assertEqual(topic["unclassified_sections"][0]["heading_path"], ["Topic 0", "Rule"])
        self.assertEqual(documents[URL]["boilerplate"], 1)
        self.assertGreaterEqual(report["counts"]["boilerplate_sections"], 6)
        open_headings = [s["heading_path"] for d in report["documents"] for s in d["unclassified_sections"]]
        self.assertNotIn("Telefon", json.dumps(open_headings))

    def test_a_reference_document_is_one_unit_cited_by_any_article(self):
        # Re-label the FAQ as a plugin document, the way the index marks a statute the Fedlex plugin resolved.
        for entry in self.index:
            if entry["source_url"] == URL:
                entry["attribution_kind"] = "plugin-document"
        (self.text / "index.json").write_text(json.dumps(self.index), encoding="utf-8")
        report = build_coverage(self.release, self.text, today=self.today)
        faq = self.documents(report)[URL]
        self.assertEqual((faq["unit"], faq["units"], faq["cited"], faq["unclassified"]), ("document", 1, 1, 0), faq)
        self.assertEqual(faq["status"], "cited")
        self.assertEqual(report["counts"]["units"]["document"], 1)

    def test_an_oversized_page_is_judged_by_its_top_two_heading_levels(self):
        report = build_coverage(self.release, self.text, today=self.today)
        tariffs = self.documents(report)["https://www.sem.example/tariffs.html"]
        self.assertEqual(tariffs["unit"], "rolled_up")
        self.assertLess(tariffs["units"], 130)
        self.assertEqual(tariffs["status"], "unclassified")
        self.assertTrue(all(len(s["heading_path"]) <= 2 for s in tariffs["unclassified_sections"]))
        self.assertGreater(len(tariffs["unclassified_sections"][0]["section_ids"]), 0)
