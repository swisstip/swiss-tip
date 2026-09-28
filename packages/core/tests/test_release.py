import json
import tempfile
import unittest
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.basis import LEVEL_WORD, basis_label, level_of_jurisdiction, make_basis, strongest_basis
from swisstip.core.hierarchy import hierarchy_of
from swisstip.core.release import (Basis, Concept, ContextFieldSpec, EvidenceRecord, FactRecord, Freshness, Institution,
                                   Manifest, Place, PlaceHierarchy, PlaceLevel, PlaceRegister, Provenance, Release,
                                   SourceDocument, Topic, content_hash, dump_release, load_release, sha256_text)
from swisstip.core.validation import ReleaseInvalid, assert_valid, contains, validate_release
from test_hierarchy import POLISH_LEVELS, POLISH_PLACES, register as polish_register

TEXT = "Heading\n\nRegister within 14 days.\n\nBefore work."


def record(document_id: str) -> dict:
    blocks, position = [], 0
    for number, text in enumerate(TEXT.split("\n\n"), 1):
        blocks.append(dict(block_id=f"{document_id}:b{number:05d}", text=text, start=position, end=position + len(text),
                           text_sha256=sha256_text(text)))
        position += len(text) + 2
    return dict(document_id=document_id, content_text=TEXT, content_sha256=sha256_text(TEXT), blocks=blocks,
                acquisition=dict(raw_sha256="r" * 64, path="pages/x/attempt-001/response.html"))


