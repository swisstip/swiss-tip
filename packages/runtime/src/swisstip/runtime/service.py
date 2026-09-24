"""The four tool operations over a validated knowledge release.

Semantics follow docs/architecture/tool-contracts.md and the mock server:
jurisdiction containment (federal answers for every canton and municipality,
cantonal for its municipalities, never upward or sideways; an answer for a
place whose own narrower level is published for another place only says so
in a gap), a jurisdiction given in names or codes and turned into codes with
the release's place register (swisstip.core.places), typed statuses
per concept, named gaps with the published values, required context from the
concept's context schema, validity windows, and a freshness window after
which results are STALE. The server never composes an answer.
"""

import math
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from swisstip.core.basis import DEFAULT_RANKING_POLICY, basis_weight, concept_authority, fact_weight
from swisstip.core.connector import ConnectorError, DatasetSummary
from swisstip.core.contracts import (CONNECTOR_TOOL_CONTRACTS, SCHEMA_VERSION, TOOL_CONTRACTS, TOOL_DESCRIPTIONS, Citation,
                                     ConceptResolution, ConceptSummary,
                                     ContextField, CoverageGap, CoverageRoot, CoverageTopic, DecisionRule, ErrorBody,
                                     ErrorCode, Evidence, ExecutedScope, Fact, FreshnessPolicy, GetCoverageRequest,
                                     GetEvidenceRequest, GetEvidenceResult, GetKnowledgeGraphRequest, LookupEvent, LookupOffer,
                                     LookupProvenance, LookupRequest, LookupResult, LookupSource, MatchSignals, MissingContext, Period,
                                     PublishedElsewhere, QueryLanguage, RequiredUserFact,
                                     ResolveRequest, ResolveResult, SearchHit, SearchRequest, SearchResult, Status,
                                     ToolError, TopicSummary, ValidationIssue, page_citations, shared_basis, shared_review,
                                     tool_input_schema)
from swisstip.core.places import PlaceError, PlaceIndex, Scope
from swisstip.core.release import Release, load_release
from swisstip.core.text import collapse_umlauts, fold
from swisstip.core.validation import assert_valid, contains

from .connectors import ConnectorRegistry, ConnectorUnavailable
from .semantic import SemanticError, SemanticSearch

ALL_TOOL_CONTRACTS = {**TOOL_CONTRACTS, **CONNECTOR_TOOL_CONTRACTS}

# Function words in their folded form (tokens() strips diacritics before filtering, so "fur" is "für").
# German is covered because the release carries German aliases taken from the cited pages; "no" and "not" are
# dropped because a Zurich German "no" ("still") matched the English "no" of an alias.
STOPWORDS = {"a", "an", "and", "as", "at", "by", "do", "for", "i", "in", "is", "it", "my", "of", "on", "or", "the",
             "to", "when", "with", "should", "must", "have", "need", "can", "am", "me", "next", "week", "latest",
             "no", "not",
             # Single characters are tokens (see tokens()), so the one-letter function words need naming. "e" is the
             # prefix of E-Mail, E-ID and e-Umzug, a morpheme and not the name of a type; it lifted the office-contact
             # concepts, whose aliases carry "E-Mail", into the hits of a rent question announced by e-mail. "s" and
             # "z" are the Zurich German article and preposition of "Wo isch s Amt z Winterthur?"; counted as words,
             # they read as the question's most distinctive unmatched terms. A pack that names a type S or Z (the
             # permit S of temporary protection is listed, not published) reaches it through the embedding only.
             "e", "s", "z",
             # Question words: the authored "questions" field is an anchor, so "what" or "does" matched every concept
             # whose sample questions used them.
             "does", "what", "where", "which", "who", "how", "why", "this", "that", "these", "those", "you", "your",
             "are", "was", "were", "be", "been", "there", "they", "their", "will", "would", "could", "from", "into",
             "about", "correct", "right",
             "der", "die", "das", "den", "dem", "des", "und", "oder", "ich", "sie", "er", "es", "wir", "ihr", "ihre",
             "ihrer", "ihrem", "ihren", "bei", "beim", "mit", "von", "vom", "vor", "nach", "auf", "aus", "an", "zu",
             "zur", "zum", "im", "um", "uber", "unter", "durch", "gegen", "ohne", "ab", "bis", "als", "wie", "wenn",
             "dass", "so", "nur", "auch", "noch", "schon", "wann", "was", "wer", "wo", "ein", "eine", "einer", "einem",
             "einen", "kein", "keine", "keinen", "nicht", "ist", "sind", "hat", "haben", "wird", "werden", "kann",
             "konnen", "muss", "mussen", "sich", "sein", "seine", "diese", "dieser", "dieses", "dies", "bin", "habe",
             "darf", "welche", "welcher", "welches", "will", "wollen", "mochte", "mochten", "soll", "sollen", "gilt",
             "gelten", "braucht", "brauche", "damit", "dafur", "dann", "denn", "doch", "hier", "dort", "jetzt", "man",
             "mich", "mir", "uns", "ihn", "ihm", "mein", "meine", "meiner", "meinem", "meinen", "unser", "unsere",
             # French and Italian function words of the cantonal office names and addresses; "de" and "en" are also
             # the Zurich German article and "a".
             "de", "la", "le", "les", "du", "et", "en", "au", "aux", "un", "une", "pour", "avec", "sur", "dans",
             "della", "delle", "del", "di", "il", "lo", "gli", "una", "che", "per", "con"}
ANCHOR_FIELDS = {"label", "aliases", "questions"}
# A token found in at least this share of a release's concepts (the municipality's own name in a municipal pack) says
# nothing about which concept a question means; it neither anchors a hit nor adds to its score. Releases with fewer
# concepts than COMMON_TOKEN_MIN_CONCEPTS are too small for the share to mean anything.
COMMON_TOKEN_SHARE = 0.75
COMMON_TOKEN_MIN_CONCEPTS = 8
# Hits scoring below this fraction of the best hit are loose matches on incidental words; they are dropped so a caller
# resolves the real candidates instead of reading a list of every concept.
RELATIVE_SCORE_FLOOR = 0.2
# A query token counts once per concept, at the best field it appears in. Aliases weigh as much as the label: they
# carry the publisher's own terms in the source language, which is the only way a German question reaches an
# English-labelled concept.
FIELD_WEIGHTS = {"label": 3.0, "aliases": 3.0, "questions": 2.0, "description": 1.0, "statements": 0.5}
# Match strength: whether the query's distinctive words reached a published concept at all, judged after fusion in
# both retrieval modes. An off-topic question ("Mehrwertsteuer", "Halbtax", "speed limit") still produces lexical hits
# on an incidental word ("Schweiz", "Anmeldung") and semantic candidates on a nearby subject, and reciprocal-rank
# fusion flattens every score, so the verdict rests on raw signals. Lexical: the rarity-weighted mass of the query
# tokens that the label, aliases or questions of one concept match, for the concept where it is highest, in units of
# the rarest possible token (a token found in one concept, or never seen), and that mass as a share of the whole
# query, where a token the index has never seen counts as unmatched. The weight carries natural questions, whose
# names and places dilute the share; the share carries one- or two-word queries, whose weight is small. Semantic:
# the best raw cosine before the candidate cut. Measured on release mvp-zurich-2026-09-16-v2
# (scripts/test/packs/test_match_strength.py replays the queries): every acceptance and round-trip question, the
# Zurich German family question included, reaches a weight of 1.6 or more; fourteen off-topic questions stay at 1.35 or less, with cosines
# up to 0.60, and the one whose share passes 0.5 (a Bern kindergarten question matching "Anmeldung") has a cosine of
# 0.40. Hits are never dropped by the verdict.
# Facet words name what the user wants to know about a subject (its address, opening hours, telephone, e-mail), not
# which subject: "opening hours of the Zurich zoo" shares them with the office-contact concepts of release
# mvp-zurich-2026-09-17-v1 and nothing else. They still rank hits, but the match strength rests on the other words, so
# a question about the opening hours of an unpublished office reads weak. Tokenised like a query at import.
FACET_WORDS = ("address addresses opening hours open phone telephone email e-mail contact "
               "Adresse Anschrift Öffnungszeiten Öffnungszeit geöffnet offen Telefon Telefonnummer E-Mail Kontakt "
               "Wegbeschreibung Anfahrt")
