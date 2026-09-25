import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import date, datetime, timedelta

from swisstip.core.basis import make_basis, strongest_basis
from swisstip.core.contracts import (SEARCH_DESCRIPTION_LANGUAGES, SEARCH_DESCRIPTION_LEAD, CoverageRoot,
                                     GetCoverageRequest, SearchRequest, Status, ToolError, tool_output_schema)
from swisstip.core.release import (Concept, ContextFieldSpec, DecisionRule, EvidenceRecord, FactRecord, Freshness, Institution,
                                   Manifest, Place, PlaceRegister, Provenance, Release, RequiredUserFact, SourceDocument, Topic,
                                   content_hash, sha256_text)
from swisstip.core.validation import assert_valid
from swisstip.runtime.service import ReleaseService, tokens
from swisstip.runtime.semantic import SemanticError, SemanticSearch

SNAPSHOT = date(2026, 9, 11)
PROVENANCE = Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed", author="test")
POPULATION = ContextFieldSpec(enum=["eu_efta", "third_country"], description="Citizenship group.")


def evidence(evidence_id: str, document_id: str, excerpt: str, language: str = "de") -> EvidenceRecord:
    return EvidenceRecord(evidence_id=evidence_id, document_id=document_id, source_title="Page", publisher="SEM",
                          url=f"https://example.gov/{document_id}", language=language, accessed_on=SNAPSHOT, start_offset=0,
                          end_offset=len(excerpt), original_excerpt=excerpt, excerpt_sha256=sha256_text(excerpt),
                          block_ids=[f"{document_id}:b00001"], content_sha256="c" * 64, raw_sha256="r" * 64)


def fact(fact_id: str, concept_id: str, statement: str, jurisdiction: str, evidence_ids: list[str], **extra) -> FactRecord:
    return FactRecord(fact_id=fact_id, concept_id=concept_id, statement=statement, jurisdiction=jurisdiction,
                      evidence_ids=evidence_ids, provenance=PROVENANCE, **extra)


def sample_release() -> Release:
    documents = [SourceDocument(document_id=f"doc-{n}", source_url=f"https://example.gov/doc-{n}", document_url=f"https://example.gov/doc-{n}",
                                title="Page", publisher="SEM", language="de", accessed_on=SNAPSHOT, raw_sha256="r" * 64,
                                content_sha256="c" * 64) for n in ("a", "b", "c", "d")]
    evidence_items = [evidence("e-a", "doc-a", "Innert 14 Tagen nach Ankunft und vor Stellenantritt anmelden."),
                      evidence("e-b", "doc-b", "Persoenlich bei der Wohngemeinde anmelden."),
                      evidence("e-c", "doc-c", "Termin beim Personenmeldeamt vereinbaren."),
                      evidence("e-d", "doc-d", "UK nationals need a work permit since 2021.", "en")]
    facts = [
        fact("deadline-1", "deadline", "Register within 14 days of arrival and before starting work.", "CH", ["e-a"],
             condition={"population": "eu_efta"}),
        fact("deadline-2", "deadline", "Third-country nationals need a permit before entry.", "CH", ["e-a"],
             condition={"population": "third_country"}),
        fact("zh-1", "zh-registration", "Register in person with the municipality within 14 days.", "CH-ZH", ["e-b"]),
        fact("city-1", "city-arrival", "Book an appointment at the Personenmeldeamt.", "CH-ZH-261", ["e-c"]),
        fact("contact-zh", "contact", "Migrationsamt Zurich.", "CH-ZH", ["e-b"]),
        fact("contact-be", "contact", "Migrationsdienst Bern.", "CH-BE", ["e-b"]),
        fact("uk-1", "uk-work", "UK nationals need a work permit.", "CH", ["e-d"], valid_from=date(2021, 1, 1)),
    ]
    concepts = [
        Concept(concept_id="deadline", topic_id="residence", label="Municipal registration deadline after arrival",
                description="When EU/EFTA nationals register.", aliases=["Anmeldefrist", "14 days"],
                questions=["By when must I register my stay?"], jurisdictions=["CH"], required_context=["population"],
                context_schema={"population": POPULATION}, fact_ids=["deadline-1", "deadline-2"],
                required_user_facts=[RequiredUserFact(name="arrival_date", status="NOT_PROVIDED_BY_SERVICE", instruction="Ask for it.")],
                decision_rule=DecisionRule(description="Earlier of two limits.", steps=["Compute both.", "Name the earlier."])),
        Concept(concept_id="zh-registration", topic_id="residence", label="Zurich registration for EU/EFTA nationals",
                description="Cantonal procedure.", jurisdictions=["CH-ZH"], fact_ids=["zh-1"]),
        Concept(concept_id="city-arrival", topic_id="residence", label="City of Zurich: registering arrival from abroad",
                description="Municipal procedure.", jurisdictions=["CH-ZH-261"], fact_ids=["city-1"]),
        Concept(concept_id="contact", topic_id="contacts", label="Cantonal migration-office contact",
                description="One fact per canton.", jurisdictions=["CH-BE", "CH-ZH"], fact_ids=["contact-zh", "contact-be"]),
        Concept(concept_id="uk-work", topic_id="residence", label="UK nationals: new employment", description="Since 2021.",
                jurisdictions=["CH"], fact_ids=["uk-1"]),
    ]
    manifest = Manifest(release_id="test-v1", pack="test", title="Test", created_at=datetime(2026, 9, 12, 12, 0),
                        scope_statement="Residence permits and registration.", out_of_scope=["Fees"],
                        out_of_scope_response="Say so.", jurisdictions=["CH", "CH-BE", "CH-ZH", "CH-ZH-261"],
                        languages=["de", "en"],
                        freshness=Freshness(snapshot_date=SNAPSHOT, max_age_days=60, stale_from=SNAPSHOT + timedelta(days=60)),
                        provenance_kinds={"curated-statement": 7}, review_statuses={"assistant-authored-unreviewed": 7},
                        limitations=["Unreviewed."], content_sha256="0" * 64)
    release = Release(manifest=manifest, documents=documents,
                      topics=[Topic(topic_id="residence", title="Residence", description="Permits."),
                              Topic(topic_id="contacts", title="Contacts", description="Offices.")],
                      concepts=concepts, facts=facts, evidence=evidence_items)
    release.manifest.content_sha256 = content_hash(release)
    assert_valid(release)
    return release


PLACES = PlaceRegister(
    title="Test register", publisher="Federal Statistical Office", url="https://example.gov/register", accessed_on=SNAPSHOT,
    raw_sha256="p" * 64, generic_words=["Canton of", "Kanton", "City of", "Stadt", "Gemeinde"],
    places=[Place(code="CH", name="Switzerland", aliases=["Schweiz", "Suisse"]),
            Place(code="CH-ZH", name="Zürich", aliases=["Zurich"]), Place(code="CH-BE", name="Bern / Berne"),
            Place(code="CH-SG", name="St. Gallen"),
            Place(code="CH-ZH-261", name="Zürich", aliases=["Zurich", "Zurigo"]), Place(code="CH-ZH-69", name="Wallisellen"),
            Place(code="CH-ZH-230", name="Winterthur"), Place(code="CH-ZH-83", name="Buchs (ZH)"),
            Place(code="CH-SG-3271", name="Buchs (SG)"), Place(code="CH-BE-351", name="Bern"),
            Place(code="CH-BE-371", name="Biel/Bienne")])


def release_with_places() -> Release:
    """The sample release with a place register, so that a jurisdiction can be given in names."""
    release = sample_release()
    release.place_register = PLACES
    release.manifest.content_sha256 = content_hash(release)
    assert_valid(release)
    return release


def release_with_one_reviewed_fact() -> Release:
    """The sample release with the Zurich fact confirmed by a person, so a reviewed_only request has
    something to serve and something to withhold in the same call."""
    release = sample_release()
    for item in release.facts:
        if item.fact_id == "zh-1":
            item.provenance = Provenance(kind="curated-statement", review_status="human-reviewed", author="test",
                                         reviewed_on=date(2026, 9, 24), reviewed_by="Expert")
    release.manifest.review_statuses = {"assistant-authored-unreviewed": 6, "human-reviewed": 1}
    release.manifest.content_sha256 = content_hash(release)
    assert_valid(release)
    return release


def with_aliases(aliases: dict[str, list[str]]) -> Release:
    """The sample release with the given aliases on the given concepts."""
    release = sample_release()
    for concept in release.concepts:
        concept.aliases = aliases.get(concept.concept_id, concept.aliases)
    release.manifest.content_sha256 = content_hash(release)
    return release


INSTITUTIONS = {"doc-a": ("ch-sem", "federal", "CH"), "doc-b": ("zh-migrationsamt", "cantonal", "CH-ZH"),
                "doc-c": ("zh-261-personenmeldeamt", "municipal", "CH-ZH-261"), "doc-d": ("ch-sem", "federal", "CH")}


