import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from swisstip.core.release import (Concept, ContextFieldSpec, EvidenceRecord, FactRecord, Freshness, Manifest, Place,
                                   PlaceRegister, Provenance, Release, SourceDocument, Topic, content_hash, dump_release,
                                   load_release, sha256_text)
from swisstip.core.validation import ReleaseInvalid, assert_valid, contains, validate_release

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
        # The fields of 16 September 2026 leave the dump, and so the content hash, of an older release untouched.
        dumped = dump_release(sample_release())
        for key in ("institutions", "institution_id", "basis", "institution_levels", "basis_kinds", "ranking_policy",
                    "place_register"):
            self.assertNotIn(f'"{key}"', dumped)

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


if __name__ == "__main__":
    unittest.main()
