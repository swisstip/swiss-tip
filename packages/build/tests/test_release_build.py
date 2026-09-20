import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from swisstip.build.curation import BasisSpec, CuratedInstitution, Curation, dump_curation, load_curation
from swisstip.build.places import PlaceFileError, place_files, place_register_for
from swisstip.build.release_build import BuildError, build_release, derive_article, record_language
from swisstip.core.validation import validate_release
from swisstip.extraction.extract_cli import run_extraction

URL = "https://www.sem.example/faq.html"
PAGE = (b"<html lang=\"en\"><head><title>FAQ</title></head><body><main><h1>FAQ</h1><h2>Registration</h2>"
        b"<p>Register within 14 days of arrival.</p><p>Register before starting work.</p></main></body></html>")
REFRESHED = PAGE.replace(b"<h2>Registration</h2>", b"<h2>News</h2><p>New online desk.</p><h2>Registration</h2>")
REWORDED = PAGE.replace(b"14 days of arrival", b"14 days after you arrive")
LAW_PAGE = (b"<html lang=\"de\"><head><title>AIG</title></head><body><main><h1>AIG</h1>"
            b"<h2>Art. 11 Bewilligungspflicht bei Erwerbst\xc3\xa4tigkeit</h2><p>Erster Absatz.</p>"
            b"<h2>Art. 1284 Zustimmungsverfahren</h2><p>Die Zustimmung des SEM ist erforderlich.</p>"
            b"<p>Der Bundesrat regelt die Einzelheiten.</p></main></body></html>")

