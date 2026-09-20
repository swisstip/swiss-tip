"""Console tests on a synthetic pack: no network, no browser, one FastAPI test client.

The areas are those of section 10 of docs/architecture/admin-console.md: data,
sources, reading view, workbench, review, relocation, release, sandbox, jobs,
modes and calls.
"""

import hashlib
import json
import time
import unittest
from datetime import date

from fastapi.testclient import TestClient

from swisstip.admin_console.app import create_app
from swisstip.admin_console.calls import aggregate, parse_line, percentile
from swisstip.admin_console.checks import load_checks, run_checks, seed_checks
from swisstip.admin_console.data import coverage_matrix, pack_card, paginate
from swisstip.admin_console.screens.documents import align_blocks, excerpt_of
from swisstip.admin_console.screens.release import release_diff
from swisstip.admin_console.screens.review import queue, sample_queue
from swisstip.admin_console.screens.runs import relocation_rows
from swisstip.admin_console.screens.sources import catalogue_rows, discovered_groups
from swisstip.admin_console.writes import WriteConflict, WriteRefused, read_audit, write_curation
from swisstip.build.curation import load_curation
from swisstip.core.release import load_release
from swisstip.ingestion.gap_report import build_report
from swisstip.ingestion.review_decisions import REVIEW_SCHEMA, observation_fingerprint, text_fingerprint
from swisstip.runtime.service import ReleaseService

from support import PackFixture

ACTOR = "Anna Meier"


class ConsoleTestCase(unittest.TestCase):
    with_checks = True
    read_only = False

    @classmethod
    def setUpClass(cls):
        cls.fixture = PackFixture(with_checks=cls.with_checks)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.fixture.restore()
        self.app = create_app(self.fixture.root, actor=ACTOR, read_only=self.read_only)
        self.client = TestClient(self.app)
        self.pack = self.app.state.console.pack("test-pack")

    def curation_sha(self) -> str:
        return self.pack.curation.sha256

    def get(self, url: str, **kwargs):
        response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200, f"{url} -> {response.status_code}: {response.text[:500]}")
        return response

    def post(self, url: str, data: dict, expected: int = 303):
        response = self.client.post(url, data=data, follow_redirects=False)
        self.assertEqual(response.status_code, expected,
                         f"{url} -> {response.status_code}: {response.text[:800]}")
        return response


# --- data --------------------------------------------------------------------


class DataTests(ConsoleTestCase):
    def test_index_reloads_when_the_file_changes(self):
        dataset = self.pack.dataset
        self.assertEqual(len(dataset.entries), 3)
        first = dataset.index_file.sha256
        path = dataset.text_dir / "index.json"
        entries = json.loads(path.read_text(encoding="utf-8"))
        entries[0]["title"] = "Changed title"
        path.write_text(json.dumps(entries), encoding="utf-8")
        self.assertNotEqual(dataset.index_file.sha256, first)
        self.assertIn("Changed title", [entry.get("title") for entry in dataset.entries])
        entries[0]["title"] = "Registration"
        path.write_text(json.dumps(entries), encoding="utf-8")

    def test_record_is_read_on_demand_and_not_retained(self):
        document_id = self.fixture.document("registration")
        record = self.pack.dataset.record(document_id)
        self.assertEqual(record["document_id"], document_id)
        self.assertFalse(hasattr(self.pack.dataset, "_records"))
        self.assertIsNone(self.pack.dataset.record("doc-does-not-exist"))

    def test_paging_clamps_and_counts(self):
        rows = list(range(250))
        first = paginate(rows, 1, 100)
        self.assertEqual((first["page"], first["pages"], first["total"], len(first["rows"])), (1, 3, 250, 100))
        last = paginate(rows, 99, 100)
        self.assertEqual((last["page"], last["first"], last["last"]), (3, 201, 250))
        self.assertEqual(paginate([], 1, 100)["first"], 0)

    def test_pack_card_counts_and_standing_case_facts(self):
        card = pack_card(self.pack)
        self.assertEqual(card["curation"]["facts"], 4)
        self.assertEqual(card["curation"]["concepts"], 2)
        self.assertEqual(card["text"]["records"], 3)
        self.assertEqual(card["curation"]["expected_missing"], [])
        self.assertEqual(card["curation"]["expected_present"],
                         ["registration-deadline-1", "registration-deadline-3"])

    def test_coverage_matrix_reaches_reviewed_only_when_a_fact_is_reviewed(self):
        matrix = coverage_matrix(self.pack)
        federal = next(row for row in matrix["rows"] if row["jurisdiction"] == "CH")
        english = next(cell for cell in federal["cells"] if cell["language"] == "en")
        self.assertEqual(english["stage"], "curated")
        self.assertEqual(english["counts"]["catalogued"], 2)
        self.assertEqual(english["counts"]["reviewed"], 0)

    def test_unknown_pack_is_a_404(self):
        self.assertEqual(self.client.get("/packs/nope/sources").status_code, 404)

    def test_a_run_committed_under_the_pack_is_read_there_and_console_state_stays_local(self):
        local_run = self.fixture.run_dir
        for name in ("pages", "text", "plan.json", "summary.json"):
            (local_run / name).rename(self.fixture.pack_dir / name)
        pack = create_app(self.fixture.root, actor=ACTOR).state.console.pack("test-pack")
        self.assertEqual(pack.run_dir, self.fixture.pack_dir)
        self.assertEqual(pack.console_dir, self.fixture.root / ".local" / "test-pack" / "console")
        self.assertEqual(len(pack.dataset.entries), 3)
        self.assertIsNotNone(pack.plan.current())