def sample_release() -> Release:
    excerpt = "Register within 14 days."
    start = TEXT.index(excerpt)
    document = SourceDocument(document_id="doc-a", source_url="https://sem.example/faq", document_url="https://sem.example/faq",
                              title="FAQ", publisher="SEM", language="en", accessed_on=date(2026, 9, 11), raw_sha256="r" * 64,
                              content_sha256=sha256_text(TEXT))
    evidence = EvidenceRecord(evidence_id="e-f1-1", document_id="doc-a", source_title="FAQ", publisher="SEM",
                              url="https://sem.example/faq", language="en", accessed_on=date(2026, 9, 11), start_offset=start,
                              end_offset=start + len(excerpt), original_excerpt=excerpt, excerpt_sha256=sha256_text(excerpt),
                              block_ids=["doc-a:b00002"], content_sha256=sha256_text(TEXT), raw_sha256="r" * 64)
    fact = FactRecord(fact_id="f1", concept_id="c1", statement="Register within 14 days of arrival.", jurisdiction="CH",
                      condition={"population": "eu_efta"}, evidence_ids=["e-f1-1"],
                      provenance=Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed", author="test"))
    concept = Concept(concept_id="c1", topic_id="t1", label="Registration deadline", description="When to register.",
                      jurisdictions=["CH"], required_context=["population"],
                      context_schema={"population": ContextFieldSpec(enum=["eu_efta", "third_country"], description="Group")},
                      fact_ids=["f1"])
    manifest = Manifest(release_id="test-v1", pack="test", title="Test", created_at=datetime(2026, 9, 12, 12, 0),
                        scope_statement="Registration.", out_of_scope=["Fees"], out_of_scope_response="Say so.",
                        jurisdictions=["CH"], languages=["en"],
                        freshness=Freshness(snapshot_date=date(2026, 9, 11), max_age_days=60, stale_from=date(2026, 9, 11) + timedelta(days=60)),
                        provenance_kinds={"curated-statement": 1}, review_statuses={"assistant-authored-unreviewed": 1},
                        limitations=["Unreviewed."], content_sha256="0" * 64)
    release = Release(manifest=manifest, documents=[document], topics=[Topic(topic_id="t1", title="Residence", description="Permits.")],
                      concepts=[concept], facts=[fact], evidence=[evidence])
    release.manifest.content_sha256 = content_hash(release)
    return release


# The synthetic Polish release of the design (section 10.1), swiss-tip-release/v3: English statements, synthetic Polish
# excerpts, the synthetic register of test_hierarchy with its declared hierarchy. Every page carries PL_TEXT.
PL_TEXT = "Budżet obywatelski\n\nProjekt zgłasza mieszkaniec gminy.\n\nGłosowanie trwa 14 dni."
PL_WORDS = {"national": "National", "voivodeship": "Voivodeship", "county": "County", "commune": "Commune"}
# institution: (name, level, body, jurisdiction)
PL_INSTITUTIONS = {
    "pl-sejm": ("Sejm of the Republic of Poland", "national", "law_collection", "PL"),
    "pl-gov": ("gov.pl", "national", "portal", "PL"),
    "pl-12-journal": ("Official Journal of the Lesser Poland Voivodeship", "voivodeship", "law_collection", "PL-12"),
    "pl-krakow": ("City of Kraków", "commune", "administration", "PL-12-61-011"),
    "pl-powiat-krakowski": ("Kraków County Office", "county", "administration", "PL-12-06"),
    "pl-warszawa-19115": ("Warsaw 19115", "commune", "portal", "PL-14-65-011")}
# document: institution
PL_DOCUMENTS = {"doc-sejm": "pl-sejm", "doc-gov": "pl-gov", "doc-journal": "pl-12-journal", "doc-krakow": "pl-krakow",
                "doc-powiat": "pl-powiat-krakowski", "doc-19115": "pl-warszawa-19115"}
# evidence: (document, block number, basis level, kind, norm); a commune resolution is published by the voivodeship's
# journal, so its institution is voivodeship-level while its basis is commune-level (section 7).
PL_EVIDENCE = {
    "e-pl-1": ("doc-sejm", 2, "national", "act",
               "Ustawa z dnia 8 marca 1990 r. o samorządzie gminnym (Dz.U. 2024 poz. 1465), Art. 5a ust. 5"),
    "e-pl-2": ("doc-gov", 2, "national", "summary", None),
    "e-krakow-1": ("doc-krakow", 3, "commune", "guidance", None),
    "e-krakow-2": ("doc-journal", 2, "commune", "ordinance", "Uchwała nr LI/1410/21 Rady Miasta Krakowa, § 13"),
    "e-county-1": ("doc-journal", 3, "county", "ordinance", "Uchwała nr XII/85/26 Rady Miasta Krakowa, § 2"),
    "e-county-2": ("doc-powiat", 2, "county", "guidance", None),
    "e-warszawa-1": ("doc-19115", 3, "commune", "summary", None)}
# fact: (jurisdiction, evidence); the county facts at PL-12-61 (Kraków's own county) and PL-12-06 (another county)
PL_FACTS = {"f-pl": ("PL", ["e-pl-1", "e-pl-2"]), "f-krakow": ("PL-12-61-011", ["e-krakow-1", "e-krakow-2"]),
            "f-county-krakow": ("PL-12-61", ["e-county-1"]), "f-county-krakowski": ("PL-12-06", ["e-county-2"]),
            "f-warszawa": ("PL-14-65-011", ["e-warszawa-1"])}


def refreshed(release: Release) -> Release:
    """What the build derives after a change: each fact's strongest basis, the manifest counts and the digest."""
    evidence = {item.evidence_id: item for item in release.evidence}
    institutions = {institution.institution_id: institution for institution in release.institutions}
    for fact in release.facts:
        basis = strongest_basis([evidence[e].basis for e in fact.evidence_ids if e in evidence])
        fact.provenance.basis = basis.model_copy() if basis else None
    release.manifest.institution_levels = dict(Counter(institutions[d.institution_id].level for d in release.documents
                                                       if d.institution_id in institutions))
    release.manifest.basis_kinds = dict(Counter(f.provenance.basis.kind for f in release.facts if f.provenance.basis))
    release.manifest.content_sha256 = content_hash(release)
    return release


def polish_release() -> Release:
    blocks = PL_TEXT.split("\n\n")
    documents = [SourceDocument(document_id=document_id, source_url=f"https://{document_id}.example.pl/",
                                document_url=f"https://{document_id}.example.pl/", title="Budżet obywatelski",
                                publisher=PL_INSTITUTIONS[institution_id][0], institution_id=institution_id, language="pl",
                                accessed_on=date(2026, 9, 27), raw_sha256="r" * 64, content_sha256=sha256_text(PL_TEXT))
                 for document_id, institution_id in PL_DOCUMENTS.items()]
    evidence = []
    for evidence_id, (document_id, block, level, kind, norm) in PL_EVIDENCE.items():
        excerpt = blocks[block - 1]
        start = PL_TEXT.index(excerpt)
        institution_id = PL_DOCUMENTS[document_id]
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, document_id=document_id, source_title="Budżet obywatelski",
            publisher=PL_INSTITUTIONS[institution_id][0], institution_id=institution_id,
            url=f"https://{document_id}.example.pl/", language="pl", accessed_on=date(2026, 9, 27), start_offset=start,
            end_offset=start + len(excerpt), original_excerpt=excerpt, excerpt_sha256=sha256_text(excerpt),
            block_ids=[f"{document_id}:b{block:05d}"], content_sha256=sha256_text(PL_TEXT), raw_sha256="r" * 64,
            basis=make_basis(level, kind, norm, words=PL_WORDS)))
    facts = [FactRecord(fact_id=fact_id, concept_id="c-budget", statement="A resident of the commune proposes a project.",
                        jurisdiction=jurisdiction, evidence_ids=evidence_ids,
                        provenance=Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed",
                                              author="test"))
             for fact_id, (jurisdiction, evidence_ids) in PL_FACTS.items()]
    jurisdictions = sorted({jurisdiction for jurisdiction, _ in PL_FACTS.values()})
    concept = Concept(concept_id="c-budget", topic_id="t-participation", label="Participatory budget proposals",
                      description="Who may propose a project.", aliases=["civic budget"], jurisdictions=jurisdictions,
                      fact_ids=list(PL_FACTS))
    manifest = Manifest(schema_version="swiss-tip-release/v3", release_id="test-pl-v1", pack="test-pl",
                        title="Test Poland", created_at=datetime(2026, 9, 27, 12, 0), scope_statement="Participation.",
                        out_of_scope=["Taxes"], out_of_scope_response="Say so.", jurisdictions=jurisdictions,
                        languages=["pl"], query_languages=["en", "pl"], evidence_languages=["pl"],
                        freshness=Freshness(snapshot_date=date(2026, 9, 27), max_age_days=60,
                                            stale_from=date(2026, 9, 27) + timedelta(days=60)),
                        provenance_kinds={"curated-statement": 5}, review_statuses={"assistant-authored-unreviewed": 5},
                        limitations=["Unreviewed."], content_sha256="0" * 64)
    institutions = [Institution(institution_id=institution_id, name=name, level=level, body=body,
                                jurisdiction=jurisdiction)
                    for institution_id, (name, level, body, jurisdiction) in PL_INSTITUTIONS.items()]
    # Copies, so that a test changing a level or a place leaves the shared synthetic register alone.
    register = polish_register([place.model_copy() for place in POLISH_PLACES],
                               [level.model_copy() for level in POLISH_LEVELS])
    release = Release(manifest=manifest, documents=documents,
                      topics=[Topic(topic_id="t-participation", title="Participation", description="Civic budget.")],
                      concepts=[concept], facts=facts, evidence=evidence, institutions=institutions,
                      place_register=register)
    return refreshed(release)