def release_with_basis() -> Release:
    """The sample release with an institution per page and a basis per excerpt, plus two concepts that a query
    matches equally: one resting on a portal summary, one on an act."""
    release = sample_release()
    names = {"ch-sem": "State Secretariat for Migration SEM", "zh-migrationsamt": "Canton of Zurich, Migration Office",
             "zh-261-personenmeldeamt": "City of Zurich, Population Office"}
    release.institutions = [Institution(institution_id=key, name=names[key], level=level, body="administration", jurisdiction=code)
                            for key, level, code in sorted({v for v in INSTITUTIONS.values()})]
    for document in release.documents:
        document.institution_id = INSTITUTIONS[document.document_id][0]
    for item in release.evidence:
        item.institution_id = INSTITUTIONS[item.document_id][0]
        item.basis = make_basis(INSTITUTIONS[item.document_id][1], "guidance")
    # The Bern contact cannot rest on the Zurich office's page once pages name their institution: it cites the SEM page.
    next(f for f in release.facts if f.fact_id == "contact-be").evidence_ids = ["e-a"]
    release.documents.append(SourceDocument(document_id="doc-law", source_url="https://law.example/aig", document_url="https://law.example/aig",
                                            title="AIG", publisher="Fedlex", institution_id="ch-sem", language="de", accessed_on=SNAPSHOT,
                                            raw_sha256="r" * 64, content_sha256="c" * 64))
    release.documents.append(SourceDocument(document_id="doc-portal", source_url="https://portal.example/permits", document_url="https://portal.example/permits",
                                            title="Portal", publisher="ch.ch", institution_id="ch-sem", language="de", accessed_on=SNAPSHOT,
                                            raw_sha256="r" * 64, content_sha256="c" * 64))
    law = evidence("e-law", "doc-law", "Art. 12 Anmeldepflicht ...")
    law.publisher = "Fedlex"
    law.institution_id, law.basis = "ch-sem", make_basis("federal", "act", "AIG, SR 142.20, Art. 12")
    portal = evidence("e-portal", "doc-portal", "Sie müssen sich anmelden.")
    portal.institution_id, portal.basis = "ch-sem", make_basis("federal", "summary")
    release.evidence.extend([law, portal])
    release.facts.append(fact("kiosk-summary-1", "kiosk-summary", "A kiosk needs a permit.", "CH", ["e-portal"]))
    release.facts.append(fact("kiosk-law-1", "kiosk-law", "A kiosk needs a permit under the act.", "CH", ["e-law"]))
    release.facts.append(fact("kiosk-law-2", "kiosk-law", "The portal says so too.", "CH", ["e-portal", "e-law"]))
    release.concepts.append(Concept(concept_id="kiosk-summary", topic_id="residence", label="Kiosk permit (portal)",
                                    description="Portal.", aliases=["Kioskbewilligung"], jurisdictions=["CH"], fact_ids=["kiosk-summary-1"]))
    release.concepts.append(Concept(concept_id="kiosk-law", topic_id="residence", label="Kiosk permit (act)",
                                    description="Act.", aliases=["Kioskbewilligung"], jurisdictions=["CH"], fact_ids=["kiosk-law-1", "kiosk-law-2"]))
    evidence_by_id = {item.evidence_id: item for item in release.evidence}
    for item in release.facts:
        item.provenance = item.provenance.model_copy(update=dict(basis=strongest_basis([evidence_by_id[e].basis for e in item.evidence_ids])))
    release.manifest.provenance_kinds = {"curated-statement": 10}
    release.manifest.review_statuses = {"assistant-authored-unreviewed": 10}
    release.manifest.institution_levels = {"federal": 4, "cantonal": 1, "municipal": 1}
    release.manifest.basis_kinds = {"guidance": 7, "summary": 1, "act": 2}
    release.manifest.content_sha256 = content_hash(release)
    assert_valid(release)
    return release