class ScopeApprovalTests(ConsoleTestCase):
    def prepare_scope_reviews(self):
        review = dict(review_status="human-reviewed", decision="approved", reviewed_by=ACTOR,
                      reviewed_on="2026-09-14", reason="Approved for the demonstration scope.")
        report = build_report(self.pack.run_dir)
        target = report["targets"][0]
        target.update(status="not_saved", gap="not-found", retriable=False, action="Rediscover the page.")
        approved = dict(target, url="https://www.sem.example/accepted.html", kind="in-scope", source_ids=[])
        pending = dict(target, url="https://www.sem.example/pending.html", kind="in-scope", source_ids=[])
        report["targets"].extend([approved, pending])
        (self.pack.run_dir / "gap-report.json").write_text(json.dumps(report), encoding="utf-8")
        index_path = self.pack.text_dir / "index.json"
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next(item for item in entries if item["document_id"] == self.fixture.document("work"))
        entry["pdf_pages_without_text"] = [2]
        index_path.write_text(json.dumps(entries), encoding="utf-8")
        decisions = dict(schema_version=REVIEW_SCHEMA, catalogue_sha256=report["catalogue_sha256"],
                         acquisition=[dict(review, url=item["url"], observation_sha256=observation_fingerprint(item))
                                      for item in (target, approved)],
                         text_documents=[dict(review, document_id=entry["document_id"],
                                              observation_sha256=text_fingerprint(entry))])
        (self.pack.run_dir / "review-decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
        return target, approved, pending, entry

    def test_approved_sources_leave_outstanding_counts_and_reopen_after_decision_removal(self):
        target, _, _, _ = self.prepare_scope_reviews()
        before_facts = self.pack.curation_path.read_bytes()
        source = next(row for row in catalogue_rows(self.pack) if row["start_url"] == target["url"])
        self.assertEqual(source["disposition"], "approved")
        self.assertEqual(source["target_status"], "not_saved")
        group = discovered_groups(self.pack)[0]
        self.assertEqual(group["approved"], 1)
        self.assertEqual(group["gaps"], {"not-found": 1})
        card = pack_card(self.pack)
        self.assertEqual((card["sources"]["approved"], card["sources"]["not_retriable"]), (2, 1))
        self.assertIn("human-reviewed - approved", self.get("/packs/test-pack/sources").text)
        (self.pack.run_dir / "review-decisions.json").unlink()
        card = pack_card(self.pack)
        self.assertEqual((card["sources"]["approved"], card["sources"]["not_retriable"]), (0, 3))
        self.assertEqual(self.pack.curation_path.read_bytes(), before_facts)

    def test_document_tabs_separate_accepted_items_and_changed_text_requires_review(self):
        target, _, pending, entry = self.prepare_scope_reviews()
        unavailable = [dict(url=item["url"], status="not_saved", error="HTTP 404",
                            attribution=dict(kind="in-scope", source_ids=[])) for item in (target, pending)]
        (self.pack.text_dir / "unavailable.json").write_text(json.dumps(unavailable), encoding="utf-8")
        (self.pack.text_dir / "errors.json").write_text(
            json.dumps([dict(document_id=entry["document_id"], error="No text on one page")]), encoding="utf-8")
        page = self.get("/packs/test-pack/documents?tab=accepted").text
        self.assertIn("Accepted scope (2)", page)
        self.assertIn("Pending unavailable targets (1)", page)
        self.assertIn("Pending extraction failures (0)", page)
        self.assertIn("not_saved", page)
        self.assertIn(ACTOR, page)
        index_path = self.pack.text_dir / "index.json"
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        next(item for item in entries if item["document_id"] == entry["document_id"])["pdf_pages_without_text"] = [2, 3]
        index_path.write_text(json.dumps(entries), encoding="utf-8")
        page = self.get("/packs/test-pack/documents?tab=accepted").text
        self.assertIn("Accepted scope (1)", page)
        self.assertIn("Pending extraction failures (1)", page)


# --- screens render ----------------------------------------------------------


class ScreenTests(ConsoleTestCase):
    def test_every_screen_renders(self):
        document_id = self.fixture.document("registration")
        for url in ("/", "/packs/test-pack/sources", "/packs/test-pack/sources?tab=matrix",
                    "/packs/test-pack/runs", "/packs/test-pack/runs/relocation", "/packs/test-pack/documents",
                    f"/packs/test-pack/documents/{document_id}", "/packs/test-pack/workbench",
                    "/packs/test-pack/workbench/topics/residence",
                    "/packs/test-pack/workbench/concepts/registration-deadline",
                    "/packs/test-pack/workbench/facts/registration-deadline-1",
                    "/packs/test-pack/review", "/packs/test-pack/review?mode=sample",
                    "/packs/test-pack/release", "/packs/test-pack/sandbox", "/packs/test-pack/operations",
                    "/packs/test-pack/audit"):
            with self.subTest(url=url):
                self.assertIn("Swiss TIP admin console", self.get(url).text)

    def test_health_endpoint_reports_the_mode(self):
        body = self.get("/healthz").json()
        self.assertEqual(body["mode"], "edit")
        self.assertEqual(body["packs"], ["test-pack"])

    def test_adding_a_pack_writes_a_catalogue_the_console_can_read(self):
        self.post("/packs", dict(name="new-pack", title="Sources of the new pack"))
        catalogue = (self.fixture.root / "releases" / "new-pack" / "sources.json")
        self.assertTrue(catalogue.is_file())
        pack = self.app.state.console.pack("new-pack")
        self.assertEqual(pack.source_entries(), [])
        self.assertIsNone(pack.catalogue.error)
        self.assertIn("new-pack", self.get("/").text)
        self.assertIn("new-pack", self.get("/healthz").json()["packs"])

    def test_a_pack_that_exists_is_not_overwritten(self):
        before = self.pack.catalogue_path.read_bytes()
        response = self.post("/packs", dict(name="test-pack", title="Another"))
        self.assertIn("exists", response.headers["location"])
        self.assertEqual(self.pack.catalogue_path.read_bytes(), before)


# --- sources -----------------------------------------------------------------


class SourceTests(ConsoleTestCase):
    def source_form(self, **overrides) -> dict:
        form = dict(source_id="sem-new", original_id="", title="New source",
                    start_url="https://www.sem.example/new.html", allowed_hosts="www.sem.example",
                    allowed_path_prefixes="/new.html", canonical_authority="SEM", jurisdiction="CH",
                    language="en", authority_level="federal", source_kind="official_guidance", priority="P1",
                    topic_hints="registration-moving", discovery_method="official_page_link",
                    reference_url="https://www.sem.example/registration.html", located_on="2026-09-13",
                    scan_status="ready", notes="Added by the console test.",
                    sha256=self.pack.catalogue.sha256)
        form.update(overrides)
        return form

    def test_catalogue_table_joins_the_gap_and_record_columns(self):
        page = self.get("/packs/test-pack/sources").text
        self.assertIn("sem-registration", page)
        self.assertIn("sem-zurich", page)

    def test_adding_a_source_writes_the_catalogue_and_the_audit_log(self):
        self.post("/packs/test-pack/sources", self.source_form())
        catalogue = json.loads(self.pack.catalogue_path.read_text(encoding="utf-8"))
        self.assertIn("sem-new", [entry["definition"]["source_id"] for entry in catalogue["sources"]])
        entry = read_audit(self.pack)[0]
        self.assertEqual(entry["actor"], ACTOR)
        self.assertEqual(entry["ids"], ["sem-new"])
        self.assertIn("add source sem-new", entry["reason"])

    def test_a_start_url_outside_its_own_allowlist_is_refused(self):
        response = self.post("/packs/test-pack/sources",
                             self.source_form(source_id="sem-bad", allowed_path_prefixes="/elsewhere"),
                             expected=422)
        self.assertIn("Seed path must be inside its allowlist", response.text)

    def test_a_language_outside_iso_639_1_is_refused(self):
        response = self.post("/packs/test-pack/sources", self.source_form(source_id="sem-lang", language="zz"),
                             expected=422)
        self.assertIn("Invalid seed language hint", response.text)

    def test_a_duplicate_source_id_is_refused(self):
        response = self.post("/packs/test-pack/sources", self.source_form(source_id="sem-registration"),
                             expected=422)
        self.assertIn("already in the catalogue", response.text)

    def test_promote_prefills_the_form_from_a_page(self):
        page = self.get("/packs/test-pack/sources/new?url=https://www.sem.example/other.html&label=Other").text
        self.assertIn("https://www.sem.example/other.html", page)
        self.assertIn("www.sem.example", page)

    def test_a_changed_catalogue_warns_that_the_run_needs_a_new_directory(self):
        self.assertFalse(self.pack.catalogue_changed())
        self.post("/packs/test-pack/sources", self.source_form(source_id="sem-later"))
        self.assertTrue(self.pack.catalogue_changed())
        self.assertIn("plan a new run directory instead", self.get("/packs/test-pack/runs").text)

    def test_a_write_against_a_changed_file_is_refused(self):
        with self.assertRaises(WriteConflict):
            from swisstip.admin_console.writes import write_catalogue
            write_catalogue(self.pack, lambda data: None, ACTOR, "test", expected_sha256="0" * 64)


# --- reading view ------------------------------------------------------------


class ReadingViewTests(ConsoleTestCase):
    def test_blocks_marks_and_tables_render(self):
        document_id = self.fixture.document("registration")
        page = self.get(f"/packs/test-pack/documents/{document_id}").text
        self.assertIn("Register within 14 days of arrival.", page)
        self.assertIn("<th", page)  # the table renders as a table with header cells
        self.assertNotIn("breadcrumb", page)  # furniture is hidden by default
        with_furniture = self.get(f"/packs/test-pack/documents/{document_id}?furniture=1").text
        self.assertIn("breadcrumb", with_furniture)

    def test_cited_blocks_carry_their_fact_ids(self):
        document_id = self.fixture.document("registration")
        marks = self.pack.facts_by_block(document_id)
        self.assertEqual(marks[4], ["registration-deadline-1"])
        self.assertEqual(marks[5], ["registration-deadline-2"])
        page = self.get(f"/packs/test-pack/documents/{document_id}").text
        self.assertIn("/workbench/facts/registration-deadline-1", page)

    def test_the_selection_excerpt_equals_the_build_cut(self):
        document_id = self.fixture.document("registration")
        record = self.pack.dataset.record(document_id)
        selection = excerpt_of(record, 4, 5)
        release = load_release(self.pack.release_path)
        built = next(item for item in release.evidence if item.evidence_id == "e-registration-deadline-1-1")
        self.assertIn(built.original_excerpt, selection["text"])
        self.assertEqual(selection["start"], record["blocks"][3]["start"])
        self.assertEqual(selection["end"], record["blocks"][4]["end"])
        self.assertIsNone(excerpt_of(record, 4, 999))

    def test_the_excerpt_partial_renders_without_the_page_frame(self):
        document_id = self.fixture.document("registration")
        response = self.get(f"/packs/test-pack/documents/{document_id}/excerpt?first=4&last=4",
                            headers={"hx-request": "true"})
        self.assertNotIn("<html", response.text)
        self.assertIn("Register within 14 days", response.text)

    def test_the_diff_view_aligns_blocks_by_hash(self):
        old = self.pack.dataset.record(self.fixture.document("registration"))
        new = json.loads(json.dumps(old))
        new["blocks"][4]["text"] = "Register before starting the work."
        new["blocks"][4]["text_sha256"] = "deadbeef"
        rows = align_blocks(old, new)
        self.assertIn("changed", [row["state"] for row in rows])
        self.assertEqual(sum(row.get("count", 0) for row in rows if row["state"] == "same"), 5)


# --- workbench ---------------------------------------------------------------


class WorkbenchTests(ConsoleTestCase):
    def test_the_fact_form_saves_through_the_models(self):
        self.post("/packs/test-pack/workbench/facts", dict(
            fact_id="registration-deadline-1", statement="Register within 14 days of arrival in Switzerland.",
            language="en", jurisdiction="CH", valid_from="", valid_through="", kind="curated-statement",
            author="test", source="", reviewed_on="", notes="", condition="population=eu_efta",
            sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = curation.concepts[0].facts[0]
        self.assertEqual(fact.statement, "Register within 14 days of arrival in Switzerland.")
        self.assertEqual(fact.condition, {"population": "eu_efta"})

    def test_a_condition_outside_the_enum_is_refused(self):
        before = self.pack.curation_path.read_bytes()
        response = self.post("/packs/test-pack/workbench/facts", dict(
            fact_id="registration-deadline-2", statement="Register before starting work.", language="en",
            jurisdiction="CH", valid_from="", valid_through="", kind="curated-statement", author="test",
            source="", reviewed_on="", notes="", condition="population=martian",
            sha256=self.curation_sha()), expected=422)
        self.assertIn("the build would fail", response.text)
        self.assertEqual(self.pack.curation_path.read_bytes(), before)

    def test_removing_the_only_citation_of_a_fact_is_refused(self):
        response = self.post("/packs/test-pack/workbench/facts/registration-deadline-3/evidence",
                             dict(action="remove", number=1, sha256=self.curation_sha()), expected=422)
        self.assertIn("at least one citation", response.text)

    def test_a_citation_outside_the_record_is_refused(self):
        response = self.post("/packs/test-pack/workbench/facts/registration-deadline-1/evidence",
                             dict(action="add", document_id=self.fixture.document("zurich"),
                                  first_block=99, last_block=99, sha256=self.curation_sha()), expected=422)
        self.assertIn("outside", response.text)

    def test_citing_a_range_adds_evidence_with_an_anchor(self):
        document_id = self.fixture.document("registration")
        self.post("/packs/test-pack/workbench/cite",
                  dict(mode="existing", document_id=document_id, first_block=6, last_block=6,
                       fact_id="registration-deadline-2", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = next(f for c in curation.concepts for f in c.facts if f.fact_id == "registration-deadline-2")
        self.assertEqual(len(fact.evidence), 2)
        self.assertIsNotNone(fact.evidence[1].anchor)
        self.assertIn("Permit", fact.evidence[1].anchor.excerpt)

    def test_a_new_concept_is_created_with_its_first_fact(self):
        document_id = self.fixture.document("work")
        self.post("/packs/test-pack/workbench/concepts/new", dict(
            concept_id="permit-authority", topic_id="residence", label="Issuing authority",
            description="Which office issues the permit.", document_id=document_id, first_block=3,
            last_block=3, statement="The cantonal migration office issues the permit.", jurisdiction="CH",
            sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        concept = next(item for item in curation.concepts if item.concept_id == "permit-authority")
        self.assertEqual(concept.facts[0].fact_id, "permit-authority-1")
        self.assertEqual(concept.facts[0].provenance.review_status, "assistant-authored-unreviewed")

    def test_a_publisher_missing_for_a_cited_host_is_flagged_and_refuses_the_save(self):
        from swisstip.admin_console.screens.workbench import missing_publishers

        self.assertEqual(missing_publishers(self.pack), [])
        text = self.pack.curation_path.read_text(encoding="utf-8")
        self.pack.curation_path.write_text(text.replace(
            "  www.sem.example: State Secretariat for Migration SEM\n", "  www.other.example: Elsewhere\n"),
            encoding="utf-8")
        self.assertEqual(missing_publishers(self.pack), ["www.sem.example"])
        self.assertIn("No publisher is named", self.get("/packs/test-pack/workbench").text)
        # An institution rule for the host covers it as well as a publisher line does (the saved file carries the
        # model's empty `institutions: []`, which the rule replaces).
        self.pack.curation_path.write_text(self.pack.curation_path.read_text(encoding="utf-8").replace(
            "institutions: []\n",
            "institutions:\n  - {institution_id: ch-sem, name: SEM, level: federal, body: administration, "
            "jurisdiction: CH, urls: [www.sem.example/faq]}\n"), encoding="utf-8")
        self.assertEqual(missing_publishers(self.pack), [])
        self.pack.curation_path.write_text(text.replace(
            "  www.sem.example: State Secretariat for Migration SEM\n", "  www.other.example: Elsewhere\n"),
            encoding="utf-8")
        with self.assertRaises(WriteRefused) as caught:
            write_curation(self.pack, lambda curation: None, ACTOR, "a save with no publisher")
        self.assertIn("No publisher known for host", str(caught.exception))

    def test_a_context_field_in_use_cannot_be_removed(self):
        response = self.post("/packs/test-pack/workbench/pack", dict(
            title="Test pack", scope_statement="Scope.", out_of_scope="Fees",
            out_of_scope_response="Not covered.", limitations="", freshness_max_age_days=60,
            publishers="www.sem.example=SEM", context_fields="", sha256=self.curation_sha()), expected=422)
        self.assertIn("cannot be removed", response.text)

    def pack_form(self, **fields) -> dict:
        return dict(dict(title="Test pack", scope_statement="Scope.", out_of_scope="Fees\nProcessing times",
                         out_of_scope_response="Not covered.", limitations="", freshness_max_age_days=60,
                         publishers="www.sem.example=SEM",
                         context_fields="population=eu_efta,third_country|Citizenship group of the person.",
                         institutions="", page_basis="", ranking_policy="", sha256=self.curation_sha()), **fields)

    def test_the_pack_form_saves_the_institutions_and_the_page_basis_and_the_queue_shows_and_filters_the_basis(self):
        from swisstip.admin_console.screens.review import filter_cards

        self.assertIn("none: the cited page has no institution", self.get("/packs/test-pack/review").text)
        self.assertTrue(all(card["basis"] is None for card in queue(self.pack)))
        self.post("/packs/test-pack/workbench/pack", self.pack_form(
            institutions="- {institution_id: ch-sem, name: State Secretariat for Migration SEM, native_name: SEM, "
                         "level: federal, body: administration, jurisdiction: CH, urls: [www.sem.example]}",
            page_basis="www.sem.example/zurich.html: {kind: summary}",
            ranking_policy="floor: 0.8\naggregate: max\nbasis_kind: {act: 1.0, ordinance: 1.0, treaty: 1.0, directive: 0.95, "
                           "guidance: 0.9, directory: 0.9, summary: 0.75}\nprovenance_kind: {curated-statement: 1.0, "
                           "source-section: 0.9, model-candidate: 0.8}\nreview_status: {human-reviewed: 1.0, "
                           "model-candidate-automated-review: 0.8, assistant-authored-unreviewed: 0.7, "
                           "automatically-derived-unreviewed: 0.6}"))
        curation = load_curation(self.pack.curation_path)
        self.assertEqual([item.institution_id for item in curation.institutions], ["ch-sem"])
        self.assertEqual(curation.page_basis["www.sem.example/zurich.html"].kind, "summary")
        self.assertEqual(curation.ranking_policy.floor, 0.8)
        form = self.get("/packs/test-pack/workbench").text
        self.assertIn("institution_id: ch-sem", form)
        self.assertIn("www.sem.example/zurich.html:", form)
        self.assertIn("floor: 0.8", form)
        cards = queue(self.pack)
        self.assertEqual({card["fact"].fact_id: card["basis"] for card in cards}, {
            "registration-deadline-1": "Federal authority guidance", "registration-deadline-2": "Federal authority guidance",
            "registration-deadline-3": "Portal summary of federal rules", "work-permit-1": "Federal authority guidance"})
        self.assertEqual([card["fact"].fact_id for card in filter_cards(cards, dict(basis="summary"))], ["registration-deadline-3"])
        self.assertEqual(filter_cards(cards, dict(basis="none")), [])
        self.assertEqual(len(filter_cards(cards, dict(basis="guidance"))), 3)
        page = self.get("/packs/test-pack/review?basis=summary&fact_id=registration-deadline-3").text
        self.assertIn("Portal summary of federal rules", page)
        self.assertIn("published by State Secretariat for Migration SEM, federal", page)
        self.assertIn('<option selected>summary</option>', page)
        self.assertIn("(page rule www.sem.example/zurich.html", page)
        self.assertIn('name="basis"', page)

    def test_a_citation_basis_is_set_in_the_fact_form_and_an_act_without_a_norm_is_refused(self):
        from swisstip.admin_console.screens.review import filter_cards

        self.post("/packs/test-pack/workbench/pack", self.pack_form(
            institutions="- {institution_id: ch-sem, name: SEM, native_name: SEM, level: federal, body: administration, "
                         "jurisdiction: CH, urls: [www.sem.example]}"))
        self.post("/packs/test-pack/workbench/facts/work-permit-1/evidence", dict(
            action="basis", number=1, basis_kind="act", basis_level="", basis_norm="AIG, SR 142.20, Art. 11",
            basis_refers_to="", sha256=self.curation_sha()))
        def fact():
            return next(f for c in load_curation(self.pack.curation_path).concepts for f in c.facts if f.fact_id == "work-permit-1")
        self.assertEqual((fact().evidence[0].basis.kind, fact().evidence[0].basis.level, fact().evidence[0].basis.norm),
                         ("act", None, "AIG, SR 142.20, Art. 11"))
        page = self.get("/packs/test-pack/workbench/facts/work-permit-1").text
        self.assertIn("Federal act: AIG, SR 142.20, Art. 11", page)
        self.assertIn("(citation; published by SEM, federal)", page)
        self.assertEqual([card["fact"].fact_id for card in filter_cards(queue(self.pack), dict(basis="act"))], ["work-permit-1"])
        before = self.pack.curation_path.read_bytes()
        response = self.post("/packs/test-pack/workbench/facts/work-permit-1/evidence", dict(
            action="basis", number=1, basis_kind="ordinance", basis_level="", basis_norm="", basis_refers_to="",
            sha256=self.curation_sha()), expected=422)
        self.assertIn("needs a norm", response.text)
        self.assertEqual(self.pack.curation_path.read_bytes(), before)
        response = self.post("/packs/test-pack/workbench/facts/work-permit-1/evidence", dict(
            action="basis", number=1, basis_kind="poem", basis_level="", basis_norm="", basis_refers_to="",
            sha256=self.curation_sha()), expected=422)
        self.assertIn("unknown basis kind", response.text)
        self.post("/packs/test-pack/workbench/facts/work-permit-1/evidence", dict(
            action="basis", number=1, basis_kind="", basis_level="", basis_norm="", basis_refers_to="",
            sha256=self.curation_sha()))
        self.assertIsNone(fact().evidence[0].basis)

    def test_a_pack_form_block_that_is_not_yaml_or_does_not_validate_is_refused(self):
        before = self.pack.curation_path.read_bytes()
        response = self.post("/packs/test-pack/workbench/pack", self.pack_form(institutions="- [unclosed"), expected=422)
        self.assertIn("not valid YAML", response.text)
        response = self.post("/packs/test-pack/workbench/pack", self.pack_form(
            institutions="- {institution_id: x, name: X, level: galactic, body: administration, jurisdiction: CH}"), expected=422)
        self.assertIn("would not validate", response.text)
        response = self.post("/packs/test-pack/workbench/pack", self.pack_form(ranking_policy="floor: 2"), expected=422)
        self.assertIn("would not validate", response.text)
        self.assertEqual(self.pack.curation_path.read_bytes(), before)

    def test_renaming_a_concept_rewrites_the_fact_ids_and_the_checks(self):
        self.post("/packs/test-pack/workbench/concepts/work-permit/rename",
                  dict(new_id="work-admission", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        concept = next(item for item in curation.concepts if item.concept_id == "work-admission")
        self.assertEqual(concept.facts[0].fact_id, "work-admission-1")


class WorkbenchGuardTests(ConsoleTestCase):
    def test_a_save_that_would_drop_a_fact_is_refused_and_writes_nothing(self):
        before = self.pack.curation_path.read_bytes()
        document_id = self.fixture.document("zurich")
        record_path = self.pack.text_dir / "documents" / f"{document_id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        keep = json.dumps(record)
        # The cited block's text changed under the same document id, so relocation cannot find the excerpt either.
        block = record["blocks"][1]
        text = record["content_text"]
        record["content_text"] = text[:block["start"]] + "x" * (block["end"] - block["start"]) + text[block["end"]:]
        record["content_sha256"] = block["text_sha256"] = "0" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
        try:
            with self.assertRaises(WriteRefused) as caught:
                write_curation(self.pack, lambda curation: None, ACTOR, "no-op save")
            self.assertIn("drop", str(caught.exception))
            self.assertEqual(self.pack.curation_path.read_bytes(), before)
        finally:
            record_path.write_text(keep, encoding="utf-8")


# --- review ------------------------------------------------------------------


class ReviewTests(ConsoleTestCase):
    def test_the_queue_puts_the_facts_a_check_expects_first(self):
        cards = queue(self.pack)
        self.assertEqual(cards[0]["fact"].fact_id, "registration-deadline-1")
        self.assertTrue(cards[0]["by_check"])
        self.assertEqual([card["priority"] for card in cards], sorted(card["priority"] for card in cards))
        self.assertEqual(cards[-1]["fact"].provenance.kind, "model-candidate")

    def test_confirm_sets_the_status_the_date_and_the_reviewer_and_refreshes_the_anchor(self):
        self.post("/packs/test-pack/review/registration-deadline-1/confirm", dict(sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = curation.concepts[0].facts[0]
        self.assertEqual(fact.provenance.review_status, "human-reviewed")
        self.assertEqual(fact.provenance.reviewed_by, ACTOR)
        self.assertEqual(fact.provenance.reviewed_on, date.today())
        record = self.pack.dataset.record(fact.evidence[0].document_id)
        self.assertEqual(fact.evidence[0].anchor.content_sha256, record["content_sha256"])
        self.assertEqual(fact.evidence[0].anchor.block_hashes, [record["blocks"][3]["text_sha256"]])

    def test_editing_a_confirmed_statement_clears_the_review_status(self):
        self.post("/packs/test-pack/review/registration-deadline-2/confirm", dict(sha256=self.curation_sha()))
        self.post("/packs/test-pack/workbench/facts", dict(
            fact_id="registration-deadline-2", statement="Register before you start work.", language="en",
            jurisdiction="CH", valid_from="", valid_through="", kind="curated-statement", author="test",
            source="", reviewed_on="", notes="", condition="population=eu_efta", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = next(f for c in curation.concepts for f in c.facts if f.fact_id == "registration-deadline-2")
        self.assertEqual(fact.provenance.review_status, "assistant-authored-unreviewed")
        self.assertIsNone(fact.provenance.reviewed_by)

    def test_flag_leaves_the_status_and_adds_a_note(self):
        self.post("/packs/test-pack/review/registration-deadline-3/flag",
                  dict(note="check the cantonal wording", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = next(f for c in curation.concepts for f in c.facts if f.fact_id == "registration-deadline-3")
        self.assertEqual(fact.provenance.review_status, "assistant-authored-unreviewed")
        self.assertTrue(any(note.startswith("flag:") for note in fact.provenance.notes))
        self.assertTrue(queue(self.pack)[0]["flagged"])

    def test_accept_turns_a_candidate_into_a_reviewed_curated_statement(self):
        self.post("/packs/test-pack/review/work-permit-1/accept",
                  dict(statement="Third-country nationals need a work permit.", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = next(f for c in curation.concepts for f in c.facts if f.fact_id == "work-permit-1")
        self.assertEqual(fact.provenance.kind, "curated-statement")
        self.assertEqual(fact.provenance.review_status, "human-reviewed")
        self.assertEqual(fact.provenance.reviewed_by, ACTOR)
        self.assertEqual(fact.provenance.author, "a model")

    def test_reject_moves_the_fact_to_the_rejected_log(self):
        self.post("/packs/test-pack/review/registration-deadline-2/reject",
                  dict(note="duplicated by the first fact", sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        self.assertNotIn("registration-deadline-2", [f.fact_id for c in curation.concepts for f in c.facts])
        entries = [json.loads(line) for line in
                   (self.pack.console_dir / "rejected.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(entries[-1]["fact"]["fact_id"], "registration-deadline-2")
        self.assertEqual(entries[-1]["reviewer"], ACTOR)
        self.assertEqual(entries[-1]["note"], "duplicated by the first fact")

    def test_rejecting_without_a_note_is_refused(self):
        self.post("/packs/test-pack/review/registration-deadline-3/reject",
                  dict(note="   ", sha256=self.curation_sha()), expected=422)

    def facts_by_id(self) -> dict:
        curation = load_curation(self.pack.curation_path)
        return {fact.fact_id: fact for concept in curation.concepts for fact in concept.facts}

    def test_bulk_confirm_marks_the_ticked_facts_and_notes_the_group(self):
        response = self.post("/packs/test-pack/review/bulk", dict(
            action="confirm", fact_ids=["registration-deadline-1", "registration-deadline-3"],
            sha256=self.curation_sha()))
        self.assertIn("confirmed+2+facts", response.headers["location"])
        facts = self.facts_by_id()
        for fact_id in ("registration-deadline-1", "registration-deadline-3"):
            fact = facts[fact_id]
            self.assertEqual(fact.provenance.review_status, "human-reviewed")
            self.assertEqual(fact.provenance.reviewed_by, ACTOR)
            self.assertEqual(fact.provenance.reviewed_on, date.today())
            record = self.pack.dataset.record(fact.evidence[0].document_id)
            self.assertEqual(fact.evidence[0].anchor.content_sha256, record["content_sha256"])
            self.assertIn("review: confirmed in a bulk review of 2 facts", fact.provenance.notes[-1])
        self.assertEqual(facts["registration-deadline-2"].provenance.review_status, "assistant-authored-unreviewed")
        audit = read_audit(self.pack)[0]
        self.assertEqual(audit["ids"], ["registration-deadline-1", "registration-deadline-3"])
        self.assertEqual(audit["reason"], "bulk confirm of 2 facts")

    def test_a_single_fact_confirmed_in_bulk_is_not_noted_as_a_group_but_keeps_the_comment(self):
        self.post("/packs/test-pack/review/bulk", dict(
            action="confirm", fact_ids=["registration-deadline-2"], note="matches block 5 word for word",
            sha256=self.curation_sha()))
        fact = self.facts_by_id()["registration-deadline-2"]
        self.assertEqual(fact.provenance.review_status, "human-reviewed")
        self.assertEqual(fact.provenance.notes, [f"review: matches block 5 word for word ({ACTOR}, {date.today()})"])

    def test_bulk_all_matching_applies_to_every_card_of_the_filter_and_keeps_it(self):
        response = self.post("/packs/test-pack/review/bulk", dict(
            action="confirm", scope="matching", concept="registration-deadline", fact_ids=[],
            sha256=self.curation_sha()))
        self.assertIn("concept=registration-deadline", response.headers["location"])
        facts = self.facts_by_id()
        self.assertEqual({fact_id for fact_id, fact in facts.items() if fact.provenance.review_status == "human-reviewed"},
                         {"registration-deadline-1", "registration-deadline-2", "registration-deadline-3"})
        self.assertEqual(facts["work-permit-1"].provenance.review_status, "model-candidate-automated-review")

    def test_bulk_flag_adds_the_comment_and_leaves_the_status(self):
        self.post("/packs/test-pack/review/bulk", dict(
            action="flag", fact_ids=["registration-deadline-1", "registration-deadline-2"],
            note="the cantonal page words this differently", sha256=self.curation_sha()))
        facts = self.facts_by_id()
        for fact_id in ("registration-deadline-1", "registration-deadline-2"):
            self.assertEqual(facts[fact_id].provenance.review_status, "assistant-authored-unreviewed")
            self.assertTrue(facts[fact_id].provenance.notes[-1].startswith(
                "flag: the cantonal page words this differently"))
        self.assertEqual({card["fact"].fact_id for card in queue(self.pack) if card["flagged"]},
                         {"registration-deadline-1", "registration-deadline-2"})

    def test_bulk_reject_removes_the_facts_and_logs_each_with_the_comment(self):
        self.post("/packs/test-pack/review/bulk", dict(
            action="reject", fact_ids=["registration-deadline-2", "work-permit-1"], note="out of scope for KB1",
            sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        self.assertNotIn("work-permit", [concept.concept_id for concept in curation.concepts])
        self.assertNotIn("registration-deadline-2", self.facts_by_id())
        entries = [json.loads(line) for line in
                   (self.pack.console_dir / "rejected.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["fact"]["fact_id"] for entry in entries[-2:]], ["registration-deadline-2", "work-permit-1"])
        self.assertTrue(all(entry["note"] == "out of scope for KB1" and entry["reviewer"] == ACTOR
                            for entry in entries[-2:]))

    def test_bulk_refusals_write_nothing(self):
        before = self.curation_sha()
        # (form, words of the refusal) - the words prove it was the bulk action that refused, not form validation.
        cases = {
            "nothing selected": (dict(action="confirm", fact_ids=[]), "no fact selected"),
            "flag without a comment": (dict(action="flag", fact_ids=["registration-deadline-1"], note="  "),
                                       "to flag facts in bulk, give a comment"),
            "reject without a comment": (dict(action="reject", fact_ids=["registration-deadline-1"]),
                                         "to reject facts in bulk, give a comment"),
            "an unknown fact among known ones": (dict(action="confirm",
                                                      fact_ids=["registration-deadline-1", "no-such-fact"]),
                                                 "unknown fact(s): no-such-fact"),
            "an unknown action": (dict(action="approve", fact_ids=["registration-deadline-1"]),
                                  "unknown bulk action"),
        }
        for label, (data, words) in cases.items():
            with self.subTest(label):
                response = self.post("/packs/test-pack/review/bulk", dict(data, sha256=before), expected=422)
                self.assertIn(words, response.text)
                self.assertEqual(self.curation_sha(), before)
        self.post("/packs/test-pack/review/bulk", dict(action="confirm", fact_ids=["registration-deadline-1"],
                                                       sha256="0" * 64), expected=409)
        self.assertEqual(self.curation_sha(), before)

    def test_the_queue_offers_ticks_select_all_and_the_bulk_form(self):
        page = self.get("/packs/test-pack/review").text
        self.assertIn('id="bulk"', page)
        self.assertIn("data-select-all", page)
        self.assertIn('name="scope" value="matching"', page)
        for fact_id in ("registration-deadline-1", "registration-deadline-2", "registration-deadline-3", "work-permit-1"):
            self.assertIn(f'name="fact_ids" value="{fact_id}"', page)
        self.assertNotIn('id="bulk"', self.get("/packs/test-pack/review?mode=sample").text)

    def test_the_sample_is_stable_between_calls(self):
        first = [card["fact"].fact_id for card in sample_queue(self.pack, 1)]
        second = [card["fact"].fact_id for card in sample_queue(self.pack, 1)]
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_a_sample_verdict_is_recorded_per_document(self):
        document_id = self.fixture.document("zurich")
        self.post("/packs/test-pack/review/sample",
                  dict(document_id=document_id, blocks_checked=2, verdict="excerpts match the page"))
        checks = load_checks(self.pack.checks_path)
        self.assertEqual(checks.samples[document_id].reviewed_by, ACTOR)
        self.assertEqual(checks.samples[document_id].verdict, "excerpts match the page")


# --- relocation --------------------------------------------------------------


class RelocationTests(ConsoleTestCase):
    def test_every_outcome_renders_and_a_pick_rewrites_the_citation(self):
        report = json.loads((self.pack.pack_dir / "build-report.json").read_text(encoding="utf-8"))
        document_id = self.fixture.document("registration")
        report["facts_resolved"][0]["citations"][0].update(
            outcome="moved", warnings=["context-changed"], document_id=document_id)
        report["facts_resolved"][1]["citations"][0].update(
            outcome="ambiguous", document_id=document_id,
            candidates=[dict(block_ids=[f"{document_id}:b00004"], heading_path=["Registration", "Deadline"]),
                        dict(block_ids=[f"{document_id}:b00005"], heading_path=["Registration", "Deadline"])])
        (self.pack.pack_dir / "build-report.json").write_text(json.dumps(report), encoding="utf-8")
        groups = relocation_rows(self.pack)
        self.assertIn("moved-with-warnings", groups)
        self.assertIn("ambiguous", groups)
        page = self.get("/packs/test-pack/runs/relocation").text
        self.assertIn("context-changed", page)
        self.assertIn("pick this range", page)

        self.post("/packs/test-pack/runs/relocation/pick",
                  dict(fact_id="registration-deadline-2", number=1, document_id=document_id,
                       first_block=4, last_block=4, sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        fact = next(f for c in curation.concepts for f in c.facts if f.fact_id == "registration-deadline-2")
        self.assertEqual((fact.evidence[0].first_block, fact.evidence[0].last_block), (4, 4))
        self.assertIn("Register within 14 days", fact.evidence[0].anchor.excerpt)

    def test_dropping_a_fact_needs_a_note_and_records_it(self):
        self.post("/packs/test-pack/runs/relocation/drop",
                  dict(fact_id="registration-deadline-3", note="", sha256=self.curation_sha()), expected=422)
        self.post("/packs/test-pack/runs/relocation/drop",
                  dict(fact_id="registration-deadline-3", note="the page no longer says this",
                       sha256=self.curation_sha()))
        curation = load_curation(self.pack.curation_path)
        self.assertNotIn("registration-deadline-3", [f.fact_id for c in curation.concepts for f in c.facts])

    def test_the_preview_shows_the_excerpt_a_candidate_would_cut(self):
        document_id = self.fixture.document("registration")
        page = self.get(f"/packs/test-pack/runs/relocation/preview?document_id={document_id}"
                        "&first_block=4&last_block=5").text
        self.assertIn("Register within 14 days", page)
        self.assertIn("Register before starting work", page)


# --- release -----------------------------------------------------------------


class ReleaseTests(ConsoleTestCase):
    def test_the_diff_between_two_releases_names_what_callers_see(self):
        old = load_release(self.pack.release_path)
        new = load_release(self.pack.release_path)
        new.manifest.release_id = "test-pack-2026-09-13-v1"
        new.facts[0].statement = "Register within fourteen days."
        new.concepts = [concept for concept in new.concepts if concept.concept_id != "work-permit"]
        removed = {fact.fact_id for fact in new.facts if fact.concept_id == "work-permit"}
        new.facts = [fact for fact in new.facts if fact.concept_id != "work-permit"]
        diff = release_diff(old, new)
        self.assertEqual(diff["concepts_removed"], ["work-permit"])
        self.assertEqual(set(diff["facts_removed"]), removed)
        self.assertEqual(diff["facts_reworded"][0]["after"], "Register within fourteen days.")

    def test_the_caller_preview_reports_the_coverage_root_size(self):
        page = self.get("/packs/test-pack/release").text
        self.assertIn("Coverage root:", page)
        self.assertIn("byte target", page)
        self.assertIn("COVERAGE.md", page)

    def test_the_working_tree_panel_survives_a_directory_without_git(self):
        page = self.get("/packs/test-pack/release").text
        self.assertIn("git status --short", page)


# --- sandbox and checks ------------------------------------------------------


class SandboxTests(ConsoleTestCase):
    def test_each_tool_round_trips_through_the_contract_models(self):
        for tool, arguments, expected in (
                ("get_coverage", {}, "Registration and work permits"),
                ("get_coverage", {"parent_id": "residence"}, "registration-deadline"),
                ("search", {"query": "register arrival", "limit": 3}, "registration-deadline"),
                ("resolve", {"concept_ids": ["registration-deadline"], "jurisdiction": {"canton_code": "CH-ZH"},
                             "as_of": "2026-09-12", "context": {"population": "eu_efta"}}, "SUPPORTED"),
                ("get_evidence", {"evidence_ids": ["e-registration-deadline-1-1"]}, "Register within 14 days")):
            with self.subTest(tool=tool):
                response = self.client.post(f"/packs/test-pack/sandbox/{tool}",
                                            data=dict(arguments=json.dumps(arguments)))
                self.assertEqual(response.status_code, 200, response.text[:400])
                self.assertIn(expected, response.text)

    def test_an_invalid_request_is_shown_as_a_typed_tool_error(self):
        response = self.client.post("/packs/test-pack/sandbox/resolve", data=dict(arguments='{"concept_ids": []}'))
        self.assertIn("INVALID_ARGUMENT", response.text)

    def test_arguments_that_are_not_json_are_reported_not_raised(self):
        response = self.client.post("/packs/test-pack/sandbox/search", data=dict(arguments="{not json"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("not JSON", response.text)

    def test_a_call_is_logged_with_its_size_and_latency(self):
        self.client.post("/packs/test-pack/sandbox/search",
                         data=dict(arguments=json.dumps({"query": "nothing matches here", "limit": 3})))
        lines = (self.pack.console_dir / "sandbox-calls.jsonl").read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[-1])
        self.assertEqual(entry["tool"], "search")
        self.assertGreater(entry["bytes"], 0)
        self.assertEqual(entry["hits"], 0)
        self.assertEqual(entry["query"], "nothing matches here")

    def test_checks_run_and_the_result_is_written_into_the_file(self):
        response = self.client.post("/packs/test-pack/sandbox/checks/run", data=dict(release_id=""))
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertIn("pass", response.text)
        checks = load_checks(self.pack.checks_path)
        self.assertEqual(checks.last_run.results, {"eu-registration": "pass"})
        self.assertEqual(checks.last_run.release_id, "test-pack-2026-09-12-v1")

    def test_a_check_whose_expectation_fails_is_reported(self):
        service = ReleaseService(load_release(self.pack.release_path))
        checks = load_checks(self.pack.checks_path)
        checks.checks[0].expect.fact_ids = ["no-such-fact"]
        _, results = run_checks(checks, service)
        self.assertFalse(results[0]["passed"])
        self.assertIn("missing", [row["detail"] for row in results[0]["expectations"]])

    def test_save_as_check_validates_the_request_against_the_contract(self):
        self.post("/packs/test-pack/sandbox/checks/save", dict(
            check_id="search-registration", title="Search finds the concept", tool="search",
            arguments=json.dumps({"query": "register arrival", "limit": 5}),
            expect_status="", expect_fact_ids="", expect_concepts="registration-deadline"))
        checks = load_checks(self.pack.checks_path)
        self.assertEqual(checks.check("search-registration").expect.concept_ids_in_top, ["registration-deadline"])
        response = self.post("/packs/test-pack/sandbox/checks/save", dict(
            check_id="broken", title="", tool="search", arguments=json.dumps({"limit": 5}),
            expect_status="", expect_fact_ids="", expect_concepts=""), expected=422)
        self.assertIn("does not match the contract", response.text)

    def test_seeding_keeps_only_the_concepts_the_release_publishes(self):
        service = ReleaseService(load_release(self.pack.release_path))
        seeded = seed_checks("test-pack", service)
        self.assertEqual(seeded.checks, [])  # this pack publishes none of the standing-case concepts


# --- jobs --------------------------------------------------------------------


class JobTests(ConsoleTestCase):
    def wait_for(self, job_id: str, timeout: float = 60.0):
        runner = self.app.state.jobs
        runner.wait(runner.jobs[job_id], timeout)
        return runner.jobs[job_id]

    def test_a_run_starts_writes_a_log_and_finishes(self):
        response = self.post("/packs/test-pack/runs", dict(start="build", until="health", workers=1,
                                                           scope="attributed"))
        job_id = response.headers["location"].split("job+")[1].split("+")[0]
        job = self.wait_for(job_id)
        self.assertEqual(job.status, "finished", job.error)
        tail = self.app.state.jobs.tail(self.pack, job_id)
        self.assertIn("build", tail["text"])
        self.assertTrue((self.pack.console_dir / "runs").is_dir())
        self.assertTrue((self.pack.console_dir / "releases" / "test-pack-2026-09-12-v1.json").is_file())

    def test_only_one_job_per_pack_runs_at_a_time(self):
        runner = self.app.state.jobs
        started = runner.start(self.pack, "test", ACTOR, {}, lambda log: time.sleep(0.4) or {})
        with self.assertRaises(RuntimeError):
            runner.start(self.pack, "test", ACTOR, {}, lambda log: {})
        runner.wait(started)

    def test_a_download_needs_the_pack_name_typed(self):
        response = self.post("/packs/test-pack/runs", dict(start="acquire", until="acquire", workers=1,
                                                           scope="attributed", download="1", confirm_pack="wrong"))
        self.assertIn("type+the+pack+name", response.headers["location"])
        self.assertIsNone(self.app.state.jobs.active("test-pack"))

    def test_a_job_still_marked_running_at_start_up_becomes_interrupted(self):
        folder = self.pack.console_dir / "jobs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "stale0000.json").write_text(json.dumps(dict(
            job_id="stale0000", pack="test-pack", kind="pipeline", actor=ACTOR, options={}, status="running",
            started_at="2026-09-12T10:00:00+00:00")), encoding="utf-8")
        create_app(self.fixture.root, actor=ACTOR)
        state = json.loads((folder / "stale0000.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "interrupted")
        self.assertIn("restarted", state["error"])


# --- modes -------------------------------------------------------------------


class ReadOnlyModeTests(ConsoleTestCase):
    read_only = True

    def test_no_write_or_job_route_is_registered(self):
        registered = {(path, method) for route in self.app.routes
                      for path in [getattr(route, "path", None)] if path
                      for method in getattr(route, "methods", ())}
        for url in ("/packs/{pack}/sources", "/packs/{pack}/runs", "/packs/{pack}/workbench/facts",
                    "/packs/{pack}/review/{fact_id}/confirm", "/packs/{pack}/review/bulk", "/packs/{pack}/release/build",
                    "/packs/{pack}/sandbox/checks/run", "/packs/{pack}/operations/refresh"):
            with self.subTest(url=url):
                self.assertNotIn((url, "POST"), registered)
                live = url.replace("{pack}", "test-pack").replace("{fact_id}", "registration-deadline-1")
                self.assertIn(self.client.post(live, data={}).status_code, (404, 405))

    def test_the_mode_is_in_the_header_and_the_forms_are_gone(self):
        page = self.get("/packs/test-pack/review").text
        self.assertIn("read-only mode", page)
        self.assertNotIn("Confirm (y)", page)
        self.assertNotIn("Add a pack", self.get("/").text)

    def test_the_tool_sandbox_still_answers(self):
        response = self.client.post("/packs/test-pack/sandbox/search",
                                    data=dict(arguments=json.dumps({"query": "register", "limit": 3})))
        self.assertEqual(response.status_code, 200)


class HostedModeTests(ConsoleTestCase):
    def setUp(self):
        super().setUp()
        auth = self.fixture.root / "console-users.txt"
        auth.write_text("\n".join([
            f"anna:{hashlib.sha256(b'secret').hexdigest()}:editor",
            f"evan:{hashlib.sha256(b'other').hexdigest()}:reader"]), encoding="utf-8")
        self.app = create_app(self.fixture.root, actor="unused", auth_file=auth)
        self.client = TestClient(self.app)
        self.pack = self.app.state.console.pack("test-pack")

    def test_a_write_without_credentials_is_refused(self):
        response = self.client.post("/packs/test-pack/review/registration-deadline-1/confirm", data={})
        self.assertEqual(response.status_code, 401)

    def test_a_reader_may_not_write(self):
        response = self.client.post("/packs/test-pack/review/registration-deadline-1/confirm",
                                    data=dict(sha256=self.pack.curation.sha256), auth=("evan", "other"))
        self.assertEqual(response.status_code, 403)

    def test_an_editor_writes_as_the_authenticated_user(self):
        response = self.client.post("/packs/test-pack/review/registration-deadline-1/confirm",
                                    data=dict(sha256=self.pack.curation.sha256), auth=("anna", "secret"),
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[:400])
        fact = load_curation(self.pack.curation_path).concepts[0].facts[0]
        self.assertEqual(fact.provenance.reviewed_by, "anna")

    def test_a_wrong_password_is_refused(self):
        response = self.client.get("/", auth=("anna", "wrong"))
        self.assertEqual(response.status_code, 401)


# --- calls -------------------------------------------------------------------


class CallLogTests(unittest.TestCase):
    LINE = ("2026-09-13 10:00:00,123 swiss-tip INFO tool=search status=OK bytes=812 ms=1.4 "
            "release=mvp-zurich-2026-09-13-v1 hits=0 query=\"register in zurich\"")

    def test_the_parser_reads_the_server_line(self):
        entry = parse_line(self.LINE)
        self.assertEqual(entry["tool"], "search")
        self.assertEqual(entry["bytes"], 812)
        self.assertEqual(entry["ms"], 1.4)
        self.assertEqual(entry["hits"], 0)
        self.assertEqual(entry["query"], "register in zurich")
        self.assertEqual(entry["at"], "2026-09-13T10:00:00")

    def test_a_line_that_is_not_a_call_is_skipped(self):
        self.assertIsNone(parse_line("2026-09-13 10:00:00 swiss-tip INFO serving release=x facts=76 concepts=31"))
        self.assertIsNone(parse_line(""))

    def test_percentiles_use_the_nearest_rank(self):
        self.assertIsNone(percentile([], 0.5))
        self.assertEqual(percentile([1.0], 0.95), 1.0)
        self.assertEqual(percentile([float(n) for n in range(1, 101)], 0.5), 50.0)
        self.assertEqual(percentile([float(n) for n in range(1, 101)], 0.99), 99.0)

    def test_the_gap_feed_counts_gaps_and_searches_without_a_hit(self):
        entries = [
            parse_line(self.LINE),
            parse_line("2026-09-13 10:01:00 swiss-tip INFO tool=resolve status=OUT_OF_COVERAGE bytes=400 ms=2 "
                       "release=r gap=jurisdiction_not_covered"),
            parse_line("2026-09-13 10:02:00 swiss-tip INFO tool=resolve status=NEEDS_CONTEXT bytes=300 ms=1 "
                       "release=r gap=population"),
        ]
        summary = aggregate(entries)
        self.assertEqual(summary["calls"], 3)
        self.assertEqual(summary["gap_feed"]["out_of_coverage"], [("jurisdiction_not_covered", 1)])
        self.assertEqual(summary["gap_feed"]["needs_context"], [("population", 1)])
        self.assertEqual(summary["gap_feed"]["searches_without_hit"], [("register in zurich", 1)])
        self.assertEqual({row["tool"] for row in summary["per_tool"]}, {"search", "resolve"})


if __name__ == "__main__":
    unittest.main()