class ReleaseTests(unittest.TestCase):
    def test_valid_release_round_trips_and_checks_against_the_text_dataset(self):
        release = sample_release()
        self.assertEqual(validate_release(release), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(dump_release(release), encoding="utf-8")
            loaded = load_release(path)
            self.assertEqual(loaded, release)
            text = Path(temporary) / "text" / "documents"
            text.mkdir(parents=True)
            (text / "doc-a.json").write_text(json.dumps(record("doc-a")), encoding="utf-8")
            self.assertEqual(validate_release(loaded, Path(temporary) / "text"), [])
            changed = record("doc-a")
            changed["content_text"] = changed["content_text"].replace("14", "10")
            (text / "doc-a.json").write_text(json.dumps(changed), encoding="utf-8")
            issues = validate_release(loaded, Path(temporary) / "text")
            self.assertTrue(any("excerpt differs" in issue for issue in issues))

    def test_tampering_is_reported(self):
        release = sample_release()
        release.facts[0].statement = "Register within 10 days."
        self.assertIn("manifest content_sha256 does not match the release body", validate_release(release))
        release = sample_release()
        release.evidence[0].original_excerpt = "Register within 10 days."
        release.manifest.content_sha256 = content_hash(release)
        issues = validate_release(release)
        self.assertTrue(any("excerpt hash mismatch" in issue for issue in issues))
        release = sample_release()
        release.facts[0].condition = {"population": "martian"}
        release.manifest.content_sha256 = content_hash(release)
        self.assertTrue(any("outside the schema enum" in issue for issue in validate_release(release)))
        release = sample_release()
        release.facts[0].jurisdiction = "CH-GE"
        release.manifest.content_sha256 = content_hash(release)
        issues = validate_release(release)
        self.assertTrue(any("not declared in the manifest" in issue for issue in issues))
        self.assertTrue(any("jurisdictions differ" in issue for issue in issues))
        with self.assertRaises(ReleaseInvalid):
            assert_valid(release)

    def test_jurisdiction_containment(self):
        self.assertTrue(contains("CH", "CH-ZH-261"))
        self.assertTrue(contains("CH-ZH", "CH-ZH-261"))
        self.assertFalse(contains("CH-ZH", "CH-BE"))
        self.assertFalse(contains("CH-ZH-261", "CH-ZH"))

    def test_a_human_reviewed_fact_names_its_reviewer(self):
        unreviewed = Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed",
                                author="assistant")
        self.assertIsNone(unreviewed.reviewed_by)
        reviewed = Provenance(kind="curated-statement", review_status="human-reviewed", author="assistant",
                              reviewed_on=date(2026, 9, 13), reviewed_by="Anna Meier")
        self.assertEqual(reviewed.reviewed_by, "Anna Meier")
        for missing in (None, "", "  "):
            with self.assertRaisesRegex(ValueError, "requires a non-empty reviewed_by"):
                Provenance(kind="curated-statement", review_status="human-reviewed", author="assistant",
                           reviewed_by=missing)

    def test_a_release_without_institutions_dumps_without_the_new_keys(self):
        # The fields of 16 September 2026 leave the dump, and so the content hash, of an older release untouched; so
        # do the country-profile fields of swiss-tip-release/v3.
        dumped = dump_release(sample_release())
        for key in ("institutions", "institution_id", "basis", "institution_levels", "basis_kinds", "ranking_policy",
                    "place_register", "hierarchy", "query_languages", "evidence_languages"):
            self.assertNotIn(f'"{key}"', dumped)

    def test_query_and_evidence_languages_require_release_v3(self):
        for version in ("swiss-tip-release/v1", "swiss-tip-release/v2"):
            for field in ("query_languages", "evidence_languages"):
                with self.subTest(version=version, field=field):
                    release = sample_release()
                    release.manifest.schema_version = version
                    setattr(release.manifest, field, ["en"])
                    with self.assertRaisesRegex(ValueError, "require swiss-tip-release/v3"):
                        Release.model_validate(release.model_dump(mode="python"))
        release = sample_release()
        release.manifest.schema_version = "swiss-tip-release/v3"
        release.manifest.query_languages = ["en", "pl"]
        release.manifest.evidence_languages = ["pl"]
        dumped = dump_release(release)
        self.assertEqual(Release.model_validate_json(dumped), release)
        # Right after languages, outside the content digest (bound by the readiness bytes of release.json).
        keys = list(json.loads(dumped)["manifest"])
        self.assertEqual(keys[keys.index("languages"):keys.index("languages") + 4],
                         ["languages", "query_languages", "evidence_languages", "freshness"])
        self.assertEqual(content_hash(release), sample_release().manifest.content_sha256)
        # v3 without the language fields loads too; the validator decides what else v3 requires.
        release = sample_release()
        release.manifest.schema_version = "swiss-tip-release/v3"
        self.assertEqual(Release.model_validate_json(dump_release(release)), release)
        with self.assertRaises(ValidationError):
            Manifest.model_validate({**sample_release().manifest.model_dump(mode="json"),
                                     "schema_version": "swiss-tip-release/v4"})

    def test_a_declared_hierarchy_travels_in_the_register_and_its_digest(self):
        levels = [PlaceLevel(id="national", adjective="National", noun="country", plural="countries", label="national"),
                  PlaceLevel(id="voivodeship", adjective="Voivodeship", noun="voivodeship", plural="voivodeships",
                             label="województwo {name}", segment="[0-9]{2}"),
                  PlaceLevel(id="county", adjective="County", noun="county", plural="counties", label="{name}",
                             segment="[0-9]{2}"),
                  PlaceLevel(id="commune", adjective="Commune", noun="commune", plural="communes",
                             label="commune of {name}", segment="[0-9]{3}", note="the gmina, not a village")]
        places = [Place(code="PL", name="Poland"), Place(code="PL-12", name="małopolskie"),
                  Place(code="PL-12-61", name="powiat m. Kraków"), Place(code="PL-12-61-011", name="Kraków")]

        def register(hierarchy: PlaceHierarchy | None) -> PlaceRegister:
            return PlaceRegister(title="TERC", publisher="GUS", url="https://example.gov/terc", accessed_on=date(2026, 9, 27),
                                 raw_sha256="a" * 64, hierarchy=hierarchy, places=places)

        declared = register(PlaceHierarchy(country_adjective="Polish", levels=levels))
        dumped = json.loads(declared.model_dump_json())
        self.assertEqual(list(dumped), ["title", "publisher", "url", "accessed_on", "raw_sha256", "hierarchy", "places"])
        # The country level has no segment and no note, and dumps without the keys.
        self.assertEqual(dumped["hierarchy"]["levels"][0],
                         {"id": "national", "adjective": "National", "noun": "country", "plural": "countries",
                          "label": "national"})
        self.assertEqual(dumped["hierarchy"]["levels"][3]["segment"], "[0-9]{3}")
        self.assertEqual(PlaceRegister.model_validate(dumped), declared)
        self.assertNotIn('"hierarchy"', register(None).model_dump_json())
        # The hierarchy decides which code is which level, so it enters the content digest with the register.
        release = sample_release()
        release.place_register = register(None)
        without = content_hash(release)
        release.place_register = declared
        self.assertNotEqual(content_hash(release), without)

    def test_a_level_declares_a_code_segment_and_nothing_else(self):
        def level(**fields) -> PlaceLevel:
            return PlaceLevel(**{**dict(id="county", adjective="County", noun="county", plural="counties",
                                        label="{name}"), **fields})

        for segment in ("[0-9]{2}", "[A-Z]{2}", "[0-9]{1,4}", "[0-9A-Z]{3}"):
            self.assertEqual(level(segment=segment).segment, segment)
        for segment in (".*", "[0-9]+", "[a-z]{2}", "[0-9]{0}", "[0-9]{10}", "[0-9]{2}|.*", "(?:[0-9]{2})", "\\d{2}"):
            with self.subTest(segment=segment), self.assertRaises(ValidationError):
                level(segment=segment)
        for level_id in ("County", "1county", "county-seat", ""):
            with self.subTest(level_id=level_id), self.assertRaises(ValidationError):
                level(id=level_id)
        with self.assertRaises(ValidationError):
            level(kind="powiat")
        with self.assertRaisesRegex(ValidationError, "at least 3"):
            PlaceHierarchy(country_adjective="Polish", levels=[level(id="national"), level()])

    def test_levels_are_level_ids_of_the_release_not_only_the_swiss_ones(self):
        # The membership check moves to the validator; the Swiss values serialise exactly as the old literal did.
        institution = Institution(institution_id="pl-krakow", name="City of Kraków", level="commune",
                                  body="administration", jurisdiction="PL-12-61-011")
        self.assertEqual(institution.level, "commune")
        basis = Basis(level="voivodeship", kind="guidance", label="Voivodeship authority guidance")
        self.assertEqual(Basis.model_validate_json(basis.model_dump_json()), basis)
        swiss = Institution(institution_id="ch-sem", name="SEM", level="federal", body="administration", jurisdiction="CH")
        self.assertEqual(swiss.model_dump_json(),
                         '{"institution_id":"ch-sem","name":"SEM","level":"federal","body":"administration",'
                         '"jurisdiction":"CH"}')

    def test_release_v1_remains_readable_but_cannot_claim_a_v2_suite_binding(self):
        release = sample_release()
        release.manifest.schema_version = "swiss-tip-release/v1"
        dumped = dump_release(release)
        self.assertEqual(Release.model_validate_json(dumped), release)
        release.manifest.acceptance_suite_sha256 = "a" * 64
        with self.assertRaisesRegex(ValueError, "requires swiss-tip-release/v2"):
            Release.model_validate(release.model_dump(mode="python"))

    def test_the_place_register_is_hashed_and_validated(self):
        def with_places(places: list[Place]) -> Release:
            release = sample_release()
            release.place_register = PlaceRegister(title="Register", publisher="FSO", url="https://example.gov/register",
                                                   accessed_on=date(2026, 9, 18), raw_sha256="a" * 64, places=places)
            release.manifest.content_sha256 = content_hash(release)
            return release

        good = [Place(code="CH", name="Switzerland", aliases=["Schweiz"]), Place(code="CH-ZH", name="Zürich"),
                Place(code="CH-ZH-261", name="Zürich")]
        release = with_places(good)
        self.assertEqual(validate_release(release), [])
        self.assertNotEqual(release.manifest.content_sha256, sample_release().manifest.content_sha256)
        # A place without aliases dumps without the key, and the register survives the round trip.
        self.assertEqual(json.loads(dump_release(release))["place_register"]["places"][1], {"code": "CH-ZH", "name": "Zürich"})
        self.assertEqual(Release.model_validate_json(dump_release(release)).place_register, release.place_register)
        release.place_register.places[0].aliases.append("Suisse")
        self.assertIn("manifest content_sha256 does not match the release body", validate_release(release))
        for places, fragment in (([*good, Place(code="CH-ZH", name="Zurich")], "duplicate codes in the place register"),
                                 ([*good, Place(code="ch-be", name="Bern")], "malformed code"),
                                 ([*good, Place(code="CH-BE-351", name="Bern")], "lies in CH-BE, which the register does not list"),
                                 ([*good, Place(code="CH-BE", name=" ")], "empty name or alias"),
                                 (good[1:], "jurisdiction CH of the manifest is not in the place register")):
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in issue for issue in validate_release(with_places(places))))

    def test_basis_labels_and_ranking_policy(self):
        from swisstip.core.basis import DEFAULT_RANKING_POLICY, basis_label, fact_weight, make_basis, strongest_basis
        from swisstip.core.release import RankingPolicy

        self.assertEqual(basis_label("federal", "act", "AIG, SR 142.20, Art. 12"), "Federal act: AIG, SR 142.20, Art. 12")
        self.assertEqual(basis_label("federal", "treaty", "FZA, SR 0.142.112.681, Annex I, Art. 6"),
                         "International agreement: FZA, SR 0.142.112.681, Annex I, Art. 6")
        self.assertEqual(basis_label("cantonal", "directive", "ZStB 87.3"), "Cantonal directive: ZStB 87.3")
        self.assertEqual(basis_label("municipal", "guidance"), "Municipal authority guidance")
        self.assertEqual(basis_label("federal", "directory"), "Federal authority directory")
        self.assertEqual(basis_label("federal", "summary"), "Portal summary of federal rules")
        self.assertEqual(basis_label("federal", "guidance", refers_to="BüG, SR 141.0, Art. 9"),
                         "Federal authority guidance, referring to BüG, SR 141.0, Art. 9")
        with self.assertRaisesRegex(ValueError, "needs a norm"):
            basis_label("federal", "ordinance")
        law, summary = make_basis("federal", "act", "AIG, Art. 12"), make_basis("federal", "summary")
        self.assertEqual(strongest_basis([summary, law, None]), law)
        self.assertIsNone(strongest_basis([None]))
        release = sample_release()
        self.assertEqual(fact_weight(release.facts[0], {e.evidence_id: e for e in release.evidence}), 0.7)  # unreviewed
        with self.assertRaisesRegex(ValueError, "unknown ranking key"):
            RankingPolicy(basis_kind={"blog": 0.5}, provenance_kind={}, review_status={})
        with self.assertRaisesRegex(ValueError, "must lie in"):
            RankingPolicy(basis_kind={"act": 1.5}, provenance_kind={}, review_status={})
        self.assertEqual(DEFAULT_RANKING_POLICY.basis_kind["summary"], 0.75)

    def test_institutions_and_bases_are_validated(self):
        from swisstip.core.basis import make_basis
        from swisstip.core.release import Institution

        def with_basis() -> Release:
            release = sample_release()
            release.institutions = [Institution(institution_id="ch-sem", name="SEM", level="federal", body="administration",
                                                jurisdiction="CH")]
            release.documents[0].institution_id = "ch-sem"
            release.evidence[0].institution_id = "ch-sem"
            release.evidence[0].basis = make_basis("federal", "guidance")
            release.facts[0].provenance.basis = make_basis("federal", "guidance")
            release.manifest.institution_levels = {"federal": 1}
            release.manifest.basis_kinds = {"guidance": 1}
            release.manifest.content_sha256 = content_hash(release)
            return release

        self.assertEqual(validate_release(with_basis()), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(dump_release(with_basis()), encoding="utf-8")
            self.assertEqual(load_release(path), with_basis())

        def issues_after(change) -> list[str]:
            release = with_basis()
            change(release)
            release.manifest.content_sha256 = content_hash(release)
            return validate_release(release)

        def set_level(release):
            release.institutions[0].level = "cantonal"
        self.assertTrue(any("is cantonal but speaks for CH" in issue for issue in issues_after(set_level)))

        def unknown_institution(release):
            release.documents[0].institution_id = "nope"
        self.assertTrue(any("unknown institution nope" in issue for issue in issues_after(unknown_institution)))

        def wrong_label(release):
            release.evidence[0].basis.label = "Federal law"
        self.assertTrue(any("label does not match" in issue for issue in issues_after(wrong_label)))

        def weaker_provenance(release):
            release.facts[0].provenance.basis = make_basis("federal", "summary")
        self.assertTrue(any("not the strongest basis" in issue for issue in issues_after(weaker_provenance)))

        def cantonal_basis_on_a_federal_fact(release):
            release.evidence[0].basis = make_basis("cantonal", "guidance")
            release.facts[0].provenance.basis = make_basis("cantonal", "guidance")
        self.assertTrue(any("rests on a cantonal basis" in issue for issue in issues_after(cantonal_basis_on_a_federal_fact)))

        def narrower_institution(release):
            release.institutions[0].level = "cantonal"
            release.institutions[0].jurisdiction = "CH-ZH"
        self.assertTrue(any("speaks for CH-ZH" in issue for issue in issues_after(narrower_institution)))

        def wrong_counts(release):
            release.manifest.basis_kinds = {"act": 1}
        self.assertTrue(any("basis kinds differ" in issue for issue in issues_after(wrong_counts)))

        def norm_missing(release):
            release.evidence[0].basis = make_basis("federal", "act", "AIG")
            release.evidence[0].basis.norm = None
            release.facts[0].provenance.basis = release.evidence[0].basis
        self.assertTrue(any("names no norm" in issue for issue in issues_after(norm_missing)))

    def test_the_wire_contract_publishes_the_same_review_statuses_as_the_release_format(self):
        # contracts.py repeats the literal instead of importing it; a status added on one side and not
        # the other would let the build store a status the served Fact cannot express.
        from typing import get_args

        from swisstip.core import contracts, release
        self.assertEqual(get_args(contracts.ReviewStatus), get_args(release.ReviewStatus))


class LevelWordTests(unittest.TestCase):
    def test_swiss_labels_are_todays(self):
        # The summary label used the raw level id; it now lower-cases the level's adjective, which is the id for each
        # Swiss level, so every Swiss level and kind recomputes to today's label.
        for level, word in LEVEL_WORD.items():
            with self.subTest(level=level):
                self.assertEqual(basis_label(level, "summary"), f"Portal summary of {level} rules")
                self.assertEqual(basis_label(level, "act", "N"), f"{word} act: N")
                self.assertEqual(basis_label(level, "ordinance", "N"), f"{word} ordinance: N")
                self.assertEqual(basis_label(level, "directive", "N"), f"{word} directive: N")
                self.assertEqual(basis_label(level, "treaty", "N"), "International agreement: N")
                self.assertEqual(basis_label(level, "guidance", refers_to="R"), f"{word} authority guidance, referring to R")
                self.assertEqual(basis_label(level, "directory"), f"{word} authority directory")
                self.assertEqual(make_basis(level, "summary").label, f"Portal summary of {level} rules")
        self.assertEqual(level_of_jurisdiction("CH-ZH-261"), "municipal")

    def test_declared_labels_and_levels(self):
        self.assertEqual(basis_label("national", "act", "Ustawa (Dz.U. 2024 poz. 1465), Art. 5a ust. 5", words=PL_WORDS),
                         "National act: Ustawa (Dz.U. 2024 poz. 1465), Art. 5a ust. 5")
        self.assertEqual(basis_label("voivodeship", "ordinance", "Uchwała nr 1 Sejmiku Województwa Małopolskiego, § 2",
                                     words=PL_WORDS),
                         "Voivodeship ordinance: Uchwała nr 1 Sejmiku Województwa Małopolskiego, § 2")
        self.assertEqual(basis_label("commune", "directive", "Zarządzenie nr 1 Prezydenta Miasta Krakowa, § 4",
                                     words=PL_WORDS),
                         "Commune directive: Zarządzenie nr 1 Prezydenta Miasta Krakowa, § 4")
        self.assertEqual(basis_label("commune", "directory", words=PL_WORDS), "Commune authority directory")
        self.assertEqual(basis_label("national", "summary", words=PL_WORDS), "Portal summary of national rules")
        self.assertEqual(basis_label("commune", "summary", words=PL_WORDS), "Portal summary of commune rules")
        basis = make_basis("county", "guidance", words=PL_WORDS)
        self.assertEqual((basis.level, basis.label), ("county", "County authority guidance"))
        polish = hierarchy_of(polish_release().place_register)
        self.assertEqual(polish.words, PL_WORDS)
        self.assertEqual([level_of_jurisdiction(code, polish) for code in ("PL", "PL-12", "PL-12-61", "PL-12-61-011")],
                         ["national", "voivodeship", "county", "commune"])
        self.assertIsNone(level_of_jurisdiction("PL-12-61-011-1", polish))


class DeclaredReleaseTests(unittest.TestCase):
    """A release that declares its country (swiss-tip-release/v3), checked against its own hierarchy and languages."""

    def issues_after(self, change) -> list[str]:
        release = polish_release()
        change(release)
        return validate_release(refreshed(release))

    def test_the_polish_release_validates_and_its_labels_round_trip(self):
        release = polish_release()
        self.assertEqual(validate_release(release), [])
        self.assertEqual(release.manifest.institution_levels, {"national": 2, "voivodeship": 1, "commune": 2, "county": 1})
        self.assertEqual(release.manifest.basis_kinds, {"act": 1, "ordinance": 2, "guidance": 1, "summary": 1})
        self.assertEqual({item.evidence_id: item.basis.label for item in release.evidence}, {
            "e-pl-1": "National act: Ustawa z dnia 8 marca 1990 r. o samorządzie gminnym (Dz.U. 2024 poz. 1465), "
                      "Art. 5a ust. 5",
            "e-pl-2": "Portal summary of national rules",
            "e-krakow-1": "Commune authority guidance",
            "e-krakow-2": "Commune ordinance: Uchwała nr LI/1410/21 Rady Miasta Krakowa, § 13",
            "e-county-1": "County ordinance: Uchwała nr XII/85/26 Rady Miasta Krakowa, § 2",
            "e-county-2": "County authority guidance",
            "e-warszawa-1": "Portal summary of commune rules"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(dump_release(release), encoding="utf-8")
            loaded = load_release(path)
        self.assertEqual(loaded, release)
        self.assertEqual(validate_release(loaded), [])
        self.assertEqual(json.loads(dump_release(release))["manifest"]["schema_version"], "swiss-tip-release/v3")

        def swiss_words(release):
            release.evidence[2].basis.label = "Municipal authority guidance"
        self.assertEqual(self.issues_after(swiss_words), ["evidence e-krakow-1 basis label does not match its fields"])

    def test_a_malformed_hierarchy_is_reported_by_its_cause(self):
        def levels_changed(depth: int, **fields):
            def change(release):
                levels = release.place_register.hierarchy.levels
                levels[depth] = levels[depth].model_copy(update=fields)
            return change

        def levels_cut(count: int):
            def change(release):
                release.place_register.hierarchy.levels = release.place_register.hierarchy.levels[:count]
            return change

        def places_changed(places):
            def change(release):
                release.place_register.places = places(release.place_register.places)
            return change

        def outside_jurisdiction(release):
            release.manifest.jurisdictions.append("CZ-10")

        # The pattern stays intact: the cause is the only issue.
        for change, issue in ((levels_changed(0, segment="[A-Z]{2}"), "level national is the country and takes no code segment"),
                              (levels_changed(1, note="not a district"), "level voivodeship carries a note, which only the lowest level may"),
                              (levels_changed(3, label="commune of {name} ({code})"),
                               "level commune label 'commune of {name} ({code})' carries a placeholder other than {name}"),
                              (levels_changed(3, label="commune of {name!r}"),
                               "level commune label 'commune of {name!r}' carries a placeholder other than {name}"),
                              (levels_changed(3, label="gmina {"), "level commune label 'gmina {' carries a placeholder other than {name}")):
            with self.subTest(issue):
                self.assertEqual(self.issues_after(change), [issue])
        # The pattern accepts no code or the wrong codes: every code is reported, and the cause comes last.
        for change, issue in ((levels_changed(2, segment=None), "level county declares no code segment"),
                              (levels_changed(3, segment="[0-9]{9,1}"), "level commune code segment '[0-9]{9,1}' is not a valid width"),
                              (levels_changed(2, id="commune"), "duplicate level ids in the place hierarchy"),
                              (levels_cut(2), "the place hierarchy has 2 level(s); it needs at least three"),
                              (levels_cut(0), "the place hierarchy has 0 level(s); it needs at least three"),
                              (places_changed(lambda places: [p for p in places if p.code != "PL"]),
                               "the place register lists 0 country places (codes without a dash); a declared hierarchy "
                               "needs exactly one"),
                              (places_changed(lambda places: [*places, Place(code="CZ", name="Czechia")]),
                               "the place register lists 2 country places (codes without a dash); a declared hierarchy "
                               "needs exactly one"),
                              (outside_jurisdiction, "manifest jurisdictions CZ-10 do not lie in the register's country PL")):
            with self.subTest(issue):
                issues = self.issues_after(change)
                self.assertIn(issue, issues)
                self.assertEqual(issues[-1], issue)

    def test_release_v3_if_and_only_if_a_hierarchy_is_declared(self):
        # A declaring register in an older release: loads (the language fields dropped), refused by the validator,
        # after every other issue.
        release = polish_release()
        release.manifest.schema_version = "swiss-tip-release/v2"
        release.manifest.query_languages = release.manifest.evidence_languages = None
        loaded = Release.model_validate_json(dump_release(release))
        self.assertEqual(validate_release(loaded), ["a place register that declares a hierarchy requires swiss-tip-release/v3"])
        loaded.facts[0].statement = "Changed."
        issues = validate_release(loaded)
        self.assertEqual((issues[0], issues[-1]), ("manifest content_sha256 does not match the release body",
                                                   "a place register that declares a hierarchy requires swiss-tip-release/v3"))
        # v3 without a hierarchy: with no register, and with a Swiss register that declares none.
        swiss = sample_release()
        swiss.manifest.schema_version = "swiss-tip-release/v3"
        self.assertEqual(validate_release(swiss), ["swiss-tip-release/v3 requires a place register with a hierarchy"])
        swiss.place_register = PlaceRegister(title="Register", publisher="FSO", url="https://example.gov/register",
                                             accessed_on=date(2026, 9, 18), raw_sha256="a" * 64,
                                             places=[Place(code="CH", name="Switzerland")])
        swiss.manifest.content_sha256 = content_hash(swiss)
        self.assertEqual(validate_release(swiss), ["swiss-tip-release/v3 requires a place register with a hierarchy"])
        # A Swiss release of v1 or v2 has nothing to declare.
        for version in ("swiss-tip-release/v1", "swiss-tip-release/v2"):
            swiss.manifest.schema_version = version
            self.assertEqual(validate_release(swiss), [])

    def test_an_unknown_basis_level_is_an_issue_not_a_crash(self):
        # Swiss: the old literal refused "national" at load; the level is a plain string now, and the validator says so.
        release = sample_release()
        release.institutions = [Institution(institution_id="ch-sem", name="SEM", level="federal", body="administration",
                                            jurisdiction="CH")]
        release.documents[0].institution_id = release.evidence[0].institution_id = "ch-sem"
        release.evidence[0].basis = Basis(level="national", kind="guidance", label="National authority guidance")
        release = Release.model_validate_json(dump_release(refreshed(release)))
        self.assertEqual(validate_release(release), ["evidence e-f1-1 basis level 'national' is not a level of this release",
                                                     "fact f1 is federal but rests on a national basis"])

        # Declared: a Swiss level in a Polish release.
        def cantonal(release):
            release.evidence[2].basis = Basis(level="cantonal", kind="guidance", label="Cantonal authority guidance")
        self.assertEqual(self.issues_after(cantonal),
                         ["evidence e-krakow-1 basis level 'cantonal' is not a level of this release"])

    def test_an_institution_level_follows_the_depth_of_its_jurisdiction(self):
        def institution(institution_id: str, **fields):
            def change(release):
                for index, item in enumerate(release.institutions):
                    if item.institution_id == institution_id:
                        release.institutions[index] = item.model_copy(update=fields)
            return change

        for change, issue in ((institution("pl-krakow", level="county"), "institution pl-krakow is county but speaks for PL-12-61-011"),
                              (institution("pl-krakow", level="municipal"),
                               "institution pl-krakow is municipal but speaks for PL-12-61-011"),
                              (institution("pl-powiat-krakowski", level="commune"),
                               "institution pl-powiat-krakowski is commune but speaks for PL-12-06"),
                              (institution("pl-12-journal", level="national"),
                               "institution pl-12-journal is national but speaks for PL-12")):
            with self.subTest(issue):
                self.assertEqual(self.issues_after(change), [issue])
        # Deeper than the lowest level: no level, and not a code of this country.
        issues = self.issues_after(institution("pl-krakow", jurisdiction="PL-12-61-011-1"))
        self.assertIn("institution pl-krakow has a malformed jurisdiction 'PL-12-61-011-1'", issues)
        issues = self.issues_after(institution("pl-krakow", jurisdiction="CH-ZH-261", level="commune"))
        self.assertIn("institution pl-krakow has a malformed jurisdiction 'CH-ZH-261'", issues)

    def test_the_country_itself_rests_on_a_national_basis(self):
        def commune_law(release):
            release.evidence[0].basis = make_basis("commune", "ordinance", "Uchwała nr 1 Rady Miasta Krakowa, § 1",
                                                   words=PL_WORDS)
        self.assertEqual(self.issues_after(commune_law), ["fact f-pl is national but rests on a commune basis"])

        # Only the country's own facts: a commune fact may rest on a national act.
        def national_law_for_krakow(release):
            release.evidence[2].basis = make_basis("national", "act", "Ustawa, Art. 1", words=PL_WORDS)
        self.assertEqual(self.issues_after(national_law_for_krakow), [])

    def test_the_declared_languages_are_enforced(self):
        def english_excerpt(release):
            release.manifest.languages = ["en", "pl"]
            release.documents[3].language = release.evidence[2].language = "en"
        self.assertEqual(self.issues_after(english_excerpt),
                         ["evidence e-krakow-1 language en is not one of the manifest's evidence_languages"])

        def german_statement(release):
            release.facts[4].language = "de"
        self.assertEqual(self.issues_after(german_statement),
                         ["fact f-warszawa language de is not one of the manifest's query_languages"])

        def polish_statement(release):
            release.facts[4].language = "pl"
        self.assertEqual(self.issues_after(polish_statement), [])

        def malformed(release):
            release.manifest.query_languages = ["en", "en", "PL"]
            release.manifest.evidence_languages = ["pl", "pol-x"]
        self.assertEqual(self.issues_after(malformed), ["duplicate languages in manifest query_languages",
                                                        "manifest query_languages has a malformed language 'PL'",
                                                        "manifest evidence_languages has a malformed language 'pol-x'"])

        def empty(release):
            release.manifest.query_languages = []
            release.manifest.evidence_languages = []
        issues = self.issues_after(empty)
        self.assertIn("manifest query_languages lists no language", issues)
        self.assertIn("manifest evidence_languages lists no language", issues)
        self.assertIn("fact f-pl language en is not one of the manifest's query_languages", issues)
        self.assertIn("evidence e-pl-1 language pl is not one of the manifest's evidence_languages", issues)


if __name__ == "__main__":
    unittest.main()