class BasisTests(unittest.TestCase):
    def setUp(self):
        self.service = ReleaseService(release_with_basis())

    def test_the_prior_ranks_the_act_above_the_summary_and_leaves_lexical_ties_to_the_id_otherwise(self):
        plain = ReleaseService(sample_release())
        self.assertTrue(all(value == 0.7 for value in plain.concept_authority.values()))  # unreviewed statements only
        with_prior = [hit.concept_id for hit in self.service.dispatch("search", {"query": "Kioskbewilligung"}).results]
        self.assertEqual(with_prior, ["kiosk-law", "kiosk-summary"])
        self.assertGreater(self.service.prior("kiosk-law"), self.service.prior("kiosk-summary"))
        self.assertEqual(self.service.concept_authority["kiosk-law"], 0.7)  # act, unreviewed
        self.assertAlmostEqual(self.service.concept_authority["kiosk-summary"], 0.75 * 0.7)
        # The same prior reorders semantic candidates before the ranks are fused.
        ranker = Mock()
        ranker.index = SimpleNamespace(model="m", model_digest="d", release_id=self.service.release_id,
                                       release_content_sha256=self.service.release.manifest.content_sha256)
        ranker.ranked.return_value = (0.90, [(0.90, "kiosk-summary"), (0.89, "kiosk-law")])
        self.service.semantic_search = ranker
        fused = self.service.dispatch("search", {"query": "kiosk", "limit": 2})
        self.assertEqual([hit.concept_id for hit in fused.results], ["kiosk-law", "kiosk-summary"])

    def test_citations_name_the_institution_and_put_the_law_first(self):
        result = self.service.dispatch("resolve", {"concept_ids": ["kiosk-law", "zh-registration"], "jurisdiction": {"canton_code": "CH-ZH"}})
        per = {item.concept_id: item for item in result.results}
        citations = per["kiosk-law"].citations
        self.assertEqual([c.url for c in citations], ["https://example.gov/doc-law", "https://example.gov/doc-portal"])
        self.assertEqual((citations[0].level, citations[0].jurisdiction, citations[0].publisher), ("federal", "CH", "Fedlex"))
        self.assertEqual((per["zh-registration"].citations[0].level, per["zh-registration"].citations[0].jurisdiction),
                         ("cantonal", "CH-ZH"))
        # Both law facts rest on the act (the second cites the portal as well), so the concept states the basis once.
        self.assertEqual(per["kiosk-law"].basis, "Federal act: AIG, SR 142.20, Art. 12")
        self.assertTrue(all(f.basis is None for f in per["kiosk-law"].facts))
        self.assertEqual(per["zh-registration"].basis, "Cantonal authority guidance")
        self.assertTrue(all(f.basis is None for f in per["zh-registration"].facts))
        summary = self.service.dispatch("resolve", {"concept_ids": ["kiosk-summary"]})
        self.assertEqual(summary.results[0].basis, "Portal summary of federal rules")
        self.assertNotIn("more exact source", summary.guidance_for_caller)
        mixed = self.service.dispatch("resolve", {"concept_ids": ["kiosk-law", "kiosk-summary"]})
        self.assertNotIn("more exact source", mixed.guidance_for_caller)  # no single concept mixes the kinds

    def test_a_concept_mixing_a_summary_with_the_law_gets_the_guidance_sentence(self):
        release = release_with_basis()
        # The second law fact rests on the portal alone: the concept now mixes an act with a summary.
        target = next(f for f in release.facts if f.fact_id == "kiosk-law-2")
        target.evidence_ids = ["e-portal"]
        target.provenance = target.provenance.model_copy(update=dict(basis=make_basis("federal", "summary")))
        release.manifest.basis_kinds = {"guidance": 7, "summary": 2, "act": 1}
        release.manifest.content_sha256 = content_hash(release)
        assert_valid(release)
        result = ReleaseService(release).dispatch("resolve", {"concept_ids": ["kiosk-law"]})
        self.assertIn("the law's excerpt is the more exact source", result.guidance_for_caller)
        self.assertEqual([f.basis for f in result.results[0].facts],
                         ["Federal act: AIG, SR 142.20, Art. 12", "Portal summary of federal rules"])

    def test_coverage_and_evidence_carry_the_new_fields_and_older_releases_omit_them(self):
        root = self.service.dispatch("get_coverage", {})
        self.assertEqual(root.institution_levels, {"federal": 4, "cantonal": 1, "municipal": 1})
        self.assertEqual(root.basis_kinds, {"guidance": 7, "summary": 1, "act": 2})
        item = self.service.dispatch("get_evidence", {"evidence_ids": ["e-law", "e-c"]}).evidence
        self.assertEqual((item[0].basis, item[0].level, item[0].jurisdiction), ("Federal act: AIG, SR 142.20, Art. 12", "federal", "CH"))
        self.assertEqual((item[1].basis, item[1].level, item[1].jurisdiction), ("Municipal authority guidance", "municipal", "CH-ZH-261"))
        plain = ReleaseService(sample_release())
        self.assertIsNone(plain.dispatch("get_coverage", {}).institution_levels)
        served = plain.dispatch("resolve", {"concept_ids": ["deadline"], "context": {"population": "eu_efta"}}).results[0]
        self.assertIsNone(served.basis)
        self.assertTrue(all(f.basis is None for f in served.facts))
        self.assertEqual((served.citations[0].level, served.citations[0].jurisdiction), (None, None))
        dumped = served.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("basis", dumped)
        self.assertNotIn("level", dumped["citations"][0])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ReleaseService(sample_release())

    def call(self, name, arguments):
        result = self.service.dispatch(name, arguments)
        self.assertNotIsInstance(result, ToolError, getattr(result, "error", None))
        return result

    def per_concept(self, result):
        return {item.concept_id: item for item in result.results}

    def fake_ranker(self):
        ranker = Mock()
        ranker.index = SimpleNamespace(model="test-embed", model_digest="test-digest",
                                       release_id=self.service.release_id,
                                       release_content_sha256=self.service.release.manifest.content_sha256)
        return ranker

    def test_coverage_root_and_topic_pages(self):
        root = self.call("get_coverage", {})
        self.assertEqual(root.release_id, "test-v1")
        self.assertEqual([t.topic_id for t in root.topics], ["residence", "contacts"])
        self.assertEqual(root.topics[0].concept_count, 4)
        # The root names no jurisdiction per topic; its own list carries them and the topic page has them per concept.
        self.assertEqual(root.topics[1].model_dump(), {"topic_id": "contacts", "title": "Contacts", "concept_count": 1})
        self.assertEqual(sorted(root.jurisdictions), ["CH", "CH-BE", "CH-ZH", "CH-ZH-261"])
        # languages lists the languages of the cited pages; the schema and the tool description say that it is not
        # the list to search in, so a caller that sees French there still translates a French question.
        languages = tool_output_schema(CoverageRoot)["$defs"]["CoverageRoot"]["properties"]["languages"]["description"]
        self.assertIn("not the languages to search in (see query_languages)", languages)
        self.assertIn("the languages of the cited pages (not the search languages)", self.service.tool_description("get_coverage"))
        self.assertEqual(root.freshness.stale_from, date(2026, 11, 10))
        self.assertTrue(any("7 assistant-authored-unreviewed" in item for item in root.limitations))
        self.assertIn("Unreviewed.", root.limitations)  # the manifest's own list rides on the coverage pages
        # Search, resolve and get_evidence carry two lines: the review status the caller passes on, and a pointer.
        for name, args in (("search", {"query": "registration"}),
                           ("resolve", {"concept_ids": ["deadline"], "context": {"population": "eu_efta"}}),
                           ("get_evidence", {"evidence_ids": ["e-a"]})):
            result = self.call(name, args)
            self.assertNotIn("Unreviewed.", result.limitations, name)
            self.assertTrue(any("7 assistant-authored-unreviewed" in item for item in result.limitations), name)
            self.assertTrue(any("full list is in get_coverage" in item for item in result.limitations), name)
        self.assertEqual(self.call("search", {"query": "registration"}).limitations, self.service.result_limitations)
        self.assertGreater(self.call("search", {"query": "registration", "limit": 10}).matched_count, 0)
        self.assertIsInstance(self.service.dispatch("search", {"query": "registration", "limit": 11}), ToolError)
        topic = self.call("get_coverage", {"parent_id": "residence"})
        summaries = {c.concept_id: c for c in topic.concepts}
        self.assertEqual(summaries["deadline"].required_context, ["population"])
        # The listing is for picking a concept: aliases and allowed context values come with search and resolve.
        self.assertEqual(set(summaries["deadline"].model_dump()),
                         {"concept_id", "topic_id", "label", "description", "jurisdictions", "required_context"})
        self.assertIsInstance(self.service.dispatch("get_coverage", {"parent_id": "other"}), ToolError)
        error = self.service.dispatch("get_coverage", {"release_id": "nope"})
        self.assertEqual(error.error.code.value, "RELEASE_UNAVAILABLE")

    def test_hidden_coverage_tool(self):
        # The MCP server's default: get_coverage is neither listed nor served, and no text a caller reads points to it.
        service = ReleaseService(sample_release(), coverage_tool=False)
        self.assertEqual(service.tools(), ["search", "resolve", "get_evidence"])
        refused = service.dispatch("get_coverage", {})
        self.assertIsInstance(refused, ToolError)
        self.assertEqual(refused.error.code.value, "INVALID_ARGUMENT")
        self.assertNotIn("get_coverage", service.instructions)
        for name in service.tools():
            self.assertNotIn("get_coverage", service.tool_description(name), name)
            self.assertNotIn("get_coverage", json.dumps(service.tool_input_schema(name)), name)
        for query in ("registration", "zzzz"):
            result = service.dispatch("search", {"query": query})
            self.assertNotIn("get_coverage", result.model_dump_json(), query)
        empty = service.dispatch("search", {"query": "zzzz"})
        self.assertEqual(empty.match_strength, "none")
        self.assertEqual(empty.out_of_scope, ["Fees"])
        self.assertEqual(empty.out_of_scope_response, "Say so.")
        # A strong result carries none of the scope fields.
        strong = service.dispatch("search", {"query": "registration"})
        self.assertEqual(strong.match_strength, "strong")
        self.assertIsNone(strong.out_of_scope)
        unknown = service.dispatch("resolve", {"concept_ids": ["nope"]})
        self.assertNotIn("get_coverage", unknown.model_dump_json())

    def test_search_ranks_by_label_aliases_and_statements_with_stemming(self):
        self.assertEqual(tokens("Registration registers registrieren"), {"regist"})
        # Umlauts, their ASCII spelling and the English form are one token; stopwords are dropped before folding.
        self.assertEqual(tokens("Zürich Zuerich Zurich"), {"zurich"})
        self.assertEqual(tokens("Führerausweis Fuehrerausweis Ausländerausweis auslaenderausweis"), {"fuhrer", "auslan"})
        self.assertEqual(tokens("Kreisbüro Kreisbuero Erwerbstätigkeit Erwerbstaetigkeit"), {"kreisb", "erwerb"})
        result = self.call("search", {"query": "I'm a Czech citizen starting work in Zurich. By when latest should I register my stay with the municipal authority?"})
        ids = [hit.concept_id for hit in result.results]
        self.assertEqual(ids[0], "deadline")
        self.assertIn("zh-registration", ids)
        self.assertIn("label", result.results[0].matched_on)
        self.assertIn("statements", result.results[0].matched_on)
        self.assertEqual(result.results[0].context_schema["population"].enum, ["eu_efta", "third_country"])
        self.assertIn("Ranked candidates", result.guidance_for_caller)
        self.assertEqual(result.retrieval_mode, "lexical")
        self.assertGreaterEqual(result.matched_count, len(result.results))
        german = self.call("search", {"query": "Anmeldefrist Wohngemeinde", "limit": 2})
        self.assertEqual(german.results[0].concept_id, "deadline")
        # Rarity: "regist" is in three labels, "anmeld" in one alias; a rare token outweighs a common one, and a
        # token counts once per concept even when several fields carry it.
        self.assertGreater(self.service.token_weight["anmeld"], self.service.token_weight["regist"])
        registration_only = self.call("search", {"query": "registration"})
        alias_only = self.call("search", {"query": "Anmeldefrist"})
        self.assertGreater(alias_only.results[0].score, registration_only.results[0].score)
        self.assertEqual(alias_only.results[0].concept_id, "deadline")
        empty = self.call("search", {"query": "zzzz"})
        self.assertEqual(empty.results, [])
        self.assertIn("does not establish", empty.guidance_for_caller)
        self.assertEqual(empty.match_strength, "none")
        self.assertEqual(empty.scope_statement, "Residence permits and registration.")
        # Overlap with a statement alone is not a hit: "permit" appears in the deadline facts only.
        self.assertEqual([h.concept_id for h in self.call("search", {"query": "permit before entry"}).results], [])

    def test_match_strength_separates_questions_the_release_covers_from_incidental_matches(self):
        # A question whose distinctive words reach a concept's label, aliases or questions is strong: the hits are
        # the caller's candidates and no scope statement rides along.
        strong = self.call("search", {"query": "By when must I register my stay after arriving in Zurich?"})
        self.assertEqual(strong.match_strength, "strong")
        self.assertIsNone(strong.scope_statement)
        self.assertEqual(strong.match_signals.best_semantic_score, None)
        self.assertGreaterEqual(strong.match_signals.anchored_weight, 1.5)
        # A one-word query that is an alias: too little mass for the weight, but the share is 1.
        alias = self.call("search", {"query": "Anmeldefrist"})
        self.assertEqual(alias.match_strength, "strong")
        self.assertEqual(alias.match_signals.lexical_share, 1.0)
        self.assertLess(alias.match_signals.anchored_weight, 1.5)
        # An off-topic question that shares one incidental word with a label ("Zurich") still returns that hit, with
        # the weak verdict, the guidance to decline and the scope statement, so the caller declines in one call.
        weak = self.call("search", {"query": "What will the weather be like in Zurich tomorrow?"})
        self.assertEqual({h.concept_id for h in weak.results}, {"zh-registration", "city-arrival"})
        self.assertEqual(weak.match_strength, "weak")
        self.assertIn("Weak candidates only", weak.guidance_for_caller)
        self.assertEqual(weak.scope_statement, "Residence permits and registration.")
        self.assertLess(weak.match_signals.lexical_share, 0.5)
        self.assertLess(weak.match_signals.anchored_weight, 1.0)
        # Every signal travels with the result, and the verdict never drops a hit: the same hits as before.
        self.assertEqual(weak.results, self.call("search", {"query": "weather Zurich tomorrow"}).results)

    def test_query_languages_are_measured_on_the_release_and_named_to_the_caller(self):
        # The sample aliases occur in no excerpt, so only the statements' language is indexed.
        self.assertEqual([(q.code, q.rank) for q in self.call("get_coverage", {}).query_languages], [("en", 1)])
        # German aliases found verbatim in the excerpts of three of the five concepts: German is listed first.
        service = ReleaseService(with_aliases({"city-arrival": ["Personenmeldeamt", "Termin"],
                                               "zh-registration": ["Wohngemeinde"], "deadline": ["Ankunft"]}))
        languages = service.get_coverage(GetCoverageRequest()).query_languages
        self.assertEqual([(q.code, q.rank) for q in languages], [("de", 1), ("en", 2)])
        self.assertEqual(languages[0].indexed, "4 search terms copied from the cited pages, on 3 of 5 concepts")
        self.assertEqual(languages[1].indexed, "the concept labels, sample questions and statements")
        self.assertFalse(any("not advertised" in item for item in service.limitations))
        # The translation comes first, naming the other national languages: an untranslated French question read
        # strong on the wrong concepts. Then one search, in either language: callers sent an English question in
        # German as well.
        note = ("Translate first: when a question is in a language other than German or English (French, Italian or "
                "Romansh, for example), search with its key terms translated into German, and still answer in the "
                "user's language; an untranslated query can match the wrong concept. Search ONCE per question, in "
                "German or English, never in more than one of them: a second search in another language rarely finds "
                "other concepts. Write the search query in German or English: lexical search matches only these "
                "languages, and a question already in one of them is sent as asked.")
        self.assertIn(note, service.instructions)
        self.assertNotIn("preferred", service.tool_description("search").split("This server:")[1])
        self.assertIn("Residence permits and registration.", service.instructions)
        self.assertIn(note, service.tool_description("search"))
        self.assertNotIn(note, service.tool_description("resolve"))
        self.assertIn(note, service.tool_input_schema("search")["properties"]["query"]["description"])
        # The release's note replaces the generic one right after the first sentence of the search description, and
        # the rules come before the scope in the instructions: a client that keeps only the first 2,048 characters
        # (Claude Code) still shows them.
        description = service.tool_description("search")
        self.assertTrue(description.startswith(SEARCH_DESCRIPTION_LEAD + " This server: Translate first"))
        self.assertNotIn(SEARCH_DESCRIPTION_LANGUAGES, description)
        self.assertLess(service.instructions.index("Translate first"), service.instructions.index("Scope:"))
        self.assertLess(service.instructions.index("guidance_for_caller"), service.instructions.index("Scope:"))
        # Weak and empty results allow one translated search; a strong one does not invite another search.
        weak = service.search(SearchRequest(query="What will the weather be like in Zurich tomorrow?"))
        self.assertEqual(weak.match_strength, "weak")
        self.assertIn("other than German or English", weak.guidance_for_caller)
        self.assertIn("translated into German", service.search(SearchRequest(query="zzzz")).guidance_for_caller)
        strong = service.search(SearchRequest(query="Personenmeldeamt Termin"))
        self.assertEqual(strong.match_strength, "strong")
        self.assertNotIn("translated", strong.guidance_for_caller)

    def test_a_source_language_on_few_concepts_is_not_advertised(self):
        # German terms on one of five concepts: a German question would mostly read weak, so German is left out of
        # the query languages and named in the limitations; English, the statements' language, stays.
        service = ReleaseService(with_aliases({"city-arrival": ["Personenmeldeamt", "Termin"]}))
        self.assertEqual([q.code for q in service.query_languages], ["en"])
        # A single query language needs no "one search" reminder: there is no other language to repeat the search in.
        # German, not advertised, is named only as a language to translate from.
        self.assertIn("Translate first: when a question is in a language other than English (German, French, Italian "
                      "or Romansh, for example), search with its key terms translated into English", service.instructions)
        self.assertIn("Write the search query in English: lexical search matches only this language, and a question "
                      "already in it is sent as asked.", service.instructions)
        self.assertNotIn("Search ONCE", service.tool_description("search").split("This server:")[1])
        self.assertNotIn("in German", service.tool_description("search").split("This server:")[1])
        self.assertIn("Search is not advertised in German (2 search terms on 1 of 5 concepts)", service.limitations[-1])
        self.assertIn(service.limitations[-1], service.get_coverage(GetCoverageRequest()).limitations)
        # The statements' language is listed however few of its terms occur in an excerpt: the labels carry it.
        english = ReleaseService(with_aliases({"uk-work": ["work permit"]}))
        self.assertEqual([(q.code, q.indexed) for q in english.query_languages], [
            ("en", "1 search term copied from the cited pages, on 1 of 5 concepts; plus the concept labels, "
                   "sample questions and statements")])

    def test_match_strength_uses_the_raw_cosine_in_hybrid_mode(self):
        ranker = self.fake_ranker()
        # A far cosine vetoes a lexical coincidence: "Zurich" alone matched, and the nearest concept is far away.
        ranker.ranked.return_value = (0.31, [])
        self.service.semantic_search = ranker
        vetoed = self.call("search", {"query": "What will the weather be like in Zurich tomorrow?"})
        self.assertEqual(vetoed.retrieval_mode, "hybrid")
        self.assertEqual(vetoed.match_strength, "weak")
        self.assertEqual(vetoed.match_signals.best_semantic_score, 0.31)
        # The veto also overrides a strong lexical share: an alias hit whose concept is semantically far.
        ranker.ranked.return_value = (0.2, [])
        self.assertEqual(self.call("search", {"query": "Anmeldefrist"}).match_strength, "weak")
        # A strong cosine rescues a question in a language the index has no words for.
        ranker.ranked.return_value = (0.74, [(0.74, "deadline")])
        rescued = self.call("search", {"query": "我的到达登记期限"})
        self.assertEqual(rescued.match_strength, "strong")
        self.assertEqual(rescued.match_signals.lexical_share, 0.0)
        self.assertIsNone(rescued.scope_statement)
        # A partial cosine rescues only together with a partial lexical weight.
        ranker.ranked.return_value = (0.62, [(0.62, "deadline")])
        self.assertEqual(self.call("search", {"query": "我的到达登记期限"}).match_strength, "weak")
        self.assertEqual(self.call("search", {"query": "Anmeldefrist 14 days Wohngemeinde"}).match_strength, "strong")
        # No candidate on either side is none, whatever the cosine.
        ranker.ranked.return_value = (0.1, [])
        self.assertEqual(self.call("search", {"query": "zzzz"}).match_strength, "none")

    def test_hybrid_search_adds_nonlexical_candidates_and_preserves_resolution(self):
        before = self.call("resolve", {"concept_ids": ["deadline"], "context": {"population": "eu_efta"}})
        ranker = self.fake_ranker()
        ranker.ranked.return_value = (0.9, [(0.9, "deadline"), (0.8, "zh-registration")])
        self.service.semantic_search = ranker
        result = self.call("search", {"query": "我的到达登记期限", "limit": 1})
        self.assertEqual(result.retrieval_mode, "hybrid")
        self.assertEqual(result.matched_count, 2)
        self.assertTrue(result.truncated)
        self.assertEqual(result.results[0].concept_id, "deadline")
        self.assertEqual(result.results[0].matched_on, ["semantic"])
        self.assertEqual(result.ranking_model_digest, "test-digest")
        self.assertEqual(result.match_strength, "strong")
        self.assertEqual(result.match_signals.best_semantic_score, 0.9)
        self.assertEqual(ranker.ranked.call_count, 1)
        after = self.call("resolve", {"concept_ids": ["deadline"], "context": {"population": "eu_efta"}})
        self.assertEqual(before, after)
        self.assertEqual(ranker.ranked.call_count, 1)

    def test_semantic_failure_reports_fallback_without_changing_lexical_hits(self):
        baseline = self.call("search", {"query": "registration"})
        ranker = self.fake_ranker()
        ranker.ranked.side_effect = SemanticError("embedding request timed out")
        self.service.semantic_search = ranker
        result = self.call("search", {"query": "registration"})
        self.assertEqual(result.results, baseline.results)
        self.assertEqual(result.retrieval_mode, "lexical-fallback")
        self.assertIn("timed out", result.fallback_reason)
        # After a fallback the verdict rests on the lexical signals alone, as without semantic search.
        self.assertEqual(result.match_strength, baseline.match_strength)
        self.assertIsNone(result.match_signals.best_semantic_score)
        ranker.index.release_id = "different-release"
        ranker.ranked.reset_mock()
        result = self.call("search", {"query": "registration"})
        self.assertEqual(result.results, baseline.results)
        self.assertEqual(result.retrieval_mode, "lexical-fallback")
        ranker.ranked.assert_not_called()
        ranker.index.release_id = self.service.release_id
        ranker.ranked.side_effect = None
        ranker.ranked.return_value = (0.99, [(0.99, "invented-concept")])
        result = self.call("search", {"query": "registration"})
        self.assertEqual(result.results, baseline.results)
        self.assertEqual(result.retrieval_mode, "lexical-fallback")
        self.assertIsNone(result.match_signals.best_semantic_score)  # a cosine read before the failure is not served

    def test_rank_fusion_and_truncation_are_deterministic(self):
        ranker = self.fake_ranker()
        ranker.ranked.return_value = (0.95, [(0.95, "zh-registration"), (0.85, "deadline")])
        self.service.semantic_search = ranker
        first = self.call("search", {"query": "registration", "limit": 1})
        full = self.call("search", {"query": "registration", "limit": 10})
        self.assertEqual(first.results, full.results[:1])
        self.assertEqual(first.matched_count, len(full.results))
        self.assertTrue(first.truncated)
        self.assertFalse(full.truncated)
        self.assertTrue(all("semantic" in hit.matched_on for hit in full.results[:2]))

    def test_invalid_index_configuration_has_visible_fallback(self):
        self.service.semantic_error = "index belongs to another release"
        result = self.call("search", {"query": "registration"})
        self.assertEqual(result.retrieval_mode, "lexical-fallback")
        self.assertEqual(result.fallback_reason, self.service.semantic_error)

    def test_resolve_statuses_and_gaps(self):
        needs = self.call("resolve", {"concept_ids": ["deadline", "zh-registration"], "jurisdiction": {"canton_code": "CH-ZH"}})
        self.assertEqual(needs.status, Status.NEEDS_CONTEXT)
        self.assertEqual(self.per_concept(needs)["deadline"].missing_context[0].field, "population")
        self.assertEqual(self.per_concept(needs)["zh-registration"].status, Status.SUPPORTED)
        self.assertTrue(needs.guidance_for_caller)

        ok = self.call("resolve", {"concept_ids": ["deadline", "zh-registration"], "jurisdiction": {"canton_code": "CH-ZH"},
                                   "as_of": "2026-09-12", "context": {"population": "eu_efta"}})
        per = self.per_concept(ok)
        self.assertEqual(ok.status, Status.SUPPORTED)
        self.assertEqual([f.fact_id for f in per["deadline"].facts], ["deadline-1"])
        self.assertEqual(per["deadline"].answering_jurisdiction, "CH (federal)")
        self.assertEqual(per["zh-registration"].answering_jurisdiction, "CH-ZH (canton)")
        self.assertEqual([c.evidence_ids for c in per["deadline"].citations], [["e-a"]])
        self.assertEqual([g.dimension for g in per["zh-registration"].gaps], ["more_specific_jurisdiction_available"])
        self.assertEqual(per["zh-registration"].gaps[0].published_values, ["CH-ZH-261"])
        self.assertEqual([f.name for f in ok.required_user_facts], ["arrival_date"])
        self.assertEqual(ok.decision_rule.steps[1], "Name the earlier.")

        city = self.call("resolve", {"concept_ids": ["city-arrival", "zh-registration"],
                                     "jurisdiction": {"canton_code": "CH-ZH", "municipality_id": "CH-ZH-261"}})
        per = self.per_concept(city)
        self.assertEqual(per["city-arrival"].status, Status.SUPPORTED)
        self.assertEqual(per["zh-registration"].gaps, [])

        bern = self.call("resolve", {"concept_ids": ["deadline", "zh-registration", "city-arrival"],
                                     "jurisdiction": {"canton_code": "CH-BE"}, "context": {"population": "eu_efta"}})
        per = self.per_concept(bern)
        self.assertEqual(bern.status, Status.SUPPORTED)
        self.assertEqual(per["zh-registration"].status, Status.OUT_OF_COVERAGE)
        self.assertEqual(per["zh-registration"].gaps[0].dimension, "jurisdiction_not_covered")
        self.assertEqual(per["zh-registration"].gaps[0].published_values, ["CH-ZH"])
        self.assertEqual(per["city-arrival"].status, Status.OUT_OF_COVERAGE)
        # The federal facts are served for Bern with the caveat that the topic's narrower levels are Zurich's.
        self.assertEqual([(g.dimension, g.published_values) for g in per["deadline"].gaps],
                         [("more_specific_jurisdiction_not_published", ["CH-ZH", "CH-ZH-261"])])
        self.assertIn("nothing narrower than CH (federal)", per["deadline"].gaps[0].message)
        self.assertIn("carry over no rule, office, fee or deadline", bern.guidance_for_caller)

        federal_only = self.call("resolve", {"concept_ids": ["zh-registration"], "context": {}})
        gap = self.per_concept(federal_only)["zh-registration"].gaps[0]
        self.assertIn("below the requested CH", gap.message)

        third = self.call("resolve", {"concept_ids": ["deadline"], "context": {"population": "third_country"}})
        self.assertEqual([f.fact_id for f in self.per_concept(third)["deadline"].facts], ["deadline-2"])
        no_canton = self.call("resolve", {"concept_ids": ["contact"], "context": {"population": "eu_efta"}})
        gap = self.per_concept(no_canton)["contact"].gaps[0]
        self.assertEqual((gap.dimension, gap.published_values), ("jurisdiction_not_covered", ["CH-BE", "CH-ZH"]))
        self.assertIn("below the requested CH", gap.message)
        typo = self.call("resolve", {"concept_ids": ["deadline"], "context": {"population": "eu_efta", "purpos": "x"}})
        self.assertEqual(self.per_concept(typo)["deadline"].gaps[0].dimension, "context_not_covered")

        contact = self.call("resolve", {"concept_ids": ["contact"], "jurisdiction": {"canton_code": "CH-BE"}})
        self.assertEqual([f.fact_id for f in self.per_concept(contact)["contact"].facts], ["contact-be"])
        self.assertTrue(ok.guidance_for_caller.startswith("These facts and citations are everything this release holds"))

        short = self.call("resolve", {"concept_ids": ["zh-registration", "city-arrival"],
                                      "jurisdiction": {"canton_code": "zh", "municipality_id": "261"}})
        self.assertEqual(short.executed_scope.model_dump(exclude_none=True), {"country_code": "CH", "canton_code": "CH-ZH", "municipality_id": "CH-ZH-261"})
        self.assertEqual([r.status for r in short.results], [Status.SUPPORTED, Status.SUPPORTED])
        only_city = self.call("resolve", {"concept_ids": ["zh-registration"], "jurisdiction": {"municipality_id": "CH-ZH-261"}})
        self.assertEqual(only_city.executed_scope.canton_code, "CH-ZH")
        self.assertEqual(only_city.results[0].status, Status.SUPPORTED)

        early = self.call("resolve", {"concept_ids": ["uk-work"], "as_of": "2020-12-31"})
        gap = self.per_concept(early)["uk-work"].gaps[0]
        self.assertEqual((gap.dimension, gap.published_values), ("date_outside_coverage", ["2021-01-01..unbounded"]))
        stale = self.call("resolve", {"concept_ids": ["deadline"], "as_of": "2027-01-15", "context": {"population": "eu_efta"}})
        self.assertEqual(stale.status, Status.STALE)
        self.assertTrue(stale.results[0].facts)
        self.assertTrue(any("older than" in item for item in stale.limitations))

        abroad = self.call("resolve", {"concept_ids": ["deadline"], "jurisdiction": {"country_code": "DE"}, "context": {"population": "eu_efta"}})
        self.assertEqual(abroad.status, Status.OUT_OF_COVERAGE)
        unknown = self.call("resolve", {"concept_ids": ["nope"]})
        self.assertEqual(self.per_concept(unknown)["nope"].gaps[0].dimension, "concept_not_published")
        self.assertIsInstance(self.service.dispatch("resolve", {"concept_ids": ["deadline", "deadline"]}), ToolError)
        self.assertIsInstance(self.service.dispatch("resolve", {"concept_ids": ["deadline"], "jurisdiction": {"canton_code": "CH-BE", "municipality_id": "CH-ZH-261"}}), ToolError)
        self.assertIsInstance(self.service.dispatch("resolve", {"concept_ids": ["deadline"], "surprise": 1}), ToolError)

    def test_a_place_served_less_deeply_than_another_is_told_so(self):
        caveat = "more_specific_jurisdiction_not_published"
        eu = {"population": "eu_efta"}

        def gaps_of(jurisdiction, concept_ids=("deadline", "zh-registration", "contact"), **extra):
            result = self.call("resolve", {"concept_ids": list(concept_ids), "jurisdiction": jurisdiction, "context": eu, **extra})
            return result, {cid: [(g.dimension, g.published_values) for g in item.gaps if item.facts]
                            for cid, item in self.per_concept(result).items()}

        # Another Zurich municipality gets the federal and the cantonal facts; the municipal level is the city's only.
        winterthur, gaps = gaps_of({"canton_code": "CH-ZH", "municipality_id": "CH-ZH-230"})
        self.assertEqual(gaps, {"deadline": [(caveat, ["CH-ZH-261"])], "zh-registration": [(caveat, ["CH-ZH-261"])], "contact": []})
        message = self.per_concept(winterthur)["zh-registration"].gaps[0].message
        self.assertIn("For CH-ZH-230 (municipality) this topic publishes nothing narrower than CH-ZH (canton)", message)
        self.assertIn("do not apply to CH-ZH-230", message)
        # A municipality of another canton: the topic's cantonal and municipal levels are both Zurich's. The contacts
        # topic serves Bern as deeply as every other canton, so its answer carries no caveat.
        bern_city, gaps = gaps_of({"municipality_id": "CH-BE-351"})
        self.assertEqual(gaps, {"deadline": [(caveat, ["CH-ZH", "CH-ZH-261"])], "zh-registration": [], "contact": []})
        self.assertIn("carry over no rule, office, fee or deadline", bern_city.guidance_for_caller)
        # Where the release publishes the user's own level, or something below the requested place, nothing is said:
        # the city is served in full, and a canton-only or country-only request is pointed at the narrower code.
        for jurisdiction in ({"canton_code": "CH-ZH", "municipality_id": "CH-ZH-261"}, {"canton_code": "CH-ZH"}, {}):
            with self.subTest(jurisdiction=jurisdiction):
                result, gaps = gaps_of(jurisdiction)
                self.assertNotIn(caveat, [dimension for found in gaps.values() for dimension, _ in found])
                self.assertNotIn("carry over no rule", result.guidance_for_caller)
        # A stale answer keeps the caveat.
        stale, gaps = gaps_of({"canton_code": "CH-BE"}, concept_ids=("deadline",), as_of="2027-01-15")
        self.assertEqual((stale.status, gaps["deadline"]), (Status.STALE, [(caveat, ["CH-ZH", "CH-ZH-261"])]))
        self.assertIn("carry over no rule", stale.guidance_for_caller)
        # The helper names the deepest level that applies and the places served more deeply.
        self.assertEqual(self.service.narrower_published_elsewhere("residence", "CH-BE"), ("CH", ["CH-ZH", "CH-ZH-261"]))
        self.assertEqual(self.service.narrower_published_elsewhere("residence", "CH-ZH-230"), ("CH-ZH", ["CH-ZH-261"]))
        for topic_id, requested in (("residence", "CH"), ("residence", "CH-ZH"), ("residence", "CH-ZH-261"),
                                    ("contacts", "CH-BE"), ("contacts", "CH-BE-351"), ("contacts", "CH-GE"), ("unknown", "CH-BE")):
            with self.subTest(topic=topic_id, requested=requested):
                self.assertIsNone(self.service.narrower_published_elsewhere(topic_id, requested))

    def test_guidance_follows_the_status_and_names_what_to_tell_the_user(self):
        arguments = {"concept_ids": ["deadline"], "context": {"population": "eu_efta"}}
        ok = self.call("resolve", {**arguments, "as_of": "2026-09-12"})
        self.assertIn("cite only the returned URLs", ok.guidance_for_caller)
        # The deadline fact is an unreviewed English statement over a German excerpt.
        self.assertIn("1 of the 1 statements have not been reviewed by a person", ok.guidance_for_caller)
        self.assertIn("English summaries of German pages, not official translations", ok.guidance_for_caller)
        self.assertIn("in the language of the user's question", ok.guidance_for_caller)
        stale = self.call("resolve", {**arguments, "as_of": "2027-01-15"})
        self.assertIn("published on 2026-09-11, not what holds on 2027-01-15", stale.guidance_for_caller)
        self.assertIn("Do not resolve again with an earlier as_of", stale.guidance_for_caller)
        self.assertNotIn("Answer now", stale.guidance_for_caller)
        uk = self.call("resolve", {"concept_ids": ["uk-work"], "as_of": "2026-09-12"})
        self.assertNotIn("not official translations", uk.guidance_for_caller)
        outside = self.call("resolve", {"concept_ids": ["zh-registration"], "jurisdiction": {"canton_code": "CH-BE"}})
        self.assertEqual(outside.status, Status.OUT_OF_COVERAGE)
        self.assertIn("do not answer it from general knowledge", outside.guidance_for_caller)
        self.assertIn("do not resolve again without reviewed_only unless the user asks", outside.guidance_for_caller)

    def test_not_served_is_returned_with_the_concept_and_omitted_when_empty(self):
        release = sample_release()
        release.concepts[1].not_served = ["fees", "appointment availability"]
        release.manifest.content_sha256 = content_hash(release)
        service = ReleaseService(release)
        result = service.dispatch("resolve", {"concept_ids": ["zh-registration", "uk-work"], "jurisdiction": {"canton_code": "CH-ZH"}})
        per = {item.concept_id: item for item in result.results}
        self.assertEqual(per["zh-registration"].not_served, ["fees", "appointment availability"])
        dumped = result.model_dump(mode="json", exclude_none=True)
        self.assertEqual([("not_served" in item) for item in dumped["results"]], [True, False])
        # An empty not_served leaves the release dump, and so the content hash of older releases, unchanged.
        plain = sample_release()
        self.assertNotIn("not_served", plain.concepts[0].model_dump(mode="json"))
        self.assertEqual(content_hash(plain), plain.manifest.content_sha256)

    def test_a_token_in_most_concepts_neither_anchors_nor_scores(self):
        release = sample_release()
        extra = []
        for n in range(4):
            concept_id = f"town-{n}"
            release.facts.append(fact(f"{concept_id}-1", concept_id, "Testtown statement.", "CH", ["e-a"]))
            extra.append(Concept(concept_id=concept_id, topic_id="residence", label=f"Testtown service {n}",
                                 description="Local.", aliases=[f"Kiosk{n}"], jurisdictions=["CH"], fact_ids=[f"{concept_id}-1"]))
        release.concepts.extend(extra)
        for concept in release.concepts[:5]:
            concept.aliases = [*concept.aliases, "Testtown"]
        release.manifest.content_sha256 = content_hash(release)
        service = ReleaseService(release)
        self.assertIn("testto", service.common_tokens)
        self.assertEqual(service.lexical_hits("What does Testtown offer?"), [])
        self.assertEqual([hit[1] for hit in service.lexical_hits("Testtown Kiosk2")], ["town-2"])

    def test_a_one_character_token_is_kept_unless_it_is_an_elision(self):
        # The letter is the whole question: without it "What is an L permit?" comes down to "permit".
        self.assertEqual(tokens("What is an L permit?"), {"l", "permit"})
        self.assertEqual(tokens("Wie bekomme ich eine B-Bewilligung?"), {"b", "bekomm", "bewill"})
        self.assertEqual(tokens("What is a D visa?"), {"d", "visa"})
        self.assertEqual(tokens("Tarif B"), {"b", "tarif"})
        # A character joined to a word by an apostrophe is grammar, in English and in French.
        self.assertEqual(tokens("the foreign national's permit"), {"foreig", "nation", "permit"})
        self.assertEqual(tokens("l’autorisation d'etablissement"), {"autori", "etabli"})
        # The articles stay stopwords, and so do the one-letter function words the length filter used to hide.
        self.assertEqual(tokens("I need a permit"), {"permit"})
        self.assertEqual(tokens("Wo isch s Amt z Winterthur? E-Mail?"), {"isch", "amt", "winter", "mail"})

    def test_a_one_character_token_no_anchor_names_does_not_weaken_the_match(self):
        # An unnamed letter is grammar, not the question's most distinctive unmatched word ("d Stadt", "z.B.").
        self.assertEqual(self.service.anchored_match("Anmeldefrist d Stadt"), self.service.anchored_match("Anmeldefrist Stadt"))
        # A letter an alias names is a word of the question like any other.
        release = sample_release()
        release.concepts[0].aliases = [*release.concepts[0].aliases, "Ausweis Q"]
        release.manifest.content_sha256 = content_hash(release)
        named = ReleaseService(release)
        self.assertGreater(named.anchored_match("Ausweis Q")[1], named.anchored_match("Ausweis")[1])

    def test_loose_hits_far_below_the_best_are_dropped(self):
        hits = self.service.lexical_hits("Anmeldefrist 14 days register permit")
        self.assertEqual(hits[0][1], "deadline")
        self.assertTrue(all(score >= hits[0][0] * 0.2 for score, _, _ in hits))

    def test_every_served_concept_names_its_review_status_once(self):
        result = self.call("resolve", {"concept_ids": ["deadline", "zh-registration"], "jurisdiction": {"canton_code": "CH-ZH"},
                                       "context": {"population": "eu_efta"}})
        self.assertTrue(all(item.facts for item in result.results))
        for item in result.results:
            self.assertEqual(item.review_status, "assistant-authored-unreviewed")
            self.assertIsNone(item.reviewed_on)
            self.assertIsNone(item.reviewed_by)
            self.assertTrue(all(f.review_status is None and f.reviewed_on is None and f.reviewed_by is None for f in item.facts))

    def test_a_page_is_cited_once_and_differing_review_statuses_stay_on_the_facts(self):
        release = release_with_one_reviewed_fact()
        release.evidence.append(evidence("e-b2", "doc-b", "Mit Pass und Mietvertrag anmelden."))
        release.facts.append(fact("zh-2", "zh-registration", "Bring a passport and the rental contract.", "CH-ZH", ["e-b2", "e-b"]))
        next(c for c in release.concepts if c.concept_id == "zh-registration").fact_ids.append("zh-2")
        release.manifest.provenance_kinds = {"curated-statement": 8}
        release.manifest.review_statuses = {"assistant-authored-unreviewed": 7, "human-reviewed": 1}
        release.manifest.content_sha256 = content_hash(release)
        assert_valid(release)
        result = ReleaseService(release).dispatch("resolve", {"concept_ids": ["zh-registration"],
                                                              "jurisdiction": {"canton_code": "CH-ZH"}})
        item = result.results[0]
        self.assertEqual([(c.url, c.evidence_ids) for c in item.citations], [("https://example.gov/doc-b", ["e-b", "e-b2"])])
        self.assertIsNone(item.review_status)
        self.assertEqual([(f.fact_id, f.review_status, f.reviewed_by) for f in item.facts],
                         [("zh-1", "human-reviewed", "Expert"), ("zh-2", "assistant-authored-unreviewed", None)])

    def test_reviewed_only_withholds_unreviewed_facts_and_says_so(self):
        result = self.call("resolve", {"concept_ids": ["zh-registration"], "jurisdiction": {"canton_code": "CH-ZH"},
                                       "reviewed_only": True})
        item = self.per_concept(result)["zh-registration"]
        self.assertEqual(item.status, Status.OUT_OF_COVERAGE)
        self.assertEqual(item.facts, [])
        self.assertEqual([g.dimension for g in item.gaps], ["review_status_not_met"])
        self.assertEqual(item.gaps[0].published_values, ["assistant-authored-unreviewed"])
        self.assertTrue(any("human-reviewed facts only" in line for line in result.limitations))

    def test_reviewed_only_serves_a_confirmed_fact_with_its_reviewer(self):
        service = ReleaseService(release_with_one_reviewed_fact())
        result = service.dispatch("resolve", {"concept_ids": ["zh-registration", "city-arrival"],
                                              "jurisdiction": {"canton_code": "CH-ZH", "municipality_id": "CH-ZH-261"},
                                              "reviewed_only": True})
        per = {item.concept_id: item for item in result.results}
        self.assertEqual(per["zh-registration"].status, Status.SUPPORTED)
        self.assertEqual([f.fact_id for f in per["zh-registration"].facts], ["zh-1"])
        self.assertEqual(per["zh-registration"].review_status, "human-reviewed")
        self.assertEqual(per["zh-registration"].reviewed_by, "Expert")
        self.assertEqual(per["zh-registration"].reviewed_on, date(2026, 9, 24))
        self.assertEqual(per["city-arrival"].gaps[0].dimension, "review_status_not_met")
        self.assertTrue(any("1 unreviewed fact(s) were withheld" in line for line in result.limitations))

        # Nothing held back, nothing said: a reviewed_only request on reviewed facts reads like any other.
        confirmed_only = service.dispatch("resolve", {"concept_ids": ["zh-registration"],
                                                      "jurisdiction": {"canton_code": "CH-ZH"}, "reviewed_only": True})
        self.assertEqual(confirmed_only.results[0].status, Status.SUPPORTED)
        self.assertFalse(any("withheld" in line for line in confirmed_only.limitations))

        unfiltered = service.dispatch("resolve", {"concept_ids": ["city-arrival"],
                                                  "jurisdiction": {"municipality_id": "CH-ZH-261"}})
        self.assertEqual(unfiltered.results[0].review_status, "assistant-authored-unreviewed")

    def test_get_evidence(self):
        result = self.call("get_evidence", {"evidence_ids": ["e-a", "e-d"]})
        self.assertEqual([e.evidence_id for e in result.evidence], ["e-a", "e-d"])
        self.assertTrue(result.evidence[0].original_excerpt.startswith("Innert 14 Tagen"))
        by_fact = self.call("get_evidence", {"evidence_ids": ["deadline-1", "e-a", "zh-1"]})
        self.assertEqual([e.evidence_id for e in by_fact.evidence], ["e-a", "e-b"])
        error = self.service.dispatch("get_evidence", {"evidence_ids": ["nope"]})
        self.assertEqual(error.error.code.value, "INVALID_ARGUMENT")
        self.assertIsInstance(self.service.dispatch("get_evidence", {"evidence_ids": []}), ToolError)
        self.assertIsInstance(self.service.dispatch("unknown_tool", {}), ToolError)

    def test_the_context_vocabulary_reaches_the_caller_before_its_first_call(self):
        # Knowing the fields and their published values, a caller maps what the user said ("a Czech citizen") to a
        # value on its first resolve instead of learning the field from a NEEDS_CONTEXT round trip. It is sent in the
        # instructions and on the resolve schema because a client may drop either.
        entry = "- population (eu_efta, third_country): Citizenship group."
        self.assertIn("Context fields of this release", self.service.instructions)
        self.assertIn(entry, self.service.instructions)
        context = self.service.tool_input_schema("resolve")["properties"]["context"]
        self.assertIn("Values for the concept's context_schema fields.", context["description"])
        self.assertIn(entry, context["description"])
        # Only resolve takes a context; the search schema is unchanged.
        self.assertNotIn("Context fields", str(self.service.tool_input_schema("search")))

    def test_a_release_without_context_fields_sends_no_vocabulary(self):
        release = sample_release()
        for concept in release.concepts:
            concept.required_context, concept.context_schema = [], {}
        service = ReleaseService(release)
        self.assertEqual(service.context_note, "")
        self.assertNotIn("Context fields", service.instructions)
        self.assertFalse(service.instructions.endswith("\n"))

    def test_a_vocabulary_over_the_budget_falls_back_to_the_field_names(self):
        # The list is a session-once cost, so it is sent in full; a pack with far more fields than a question can
        # plausibly need sends the names, and each search hit still carries its own concept's values.
        release = sample_release()
        release.concepts[0].context_schema |= {f"field_{n:03}": ContextFieldSpec(enum=["yes", "no"],
                                                                                 description="Long description. " * 5)
                                               for n in range(60)}
        service = ReleaseService(release)
        self.assertIn("This release has 61 context fields, too many to list here", service.context_note)
        self.assertIn("field_000, field_001", service.context_note)
        self.assertNotIn("Long description", service.context_note)