LEXICAL_STRONG_WEIGHT = 1.5
LEXICAL_PARTIAL_WEIGHT = 1.0
LEXICAL_STRONG_SHARE = 0.5
SEMANTIC_STRONG_SCORE = 0.7
SEMANTIC_PARTIAL_SCORE = 0.6
SEMANTIC_VETO_SCORE = 0.45
SEARCH_MATCH_NOTE = ("Ranked candidates from this release. Resolve the relevant concept IDs with the known jurisdiction "
                     "and context to select applicable facts; rephrasing the query rarely finds other concepts. The "
                     "ranking does not establish complete coverage of the question.")
SEARCH_WEAK_NOTE = ("Weak candidates only: the query's distinctive words match no published concept, so these hits rest "
                    "on incidental words or a nearby subject. The question probably lies outside the scope_statement in "
                    "this result; if so, tell the user that this service does not cover it and answer nothing from "
                    "general knowledge. Resolve a listed concept only if the question is clearly about it. Do not search "
                    "again with rephrased queries.")
SEARCH_EMPTY_NOTE = ("No candidates matched this query under the active retrieval method. This does not establish "
                     "that the subject is outside coverage: compare the question with the scope_statement in this "
                     "result. If no published topic fits, tell the user that this service does not cover it instead of "
                     "searching again with rephrased queries or answering from general knowledge; if one fits, call "
                     "get_coverage once with that topic to find its concepts.")
# Search with a jurisdiction: a concept published for other places only cannot apply where the user lives. It is kept
# out of the ranking, so a caller does not resolve it only to be refused, and named, so the caller can say that the
# subject is published, but not for the user's place.
SEARCH_JURISDICTION_NOTE = ("Optional for search: give it when the question or the conversation says where the user "
                            "lives, and omit it otherwise. The hits are then the concepts that can apply there, and a "
                            "concept published for other places only is named in published_elsewhere, not ranked.")
SEARCH_ELSEWHERE_NOTE = (" The concepts in published_elsewhere match the question{best} but are published for other "
                         "places only and do not apply in {place}: do not resolve them and carry over no rule, office, "
                         "fee or deadline from them. If no result answers the question, tell the user that this service "
                         "does not publish it for their place.")
SEARCH_ELSEWHERE_BEST = ", the first of them better than any result,"
SEARCH_COUNTRY_NOTE = " Only {served} is served, so no published concept applies in {country}."
SEARCH_PLACE_NOT_RECOGNISED = (" The place register does not hold {parts}, so this search ran for {place}; give resolve "
                               "the official name of the municipality (the political commune, not a district, a quarter "
                               "or a postcode) if the narrower place matters.")
# A source language is named to callers only when its source terms reach this share of the concepts; below it, a
# question in that language is better translated into the preferred language than sent as asked.
QUERY_LANGUAGE_MIN_CONCEPT_SHARE = 0.5
SEARCH_NOTES = {"strong": SEARCH_MATCH_NOTE, "weak": SEARCH_WEAK_NOTE, "none": SEARCH_EMPTY_NOTE}
RESULT_LIMITATIONS_NOTE = ("Not a legal review: English paraphrases of the cited excerpts, no eligibility decision for a "
                           "person, freshness counted from the access date; the full list is in get_coverage.")
# Added to weak and none results: a question in a language the index does not carry reads weak even when its subject
# is covered, so one translated search is the exception to "do not search again".
SEARCH_LANGUAGE_RETRY = (" One exception: if the question is in a language other than {languages} and this query was "
                         "not already translated, search once more with its key terms translated into {preferred} "
                         "before declining.")
# With several query languages the note opens with the rule of one search: callers sent an English question in English
# and in German, side by side, and both searches found the same concepts (mvp-zurich-2026-09-19-v15, lexical and
# hybrid). The better-indexed language is named only as the target of a translation, no longer as "preferred". The
# wording alone does not stop a caller that opens with two parallel calls; the caller's own prompt does
# (docs/architecture/tool-contracts.md, section 4.2).
QUERY_LANGUAGE_NOTE = ("{one_search}Write the search query in {languages}: lexical search matches only {these}. Send a "
                       "question in {one_of} as asked, without translating it; translate the key terms of a "
                       "question in any other language into {preferred} before searching, and still answer in the "
                       "user's language.")
QUERY_LANGUAGE_ONE_SEARCH = ("Search ONCE per question, in {languages}, never in more than one of them: a second "
                             "search in another language rarely finds other concepts. ")
# With a knowledge graph the call pattern gains a first turn: orientation (which level decides, who carries the rules
# out, the office at the user's place, the laws and their source), then search and resolve in the next turn.
INSTRUCTIONS_GRAPH = ("Swiss TIP serves published, cited facts from official Swiss sources; it composes no answers. Scope: "
                      "{scope} Start every new subject with get_knowledge_graph, with the question and, when known, the "
                      "user's canton or municipality as jurisdiction: it says which level of the state sets the rules, who "
                      "carries them out and decides, the office at the user's place, the laws and the authoritative source, "
                      "whether the answer depends on the canton or municipality, and what to search for. In the next turn "
                      "search with its next_search, then resolve the relevant concept_ids with the user's jurisdiction, "
                      "today's date and the context the question implies; a follow-up on the same subject needs no new "
                      "graph call. The graph is orientation, not citable evidence: answer only from resolve's facts. Call "
                      "get_coverage only when unsure whether the question is in scope, and get_evidence only for a "
                      "verbatim quote. {languages} Follow guidance_for_caller in every result.")
GRAPH_FIRST = "For a new subject call get_knowledge_graph first and search in the next turn. "
GRAPH_JURISDICTION_NOTE = ("Optional: give it when the question or the conversation says where the user lives, and omit "
                           "it otherwise; the result then says whether the answer depends on the place and what to ask.")
INSTRUCTIONS = ("Swiss TIP serves published, cited facts from official Swiss sources; it composes no answers. Scope: "
                "{scope} A question normally takes two calls: search with the question and, when known, the user's "
                "canton or municipality as jurisdiction, then resolve the relevant "
                "concept_ids with the user's jurisdiction, today's date and the context the question implies. "
                "Call get_coverage only when unsure whether the question is in scope, and get_evidence only for a "
                "verbatim quote. {languages} Follow guidance_for_caller in every result.")
# The release's context vocabulary, sent once per session with the instructions and again on the resolve schema,
# because a client may drop either. A caller that knows the fields and their published values before its first call
# fills the context on the first resolve instead of learning it from a NEEDS_CONTEXT round trip. The mapping from
# what the user said ("a Czech citizen") to a published value (population eu_efta) stays the caller's: the release
# publishes the categories and what each one means, never a table of nationalities that would go stale.
CONTEXT_NOTE = ("Context fields of this release, with the values its facts are published for. Derive them from what "
                "the user said and send them on the first resolve; a field a concept does not use is ignored, and "
                "each search hit names the fields its own concept requires.")
CONTEXT_NOTE_BRIEF = ("This release has {count} context fields, too many to list here; a search hit names the fields "
                      "its concept requires, with their values: {names}.")
# The list is a session-once cost, not a per-call one, so the whole vocabulary is worth its bytes; a pack with far
# more fields than the ones a question can plausibly need falls back to the names alone.
CONTEXT_NOTE_BUDGET = 4000
GUIDANCE_SUPPORTED = ("These facts and citations are everything this release holds for the requested concepts. Answer "
                      "now from the statements only, cite only the returned URLs, and collect the required_user_facts "
                      "before computing any date. Do not add documents, fees, deadlines, contacts, links or procedures that "
                      "the statements do not contain: for anything the user asks that is listed in not_served or missing "
                      "from the statements, say that this service does not publish it and point to the cited page. Do not "
                      "search or resolve again unless the user asks something new.")
GUIDANCE_STALE = ("The source snapshot of {snapshot} is older than {max_age} days on {as_of}. These facts say what the "
                  "official pages published on {snapshot}, not what holds on {as_of}: present them as published on "
                  "{snapshot}, do not confirm that opening hours, availability, officeholders, rates, contacts or rules "
                  "still apply, and tell the user to check the cited page. Do not resolve again with an earlier as_of to "
                  "avoid this status. Cite only the returned URLs and do not add documents, fees, deadlines, contacts, "
                  "links or procedures that the statements do not contain.")
GUIDANCE_OUT_OF_COVERAGE = ("No published fact applies to this request, for the reasons named in gaps. Tell the user that "
                            "this service does not cover it (or has no reviewed coverage, for review_status_not_met) and do "
                            "not answer it from general knowledge as if it were grounded. Resolve again only if a gap's "
                            "published values fit what the user actually said; after review_status_not_met, do not resolve "
                            "again without reviewed_only unless the user asks for unreviewed statements.")
