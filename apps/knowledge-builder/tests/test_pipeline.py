import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from swisstip.build.acceptance import load_acceptance
from swisstip.builder.pipeline import STAGES, Pipeline, default_run_dir, next_release_id
from swisstip.core.readiness import readiness_status
from swisstip.core.release import load_release
from swisstip.ingestion.review_decisions import REVIEW_SCHEMA

URL = "https://www.sem.example/faq.html"
# The page was saved yesterday: the snapshot date differs from the day a test runs, and the release's freshness
# window (60 days) leaves the ready stage its runway on any day.
RETRIEVED = date.today() - timedelta(days=1)
RETRIEVED_AT = f"{RETRIEVED.isoformat()}T06:00:00+00:00"
PAGE = (b"<html lang=\"en\"><head><title>FAQ</title></head><body><main><h1>FAQ</h1><h2>Registration</h2>"
        b"<p>Register within 14 days of arrival.</p><p>Register before starting work.</p></main></body></html>")
CURATION = """
schema_version: swiss-tip-curation/v1
pack: test
title: Test pack
scope_statement: Registration of EU/EFTA nationals.
out_of_scope: [Fees]
out_of_scope_response: Say that it is not covered.
limitations: [Assistant-authored, unreviewed.]
publishers:
  www.sem.example: State Secretariat for Migration SEM
context_fields:
  population: {enum: [eu_efta, third_country], description: Citizenship group.}
topics:
  - {topic_id: residence, title: Residence, description: Permits and registration.}
concepts:
  - concept_id: registration-deadline
    topic_id: residence
    label: Registration deadline
    description: When EU/EFTA nationals register.
    required_context: [population]
    facts:
      - fact_id: registration-deadline-1
        statement: Register within 14 days of arrival and before starting work.
        condition: {population: eu_efta}
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: test}
        evidence:
          - {document_id: DOC, first_block: 3, last_block: 4}
"""
ACCEPTANCE = """
schema_version: swiss-tip-acceptance/v1
pack: test
cases:
  - case_id: T-1
    label: registration deadline
    question: By when must I register?
    expected_answer: Within 14 days of arrival and before starting work.
    steps:
      - resolve:
          concept_ids: [registration-deadline]
          context: {population: eu_efta}
          expect_status: {registration-deadline: SUPPORTED}
    claims:
      - claim: Registration is due within 14 days
        concept: registration-deadline
        statement_contains: [within 14 days]
        excerpt_contains: [within 14 days of arrival]
    answer:
      criteria: [C1]
      must_mention: {fourteen_days: '\\b14\\b'}
BROKEN"""
BROKEN_CASE = """
  - case_id: T-2
    label: wording the release does not state
    question: By when must I register?
    expected_answer: Within 30 days.
    steps:
      - resolve:
          concept_ids: [registration-deadline]
          context: {population: eu_efta}
    claims:
      - claim: Registration is due within 30 days
        concept: registration-deadline
        statement_contains: [within 30 days]
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_root(root: Path) -> Path:
    pack = root / "releases" / "test"
    pack.mkdir(parents=True)
    catalogue = json.dumps(dict(catalog=dict(artifact_id="test", version="1"), sources=[])).encode()
    (pack / "sources.json").write_bytes(catalogue)
    run = root / ".local" / "test"
    folder = run / "pages" / sha256(URL.encode())
    attempt = folder / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "response.html").write_bytes(PAGE)
    snapshot = dict(relative_path=f"pages/{folder.name}/attempt-001/response.html", requested_url=URL, final_url=URL,
                    content_type="text/html", sha256=sha256(PAGE), bytes_downloaded=len(PAGE),
                    retrieved_at=RETRIEVED_AT, review_flags=[])
    manifest = dict(url=URL, url_id=folder.name, references=[], registry_entries=[], snapshots=[snapshot], status="saved",
                    http_status=200, started_at=RETRIEVED_AT, finished_at=RETRIEVED_AT.replace(":00+", ":01+"))
    (attempt / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    target = dict(url=URL, url_id=folder.name, references=[dict(label="FAQ", catalogue_line=1, catalogue="sources.json")],
                  registry_entries=[dict(definition=dict(source_id="sem-faq", start_url=URL, allowed_hosts=["www.sem.example"],
                                                         allowed_path_prefixes=["/"], canonical_authority="SEM", jurisdiction="CH",
                                                         language="en"), scan_status="ready")])
    plan = dict(schema_version="swisstip.catalogue-download-plan/v1", catalogue=str(pack / "sources.json"),
                catalogue_sha256=sha256(catalogue), targets=[target], allowed_redirect_hosts=["www.sem.example"])
    (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps(dict(schema_version="swisstip.catalogue-download/v1", catalogue_sha256=sha256(catalogue),
                                                       target_count=1, counts=dict(saved=1), results=[dict(url=URL, status="saved")])),
                                      encoding="utf-8")
    return root


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self.temporary.name))
        self.logs = []

    def tearDown(self):
        self.temporary.cleanup()

    def pipeline(self, **options) -> Pipeline:
        return Pipeline(self.root, "test", log=self.logs.append, **options)

    def statuses(self, report) -> dict:
        return {s["stage"]: s["status"] for s in report["stages"]}

    def write_curation(self):
        index = json.loads((self.root / ".local/test/text/index.json").read_text(encoding="utf-8"))
        (self.root / "releases/test/curation.yaml").write_text(CURATION.replace("DOC", index[0]["document_id"]), encoding="utf-8")

    def test_stages_run_in_order_and_skip_when_unchanged(self):
        first = self.pipeline().run_stages()
        self.assertEqual(self.statuses(first), {"acquire": "skipped", "gaps": "ran", "extract": "ran", "validate-text": "ran",
                                                "build": "skipped", "validate-release": "skipped", "coverage": "skipped",
                                                "health": "skipped", "accept": "skipped"})
        self.assertEqual(first["exit_code"], 0)
        self.assertTrue((self.root / ".local/test/gap-report.md").is_file())
        self.write_curation()
        second = self.pipeline().run_stages()
        self.assertEqual(self.statuses(second)["build"], "ran")
        self.assertEqual(self.statuses(second)["validate-release"], "ran")
        self.assertEqual(self.statuses(second)["health"], "ran")
        self.assertEqual(self.statuses(second)["accept"], "skipped")
        release_id = load_release(self.root / "releases/test/release.json").manifest.release_id
        self.assertEqual(release_id, f"test-{date.today().isoformat()}-v1")
        self.assertTrue((self.root / "releases/test/pipeline-report.json").is_file())
        third = self.pipeline().run_stages()
        self.assertEqual(self.statuses(third)["build"], "skipped")
        self.assertEqual(self.statuses(third)["gaps"], "skipped")
        self.assertEqual(load_release(self.root / "releases/test/release.json").manifest.release_id, release_id)

    def test_changed_curation_bumps_the_release_id(self):
        self.pipeline().run_stages(until="validate-text")
        self.write_curation()
        self.pipeline().run_stages(start="build")
        self.assertEqual(self.statuses(self.pipeline().run_stages(start="build"))["build"], "skipped")
        path = self.root / "releases/test/curation.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("When EU/EFTA nationals register.", "When to register."), encoding="utf-8")
        report = self.pipeline().run_stages(start="build")
        self.assertEqual(self.statuses(report)["build"], "ran")
        self.assertEqual(load_release(self.root / "releases/test/release.json").manifest.release_id, f"test-{date.today().isoformat()}-v2")
        explicit = self.pipeline(release_id="test-final").run_stages(start="build")
        self.assertEqual(self.statuses(explicit)["build"], "ran")
        self.assertEqual(load_release(self.root / "releases/test/release.json").manifest.release_id, "test-final")

    def test_the_place_files_are_embedded_and_a_changed_one_rebuilds_the_release(self):
        self.pipeline().run_stages(until="validate-text")
        self.write_curation()
        places = self.root / "config/places"
        places.mkdir(parents=True)
        register = dict(schema_version="swiss-tip-places/v1", country="CH", title="Register", publisher="FSO",
                        url="https://example.gov/snapshot", accessed_on="2026-09-18", raw_sha256="a" * 64,
                        places=[dict(code="CH-ZH", name="Zürich"), dict(code="CH-ZH-261", name="Zürich")])
        aliases = dict(schema_version="swiss-tip-place-aliases/v1", country=dict(code="CH", name="Switzerland"), aliases={})
        (places / "ch-register.json").write_text(json.dumps(register), encoding="utf-8")
        (places / "ch-aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
        path = self.root / "releases/test/curation.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "pack: test\n", "pack: test\nplace_register: ../../config/places/ch-register.json\n"
                            "place_aliases: ../../config/places/ch-aliases.json\n"), encoding="utf-8")
        self.assertEqual(self.statuses(self.pipeline().run_stages(start="build"))["build"], "ran")
        release = load_release(self.root / "releases/test/release.json")
        self.assertEqual([place.code for place in release.place_register.places], ["CH", "CH-ZH", "CH-ZH-261"])
        self.assertEqual(self.statuses(self.pipeline().run_stages(start="build"))["build"], "skipped")
        # An edited alias is an input of the build like an edited fact: the release is rebuilt under the next ID.
        aliases["aliases"] = {"CH-ZH": ["Zurigo"]}
        (places / "ch-aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
        self.assertEqual(self.statuses(self.pipeline().run_stages(start="build"))["build"], "ran")
        rebuilt = load_release(self.root / "releases/test/release.json")
        self.assertEqual(rebuilt.place_register.places[1].aliases, ["Zurigo"])
        self.assertNotEqual(rebuilt.manifest.release_id, release.manifest.release_id)
        # An alias for a place the register does not list stops the build.
        aliases["aliases"] = {"CH-ZH-9999": ["Gone"]}
        (places / "ch-aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
        failed = self.pipeline().run_stages(start="build")
        self.assertEqual(self.statuses(failed)["build"], "failed")
        self.assertIn("CH-ZH-9999", next(s["error"] for s in failed["stages"] if s["stage"] == "build"))

    def test_gap_report_cache_tracks_review_file_creation_change_and_removal(self):
        pipeline = self.pipeline()
        self.assertEqual(pipeline.gaps()[0], "ran")
        self.assertEqual(pipeline.gaps()[0], "skipped")
        decisions_path = pipeline.run / "review-decisions.json"
        decisions = dict(schema_version=REVIEW_SCHEMA, catalogue_sha256=sha256(pipeline.catalogue.read_bytes()),
                         acquisition=[], text_documents=[])
        decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        self.assertEqual(pipeline.gaps()[0], "ran")
        self.assertEqual(pipeline.gaps()[0], "skipped")
        decisions["scope"] = dict(review_status="human-reviewed", decision="approved", reviewed_by="test",
                                 reviewed_on="2026-09-14", reason="Approved demonstration scope")
        decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        self.assertEqual(pipeline.gaps()[0], "ran")
        decisions_path.unlink()
        self.assertEqual(pipeline.gaps()[0], "ran")
        self.assertEqual(pipeline.gaps()[0], "skipped")

    def test_changed_catalogue_stops_the_pipeline(self):
        (self.root / "releases/test/sources.json").write_bytes(b'{"sources": ["changed"]}')
        report = self.pipeline().run_stages()
        self.assertEqual(self.statuses(report), {"acquire": "failed"})
        self.assertIn("new run directory", report["stages"][0]["error"])
        self.assertEqual(report["exit_code"], 1)

    def test_stage_range_is_validated(self):
        with self.assertRaises(ValueError):
            self.pipeline().run_stages(start="build", until="extract")
        self.assertEqual(STAGES[0], "acquire")

    def test_a_run_committed_under_the_pack_is_the_default_run(self):
        self.assertEqual(default_run_dir(self.root, "test"), self.root / ".local" / "test")
        pack, local = self.root / "releases/test", self.root / ".local/test"
        for name in ("pages", "plan.json", "summary.json"):
            (local / name).rename(pack / name)
        self.assertEqual(default_run_dir(self.root, "test"), pack)
        report = self.pipeline().run_stages(until="validate-text")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(Path(report["run"]), pack.resolve())
        self.assertTrue((pack / "text/index.json").is_file())
        self.assertFalse((local / "text").exists())

    def write_acceptance(self, broken: str = ""):
        (self.root / "releases/test/acceptance.yaml").write_text(ACCEPTANCE.replace("BROKEN", broken), encoding="utf-8")

    def test_accept_stage_replays_the_suite_and_writes_its_report(self):
        self.pipeline().run_stages(until="validate-text")
        self.write_curation()
        self.pipeline().run_stages(until="health")
        self.write_acceptance()
        report = self.pipeline().run_stages(start="accept", until="accept")
        entry = next(s for s in report["stages"] if s["stage"] == "accept")
        self.assertEqual(entry["status"], "ran")
        self.assertEqual((entry["cases"], entry["blocking"], entry["failed"]), (1, 1, []))
        self.assertEqual(report["exit_code"], 0)
        written = json.loads((self.root / "releases/test/acceptance-report.json").read_text(encoding="utf-8"))
        self.assertTrue(written["passed"])
        self.assertEqual(written["as_of"], RETRIEVED.isoformat())  # the snapshot date, not the day the test runs
        self.assertEqual(written["results"][0]["claims"][0]["fact_ids"], ["registration-deadline-1"])

        self.write_acceptance(BROKEN_CASE)
        report = self.pipeline().run_stages(start="accept", until="accept")
        entry = next(s for s in report["stages"] if s["stage"] == "accept")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("1 of 2 blocking acceptance case(s) failed", entry["error"])
        self.assertIn("T-2 (wording the release does not state)", entry["error"])
        self.assertEqual(report["exit_code"], 1)
        written = json.loads((self.root / "releases/test/acceptance-report.json").read_text(encoding="utf-8"))
        self.assertEqual((written["passed"], written["failed"]), (False, ["T-2"]))

    def test_accept_stage_skips_a_pack_without_a_suite_and_rejects_another_packs_suite(self):
        self.pipeline().run_stages(until="validate-text")
        self.write_curation()
        self.pipeline().run_stages(until="health")
        report = self.pipeline().run_stages(start="accept", until="accept")
        self.assertEqual(self.statuses(report)["accept"], "skipped")
        (self.root / "releases/test/acceptance.yaml").write_text(ACCEPTANCE.replace("pack: test", "pack: other").replace("BROKEN", ""),
                                                                 encoding="utf-8")
        report = self.pipeline().run_stages(start="accept", until="accept")
        self.assertEqual(self.statuses(report)["accept"], "failed")
        self.assertIn("for pack 'other'", next(s for s in report["stages"] if s["stage"] == "accept")["error"])

    def build_and_accept(self, suite: str = ACCEPTANCE):
        self.pipeline().run_stages(until="validate-text")
        self.write_curation()
        self.pipeline().run_stages(until="health")
        (self.root / "releases/test/acceptance.yaml").write_text(suite.replace("BROKEN", ""), encoding="utf-8")
        self.pipeline().run_stages(start="accept", until="accept")

    def ready(self, **options) -> dict:
        report = self.pipeline(**options).run_stages(start="ready", until="ready")
        return next(s for s in report["stages"] if s["stage"] == "ready")

    def test_ready_stage_needs_an_attestation_and_binds_the_record_to_the_release_bytes(self):
        self.build_and_accept()
        release = self.root / "releases/test/release.json"
        entry = self.ready()
        self.assertEqual(entry["status"], "failed")
        self.assertIn("--attested-by", entry["error"])
        self.assertEqual(readiness_status(release)["status"], "candidate")

        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "ran", entry.get("error"))
        self.assertEqual(entry["gates"], {"G1": "passed", "G2": "passed", "G3": "passed", "G4": "passed", "G5": "passed",
                                          "G6": "passed"})
        record = json.loads((self.root / "releases/test/readiness.json").read_text(encoding="utf-8"))
        self.assertEqual(record["release_sha256"], sha256(release.read_bytes()))
        self.assertEqual((record["attested_by"], record["cases"]["total"], record["min_runway_days"]), ("A. Person", 1, 14))
        self.assertEqual(readiness_status(release)["status"], "ready")

        # A suite edited after the accept stage (here only its expected answer) is another suite: G6 fails until accept runs again.
        (self.root / "releases/test/acceptance.yaml").write_text(
            ACCEPTANCE.replace("BROKEN", "").replace("Within 14 days of arrival and before starting work.", "Within 14 days."), encoding="utf-8")
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("G6", entry["error"])
        self.assertNotIn("G3", entry["error"])
        self.pipeline().run_stages(start="accept", until="accept")
        self.assertEqual(self.ready(attested_by="A. Person")["status"], "ran")

        # A changed release file is a candidate again, whatever the record says.
        release.write_bytes(release.read_bytes() + b"\n")
        self.assertEqual(readiness_status(release)["status"], "candidate")

    def test_ready_stage_fails_on_a_failing_case_or_a_short_runway(self):
        self.build_and_accept(ACCEPTANCE.replace("BROKEN", BROKEN_CASE))
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("G3 failed: T-2", entry["error"])
        self.assertFalse((self.root / "releases/test/readiness.json").exists())

        self.build_and_accept(ACCEPTANCE.replace("pack: test\n", "pack: test\npolicy: {min_runway_days: 100000}\n"))
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("G4", entry["error"])
        self.assertIn("runway until", entry["error"])

    def test_ready_stage_reads_the_graded_answers_as_gate_g5(self):
        required = ACCEPTANCE.replace("pack: test\n", "pack: test\npolicy: {answer_check: required, answer_repetitions: 2}\n")
        self.build_and_accept(required)
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("G5 no acceptance-answers.json", entry["error"])

        release = load_release(self.root / "releases/test/release.json")
        suite = load_acceptance(self.root / "releases/test/acceptance.yaml")
        answers_path = self.root / "releases/test/acceptance-answers.json"

        def write_answers(graded, trap_held, criteria_pass, suite_sha256=None):
            answers_path.write_text(json.dumps(dict(
                schema_version="swiss-tip-acceptance-answers/v1", pack="test", release_id=release.manifest.release_id,
                content_sha256=release.manifest.content_sha256, suite_sha256=suite_sha256 or suite.digest(),
                aggregated_at="2026-09-15T12:00:00+00:00",
                cases={"T-1": dict(runs=graded, graded=graded, trap_held=trap_held, criteria_pass=criteria_pass,
                                   models=["m"], unsupported_claims=[], run_folders=[])})), encoding="utf-8")

        write_answers(2, 2, 1)
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("criteria passed in 1 of 2", entry["error"])
        write_answers(2, 2, 2, suite_sha256="other")
        self.assertIn("another release or suite", self.ready(attested_by="A. Person")["error"])
        write_answers(2, 2, 2)
        entry = self.ready(attested_by="A. Person")
        self.assertEqual(entry["status"], "ran", entry.get("error"))
        self.assertEqual(entry["gates"]["G5"], "passed")

    def test_coverage_stage_writes_its_report_and_the_ready_stage_carries_the_counts(self):
        report = self.pipeline().run_stages("acquire", "validate-text")
        self.write_curation()
        report = self.pipeline().run_stages("build", "coverage")
        self.assertEqual(self.statuses(report)["coverage"], "ran")
        coverage = json.loads((self.root / "releases/test/curation-coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(coverage["policy"], "report")
        self.assertEqual(coverage["counts"]["candidates"], 1)
        self.assertEqual(coverage["counts"]["unclassified_sections"], 0)
        self.assertTrue(coverage["clean"])
        self.assertTrue((self.root / "releases/test/curation-coverage.md").exists())
        self.assertEqual(coverage["sources"][0]["source_id"], "sem-faq")
        # A disposition file for another pack stops the stage; one for this pack is read.
        dispositions = self.root / "releases/test/curation-coverage.yaml"
        dispositions.write_text("schema_version: swiss-tip-curation-coverage/v1\npack: other\ndispositions: []\n", encoding="utf-8")
        report = self.pipeline().run_stages("coverage", "coverage")
        self.assertEqual(self.statuses(report)["coverage"], "failed")
        dispositions.write_text("schema_version: swiss-tip-curation-coverage/v1\npack: test\ndispositions: []\n", encoding="utf-8")
        report = self.pipeline().run_stages("coverage", "coverage")
        self.assertEqual(self.statuses(report)["coverage"], "ran")
        (self.root / "releases/test/acceptance.yaml").write_text(ACCEPTANCE.replace("BROKEN", ""), encoding="utf-8")
        report = self.pipeline(attested_by="Tester").run_stages("accept", "ready")
        self.assertEqual(self.statuses(report)["ready"], "ran")
        readiness = json.loads((self.root / "releases/test/readiness.json").read_text(encoding="utf-8"))
        self.assertEqual(readiness["coverage"]["candidates"], 1)
        self.assertTrue(readiness["coverage"]["clean"])
        self.assertEqual(readiness_status(self.root / "releases/test/release.json")["status"], "ready")

    def test_a_report_record_of_the_former_ground_stage_is_dropped(self):
        self.pipeline().run_stages(until="validate-text")
        path = self.root / "releases/test/pipeline-report.json"
        previous = json.loads(path.read_text(encoding="utf-8"))
        previous["stages"].append(dict(stage="ground", status="ran", cases=7))
        path.write_text(json.dumps(previous), encoding="utf-8")
        report = self.pipeline().run_stages(start="health", until="health")
        self.assertNotIn("ground", self.statuses(report))
        self.assertEqual(self.statuses(report)["validate-text"], "ran")

    def test_next_release_id(self):
        today = date(2026, 9, 12)
        self.assertEqual(next_release_id(None, "mvp-zurich", today), "mvp-zurich-2026-09-12-v1")
        self.assertEqual(next_release_id("mvp-zurich-2026-09-12-v2", "mvp-zurich", today), "mvp-zurich-2026-09-12-v3")
        self.assertEqual(next_release_id("mvp-zurich-2026-09-11-v4", "mvp-zurich", today), "mvp-zurich-2026-09-12-v1")
        self.assertEqual(next_release_id("custom", "mvp-zurich", today), "mvp-zurich-2026-09-12-v1")


if __name__ == "__main__":
    unittest.main()