class JurisdictionNameTests(unittest.TestCase):
    """A jurisdiction in names, read with the release's place register."""

    def setUp(self):
        self.service = ReleaseService(release_with_places())

    def resolve(self, jurisdiction, concept_ids=("zh-registration",)):
        return self.service.dispatch("resolve", {"concept_ids": list(concept_ids), "jurisdiction": jurisdiction})

    def scope(self, jurisdiction) -> dict:
        result = self.resolve(jurisdiction)
        self.assertNotIsInstance(result, ToolError, getattr(result, "error", None))
        return result.executed_scope.model_dump(exclude_none=True)

    def test_names_become_codes_and_are_echoed(self):
        wallisellen = {"country_code": "CH", "canton_code": "CH-ZH", "municipality_id": "CH-ZH-69",
                       "country": "Switzerland", "canton": "Zürich", "city": "Wallisellen"}
        self.assertEqual(self.scope({"country": "Switzerland", "canton": "Zurich", "city": "Wallisellen"}), wallisellen)
        # The country is the release's own when omitted, and a city alone supplies its canton.
        self.assertEqual(self.scope({"city": "Wallisellen"}), wallisellen)
        zurich = {**wallisellen, "municipality_id": "CH-ZH-261", "city": "Zürich"}
        for city in ("Zürich", "Zurich", "zuerich", "ZURIGO", "Stadt Zürich", "City of Zurich", "Zürich ZH", "ZH-261", "ch-zh-261"):
            with self.subTest(city=city):
                self.assertEqual(self.scope({"city": city}), zurich)
        # A bare number is a municipality only next to its canton: alone it could as well be a postcode.
        self.assertEqual(self.scope({"canton": "ZH", "city": "0261"}), zurich)
        self.assertEqual(self.scope({"city": "261"}), {"country_code": "CH", "country": "Switzerland", "not_recognised": {"city": "261"}})
        for canton in ("Zürich", "Zurich", "Kanton Zürich", "Canton of Zurich", "ZH", "zh", "CH-ZH"):
            with self.subTest(canton=canton):
                self.assertEqual(self.scope({"canton": canton}), {"country_code": "CH", "canton_code": "CH-ZH",
                                                                  "country": "Switzerland", "canton": "Zürich"})
        for country in ("Schweiz", "suisse", "ch", "CH"):
            with self.subTest(country=country):
                self.assertEqual(self.scope({"country": country}), {"country_code": "CH", "country": "Switzerland"})
        # Each part of a name in two languages is the place, and so is the register's qualified spelling.
        self.assertEqual(self.scope({"city": "Bienne"})["municipality_id"], "CH-BE-371")
        self.assertEqual(self.scope({"canton": "Berne"})["canton_code"], "CH-BE")
        self.assertEqual(self.scope({"canton": "St Gallen", "city": "Buchs"})["municipality_id"], "CH-SG-3271")
        self.assertEqual(self.scope({"city": "Buchs SG"})["municipality_id"], "CH-SG-3271")
        self.assertEqual(self.scope({"city": "Buchs (ZH)"})["municipality_id"], "CH-ZH-83")
        # The field names of schema v3 and the level names of other countries are accepted.
        self.assertEqual(self.scope({"canton_code": "CH-ZH", "municipality_id": "CH-ZH-69"}), wallisellen)
        self.assertEqual(self.scope({"state": "Zurich", "municipality": "Wallisellen"}), wallisellen)

    def test_a_named_place_reaches_its_facts(self):
        city = self.resolve({"city": "Zurich"}, ("zh-registration", "city-arrival"))
        self.assertEqual([r.status for r in city.results], [Status.SUPPORTED, Status.SUPPORTED])
        self.assertEqual([r.answering_jurisdiction for r in city.results],
                         ["CH-ZH (canton of Zürich)", "CH-ZH-261 (municipality of Zürich)"])
        # Another municipality of the canton gets the cantonal fact and is told, by name, whose the municipal level is.
        winterthur = self.resolve({"city": "Winterthur"}, ("zh-registration", "city-arrival"))
        self.assertEqual([r.status for r in winterthur.results], [Status.SUPPORTED, Status.OUT_OF_COVERAGE])
        self.assertIn("For CH-ZH-230 (municipality of Winterthur) this topic publishes nothing narrower than CH-ZH (canton "
                      "of Zürich); its narrower facts are published for CH-ZH-261 (municipality of Zürich) only",
                      winterthur.results[0].gaps[0].message)
        self.assertIn("published for CH-ZH-261 (municipality of Zürich), not for CH-ZH-230 (municipality of Winterthur)",
                      winterthur.results[1].gaps[0].message)
        self.assertEqual(winterthur.results[1].gaps[0].published_values, ["CH-ZH-261"])

    def test_a_place_the_register_does_not_hold_runs_for_the_broader_one(self):
        quarter = self.resolve({"canton": "Zurich", "city": "Oerlikon"}, ("zh-registration", "city-arrival"))
        self.assertEqual(quarter.executed_scope.model_dump(exclude_none=True),
                         {"country_code": "CH", "canton_code": "CH-ZH", "country": "Switzerland", "canton": "Zürich",
                          "not_recognised": {"city": "Oerlikon"}})
        self.assertEqual([r.status for r in quarter.results], [Status.SUPPORTED, Status.OUT_OF_COVERAGE])
        self.assertIn("does not hold the city 'Oerlikon', so this result is for CH-ZH (canton of Zürich)", quarter.guidance_for_caller)
        self.assertIn("not a district, a quarter or a postcode", quarter.guidance_for_caller)
        # Without a canton the request falls back to the country, and an out-of-coverage result says so as well.
        alone = self.resolve({"city": "8050"}, ("city-arrival",))
        self.assertEqual(alone.executed_scope.model_dump(exclude_none=True),
                         {"country_code": "CH", "country": "Switzerland", "not_recognised": {"city": "8050"}})
        self.assertEqual(alone.status, Status.OUT_OF_COVERAGE)
        self.assertIn("so this result is for CH (federal)", alone.guidance_for_caller)
        both = self.resolve({"canton": "Bavaria", "city": "Munich"})
        self.assertEqual(both.executed_scope.not_recognised, {"canton": "Bavaria", "city": "Munich"})
        self.assertIn("the canton 'Bavaria' and the city 'Munich'", both.guidance_for_caller)
        # A code the register does not hold is not recognised either; every part recognised says nothing.
        self.assertEqual(self.resolve({"city": "CH-ZH-999"}).executed_scope.not_recognised, {"city": "CH-ZH-999"})
        self.assertNotIn("place register", self.resolve({"city": "Zurich"}).guidance_for_caller)

    def test_another_country_is_not_served(self):
        for country, echoed in (("Germany", {"country": "Germany"}), ("de", {"country_code": "DE"})):
            with self.subTest(country=country):
                abroad = self.resolve({"country": country, "city": "Zurich"})
                self.assertEqual(abroad.status, Status.OUT_OF_COVERAGE)
                self.assertEqual(abroad.executed_scope.model_dump(exclude_none=True), echoed)
                gap = abroad.results[0].gaps[0]
                self.assertEqual((gap.dimension, gap.message, gap.published_values),
                                 ("jurisdiction_not_covered", "Only Switzerland (CH) is served.", ["CH"]))

    def test_an_ambiguous_or_inconsistent_place_is_an_error(self):
        shared = self.resolve({"city": "Buchs"})
        self.assertIsInstance(shared, ToolError)
        issue = shared.error.issues[0]
        self.assertEqual(issue.path, "jurisdiction.city")
        self.assertIn("Buchs (ZH) (CH-ZH-83) in Zürich (CH-ZH)", issue.message)
        self.assertIn("Buchs (SG) (CH-SG-3271) in St. Gallen (CH-SG)", issue.message)
        elsewhere = self.resolve({"canton": "Bern", "city": "Zurich"})
        self.assertIsInstance(elsewhere, ToolError)
        self.assertIn("Zürich (CH-ZH-261) lies in Zürich (CH-ZH), not in Bern / Berne (CH-BE)", elsewhere.error.issues[0].message)
        self.assertIsInstance(self.resolve({"canton": "BE", "city": "CH-ZH-261"}), ToolError)
        self.assertIsInstance(self.resolve({"canton": "Zurich", "kanton": "ZH"}), ToolError)

    def test_a_flattened_place_is_folded_into_the_jurisdiction(self):
        """A small model that sends the place next to the other arguments is served, not sent round again."""
        wallisellen = self.scope({"city": "Wallisellen"})
        flat = self.service.dispatch("resolve", {"concept_ids": ["zh-registration"], "city": "Wallisellen"})
        self.assertEqual(flat.executed_scope.model_dump(exclude_none=True), wallisellen)
        self.assertEqual(flat.results[0].status, Status.SUPPORTED)
        # Every name the field accepts is folded, next to a jurisdiction that carries the other parts.
        mixed = self.service.dispatch("resolve", {"concept_ids": ["zh-registration"], "municipality_id": "CH-ZH-69",
                                                  "jurisdiction": {"canton": "Zurich"}})
        self.assertEqual(mixed.executed_scope.model_dump(exclude_none=True), wallisellen)
        found = self.service.dispatch("search", {"query": "registration", "canton": "Zurich"})
        self.assertEqual(found.executed_scope.canton_code, "CH-ZH")
        # The same part given twice is a caller error, and an unknown argument is still rejected.
        twice = self.service.dispatch("resolve", {"concept_ids": ["zh-registration"], "city": "Bern",
                                                  "jurisdiction": {"city": "Wallisellen"}})
        self.assertIsInstance(twice, ToolError)
        self.assertIn("The city is given twice", twice.error.issues[0].message)
        self.assertIsInstance(self.service.dispatch("resolve", {"concept_ids": ["zh-registration"], "citty": "Bern"}),
                              ToolError)


    def test_the_input_schema_names_the_default_country(self):
        description = self.service.tool_input_schema("resolve")["properties"]["jurisdiction"]["description"]
        self.assertIn("This server covers Switzerland (CH); omit country.", description)
        self.assertNotIn("no place register", description)
        self.assertEqual(sorted(self.service.tool_input_schema("resolve")["properties"]["jurisdiction"]["properties"]),
                         ["canton", "city", "country"])
        without = ReleaseService(sample_release()).tool_input_schema("resolve")["properties"]["jurisdiction"]["description"]
        self.assertIn("This server covers CH; omit country. This release carries no place register: give codes", without)

    def test_a_release_without_a_register_reads_codes_only(self):
        service = ReleaseService(sample_release())
        named = service.dispatch("resolve", {"concept_ids": ["zh-registration"], "jurisdiction": {"canton": "Zurich"}})
        self.assertEqual(named.executed_scope.model_dump(exclude_none=True), {"country_code": "CH", "not_recognised": {"canton": "Zurich"}})
        coded = service.dispatch("resolve", {"concept_ids": ["zh-registration"], "jurisdiction": {"canton": "zh", "city": "261"}})
        self.assertEqual(coded.executed_scope.model_dump(exclude_none=True),
                         {"country_code": "CH", "canton_code": "CH-ZH", "municipality_id": "CH-ZH-261"})