LANGUAGE_NAMES = {"de": "German", "fr": "French", "it": "Italian", "rm": "Romansh", "en": "English"}
GUIDANCE_NEEDS_CONTEXT = ("Derive the missing fields from what the user already said (for example a Czech citizen is "
                          "population eu_efta) and call resolve again with them. Ask the user only for fields that "
                          "cannot be derived.")
GUIDANCE_MIXED_BASIS = (" Where a portal summary and the cited law or authority page differ, the law's excerpt is the "
                        "more exact source.")
# The caveat on facts served for a place whose own cantonal or municipal level the release does not publish, while it
# publishes that level for another place: said once per result, next to the typed gap on each concept.
GUIDANCE_NARROWER_NOT_PUBLISHED = (" For the user's place this release publishes only the broader level named in the "
                                   "gaps: where the question has a cantonal or municipal part, tell the user that it "
                                   "is not published for their place, and carry over no rule, office, fee or deadline "
                                   "published for another canton or municipality.")
# A part of the jurisdiction the place register does not hold (a district, a postcode, a misspelling): the request ran
# for the broader place, which still reaches its facts, and the caller is told so with every status.
GUIDANCE_PLACE_NOT_RECOGNISED = (" The place register does not hold {parts}, so this result is for {place}. If the "
                                 "narrower place matters, resolve again with the official name of the municipality "
                                 "(the political commune, not a district, a quarter or a postcode) or with the "
                                 "canton; otherwise tell the user which place the answer is for.")
NORM_OR_AUTHORITY = {"act", "ordinance", "treaty", "directive", "guidance"}
# Dataset connectors (docs/architecture/dataset-connectors.md): the facts give the rule, a registered dataset gives
# the dates. Said once per resolve result when a concept carries an offer, and on every lookup result.
GUIDANCE_LOOKUP_OFFER = (" A dataset of published dates stands behind a concept of this result (lookups): to name a "
                         "date, such as the next collection day, ask the user for what the offer's requires names unless "
                         "the conversation gave it - the four-digit postal code, or the collection zone - then call lookup "
                         "with the offer's dataset_id; never derive a date from the facts.")
GUIDANCE_LOOKUP_ZONE = (" An offer that requires zone serves dates by the publisher's collection zone ({zones}): most "
                        "residents do not know theirs, so ask for it and give them the publisher's page that finds it from "
                        "the address, zone_lookup_url; never guess a zone from an address, a street or a postal code.")
GUIDANCE_LOOKUP_SUPPORTED = ("State these dates as published by {publisher} for {key}, oldest first, "
                             "and cite provenance.publisher_url. They are rows of the publisher's open-data file, not "
                             "reviewed statements, and the file covers {start} to {end}; say so when the user asks "
                             "beyond it. Do not compute further dates from the interval between these.")
GUIDANCE_LOOKUP_OUT_OF_COVERAGE = ("No published date answers this request, for the reason named in gaps: tell the user "
                                   "what the gap says, and do not derive a date from the facts or from general "
                                   "knowledge. For postal_code_not_covered, ask the user to check the postal code; for "
                                   "zone_not_covered, give the user the zones it lists and the publisher's page for "
                                   "finding theirs; for "
                                   "period_not_published, say that the publisher has not published that period yet; "
                                   "for connector_unavailable, say that the dates cannot be read at the moment and that "
                                   "the facts are unaffected.")


def match_strength(lexical_share: float, anchored_weight: float, best_semantic_score: float | None) -> str:
    """`strong` or `weak`: whether the query's distinctive words reached a published concept.

    A strong anchored weight or share settles it; otherwise a strong cosine, or a partial weight with a partial
    cosine, rescues an other-language question. A cosine below the veto says the nearest concept is far from the
    question, whatever incidental word matched. Without semantic search, the lexical signals decide alone.
    """
    if best_semantic_score is not None and best_semantic_score < SEMANTIC_VETO_SCORE:
        return "weak"
    if anchored_weight >= LEXICAL_STRONG_WEIGHT or lexical_share >= LEXICAL_STRONG_SHARE:
        return "strong"
    if best_semantic_score is not None and (best_semantic_score >= SEMANTIC_STRONG_SCORE or (
            anchored_weight >= LEXICAL_PARTIAL_WEIGHT and best_semantic_score >= SEMANTIC_PARTIAL_SCORE)):
        return "strong"
    return "weak"


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def language_list(codes: list[str], conjunction: str = "or") -> str:
    names = [language_name(code) for code in codes]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


def search_terms(count: int) -> str:
    return f"{count} search term" if count == 1 else f"{count} search terms"


def context_vocabulary(fields: dict[str, ContextField]) -> str:
    """The release's context fields with their published values, for the instructions and the resolve schema."""
    if not fields:
        return ""
    lines = [f"- {name} ({', '.join(spec.enum) if spec.enum else 'any value'}): {' '.join(spec.description.split())}"
             for name, spec in sorted(fields.items())]
    block = "\n".join([CONTEXT_NOTE, *lines])
    if len(block) <= CONTEXT_NOTE_BUDGET:
        return block
    return CONTEXT_NOTE_BRIEF.format(count=len(fields), names=", ".join(sorted(fields)))


def query_languages(release: Release) -> tuple[list[QueryLanguage], list[str]]:
    """The languages lexical search matches, best first, and the source languages left out as partial.

    An alias found verbatim in an excerpt of its concept is a source term in that excerpt's language; the labels,
    sample questions and statements are in the statements' language. A source language is listed only when its terms
    reach QUERY_LANGUAGE_MIN_CONCEPT_SHARE of the concepts: a caller sends a question in a listed language untranslated,
    and a language that reaches few concepts would make most such questions read weak. Listed source languages rank
    first, by their number of terms, because a query in them can meet the publisher's own words; the statements'
    language is always listed and follows them.
    """
    facts = {f.fact_id: f for f in release.facts}
    evidence = {e.evidence_id: e for e in release.evidence}
    terms, concepts = Counter(), Counter()
    for concept in release.concepts:
        excerpts = [evidence[e] for f in concept.fact_ids for e in facts[f].evidence_ids]
        found_in = set()
        for alias in concept.aliases:
            found = Counter(e.language.split("-")[0] for e in excerpts if fold(alias) in fold(e.original_excerpt))
            if found:
                code = found.most_common(1)[0][0]
                terms[code] += 1
                found_in.add(code)
        concepts.update(found_in)
    total = len(release.concepts)
    authored = {f.language.split("-")[0] for f in release.facts}
    source = [code for code in terms if re.fullmatch(r"[a-z]{2}", code)]
    listed = [code for code in source if code in authored or concepts[code] >= QUERY_LANGUAGE_MIN_CONCEPT_SHARE * total]
    partial = [f"{language_name(code)} ({search_terms(terms[code])} on {concepts[code]} of {total} concepts)"
               for code in sorted(set(source) - set(listed))]
    order = sorted(listed, key=lambda code: (-terms[code], code)) + sorted(authored - set(listed))
    result = []
    for rank, code in enumerate(order, 1):
        parts = [f"{search_terms(terms[code])} copied from the cited pages, on {concepts[code]} of {total} concepts"]             if code in terms else []
        if code in authored:
            parts.append("the concept labels, sample questions and statements")
        result.append(QueryLanguage(code=code, rank=rank, indexed="; plus ".join(parts)))
    return result, partial


def stem(token: str) -> str:
    """Language-neutral prefix stem so that 'registration', 'register' and 'registrieren' meet."""
    return token[:6] if len(token) > 6 else token


# A one-character token often carries the whole question when a type is named by a letter or a digit: "What is an L
# permit?", "Tarif B", "Visum D", "Kreis 5". Without it the question comes down to a noun that nearly every concept
# of the topic carries. Such a token is kept; which types a pack names is the pack's business, read from its
# aliases like any other term. A character attached to a word by an apostrophe is grammar, not a name ("the
# national's permit", "l'autorisation"), and is dropped.
ELISION = re.compile(r"(?<!\w)[a-z0-9]['’]|['’][a-z0-9](?!\w)")


def tokens(text: str) -> set[str]:
    folded = ELISION.sub(" ", fold(text))
    return {stem(collapse_umlauts(t)) for t in re.findall(r"[a-z0-9]+", folded) if t not in STOPWORDS}


FACET_TOKENS = frozenset(tokens(FACET_WORDS))


def invalid(exc: ValidationError) -> ToolError:
    issues = [ValidationIssue(path=".".join(str(part) for part in error["loc"]) or "request", message=error["msg"])
              for error in exc.errors()]
    return ToolError(error=ErrorBody(code=ErrorCode.INVALID_ARGUMENT, issues=issues))