CURATION = """
schema_version: swiss-tip-curation/v1
pack: test
title: Test pack
scope_statement: Registration of EU/EFTA nationals.
out_of_scope: [Fees]
out_of_scope_response: Say that it is not covered.
limitations: [Assistant-authored, unreviewed.]
freshness_max_age_days: 60
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_run(root: Path, body: bytes) -> Path:
    run = root / "run"
    folder = run / "pages" / sha256(URL.encode())
    attempt = folder / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "response.html").write_bytes(body)
    snapshot = dict(relative_path=f"pages/{folder.name}/attempt-001/response.html", requested_url=URL, final_url=URL,
                    content_type="text/html", sha256=sha256(body), bytes_downloaded=len(body),
                    retrieved_at="2026-09-11T06:00:00+00:00", review_flags=[])
    manifest = dict(url=URL, url_id=folder.name, references=[], registry_entries=[], snapshots=[snapshot], status="saved", http_status=200)
    (attempt / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps(dict(catalogue_sha256="c" * 64, targets=[dict(
        url=URL, url_id=folder.name, references=[dict(label="FAQ", catalogue_line=1)],
        registry_entries=[dict(definition=dict(source_id="sem-faq", start_url=URL, allowed_hosts=["www.sem.example"],
                                               allowed_path_prefixes=["/"], canonical_authority="SEM", jurisdiction="CH", language="en"))])])),
        encoding="utf-8")
    return run


class ReleaseBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = make_run(self.root, PAGE)
        run_extraction(self.run, log=lambda *a, **k: None)
        self.text = self.run / "text"
        self.document_id = next(iter(json.loads((self.text / "index.json").read_text(encoding="utf-8"))))["document_id"]
        self.curation = Curation.model_validate(__import__("yaml").safe_load(CURATION.replace("DOC", self.document_id)))

    def tearDown(self):
        self.temporary.cleanup()

    def refresh(self, body: bytes) -> None:
        folder = self.run / "pages" / sha256(URL.encode())
        attempt = folder / "attempt-002"
        attempt.mkdir()
        (attempt / "response.html").write_bytes(body)
        manifest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
        manifest["snapshots"] = [dict(manifest["snapshots"][0], relative_path=f"pages/{folder.name}/attempt-002/response.html",
                                      sha256=sha256(body), bytes_downloaded=len(body), retrieved_at="2026-09-12T06:00:00+00:00")]
        (folder / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_extraction(self.run, log=lambda *a, **k: None)

    def test_build_resolves_citations_and_validates(self):
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(validate_release(release, self.text), [])
        self.assertEqual(report["citation_outcomes"], {"same-snapshot": 1})
        self.assertEqual(report["dropped"], [])
        evidence = release.evidence[0]
        self.assertEqual(evidence.original_excerpt, "Register within 14 days of arrival.\n\nRegister before starting work.")
        self.assertEqual(evidence.publisher, "State Secretariat for Migration SEM")
        self.assertEqual(release.manifest.jurisdictions, ["CH"])
        self.assertEqual(release.manifest.languages, ["en"])
        self.assertEqual(release.manifest.provenance_kinds, {"curated-statement": 1})
        self.assertEqual(release.concepts[0].context_schema["population"].enum, ["eu_efta", "third_country"])
        self.assertIsNotNone(self.curation.concepts[0].facts[0].evidence[0].anchor)
        text = dump_curation(self.curation)
        self.assertIn("excerpt: |-\n", text)
        self.assertNotIn("\n\n\n", text)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
            handle.write(text)
        self.assertEqual(load_curation(Path(handle.name)), self.curation)

    def test_refreshed_page_relocates_the_citation(self):
        build_release(self.curation, self.text, "test-v1")
        self.refresh(REFRESHED)
        release, report = build_release(self.curation, self.text, "test-v2", update_citations=True)
        self.assertEqual(report["citation_outcomes"], {"moved": 1})
        self.assertEqual(release.evidence[0].original_excerpt, "Register within 14 days of arrival.\n\nRegister before starting work.")
        citation = self.curation.concepts[0].facts[0].evidence[0]
        self.assertNotEqual(citation.document_id, self.document_id)
        self.assertEqual((citation.first_block, citation.last_block), (5, 6))
        self.assertEqual(validate_release(release, self.text), [])

    def test_renumbered_blocks_of_the_same_record_relocate_the_citation(self):
        # A newer extractor can number the blocks of the same saved response differently.
        build_release(self.curation, self.text, "test-v1")
        citation = self.curation.concepts[0].facts[0].evidence[0]
        citation.first_block, citation.last_block = 2, 3
        release, report = build_release(self.curation, self.text, "test-v2", update_citations=True)
        self.assertEqual(report["citation_outcomes"], {"same-text": 1})
        self.assertEqual(release.evidence[0].original_excerpt, "Register within 14 days of arrival.\n\nRegister before starting work.")
        self.assertEqual((citation.document_id, citation.first_block, citation.last_block), (self.document_id, 3, 4))

    def test_reworded_page_drops_the_fact(self):
        build_release(self.curation, self.text, "test-v1")
        self.refresh(REWORDED)
        with self.assertRaises(BuildError):
            build_release(self.curation, self.text, "test-v2")

    def test_source_terms_are_verified_against_the_excerpts_and_merged_into_aliases(self):
        concept = self.curation.concepts[0]
        concept.aliases = ["registration deadline"]
        concept.source_terms = ["within 14 days", "Before  starting work"]
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(release.concepts[0].aliases, ["registration deadline", "within 14 days", "Before  starting work"])
        self.assertEqual(report["source_terms"], 2)
        concept.source_terms = ["within 14 days", "quota"]
        with self.assertRaises(BuildError) as raised:
            build_release(self.curation, self.text, "test-v1")
        self.assertIn("registration-deadline: ['quota']", str(raised.exception))

    def test_question_languages_require_a_sample_question_in_each_language(self):
        concept = self.curation.concepts[0]
        concept.questions = ["When do I have to register?"]
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(report["questions"], 1)
        self.curation.question_languages = ["en", "de"]
        with self.assertRaises(BuildError) as raised:
            build_release(self.curation, self.text, "test-v1")
        self.assertIn("registration-deadline: ['de']", str(raised.exception))
        concept.questions.append("Bis wann muss ich mich anmelden?")
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(release.concepts[0].questions, ["When do I have to register?", "Bis wann muss ich mich anmelden?"])

    def test_missing_publisher_is_an_error(self):
        # The records of this run carry no catalogue entry, so without a publisher line or an institution rule the
        # build cannot name who published the page.
        self.curation.publishers = {}
        with self.assertRaises(BuildError):
            build_release(self.curation, self.text, "test-v1")

    def test_a_page_without_a_catalogue_entry_keeps_its_publisher_and_gets_no_institution(self):
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(release.institutions, [])
        self.assertIsNone(release.documents[0].institution_id)
        self.assertIsNone(release.evidence[0].basis)
        self.assertIsNone(release.facts[0].provenance.basis)
        self.assertEqual(release.manifest.institution_levels, {})
        self.assertIsNone(release.manifest.ranking_policy)
        self.assertEqual(report["documents_attributed"][0]["institution_rule"], "none")

    def test_the_catalogue_entry_names_the_institution_when_the_pack_has_no_registry(self):
        # The saved page's manifest carries its catalogue entry, as a downloaded run does.
        folder = self.run / "pages" / sha256(URL.encode())
        for name in ("latest.json", "attempt-001/manifest.json"):
            manifest = json.loads((folder / name).read_text(encoding="utf-8"))
            manifest["registry_entries"] = [dict(
                definition=dict(source_id="sem-faq", start_url=URL, allowed_hosts=["www.sem.example"], allowed_path_prefixes=["/"],
                                canonical_authority="SEM", jurisdiction="CH", language="en"),
                authority_level="federal", source_kind="official_guidance")]
            (folder / name).write_text(json.dumps(manifest), encoding="utf-8")
        shutil.rmtree(self.text)  # unchanged records are reused, so the dataset is built anew
        run_extraction(self.run, log=lambda *a, **k: None)
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual([i.institution_id for i in release.institutions], ["catalogue-sem-faq"])
        self.assertEqual((release.institutions[0].name, release.institutions[0].level, release.institutions[0].jurisdiction),
                         ("State Secretariat for Migration SEM", "federal", "CH"))
        self.assertEqual(release.documents[0].institution_id, "catalogue-sem-faq")
        self.assertEqual(release.evidence[0].basis.label, "Federal authority guidance")
        self.assertEqual(release.facts[0].provenance.basis.label, "Federal authority guidance")
        self.assertEqual((release.manifest.institution_levels, release.manifest.basis_kinds), ({"federal": 1}, {"guidance": 1}))
        self.assertEqual(release.manifest.ranking_policy.floor, 0.7)
        self.assertEqual(report["institutions"][0]["from_catalogue"], True)
        self.assertEqual(report["facts_resolved"][0]["citations"][0]["basis_rule"], "default")

    def test_a_page_rule_gives_the_language_only_to_a_record_without_one(self):
        rules = {"www.city.example": "de", "www.city.example/_doc/2": "fr"}
        pdf = {"source_url": "https://www.city.example/_doc/1", "language_declared": None, "language_hint": None}
        self.assertEqual(record_language(pdf), "und")
        self.assertEqual(record_language(pdf, rules), "de")
        self.assertEqual(record_language(dict(pdf, source_url="https://www.city.example/_doc/2"), rules), "fr")
        self.assertEqual(record_language(dict(pdf, language_declared="it-CH"), rules), "it")
        self.assertEqual(record_language(dict(pdf, language_hint="en"), rules), "en")
        self.assertEqual(record_language(dict(pdf, source_url="https://other.example/x"), rules), "und")
        # The rule reaches the served document.
        self.curation.page_languages = {"www.sem.example": "de"}
        release, _ = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(release.documents[0].language, "en")  # the record declares its own language

    def test_articles_derive_from_heading_paths_with_glued_footnotes_and_annexes(self):
        # Fedlex glues footnote numbers to the article numbers of its headings, and the numbering of a treaty's
        # annex restarts at 1: the sequence of the headings decides.
        blocks = [dict(block_id="d:b1", heading_path=["AIG", "Art. 15 Gegenstand"]),  # Article 1, footnote 5
                  dict(block_id="d:b2", heading_path=["AIG", "Art. 2 Geltungsbereich"]),
                  dict(block_id="d:b3", heading_path=["AIG", "Art. 2a Titel"]),
                  dict(block_id="d:b4", heading_path=["AIG", "Art. 1284 Zustimmung", "1. Abschnitt"]),  # Article 12, footnote 84
                  dict(block_id="d:b5", heading_path=["Anhang I42", "Freiz\u00fcgigkeit", "Art. 1 Einreise"]),
                  dict(block_id="d:b6", heading_path=["Anhang I42", "Freiz\u00fcgigkeit", "Art. 6 Aufenthalt"]),
                  dict(block_id="d:b7", heading_path=["AIG", "Schlussbestimmungen"])]
        record = dict(blocks=blocks)
        self.assertEqual(derive_article(record, "d:b1"), (None, "Art. 1"))
        self.assertEqual(derive_article(record, "d:b2"), (None, "Art. 2"))
        self.assertEqual(derive_article(record, "d:b3"), (None, "Art. 2a"))
        self.assertEqual(derive_article(record, "d:b4"), (None, "Art. 12"))
        self.assertEqual(derive_article(record, "d:b5"), ("I", "Art. 1"))
        self.assertEqual(derive_article(record, "d:b6"), ("I", "Art. 6"))
        self.assertEqual(derive_article(record, "d:b7"), (None, None))
        self.assertEqual(derive_article(record, "d:b99"), (None, None))

    def test_the_article_of_a_law_citation_comes_from_the_heading_path_and_a_wrong_norm_is_reported(self):
        run = make_run(self.root / "law", LAW_PAGE)
        run_extraction(run, log=lambda *a, **k: None)
        text = run / "text"
        document_id = next(iter(json.loads((text / "index.json").read_text(encoding="utf-8"))))["document_id"]
        curation = Curation.model_validate(__import__("yaml").safe_load(
            CURATION.replace("DOC", document_id).replace("first_block: 3, last_block: 4", "first_block: 5, last_block: 6")))
        curation.institutions = [CuratedInstitution(
            institution_id="ch-fedlex", name="Federal Council", native_name="Bundesrat", level="federal",
            body="law_collection", jurisdiction="CH", urls=["www.sem.example"])]
        curation.page_basis = {"www.sem.example/faq": BasisSpec(kind="act", norm="AIG, SR 142.20")}
        release, report = build_release(curation, text, "test-v1")
        self.assertEqual(release.evidence[0].basis.label, "Federal act: AIG, SR 142.20, Art. 12")
        self.assertEqual(release.facts[0].provenance.basis.norm, "AIG, SR 142.20, Art. 12")
        citation_report = report["facts_resolved"][0]["citations"][0]
        self.assertEqual((citation_report["basis_rule"], citation_report["basis_warnings"]), ("page rule www.sem.example/faq", []))
        self.assertEqual(validate_release(release, text), [])
        citation = curation.concepts[0].facts[0].evidence[0]
        citation.basis = BasisSpec(kind="act", norm="AIG, SR 142.20, Art. 13")
        release, report = build_release(curation, text, "test-v1")
        self.assertEqual(release.evidence[0].basis.label, "Federal act: AIG, SR 142.20, Art. 13")
        self.assertEqual(report["facts_resolved"][0]["citations"][0]["basis_warnings"],
                         ["the norm names Art. 13 but the cited block lies under Art. 12"])
        citation.basis = BasisSpec(kind="guidance")
        release, report = build_release(curation, text, "test-v1")
        self.assertEqual(release.evidence[0].basis.label, "Federal authority guidance")
        self.assertEqual(report["facts_resolved"][0]["citations"][0]["basis_warnings"], [])

    def test_the_registry_page_rules_and_citation_bases_decide_what_an_excerpt_is(self):
        self.curation.institutions = [CuratedInstitution(
            institution_id="ch-sem", name="State Secretariat for Migration SEM", native_name="Staatssekretariat für Migration",
            level="federal", body="administration", jurisdiction="CH", urls=["www.sem.example"])]
        self.curation.page_basis = {"www.sem.example/faq": BasisSpec(kind="summary")}
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual([i.institution_id for i in release.institutions], ["ch-sem"])
        self.assertEqual(release.evidence[0].publisher, "State Secretariat for Migration SEM")
        self.assertEqual(release.evidence[0].basis.label, "Portal summary of federal rules")
        self.assertEqual(report["facts_resolved"][0]["citations"][0]["basis_rule"], "page rule www.sem.example/faq")
        self.assertEqual(report["documents_attributed"][0]["institution_rule"], "registry rule www.sem.example")
        citation = self.curation.concepts[0].facts[0].evidence[0]
        citation.basis = BasisSpec(kind="act", norm="AIG, SR 142.20, Art. 12", refers_to="VZAE, SR 142.201, Art. 9")
        release, report = build_release(self.curation, self.text, "test-v1")
        self.assertEqual(release.evidence[0].basis.label, "Federal act: AIG, SR 142.20, Art. 12, referring to VZAE, SR 142.201, Art. 9")
        self.assertEqual(release.facts[0].provenance.basis.kind, "act")
        self.assertEqual(release.manifest.basis_kinds, {"act": 1})
        self.assertEqual(validate_release(release, self.text), [])
        citation.basis = BasisSpec(kind="ordinance")
        with self.assertRaisesRegex(BuildError, "needs a norm"):
            build_release(self.curation, self.text, "test-v1")
        citation.basis = None
        self.curation.institutions[0].urls = ["www.other.example"]
        with self.assertRaisesRegex(BuildError, "No institution matches"):
            build_release(self.curation, self.text, "test-v1")

    def write_place_files(self, aliases: dict | None = None) -> Path:
        """A curation file that names a place file and an alias file beside it; returns the curation path."""
        places = dict(schema_version="swiss-tip-places/v1", country="CH", title="Register", publisher="Federal Statistical Office FSO",
                      url="https://example.gov/snapshot", accessed_on="2026-09-18", raw_sha256="a" * 64,
                      places=[dict(code="CH-GE", name="Genève"), dict(code="CH-ZH", name="Zürich"),
                              dict(code="CH-GE-6621", name="Genève"), dict(code="CH-ZH-261", name="Zürich")])
        (self.root / "places").mkdir(exist_ok=True)
        (self.root / "places" / "ch-register.json").write_text(json.dumps(places), encoding="utf-8")
        (self.root / "places" / "ch-aliases.json").write_text(json.dumps(aliases or dict(
            schema_version="swiss-tip-place-aliases/v1", country=dict(code="CH", name="Switzerland", aliases=["Schweiz"]),
            generic_words=["Kanton", "Stadt"], aliases={"CH-GE": ["Geneva", "Genf"], "CH-ZH": ["Zurich", "Zurigo"]})), encoding="utf-8")
        self.curation.place_register, self.curation.place_aliases = "../places/ch-register.json", "../places/ch-aliases.json"
        (self.root / "pack").mkdir(exist_ok=True)
        return self.root / "pack" / "curation.yaml"

    def test_the_place_files_the_curation_names_are_embedded(self):
        curation_path = self.write_place_files()
        self.assertEqual([path.name for path in place_files(self.curation, curation_path)], ["ch-register.json", "ch-aliases.json"])
        register = place_register_for(self.curation, curation_path)
        # The country's own entry comes first; an alias that only repeats the official name in ASCII is dropped.
        self.assertEqual([(p.code, p.name, p.aliases) for p in register.places[:3]],
                         [("CH", "Switzerland", ["Schweiz"]), ("CH-GE", "Genève", ["Geneva", "Genf"]), ("CH-ZH", "Zürich", ["Zurigo"])])
        self.assertEqual((register.generic_words, register.accessed_on.isoformat()), (["Kanton", "Stadt"], "2026-09-18"))
        release, report = build_release(self.curation, self.text, "test-v1", place_register=register)
        self.assertEqual(validate_release(release, self.text), [])
        self.assertEqual(report["place_register"], dict(url="https://example.gov/snapshot", accessed_on="2026-09-18",
                                                        raw_sha256="a" * 64, places=5, aliases=4))
        # The register decides which facts a named place reaches, so it is part of the content digest.
        plain, plain_report = build_release(self.curation, self.text, "test-v1")
        self.assertIsNone(plain.place_register)
        self.assertIsNone(plain_report["place_register"])
        self.assertNotIn("place_register", plain.model_dump(mode="json"))
        self.assertNotEqual(plain.manifest.content_sha256, release.manifest.content_sha256)
        release.place_register.places[1].aliases.append("Ginevra")
        self.assertIn("manifest content_sha256 does not match the release body", validate_release(release))

    def test_a_curation_without_place_files_builds_a_release_without_a_register(self):
        self.assertIsNone(place_register_for(self.curation, self.root / "pack" / "curation.yaml"))
        self.curation.place_aliases = "../places/ch-aliases.json"
        with self.assertRaisesRegex(PlaceFileError, "place_aliases without a place_register"):
            place_register_for(self.curation, self.root / "pack" / "curation.yaml")

    def test_place_files_that_do_not_fit_are_refused(self):
        aliases = dict(schema_version="swiss-tip-place-aliases/v1", country=dict(code="CH", name="Switzerland"),
                       aliases={"CH-ZH-9999": ["Gone in a merger"]})
        curation_path = self.write_place_files(aliases)
        with self.assertRaisesRegex(PlaceFileError, "the register does not list: CH-ZH-9999"):
            place_register_for(self.curation, curation_path)
        aliases["aliases"], aliases["country"] = {}, dict(code="LI", name="Liechtenstein")
        self.write_place_files(aliases)
        with self.assertRaisesRegex(PlaceFileError, "is for LI, the register for CH"):
            place_register_for(self.curation, curation_path)
        (self.root / "places" / "ch-register.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(PlaceFileError, "is not a swiss-tip-places/v1 file"):
            place_register_for(self.curation, curation_path)
        self.curation.place_register = "../places/missing.json"
        with self.assertRaisesRegex(PlaceFileError, "cannot read"):
            place_register_for(self.curation, curation_path)

    def test_a_register_must_list_every_published_jurisdiction(self):
        register = place_register_for(self.curation, self.write_place_files())
        register.places = [place for place in register.places if place.code != "CH"]
        with self.assertRaisesRegex(BuildError, "jurisdiction CH of the manifest is not in the place register"):
            build_release(self.curation, self.text, "test-v1", place_register=register)


if __name__ == "__main__":
    unittest.main()