class FakeSemantic:
    """A semantic search with fixed cosines and the real candidate cut."""

    min_score, candidate_limit, cut = 0.5, 10, SemanticSearch.cut

    def __init__(self, service: ReleaseService, scored: list[tuple[float, str]]):
        self.index = SimpleNamespace(model="test-embed", model_digest="test-digest", release_id=service.release_id,
                                     release_content_sha256=service.release.manifest.content_sha256)
        self.scored, self.requests = scored, 0

    def scores(self, query):
        self.requests += 1
        return self.scored


class SearchJurisdictionTests(unittest.TestCase):
    """Search for a user whose place is known: what can apply there is ranked, what cannot is named."""

    def setUp(self):
        self.service = ReleaseService(release_with_places())

    def search(self, query, jurisdiction=None, **extra):
        arguments = {"query": query, **extra, **({"jurisdiction": jurisdiction} if jurisdiction is not None else {})}
        result = self.service.dispatch("search", arguments)
        self.assertNotIsInstance(result, ToolError, getattr(result, "error", None))
        return result

    def ids(self, result):
        return [hit.concept_id for hit in result.results]

    def test_without_a_place_the_result_is_what_it_was(self):
        plain = self.search("registration")
        self.assertEqual(sorted(self.ids(plain)), ["city-arrival", "deadline", "zh-registration"])
        self.assertIsNone(plain.executed_scope)
        self.assertEqual(plain.published_elsewhere, [])
        dumped = plain.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("published_elsewhere", dumped)
        self.assertNotIn("executed_scope", dumped)
        # An empty jurisdiction and the country alone are no place: every concept lies in the country.
        for jurisdiction in ({}, {"country": "Switzerland"}):
            with self.subTest(jurisdiction=jurisdiction):
                same = self.search("registration", jurisdiction)
                self.assertEqual(self.ids(same), self.ids(plain))
                self.assertEqual(same.published_elsewhere, [])
                self.assertEqual((same.match_strength, same.match_signals), (plain.match_strength, plain.match_signals))

    def test_concepts_published_for_other_places_are_named_not_ranked(self):
        bern = self.search("registration", {"canton": "Bern"})
        self.assertEqual(self.ids(bern), ["deadline"])
        self.assertEqual(bern.matched_count, 1)
        self.assertEqual(bern.executed_scope.model_dump(exclude_none=True),
                         {"country_code": "CH", "canton_code": "CH-BE", "country": "Switzerland", "canton": "Bern / Berne"})
        self.assertEqual([(item.concept_id, item.jurisdictions) for item in bern.published_elsewhere],
                         [("city-arrival", ["CH-ZH-261"]), ("zh-registration", ["CH-ZH"])])
        self.assertIn("do not apply in CH-BE (canton of Bern / Berne)", bern.guidance_for_caller)
        self.assertIn("does not publish it for their place", bern.guidance_for_caller)
        # A municipality keeps its canton's concept and loses the other municipality's.
        winterthur = self.search("registration", {"city": "Winterthur"})
        self.assertEqual(sorted(self.ids(winterthur)), ["deadline", "zh-registration"])
        self.assertEqual([item.concept_id for item in winterthur.published_elsewhere], ["city-arrival"])
        # A canton given without a city keeps what lies below it: resolve asks for the city then.
        canton = self.search("registration", {"canton": "ZH"})
        self.assertEqual(sorted(self.ids(canton)), ["city-arrival", "deadline", "zh-registration"])
        self.assertEqual(canton.published_elsewhere, [])
        self.assertNotIn("published_elsewhere", canton.guidance_for_caller)
        # A concept with one fact per canton applies in each of them.
        self.assertEqual(self.ids(self.search("migration office contact", {"canton": "Bern"})), ["contact"])

    def test_only_what_the_caller_would_have_seen_is_named(self):
        # With a limit of one, the unfiltered first hit alone is named when it cannot apply.
        first = self.ids(self.search("registration", limit=1))[0]
        bern = self.search("registration", {"canton": "Bern"}, limit=1)
        self.assertEqual([item.concept_id for item in bern.published_elsewhere], [first] if first != "deadline" else [])

    def test_the_match_strength_stays_that_of_the_whole_release(self):
        # Whether the subject is published at all: a sibling concept of another place often carries the words that
        # say so, and the caller learns from published_elsewhere that it is not published for the user's place.
        query = "City of Zurich registering arrival from abroad"
        plain = self.search(query)
        bern = self.search(query, {"city": "Bern"})
        self.assertEqual((plain.match_strength, bern.match_strength), ("strong", "strong"))
        self.assertEqual(bern.match_signals, plain.match_signals)
        self.assertEqual(bern.published_elsewhere[0].concept_id, "city-arrival")
        self.assertNotIn("city-arrival", self.ids(bern))
        self.assertIn("the first of them better than any result", bern.guidance_for_caller)
        second = self.search("Municipal registration deadline after arrival", {"city": "Winterthur"})
        self.assertEqual((self.ids(second)[0], [item.concept_id for item in second.published_elsewhere]), ("deadline", ["city-arrival"]))
        self.assertIn("published_elsewhere match the question but", second.guidance_for_caller)
        # No applicable concept matched at all: none with the scope statement, and what matched elsewhere is named.
        nothing = self.search("City of Zurich", {"canton": "Bern"})
        self.assertEqual((self.ids(nothing), nothing.match_strength), ([], "none"))
        self.assertEqual(nothing.scope_statement, "Residence permits and registration.")
        self.assertEqual([item.concept_id for item in nothing.published_elsewhere], ["city-arrival", "zh-registration"])

    def test_a_place_not_recognised_another_country_and_an_ambiguous_name(self):
        quarter = self.search("registration", {"canton": "Zurich", "city": "Oerlikon"})
        self.assertEqual(quarter.executed_scope.not_recognised, {"city": "Oerlikon"})
        self.assertIn("does not hold the city 'Oerlikon', so this search ran for CH-ZH (canton of Zürich)", quarter.guidance_for_caller)
        self.assertEqual(sorted(self.ids(quarter)), ["city-arrival", "deadline", "zh-registration"])
        abroad = self.search("registration", {"country": "Germany"})
        self.assertEqual((self.ids(abroad), abroad.match_strength), ([], "none"))
        self.assertIn("Only Switzerland (CH) is served, so no published concept applies in Germany.", abroad.guidance_for_caller)
        self.assertEqual(len(abroad.published_elsewhere), 3)
        shared = self.service.dispatch("search", {"query": "registration", "jurisdiction": {"city": "Buchs"}})
        self.assertIsInstance(shared, ToolError)
        self.assertEqual(shared.error.issues[0].path, "jurisdiction.city")

    def test_hybrid_search_cuts_the_candidates_among_the_applicable_concepts_with_one_embedding(self):
        semantic = FakeSemantic(self.service, [(0.9, "city-arrival"), (0.8, "zh-registration"), (0.6, "deadline"),
                                               (0.55, "uk-work"), (0.2, "contact")])
        self.service.semantic_search = semantic
        bern = self.search("我的到达登记期限", {"canton": "Bern"})
        self.assertEqual(bern.retrieval_mode, "hybrid")
        self.assertEqual(semantic.requests, 1)
        self.assertEqual(self.ids(bern), ["deadline", "uk-work"])
        self.assertEqual(bern.match_signals.best_semantic_score, 0.9)  # of the whole release, like the lexical signals
        self.assertEqual([item.concept_id for item in bern.published_elsewhere], ["city-arrival", "zh-registration"])
        # A failing embedding falls back to the lexical ranking of the applicable concepts.
        semantic.scores = Mock(side_effect=SemanticError("offline"))
        fallback = self.search("registration", {"canton": "Bern"})
        self.assertEqual((fallback.retrieval_mode, self.ids(fallback)), ("lexical-fallback", ["deadline"]))
        self.assertEqual(len(fallback.published_elsewhere), 2)

    def test_the_search_schema_carries_the_jurisdiction_like_resolve(self):
        jurisdiction = self.service.tool_input_schema("search")["properties"]["jurisdiction"]
        self.assertEqual(sorted(jurisdiction["properties"]), ["canton", "city", "country"])
        self.assertNotIn("allOf", jurisdiction)
        self.assertIn("This server covers Switzerland (CH); omit country.", jurisdiction["description"])
        self.assertIn("Optional for search", jurisdiction["description"])
        self.assertNotIn("Optional for search", self.service.tool_input_schema("resolve")["properties"]["jurisdiction"]["description"])
        self.assertNotIn("jurisdiction", self.service.tool_input_schema("search").get("required", []))
        self.assertIn("canton or municipality as jurisdiction", self.service.instructions)


if __name__ == "__main__":
    unittest.main()