def argument_error(path: str, message: str, code: ErrorCode = ErrorCode.INVALID_ARGUMENT) -> ToolError:
    return ToolError(error=ErrorBody(code=code, issues=[ValidationIssue(path=path, message=message)]))


class ReleaseService:
    def __init__(self, release: Release, semantic_search: SemanticSearch | None = None,
                 semantic_error: str | None = None, connectors: ConnectorRegistry | None = None,
                 knowledge_graph: bool = True):
        self.release = release
        self.semantic_search = semantic_search
        self.semantic_error = semantic_error
        # The dataset connectors registered behind the release's concepts; None or empty means no lookup tool.
        self.connectors = connectors
        manifest = release.manifest
        self.release_id = manifest.release_id
        self.topics = {t.topic_id: t for t in release.topics}
        self.concepts = {c.concept_id: c for c in release.concepts}
        self.facts = {f.fact_id: f for f in release.facts}
        self.evidence = {e.evidence_id: e for e in release.evidence}
        self.institutions = {i.institution_id: i for i in release.institutions}
        # The places a caller can name; without a register in the release, codes only.
        self.place_index = PlaceIndex(release.place_register, manifest.jurisdictions)
        # Ranking prior: the basis of a concept's facts (the law above guidance above a portal summary) and the review
        # of its statements; never the level of the publisher. A release without bases and with reviewed curated
        # statements gets authority 1 everywhere, so its ranking is the plain lexical one.
        self.policy = manifest.ranking_policy or DEFAULT_RANKING_POLICY
        self.fact_weights = {f.fact_id: fact_weight(f, self.evidence, self.policy) for f in release.facts}
        self.concept_authority = {c.concept_id: concept_authority([self.fact_weights[f] for f in c.fact_ids], self.policy)
                                  for c in release.concepts}
        self.context_fields = {name: spec for concept in release.concepts for name, spec in concept.context_schema.items()}
        self.context_note = context_vocabulary(self.context_fields)
        # The levels each topic publishes, to tell a user elsewhere that a narrower level exists for another place only.
        self.topic_jurisdictions: dict[str, set[str]] = {}
        for concept in release.concepts:
            self.topic_jurisdictions.setdefault(concept.topic_id, set()).update(
                self.facts[fact_id].jurisdiction for fact_id in concept.fact_ids)
        self.freshness = FreshnessPolicy(snapshot_date=manifest.freshness.snapshot_date,
                                         max_age_days=manifest.freshness.max_age_days,
                                         stale_from=manifest.freshness.stale_from)
        statuses = ", ".join(f"{count} {status}" for status, count in sorted(manifest.review_statuses.items()))
        review_line = (f"Review status of the {len(release.facts)} served facts: {statuses}. "
                       "Verify the cited pages before relying on a fact.")
        # The manifest's full list rides on the coverage pages, read once per session at most. Search, resolve and
        # get_evidence, read on every call, carry the review status the caller must pass on and one pointer: the
        # full list cost about 1.2 KB per call, a tenth of a single-turn budget, and said the same thing every time.
        self.limitations = [*manifest.limitations, review_line]
        self.result_limitations = [review_line, RESULT_LIMITATIONS_NOTE]
        self.search_limitations = list(self.result_limitations)
        # Search index: the tokens of every field per concept, and the rarity of each token across concepts. A token
        # found in most concepts ("Aufenthalt", "Schweiz", "Bewilligung") says little about which concept a question
        # means; one found in one or two ("Familiennachzug", "Grenzgänger") says a lot.
        self.search_index = {c.concept_id: self.field_tokens(c) for c in release.concepts}
        frequency = Counter(token for fields in self.search_index.values() for token in set().union(*fields.values()))
        self.token_weight = {token: math.log(1 + len(self.search_index) / count) for token, count in frequency.items()}
        self.common_tokens = ({token for token, count in frequency.items() if count >= COMMON_TOKEN_SHARE * len(self.search_index)}
                              if len(self.search_index) >= COMMON_TOKEN_MIN_CONCEPTS else set())
        # A query token the index has never seen weighs as much as a token found in one concept: it is the most
        # distinctive word of the question, and nothing matched it.
        self.unknown_token_weight = math.log(1 + len(self.search_index))
        self.query_languages, partial = query_languages(release)
        if partial:
            unadvertised = ("Search is not advertised in " + ", ".join(partial) + ", because their terms reach too few "
                            "concepts; search with the key terms in a listed query language instead.")
            self.limitations.append(unadvertised)
            self.search_limitations.append(unadvertised)
        codes = [item.code for item in self.query_languages]
        if codes:
            several = len(codes) > 1
            self.query_language_note = QUERY_LANGUAGE_NOTE.format(
                languages=language_list(codes), these="these languages" if several else "this language",
                one_search=QUERY_LANGUAGE_ONE_SEARCH.format(languages=language_list(codes)) if several else "",
                one_of="one of them" if several else "it", preferred=language_name(codes[0]))
            retry = SEARCH_LANGUAGE_RETRY.format(languages=language_list(codes), preferred=language_name(codes[0]))
        else:
            self.query_language_note, retry = "", ""
        self.search_notes = {"strong": SEARCH_MATCH_NOTE, "weak": SEARCH_WEAK_NOTE + retry, "none": SEARCH_EMPTY_NOTE + retry}
        # The orientation graph, when the release carries one and the operator has not switched it off; the tool is
        # offered only then. Switched off, the server behaves as for a release without a graph.
        from .graph import GraphIndex
        graph = release.knowledge_graph if knowledge_graph else None
        self.graph_disabled = release.knowledge_graph is not None and graph is None
        self.graph_index = GraphIndex(graph, release.topics) if graph is not None else None
        self.graph_evidence = {e.evidence_id: e for e in graph.evidence} if graph is not None else {}
        self.graph_institutions = {i.institution_id: i for i in graph.institutions} if graph is not None else {}
        template = INSTRUCTIONS_GRAPH if graph is not None else INSTRUCTIONS
        self.instructions = " ".join(template.format(scope=manifest.scope_statement.strip(),
                                                     languages=self.query_language_note).split())
        if self.context_note:
            self.instructions += "\n" + self.context_note

    def tools(self) -> list[str]:
        """The tools this server lists: the four of the release, get_knowledge_graph first when the release carries a
        graph, and lookup while a dataset is registered."""
        names = [name for name in TOOL_CONTRACTS if name != "get_knowledge_graph" or self.graph_index is not None]
        if self.connectors is not None and self.connectors.datasets:
            names.extend(CONNECTOR_TOOL_CONTRACTS)
        return names

    def tool_description(self, name: str) -> str:
        """The contract's description, with this release's query languages on search and, when the release carries a
        knowledge graph, the graph-first call pattern on every other tool."""
        description = TOOL_DESCRIPTIONS[name]
        if self.graph_index is not None and name != "get_knowledge_graph":
            description = GRAPH_FIRST + description.replace(
                "A question normally needs two calls in total: search, then resolve.",
                "A question normally needs three calls: get_knowledge_graph, then search and resolve in the next turn.")
        if name == "search" and self.query_language_note:
            description += " This server: " + self.query_language_note
        return description

    def tool_input_schema(self, name: str) -> dict:
        schema = tool_input_schema(ALL_TOOL_CONTRACTS[name][0])
        if name == "search" and self.query_language_note:
            query = schema["properties"]["query"]
            query["description"] = "Question or key terms to find published concepts. " + self.query_language_note
        if name == "resolve" and self.context_note:
            context = schema["properties"]["context"]
            context["description"] = " ".join(context["description"].split()) + "\n" + self.context_note
        if name in ("search", "resolve", "get_knowledge_graph"):
            jurisdiction = schema["properties"]["jurisdiction"]
            jurisdiction["description"] = " ".join(jurisdiction["description"].split()) + " " + self.jurisdiction_note()
            if name == "search":
                jurisdiction["description"] += " " + SEARCH_JURISDICTION_NOTE
            if name == "get_knowledge_graph":
                jurisdiction["description"] += " " + GRAPH_JURISDICTION_NOTE
        return schema

    def jurisdiction_note(self) -> str:
        """What this release makes of a jurisdiction: its default country, and whether it can read names at all."""
        index = self.place_index
        if index.default_country:
            country = index.name(index.default_country)
            served = f"This server covers {f'{country} ({index.default_country})' if country else index.default_country}; omit country."
        else:
            served = f"This server covers {', '.join(index.countries)}; name the country."
        if index.register is None:
            served += " This release carries no place register: give codes (CH-ZH, CH-ZH-261), not names."
        return served

    def field_tokens(self, concept) -> dict[str, set[str]]:
        return {"label": tokens(concept.label), "aliases": tokens(" ".join(concept.aliases)),
                "questions": tokens(" ".join(concept.questions)), "description": tokens(concept.description),
                "statements": tokens(" ".join(self.facts[f].statement for f in concept.fact_ids))}

    @classmethod
    def from_file(cls, path: Path, semantic_search: SemanticSearch | None = None,
                  semantic_error: str | None = None, knowledge_graph: bool = True) -> "ReleaseService":
        release = load_release(path)
        assert_valid(release)
        return cls(release, semantic_search=semantic_search, semantic_error=semantic_error,
                   knowledge_graph=knowledge_graph)

    # --- helpers -------------------------------------------------------------

    def concept_summary(self, concept_id: str) -> ConceptSummary:
        concept = self.concepts[concept_id]
        return ConceptSummary(
            concept_id=concept_id, topic_id=concept.topic_id, label=concept.label, description=concept.description,
            jurisdictions=concept.jurisdictions, required_context=concept.required_context)

    def context_schema(self, concept_id: str) -> dict[str, ContextField]:
        return {name: ContextField(enum=spec.enum, description=spec.description)
                for name, spec in self.concepts[concept_id].context_schema.items()}

    def prior(self, concept_id: str) -> float:
        return self.policy.floor + (1 - self.policy.floor) * self.concept_authority[concept_id]

    def page_fields(self, item) -> dict:
        institution = (self.institutions.get(item.institution_id) or self.graph_institutions.get(item.institution_id)
                       if item.institution_id else None)
        return dict(level=institution.level if institution else None, jurisdiction=institution.jurisdiction if institution else None)

    def citations(self, evidence_ids: list[str]) -> list[Citation]:
        """One citation per page, the page with the strongest basis first, then in first-cited order."""
        pages = page_citations([(evidence_id, dict(source_title=item.source_title, publisher=item.publisher, url=item.url,
                                                   language=item.language, accessed_on=item.accessed_on, **self.page_fields(item)))
                                for evidence_id in evidence_ids for item in [self.evidence[evidence_id]]])
        return sorted(pages, key=lambda page: -max(basis_weight(self.evidence[e].basis, self.policy) for e in page.evidence_ids))

    def topic_summary(self, topic_id: str) -> TopicSummary:
        concepts = [c for c in self.release.concepts if c.topic_id == topic_id]
        return TopicSummary(topic_id=topic_id, title=self.topics[topic_id].title, concept_count=len(concepts))

    def check_release(self, release_id: str | None) -> ToolError | None:
        if release_id not in (None, self.release_id):
            return argument_error("release_id", f"Unknown release_id {release_id!r}; the active release is {self.release_id!r}.",
                                  ErrorCode.RELEASE_UNAVAILABLE)
        return None

    # --- tools ---------------------------------------------------------------

    def get_coverage(self, request: GetCoverageRequest):
        error = self.check_release(request.release_id)
        if error:
            return error
        manifest = self.release.manifest
        if request.parent_id is None:
            return CoverageRoot(
                release_id=self.release_id, schema_version=SCHEMA_VERSION, scope_statement=manifest.scope_statement,
                out_of_scope=manifest.out_of_scope, out_of_scope_response=manifest.out_of_scope_response,
                jurisdictions=manifest.jurisdictions, languages=manifest.languages,
                query_languages=self.query_languages or None, freshness=self.freshness,
                topics=[self.topic_summary(t.topic_id) for t in self.release.topics],
                institution_levels=manifest.institution_levels or None, basis_kinds=manifest.basis_kinds or None,
                limitations=self.limitations)
        if request.parent_id not in self.topics:
            return argument_error("parent_id", f"Unknown parent_id {request.parent_id!r}; use one of {sorted(self.topics)} or omit it.")
        return CoverageTopic(release_id=self.release_id, topic_id=request.parent_id, title=self.topics[request.parent_id].title,
                             concepts=[self.concept_summary(c.concept_id) for c in self.release.concepts
                                       if c.topic_id == request.parent_id],
                             limitations=self.limitations)

    def applicable(self, requested: str | None) -> set[str]:
        """The concepts that can apply at a place: one of their jurisdictions contains it (a federal concept every
        canton, a cantonal one its municipalities) or lies below it, because a caller who gave the canton may not know
        the city yet and resolve then asks for it. A concept published beside the place only, for another canton or
        municipality, cannot. No concept applies in a country the release does not serve."""
        if requested is None:
            return set()
        return {concept.concept_id for concept in self.release.concepts
                if any(contains(code, requested) or contains(requested, code) for code in concept.jurisdictions)}

    def lexical_hits(self, query_text: str, allowed: set[str] | None = None) -> list[tuple[float, str, list[str]]]:
        """Rank concepts by query overlap; a hit needs a match on the label, an alias or a question.

        With `allowed`, only those concepts are ranked, and the score floor follows the best of them.


        Each query token counts once per concept, at the best field it appears in
        (FIELD_WEIGHTS), multiplied by its rarity across the concepts, so a
        question is ranked by its distinctive words and not by how often
        "Aufenthalt" or "Schweiz" recurs. Description and statement overlap only
        adds to the score, so a query about something the release does not
        publish comes back empty instead of with loose hits that invite another
        search. The score is then multiplied by the concept's prior (the basis
        of its facts and their review), which reorders near-ties only.
        """
        query = tokens(query_text) - self.common_tokens
        hits = []
        for concept in self.release.concepts:
            if allowed is not None and concept.concept_id not in allowed:
                continue
            fields = self.search_index[concept.concept_id]
            best, matched = {}, []
            for name, weight in FIELD_WEIGHTS.items():
                overlap = query & fields[name]
                if overlap:
                    matched.append(name)
                    for token in overlap:
                        best[token] = max(best.get(token, 0.0), weight)
            score = sum(weight * self.token_weight[token] for token, weight in best.items()) * self.prior(concept.concept_id)
            if score and set(matched) & ANCHOR_FIELDS:
                hits.append((score, concept.concept_id, matched))
        hits.sort(key=lambda item: (-item[0], item[1]))
        if hits:
            floor = hits[0][0] * RELATIVE_SCORE_FLOOR
            hits = [hit for hit in hits if hit[0] >= floor]
        return hits

    def anchored_match(self, query_text: str) -> tuple[float, float]:
        """The share and the weight of the query's rarity-weighted tokens that the label, aliases or questions of one
        concept match, for the concept where the mass is highest; facet words (FACET_WORDS) are left out. The share is 0 when no anchor field matches
        anything and 1 when one concept's anchors carry every distinctive word of the question; the weight is the
        same mass in units of the rarest possible token. Unlike the score, both ignore field weights and the prior."""
        query = tokens(query_text) - self.common_tokens - FACET_TOKENS
        # A one-character token that no label, alias or question carries is grammar the stopword list does not know
        # (the Zurich German "s" and "z" of "Wo isch s Amt z Winterthur?"), not the name of a type: a type a pack
        # names is in its aliases. It is left out rather than counted as the question's most distinctive unmatched
        # word; a statement that merely mentions the letter does not make it a name.
        named = {token for fields in self.search_index.values() for name in ANCHOR_FIELDS for token in query & fields[name]}
        query = {token for token in query if len(token) > 1 or token in named}
        weights = {token: self.token_weight.get(token, self.unknown_token_weight) for token in query}
        total = sum(weights.values())
        if not total:
            return 0.0, 0.0
        mass = 0.0
        for fields in self.search_index.values():
            anchored = {token for name in ANCHOR_FIELDS for token in query & fields[name]}
            mass = max(mass, sum(weights[token] for token in anchored))
        return round(mass / total, 4), round(mass / self.unknown_token_weight, 4)

    def search(self, request: SearchRequest):
        """Lexical baseline or reciprocal-rank fusion with independently retrieved semantic candidates.

        Semantic inference affects discovery only. Each list contributes 1/(60+rank), with ranks starting at one.
        The union is sorted by fused score and concept ID, before the caller's limit is applied. No fact is changed
        or dropped by ranking, and no query result is cached, so benchmark repetitions measure actual inference.

        With a jurisdiction, the ranking is that of the concepts that can apply at the place (`applicable`). The
        ranking over every concept is still computed, without another embedding request: the concepts among its first
        hits that cannot apply there are named in published_elsewhere. The match strength stays that of the whole
        release: it says whether the subject is published at all, and a sibling concept of another place often carries
        the words that say so. Judged on the applicable concepts alone, the lexical verdict turned six covered
        questions of the regression pack weak, five of them with their concept found, and corrected none (release
        mvp-zurich-2026-09-18-v19; docs/architecture/tool-contracts.md, section 4.4).
        """
        place = request.jurisdiction
        scope = allowed = None
        if place.country or place.canton or place.city:
            try:
                scope = self.place_index.resolve(place.country, place.canton, place.city)
            except PlaceError as exc:
                return argument_error(exc.path, exc.message)
            allowed = self.applicable(scope.code)
        everywhere = self.lexical_hits(request.query)
        hits = everywhere if allowed is None else self.lexical_hits(request.query, allowed)
        lexical_share, anchored_weight = self.anchored_match(request.query)
        best_semantic_score = None
        mode = "lexical-fallback" if self.semantic_error else "lexical"
        reason = self.semantic_error
        model = digest = None
        if self.semantic_search is not None:
            model = self.semantic_search.index.model
            digest = self.semantic_search.index.model_digest
            try:
                if self.semantic_search.index.release_id != self.release_id or \
                        self.semantic_search.index.release_content_sha256 != self.release.manifest.content_sha256:
                    raise SemanticError("Semantic index belongs to another release")
                if allowed is None:
                    best, semantic_hits = self.semantic_search.ranked(request.query)
                    semantic_everywhere = semantic_hits
                else:
                    scored = self.semantic_search.scores(request.query)
                    best, semantic_everywhere = self.semantic_search.cut(scored)
                    semantic_hits = self.semantic_search.cut(scored, allowed)[1]
                if any(concept_id not in self.concepts for _, concept_id in semantic_everywhere):
                    raise SemanticError("Semantic index returned a concept outside the active release")
                fused = self.fuse(hits, semantic_hits)
                everywhere = fused if allowed is None else self.fuse(everywhere, semantic_everywhere)
                hits, best_semantic_score, mode, reason = fused, best, "hybrid", None
            except SemanticError as exc:
                mode, reason = "lexical-fallback", str(exc)
        elsewhere = [] if allowed is None else [concept_id for _, concept_id, _ in everywhere[:request.limit]
                                                if concept_id not in allowed]
        results = []
        for score, concept_id, matched in hits[:request.limit]:
            summary = self.concept_summary(concept_id)
            results.append(SearchHit(concept_id=concept_id, topic_id=summary.topic_id, label=summary.label,
                                     description=summary.description, jurisdictions=summary.jurisdictions,
                                     required_context=summary.required_context, context_schema=self.context_schema(concept_id),
                                     score=round(score, 6 if mode == "hybrid" else 2), matched_on=matched))
        strength = match_strength(lexical_share, anchored_weight, best_semantic_score) if hits else "none"
        guidance = self.search_notes[strength]
        if scope is not None and scope.code is None:
            served = ", ".join(self.place_index.described(code) for code in self.place_index.countries)
            guidance += SEARCH_COUNTRY_NOTE.format(served=served, country=scope.country or scope.country_code)
        elif scope is not None:
            if elsewhere:
                guidance += SEARCH_ELSEWHERE_NOTE.format(place=self.place_index.label(scope.code),
                                                         best=SEARCH_ELSEWHERE_BEST if everywhere[0][1] not in allowed else "")
            if scope.not_recognised:
                parts = " and ".join(f"the {part} {value!r}" for part, value in scope.not_recognised.items())
                guidance += SEARCH_PLACE_NOT_RECOGNISED.format(parts=parts, place=self.place_index.label(scope.code))
        return SearchResult(release_id=self.release_id, query=request.query, results=results,
                            executed_scope=self.executed_scope(scope) if scope is not None else None,
                            published_elsewhere=[PublishedElsewhere(concept_id=concept_id, label=self.concepts[concept_id].label,
                                                                    jurisdictions=self.concepts[concept_id].jurisdictions)
                                                 for concept_id in elsewhere],
                            guidance_for_caller=guidance, match_strength=strength,
                            match_signals=MatchSignals(lexical_share=lexical_share, anchored_weight=anchored_weight,
                                                       best_semantic_score=best_semantic_score),
                            scope_statement=self.release.manifest.scope_statement if strength != "strong" else None,
                            matched_count=len(hits), truncated=len(hits) > request.limit, retrieval_mode=mode,
                            ranking_model=model, ranking_model_digest=digest, fallback_reason=reason,
                            limitations=self.search_limitations)

    def fuse(self, lexical: list[tuple[float, str, list[str]]], semantic: list[tuple[float, str]]) -> list[tuple[float, str, list[str]]]:
        """Reciprocal-rank fusion of the lexical hits with the semantic candidates."""
        # The same prior as on the lexical scores, applied to each retriever before ranks are fused.
        semantic = sorted(((score * self.prior(concept_id), concept_id) for score, concept_id in semantic),
                          key=lambda item: (-item[0], item[1]))
        fused = {concept_id: 1 / (60 + rank) for rank, (_, concept_id, _) in enumerate(lexical, 1)}
        fields = {concept_id: list(matched) for _, concept_id, matched in lexical}
        for rank, (_, concept_id) in enumerate(semantic, 1):
            fused[concept_id] = fused.get(concept_id, 0.0) + 1 / (60 + rank)
            fields.setdefault(concept_id, []).append("semantic")
        return sorted(((score, concept_id, fields[concept_id]) for concept_id, score in fused.items()),
                      key=lambda item: (-item[0], item[1]))

    @staticmethod
    def executed_scope(scope: Scope) -> ExecutedScope:
        return ExecutedScope(country_code=scope.country_code, canton_code=scope.canton_code,
                             municipality_id=scope.municipality_id, country=scope.country, canton=scope.canton,
                             city=scope.city, not_recognised=scope.not_recognised)

    def named(self, codes: list[str]) -> str:
        """The codes of a message with their level and name; a long list (the 26 cantons) stays bare codes."""
        return ", ".join(self.place_index.label(code) for code in codes) if len(codes) <= 6 else ", ".join(codes)

    def resolve_concept(self, concept_id: str, request: ResolveRequest, as_of: date, scope: Scope,
                        withheld: list[str] | None = None) -> ConceptResolution:
        concept = self.concepts.get(concept_id)
        label = self.place_index.label
        if concept is None:
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="concept_not_published", message="Unknown concept_id; take IDs from get_coverage or search.",
                published_values=sorted(self.concepts))])
        if scope.code is None:
            served = ", ".join(self.place_index.described(code) for code in self.place_index.countries)
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="jurisdiction_not_covered", message=f"Only {served} is served.",
                published_values=self.place_index.countries)])
        requested = scope.code
        facts = [self.facts[f] for f in concept.fact_ids]
        applicable = [f for f in facts if contains(f.jurisdiction, requested)]
        if not applicable:
            below = sorted({f.jurisdiction for f in facts if contains(requested, f.jurisdiction)})
            if below:
                message = (f"This concept is published for {self.named(below)}, below the requested {label(requested)}; add "
                           "the canton or the city if the user lives there, otherwise only broader concepts apply.")
            else:
                message = (f"This concept is published for {self.named(concept.jurisdictions)}, not for {label(requested)}; "
                           "no procedure is published for that jurisdiction. Federal concepts still apply.")
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="jurisdiction_not_covered", message=message, published_values=concept.jurisdictions)])
        missing = [MissingContext(field=field, options=concept.context_schema[field].enum,
                                  hint=concept.context_schema[field].description)
                   for field in concept.required_context if not request.context.get(field)]
        if missing:
            return ConceptResolution(concept_id=concept_id, status=Status.NEEDS_CONTEXT, missing_context=missing)
        unknown = sorted(field for field in request.context if field not in self.context_fields)
        if unknown:
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="context_not_covered", message=f"Unknown context fields: {unknown}.",
                published_values=sorted(self.context_fields))])
        matching = [f for f in applicable
                    if all(request.context.get(field) == value for field, value in (f.condition or {}).items())]
        if not matching:
            published = sorted({f"{k}={v}" for f in applicable for k, v in (f.condition or {}).items()})
            given = ", ".join(f"{k}={request.context[k]}" for f in applicable for k in (f.condition or {}) if k in request.context)
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="context_not_covered",
                message=f"This concept has no facts for {given or 'the given context'}; it is published for {', '.join(published)}. "
                        "Say that the service has no coverage for it.",
                published_values=published)])
        in_window = [f for f in matching if (f.valid_from is None or f.valid_from <= as_of)
                     and (f.valid_through is None or as_of <= f.valid_through)]
        if not in_window:
            windows = sorted({f"{f.valid_from or 'unbounded'}..{f.valid_through or 'unbounded'}" for f in matching})
            return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                dimension="date_outside_coverage", message=f"No fact of this concept is valid on {as_of.isoformat()}.",
                published_values=windows)])
        if request.reviewed_only:
            reviewed = [f for f in in_window if f.provenance.review_status == "human-reviewed"]
            if withheld is not None:
                withheld.extend(f.fact_id for f in in_window if f not in reviewed)
            if not reviewed:
                statuses = sorted({f.provenance.review_status for f in in_window})
                return ConceptResolution(concept_id=concept_id, status=Status.OUT_OF_COVERAGE, gaps=[CoverageGap(
                    dimension="review_status_not_met",
                    message="No fact of this concept has been confirmed by a person, and the request asked for "
                            "human-reviewed facts only. Say that the service has no reviewed coverage for it and do "
                            "not pass on unreviewed statements; resolve again without reviewed_only only if the user "
                            "then agrees to unreviewed statements.",
                    published_values=statuses)])
            in_window = reviewed
        answering = max((f.jurisdiction for f in in_window), key=lambda code: code.count("-"))
        evidence_ids: list[str] = []
        for fact in in_window:
            for evidence_id in fact.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        gaps = []
        narrower = sorted({f.jurisdiction for f in facts if contains(requested, f.jurisdiction) and f.jurisdiction != requested})
        if narrower:
            gaps.append(CoverageGap(dimension="more_specific_jurisdiction_available",
                                    message=f"This concept also publishes facts for {self.named(narrower)}; add the canton "
                                            "or the city to get them if the user lives there.",
                                    published_values=narrower))
        others = sorted({f.jurisdiction for other in self.release.concepts if other.topic_id == concept.topic_id
                         and other.concept_id != concept_id for f_id in other.fact_ids
                         for f in [self.facts[f_id]] if contains(requested, f.jurisdiction) and f.jurisdiction != requested
                         and "-" in requested})
        if others:
            names = sorted({other.concept_id for other in self.release.concepts if other.topic_id == concept.topic_id
                            and any(self.facts[f].jurisdiction in others for f in other.fact_ids)})
            gaps.append(CoverageGap(dimension="more_specific_jurisdiction_available",
                                    message=f"Narrower procedures are published in this topic by {', '.join(names)} for "
                                            f"{self.named(others)}; add the city to resolve them if the user lives there.",
                                    published_values=others))
        elsewhere = self.narrower_published_elsewhere(concept.topic_id, requested)
        if elsewhere:
            deepest, places = elsewhere
            gaps.append(CoverageGap(dimension="more_specific_jurisdiction_not_published",
                                    message=f"For {label(requested)} this topic publishes nothing narrower than "
                                            f"{label(deepest)}; its narrower facts are published for "
                                            f"{self.named(places)} only and do not apply to {requested}.",
                                    published_values=places))
        status = Status.STALE if as_of >= self.freshness.stale_from else Status.SUPPORTED
        served, review = shared_review([
            Fact(fact_id=f.fact_id, statement=f.statement, jurisdiction=f.jurisdiction, condition=f.condition,
                 valid_from=f.valid_from, valid_through=f.valid_through, evidence_ids=f.evidence_ids,
                 review_status=f.provenance.review_status, reviewed_on=f.provenance.reviewed_on,
                 reviewed_by=f.provenance.reviewed_by,
                 basis=f.provenance.basis.label if f.provenance.basis else None) for f in in_window])
        served, basis = shared_basis(served)
        return ConceptResolution(
            concept_id=concept_id, status=status, answering_jurisdiction=label(answering), **review, basis=basis,
            facts=served, citations=self.citations(evidence_ids), gaps=gaps, not_served=concept.not_served,
            lookups=self.lookup_offers(concept_id, requested))

    def lookup_offers(self, concept_id: str, requested: str) -> list[LookupOffer]:
        """The datasets a registered connector serves behind the concept for the resolved place."""
        if self.connectors is None:
            return []
        return [LookupOffer(dataset_id=s.dataset_id, type=s.type, title=s.title, label=s.label, jurisdiction=s.jurisdiction,
                            requires=s.requires, accepts=s.accepts, period=Period(start=s.period.start, end=s.period.end),
                            publisher=s.publisher, zones=s.zones, zone_lookup_url=s.zone_lookup_url)
                for s in self.connectors.offers(concept_id, requested)]

    def narrower_published_elsewhere(self, topic_id: str, requested: str) -> tuple[str, list[str]] | None:
        """For a place the topic serves less deeply than another place: the deepest level that applies there, and
        the places beside it for which the topic publishes a narrower one (for Bern in a topic with federal, Canton
        of Zurich and City of Zurich facts: CH, and CH-ZH with CH-ZH-261).

        None when no other place is served more deeply, and when the topic publishes something below the requested
        place: the caller is then told to add the narrower code (more_specific_jurisdiction_available)."""
        published = self.topic_jurisdictions.get(topic_id, set())
        applying = [code for code in published if contains(code, requested)]
        if not applying or any(contains(requested, code) and code != requested for code in published):
            return None
        deepest = max(applying, key=lambda code: code.count("-"))
        elsewhere = sorted(code for code in published if not contains(code, requested) and not contains(requested, code)
                           and code.count("-") > deepest.count("-"))
        return (deepest, elsewhere) if elsewhere else None

    def disclosures(self, results: list[ConceptResolution]) -> list[str]:
        """What the user must be told about the served statements themselves: review status and translation."""
        served = [self.facts[fact.fact_id] for result in results for fact in result.facts]
        notes = []
        unreviewed = [f for f in served if f.provenance.review_status != "human-reviewed"]
        if unreviewed:
            notes.append(f"{len(unreviewed)} of the {len(served)} statements have not been reviewed by a person")
        sources = sorted({self.evidence[e].language.split("-")[0] for f in served for e in f.evidence_ids}
                         - {f.language.split("-")[0] for f in served})
        if sources:
            names = " and ".join(LANGUAGE_NAMES.get(code, code) for code in sources)
            statement_languages = sorted({f.language.split("-")[0] for f in served})
            written = " and ".join(LANGUAGE_NAMES.get(code, code) for code in statement_languages)
            notes.append(f"the statements are {written} summaries of {names} pages, not official translations")
        return notes

    def scope_guidance(self, scope: Scope) -> str:
        """Said with every status: a place the register does not hold moved the request to the broader one."""
        if not scope.not_recognised:
            return ""
        parts = " and ".join(f"the {part} {value!r}" for part, value in scope.not_recognised.items())
        return GUIDANCE_PLACE_NOT_RECOGNISED.format(parts=parts, place=self.place_index.label(scope.code))

    def resolve_guidance(self, status: Status, results: list[ConceptResolution], as_of: date, scope: Scope) -> str | None:
        if status == Status.NEEDS_CONTEXT:
            return GUIDANCE_NEEDS_CONTEXT + self.scope_guidance(scope)
        if status == Status.OUT_OF_COVERAGE:
            return GUIDANCE_OUT_OF_COVERAGE + self.scope_guidance(scope)
        if status == Status.STALE:
            text = GUIDANCE_STALE.format(snapshot=self.freshness.snapshot_date.isoformat(),
                                         max_age=self.freshness.max_age_days, as_of=as_of.isoformat())
        else:
            text = GUIDANCE_SUPPORTED
        notes = self.disclosures(results)
        if notes:
            text += " Tell the user that " + "; and that ".join(notes) + "."
        if any(self.mixes_summary_with_authority(result) for result in results):
            text += GUIDANCE_MIXED_BASIS
        if any(gap.dimension == "more_specific_jurisdiction_not_published" for result in results for gap in result.gaps):
            text += GUIDANCE_NARROWER_NOT_PUBLISHED
        if any(result.lookups for result in results):
            text += GUIDANCE_LOOKUP_OFFER
            zoned = sorted({zone for result in results for offer in result.lookups for zone in offer.zones or []})
            if zoned:
                text += GUIDANCE_LOOKUP_ZONE.format(zones=", ".join(zoned))
        text += self.scope_guidance(scope)
        if any(result.facts for result in results):
            text += " Write the whole answer in the language of the user's question, even when the excerpts are in another language."
        return text

    def mixes_summary_with_authority(self, result: ConceptResolution) -> bool:
        """Whether a concept's served facts rest on a portal summary next to the law or an authority's own page."""
        kinds = {self.facts[fact.fact_id].provenance.basis.kind for fact in result.facts
                 if self.facts[fact.fact_id].provenance.basis is not None}
        return "summary" in kinds and bool(kinds & NORM_OR_AUTHORITY)

    def resolve(self, request: ResolveRequest):
        if len(set(request.concept_ids)) != len(request.concept_ids):
            return argument_error("concept_ids", "Concept IDs must be unique.")
        # Names and short forms ("Wallisellen", "ZH", "261") become the published codes; nothing is guessed, and a
        # name several municipalities share or a city outside the given canton is the caller's to correct.
        try:
            scope = self.place_index.resolve(request.jurisdiction.country, request.jurisdiction.canton, request.jurisdiction.city)
        except PlaceError as exc:
            return argument_error(exc.path, exc.message)
        as_of = request.as_of or date.today()
        withheld: list[str] = []
        results = [self.resolve_concept(concept_id, request, as_of, scope, withheld) for concept_id in request.concept_ids]
        statuses = {result.status for result in results}
        if Status.NEEDS_CONTEXT in statuses:
            status = Status.NEEDS_CONTEXT
        elif Status.STALE in statuses:
            status = Status.STALE
        elif Status.SUPPORTED in statuses:
            status = Status.SUPPORTED
        else:
            status = Status.OUT_OF_COVERAGE
        limitations = list(self.result_limitations)
        # Said only when something was held back: on a fully reviewed release the filter changes nothing.
        if withheld:
            limitations.append(f"This request asked for human-reviewed facts only; {len(withheld)} unreviewed "
                               f"fact(s) were withheld, so the answer is narrower than the published coverage.")
        if status == Status.STALE:
            limitations.append(f"The source snapshot of {self.freshness.snapshot_date.isoformat()} is older than "
                               f"{self.freshness.max_age_days} days on {as_of.isoformat()}; verify the cited pages before relying on the facts.")
        user_facts: list[RequiredUserFact] = []
        decision_rule: DecisionRule | None = None
        for result in results:
            if result.status not in (Status.SUPPORTED, Status.STALE):
                continue
            concept = self.concepts[result.concept_id]
            for item in concept.required_user_facts:
                if all(existing.name != item.name for existing in user_facts):
                    given = request.context.get(item.name)
                    user_facts.append(RequiredUserFact(name=item.name, status=given or item.status, instruction=item.instruction))
            if decision_rule is None and concept.decision_rule is not None:
                decision_rule = DecisionRule(description=concept.decision_rule.description, steps=concept.decision_rule.steps)
        return ResolveResult(release_id=self.release_id, status=status, as_of=as_of, executed_scope=self.executed_scope(scope),
                             freshness=self.freshness, results=results, required_user_facts=user_facts,
                             decision_rule=decision_rule,
                             guidance_for_caller=self.resolve_guidance(status, results, as_of, scope),
                             limitations=limitations)

    def get_evidence(self, request: GetEvidenceRequest):
        error = self.check_release(request.release_id)
        if error:
            return error
        # A fact ID is accepted and expands to the fact's evidence; callers confuse the two and a
        # round trip for a typed error is more expensive than answering.
        selected: list[str] = []
        unknown = []
        evidence = {**self.evidence, **self.graph_evidence}
        for item in request.evidence_ids:
            if item in evidence:
                ids = [item]
            elif item in self.facts:
                ids = self.facts[item].evidence_ids
            else:
                unknown.append(item)
                continue
            selected.extend(i for i in ids if i not in selected)
        if unknown:
            return argument_error("evidence_ids", f"Unknown evidence IDs: {unknown}. Use the evidence_ids or fact_ids returned by resolve.")
        return GetEvidenceResult(
            release_id=self.release_id,
            evidence=[Evidence(evidence_id=e.evidence_id, source_title=e.source_title, publisher=e.publisher, url=e.url,
                               language=e.language, accessed_on=e.accessed_on, original_excerpt=e.original_excerpt,
                               basis=e.basis.label if e.basis else None, **self.page_fields(e))
                      for e in (evidence[item] for item in selected[:5])],
            limitations=self.result_limitations)

    def lookup(self, request: LookupRequest):
        """Forward a lookup to the connector that serves the dataset and put the answer into the release's shape."""
        error = self.check_release(request.release_id)
        if error:
            return error
        registered = sorted(self.connectors.datasets) if self.connectors is not None else []
        if request.dataset_id not in registered:
            return argument_error("dataset_id", f"Unknown dataset_id {request.dataset_id!r}; the registered datasets are "
                                                f"{registered}. Take it from a resolve result's lookups.")
        summary: DatasetSummary = self.connectors.datasets[request.dataset_id].summary
        key = summary.key or "postal_code"
        given = request.zone if request.zone is not None else request.postal_code
        if (request.zone is not None) != (key == "zone"):
            other = "zone" if request.zone is not None else "postal_code"
            return argument_error(other, f"Dataset {request.dataset_id} is keyed by {key}: give {key}, as the offer's "
                                         f"requires says, not {other}.")
        as_of = request.as_of or date.today()
        body = dict(dataset_id=request.dataset_id, **{key: given}, start=(request.start or as_of).isoformat(),
                    end=request.end.isoformat() if request.end else None, limit=request.limit)
        provenance = LookupProvenance(
            publisher=summary.publisher, publisher_url=summary.publisher_url, licence=summary.licence,
            sources=[LookupSource(url=s.url, sha256=s.sha256, bytes=s.bytes, downloaded_on=s.downloaded_on) for s in summary.sources],
            period=Period(start=summary.period.start, end=summary.period.end), dataset_version=summary.dataset_version)
        try:
            answer = self.connectors.lookup(request.dataset_id, body)
        except ConnectorUnavailable as exc:
            gap = CoverageGap(dimension="connector_unavailable", published_values=[], message=(
                f"The connector serving {summary.title} did not answer ({exc}); the facts of the release are unaffected. "
                "Tell the user that the dates cannot be read at the moment."))
            return LookupResult(release_id=self.release_id, dataset_id=summary.dataset_id, dataset_version=summary.dataset_version,
                                type=summary.type, label=summary.label, status=Status.OUT_OF_COVERAGE, as_of=as_of, events=[],
                                truncated=False, provenance=provenance, gaps=[gap], guidance_for_caller=GUIDANCE_LOOKUP_OUT_OF_COVERAGE,
                                limitations=[*self.result_limitations, *summary.limitations])
        if isinstance(answer, ConnectorError):
            return argument_error(answer.path or "lookup", answer.message)
        status = Status.SUPPORTED if answer.status == "SUPPORTED" else Status.OUT_OF_COVERAGE
        named = f"zone {given}" if key == "zone" else f"postal code {given}"
        guidance = (GUIDANCE_LOOKUP_SUPPORTED.format(publisher=summary.publisher, key=named,
                                                    start=summary.period.start.isoformat(), end=summary.period.end.isoformat())
                    if status == Status.SUPPORTED else GUIDANCE_LOOKUP_OUT_OF_COVERAGE)
        return LookupResult(
            release_id=self.release_id, dataset_id=answer.dataset_id, dataset_version=answer.dataset_version,
            type=summary.type, label=summary.label, status=status, as_of=as_of,
            events=[LookupEvent(date=e.date, label=e.label, location=e.location) for e in answer.events],
            truncated=answer.truncated, provenance=provenance,
            gaps=[CoverageGap(dimension=g.dimension, message=g.message, published_values=g.published_values) for g in answer.gaps],
            guidance_for_caller=guidance, limitations=[*self.result_limitations, *summary.limitations])
    def get_knowledge_graph(self, request: GetKnowledgeGraphRequest):
        place = request.jurisdiction
        scope = None
        if place.country or place.canton or place.city:
            try:
                scope = self.place_index.resolve(place.country, place.canton, place.city)
            except PlaceError as exc:
                return argument_error(exc.path, exc.message)
        else:
            scope = self.place_index.resolve() if self.place_index.default_country else None
        return self.graph_index.orient(request, scope, self.release_id, date.today(),
                                       self.executed_scope(scope) if scope is not None else None)

    def dispatch(self, name: str, arguments: dict | None):
        """Validate the arguments against the request model and run the tool."""
        handlers = {"get_coverage": self.get_coverage, "search": self.search, "resolve": self.resolve,
                    "get_evidence": self.get_evidence, "lookup": self.lookup}
        if self.graph_index is not None:
            handlers["get_knowledge_graph"] = self.get_knowledge_graph
        if name not in self.tools():
            return argument_error("name", f"Unknown tool {name!r}.")
        try:
            request = ALL_TOOL_CONTRACTS[name][0].model_validate(arguments or {})
        except ValidationError as exc:
            return invalid(exc)
        return handlers[name](request)
