"""Contract models for the Swiss TIP (Swisscom Trusted Information Platform) MCP tools (schema version 4).

These Pydantic models define the wire shape of the four tools described in
section 4.3 of the functional specification: get_coverage, search, resolve and
get_evidence, plus the typed tool error. The mock server and the real server
serve instances of the same models, so callers see one contract.

Print the JSON Schema bundle of every model:

    python -m swisstip.core.contracts --output docs/architecture/tool-contracts.schema.json
"""

import argparse
from datetime import date
from enum import Enum
import json
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

SCHEMA_VERSION = "swiss-tip/v4"


class Strict(BaseModel):
    """Every contract model rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class Status(str, Enum):
    """Result status of a resolution; see section 4.3 of the specification."""

    SUPPORTED = "SUPPORTED"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"
    OUT_OF_COVERAGE = "OUT_OF_COVERAGE"
    STALE = "STALE"


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    RELEASE_UNAVAILABLE = "RELEASE_UNAVAILABLE"
    OPERATIONAL_ERROR = "OPERATIONAL_ERROR"


GapDimension = Literal[
    "jurisdiction_not_covered",
    "more_specific_jurisdiction_available",
    "more_specific_jurisdiction_not_published",
    "date_outside_coverage",
    "concept_not_published",
    "context_not_covered",
    "review_status_not_met",
    # The five of the lookup tool (docs/architecture/dataset-connectors.md, section 6).
    "postal_code_not_covered",
    "zone_not_covered",
    "period_not_published",
    "no_dates_in_range",
    "connector_unavailable",
]

# The wire contract repeats the release format's review statuses instead of importing them, so the
# served shape does not move when the storage model does; a test in packages/core keeps them equal.
ReviewStatus = Literal[
    "human-reviewed",
    "assistant-authored-unreviewed",
    "automatically-derived-unreviewed",
    "model-candidate-automated-review",
]


class Jurisdiction(Strict):
    """Where the user lives, in parts, at the most specific level known. Every part takes a name, in English or in
    the local language, or a code; the server turns it into the code the facts carry and echoes what it understood
    in executed_scope. The field names of schema v3 (country_code, canton_code, municipality_id) are still accepted."""

    country: str | None = Field(default=None, validation_alias=AliasChoices("country", "country_code"), description=(
        "Country name or ISO 3166-1 code, for example Switzerland, Schweiz or CH. Omit it for the country this "
        "server covers; another country resolves OUT_OF_COVERAGE."))
    canton: str | None = Field(
        default=None, validation_alias=AliasChoices("canton", "canton_code", "state", "region"), description=(
            "Canton name, abbreviation or ISO 3166-2 code, for example Zurich, Zürich, ZH or CH-ZH. Not needed when "
            "the city is given, unless several cantons have a city of that name."))
    city: str | None = Field(
        default=None, validation_alias=AliasChoices("city", "municipality", "municipality_id", "commune", "town"), description=(
            "The municipality (political commune) the user lives in, not a district, a quarter or a postcode: its "
            "name, for example Wallisellen or Zurich, or its code, the canton code plus the BFS number (CH-ZH-261; "
            "the number alone only next to the canton)."))


PLACE_ALIASES = {str(alias): name for name, field in Jurisdiction.model_fields.items()
                 for alias in (field.validation_alias.choices if isinstance(field.validation_alias, AliasChoices)
                               else [name])}


class Placed(Strict):
    """A request that takes a jurisdiction.

    Small models flatten the nested argument and send {"city": "Wallisellen"} next to the other arguments; the
    rejection then costs a call or two before the same question is asked again. A place part sent at the top level
    is folded into `jurisdiction` instead, under every name that field accepts. The schema is unchanged: it
    advertises the nested object, which is what a caller should send, and executed_scope echoes what was understood.
    """

    @model_validator(mode="before")
    @classmethod
    def fold_place_parts(cls, data):
        if not isinstance(data, dict) or not data.keys() & PLACE_ALIASES.keys():
            return data
        jurisdiction = data.get("jurisdiction") or {}
        if not isinstance(jurisdiction, dict):
            return data
        given = {PLACE_ALIASES[key]: value for key, value in jurisdiction.items() if key in PLACE_ALIASES}
        folded, rest = dict(jurisdiction), {}
        for key, value in data.items():
            part = None if key in cls.model_fields else PLACE_ALIASES.get(key)
            if part is None:
                rest[key] = value
            elif part not in given:
                folded[key] = given[part] = value
            elif given[part] != value:
                raise ValueError(f"The {part} is given twice, as {value!r} next to jurisdiction and as "
                                 f"{given[part]!r} inside it; give the place once, inside jurisdiction.")
        return {**rest, "jurisdiction": folded}


class ExecutedScope(Strict):
    """The jurisdiction a resolve ran for, as the server understood the request: the codes the facts are matched
    against and the register's official names for them."""

    country_code: str | None = Field(default=None, description="ISO 3166-1 code; absent for a country named in words that is not served.")
    canton_code: str | None = Field(default=None, description="ISO 3166-2 canton code, for example CH-ZH.")
    municipality_id: str | None = Field(default=None, description="Canton code plus BFS municipality number, for example CH-ZH-261.")
    country: str | None = Field(default=None, description="Official name of the country, or the caller's words for one not served.")
    canton: str | None = Field(default=None, description="Official name of the canton.")
    city: str | None = Field(default=None, description="Official name of the municipality.")
    not_recognised: dict[str, str] = Field(default_factory=dict, exclude_if=lambda value: not value, description=(
        "Parts of the request the place register does not hold, by field, as the caller sent them. The request ran "
        "for the broader place above; correct the part (the municipality's official name, not a district) and "
        "resolve again if the narrower place matters. Omitted when every part was recognised."))


class FreshnessPolicy(Strict):
    snapshot_date: date = Field(description="Date on which the source pages were saved.")
    max_age_days: int = Field(description="Days after the snapshot during which results are current.")
    stale_from: date = Field(description="First date on which results report STALE.")


# --- get_coverage -----------------------------------------------------------


class GetCoverageRequest(Strict):
    release_id: str | None = Field(default=None, description="Release to read; omit for the active release.")
    parent_id: str | None = Field(
        default=None, description="A topic_id from the root page to list its concepts; omit for the root page.")


class TopicSummary(Strict):
    """A topic on the coverage root. The jurisdictions it publishes for are not repeated here: the root's own
    `jurisdictions` lists every one of them, and the topic page carries them per concept. The root has to stay small
    enough for one call to settle whether a question is in scope."""

    topic_id: str
    title: str
    concept_count: int


class QueryLanguage(Strict):
    """A language that lexical search matches, with what the release indexes in it."""

    code: str = Field(description="ISO 639-1 code.")
    rank: int = Field(ge=1, description="1 is the preferred language for search queries.")
    indexed: str = Field(description="What the release indexes in this language, which is why it ranks where it does.")


class CoverageRoot(Strict):
    """Compact root page: about one kilobyte, enough to refuse an outside question."""

    release_id: str
    schema_version: str
    scope_statement: str
    out_of_scope: list[str]
    out_of_scope_response: str
    jurisdictions: list[str]
    languages: list[str]
    query_languages: list[QueryLanguage] | None = Field(default=None, description=(
        "The languages to write search queries in, best first. Send a question in one of them as asked; translate "
        "the key terms of a question in any other language into the first one before searching."))
    freshness: FreshnessPolicy
    topics: list[TopicSummary]
    institution_levels: dict[str, int] | None = Field(default=None, description=(
        "Cited documents per level of the institution that published them (federal, cantonal, municipal)."))
    basis_kinds: dict[str, int] | None = Field(default=None, description=(
        "Facts per kind of the excerpt they rest on: act, ordinance, treaty, directive, guidance, directory, summary."))
    limitations: list[str]


class ContextField(Strict):
    type: Literal["string"] = "string"
    enum: list[str] | None = None
    description: str


class ConceptSummary(Strict):
    """One line of a topic listing: enough to pick a concept. The aliases serve search only, and the allowed
    context values arrive with a search hit or with resolve's missing_context, so neither is listed here."""
    concept_id: str
    topic_id: str
    label: str
    description: str
    jurisdictions: list[str]
    required_context: list[str]


class CoverageTopic(Strict):
    release_id: str
    topic_id: str
    title: str
    concepts: list[ConceptSummary]
    limitations: list[str]


# --- search -----------------------------------------------------------------


class SearchRequest(Placed):
    query: str = Field(min_length=1, description=(
        "Question or key terms to find published concepts, in one of the server's query languages (get_coverage "
        "query_languages; English and German unless the server names others). Send a question in one of them as "
        "asked, in one search; translate the key terms of a question in any other language into the first one named "
        "before searching. Optional "
        "local semantic search also accepts other languages, less reliably."))
    limit: int = Field(default=3, ge=1, le=10, description=(
        "Maximum hits. Three is enough for a question about one subject; raise it only to explore, at most ten."))
    # No description of its own: next to the model's it would be inlined as an allOf, which small models read poorly.
    # The tool description and the server's note on the field (ReleaseService.tool_input_schema) say what it does.
    jurisdiction: Jurisdiction = Field(default_factory=Jurisdiction)


class SearchHit(Strict):
    concept_id: str
    topic_id: str
    label: str
    description: str
    jurisdictions: list[str]
    required_context: list[str]
    context_schema: dict[str, ContextField] = Field(
        default_factory=dict,
        description="Allowed values and meaning of every context field, so resolve can be called with the context filled in.")
    score: float
    matched_on: list[str]


class MatchSignals(Strict):
    lexical_share: float = Field(ge=0, le=1, description=(
        "Share of the query's rarity-weighted words that the label, aliases or questions of the best concept matched; "
        "a word the index has never seen counts as unmatched."))
    anchored_weight: float = Field(ge=0, description=(
        "The same matched mass in units of the rarest possible word, so a long question with names and places is "
        "judged by what it matched, not by what it did not."))
    best_semantic_score: float | None = Field(default=None, ge=-1, le=1, description=(
        "Best raw cosine similarity over every concept before the candidate cut; absent without semantic search."))


class PublishedElsewhere(Strict):
    """A concept the query found that is published for other places only, so it cannot apply where the user lives."""

    concept_id: str
    label: str
    jurisdictions: list[str]


class SearchResult(Strict):
    release_id: str
    query: str
    results: list[SearchHit]
    executed_scope: ExecutedScope | None = Field(default=None, description=(
        "The place the search ran for, as the server understood the request's jurisdiction; absent when none was given."))
    published_elsewhere: list[PublishedElsewhere] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "Concepts that would have been among the first hits but are published for other cantons or municipalities "
        "only: they do not apply at the user's place, so do not resolve them and carry nothing over from them. When "
        "no result answers the question, tell the user that this service does not publish it for their place. Omitted "
        "when empty or when no jurisdiction was given."))
    match_strength: Literal["strong", "weak", "none"] | None = Field(default=None, description=(
        "Whether the query's distinctive words reached a published concept: strong (resolve the relevant hits), weak "
        "(the hits rest on incidental words or a nearby subject; the question probably lies outside the scope, decline "
        "it unless a hit is clearly its subject) or none (no candidate). Older mock responses omit it."))
    match_signals: MatchSignals | None = Field(default=None, description="The two signals behind match_strength.")
    scope_statement: str | None = Field(default=None, description=(
        "The release's scope statement, sent with weak and none results so the caller can decline without another call."))
    matched_count: int | None = Field(default=None, ge=0, description=(
        "Candidates selected by the active retrieval method before applying limit; not a count of all relevant concepts."))
    truncated: bool = Field(default=False, description="Some selected candidates were omitted by limit.")
    retrieval_mode: Literal["lexical", "hybrid", "lexical-fallback"] = "lexical"
    ranking_model: str | None = None
    ranking_model_digest: str | None = None
    fallback_reason: str | None = Field(default=None, description="Why configured semantic search was unavailable.")
    guidance_for_caller: str | None = Field(
        default=None, description="How to interpret the ranked matches; search does not establish applicability or complete coverage.")
    limitations: list[str]


# --- resolve ----------------------------------------------------------------


class ResolveRequest(Placed):
    concept_ids: list[str] = Field(min_length=1, max_length=5, description="Concept IDs from get_coverage or search.")
    jurisdiction: Jurisdiction = Field(default_factory=Jurisdiction)
    as_of: date | None = Field(default=None, description="Applicability date; omit for today.")
    context: dict[str, str] = Field(default_factory=dict, description="Values for the concept's context_schema fields.")
    reviewed_only: bool = Field(default=False, description=(
        "Serve only facts a person has confirmed (review_status 'human-reviewed'). Use it when the question is "
        "critical enough that an unreviewed statement is not good enough; a concept whose facts are all unreviewed "
        "then resolves OUT_OF_COVERAGE with a review_status_not_met gap instead of returning them."))


LEVEL_DESCRIPTION = "Level of the state of the institution that published the page: federal, cantonal or municipal."
INSTITUTION_JURISDICTION_DESCRIPTION = "Whom that institution speaks for: CH, a canton code such as CH-ZH, or a municipality code."


class Citation(Strict):
    """One cited page, listed once per concept however many of its facts rest on it."""

    evidence_ids: list[str] = Field(description=(
        "The evidence IDs of this concept's facts that rest on this page; a fact's evidence_ids name its page here."))
    source_title: str
    publisher: str
    level: str | None = Field(default=None, description=LEVEL_DESCRIPTION)
    jurisdiction: str | None = Field(default=None, description=INSTITUTION_JURISDICTION_DESCRIPTION)
    url: str
    language: str
    accessed_on: date


REVIEW_STATUS_DESCRIPTION = (
    "Whether a person has confirmed the statement against its cited excerpt. 'human-reviewed' means a named "
    "reviewer did; every other value means no person has. Weigh a statement that is not human-reviewed "
    "accordingly, and for a critical question either quote the cited excerpt or resolve again with reviewed_only.")
BASIS_DESCRIPTION = (
    "What the cited excerpt is, stated independently of the page that carries it: 'Federal act: AIG, SR 142.20, "
    "Art. 12' when the excerpt is the text of a norm (also 'Federal ordinance', 'International agreement', "
    "'Cantonal directive'), '<Level> authority guidance' for an authority's own explanation such as a FAQ, "
    "'<Level> authority directory' for a list of offices, 'Portal summary of <level> rules' for a plain-language "
    "restatement by a portal; ', referring to <norm>' names a norm the excerpt cites without reproducing it. Where a "
    "summary or guidance and the law differ, the law's excerpt is the more exact source; name the basis to the user "
    "when it matters.")


class Fact(Strict):
    fact_id: str
    statement: str
    jurisdiction: str
    condition: dict[str, str] | None = None
    valid_from: date | None = None
    valid_through: date | None = None
    evidence_ids: list[str]
    review_status: ReviewStatus | None = Field(default=None, description=(
        REVIEW_STATUS_DESCRIPTION + " Absent when the concept states one review status for all its facts."))
    reviewed_on: date | None = Field(default=None, description="Date of the human review; absent when not human-reviewed.")
    reviewed_by: str | None = Field(default=None, description="Person who confirmed the statement; absent when not human-reviewed.")
    basis: str | None = Field(default=None, description=(
        BASIS_DESCRIPTION + " Absent when the concept states one basis for all its facts."))


class MissingContext(Strict):
    field: str
    options: list[str] | None
    hint: str


class CoverageGap(Strict):
    dimension: GapDimension
    message: str
    published_values: list[str]


# --- lookup -----------------------------------------------------------------
# Additive on schema v4: the tool of the dataset connectors (docs/architecture/dataset-connectors.md). It is served
# only when a connector is registered, so it has a table of its own next to TOOL_CONTRACTS.


class Period(Strict):
    start: date = Field(description="First published date, inclusive.")
    end: date = Field(description="Last published date, inclusive; next year's file is not published before it exists.")


class LookupOffer(Strict):
    """What resolve says about a dataset a caller can look up for the concept."""

    dataset_id: str = Field(description="What to send to lookup.")
    type: str = Field(description="calendar: dates for a postal code or a collection zone.")
    title: str
    label: str
    jurisdiction: str = Field(description="The municipality the dataset is published for.")
    requires: list[str] = Field(description=(
        "Input fields lookup needs, postal_code or zone: ask the user for them when the conversation has not given them."))
    accepts: list[str] = Field(description="Optional input fields of lookup.")
    period: Period
    publisher: str
    zones: list[str] | None = Field(default=None, exclude_if=lambda value: value is None, description=(
        "When requires names zone: the collection zones the publisher uses, as published."))
    zone_lookup_url: str | None = Field(default=None, exclude_if=lambda value: value is None, description=(
        "When requires names zone: the publisher's page where the user finds their zone from the address. Give it to "
        "the user; never guess a zone from an address."))


class LookupRequest(Strict):
    dataset_id: str = Field(description="From a resolve result's lookups.")
    postal_code: str | None = Field(default=None, pattern=r"^\d{4}$", description=(
        "Four digits, as the user gave it, when the offer requires postal_code."))
    zone: str | None = Field(default=None, pattern=r"^\S(?:.{0,38}\S)?$", description=(
        "The collection zone as the user gave it, when the offer requires zone."))
    as_of: date | None = Field(default=None, description="The date the next dates count from; omit for today.")
    start: date | None = Field(default=None, description="First date wanted; omit for as_of.")
    end: date | None = Field(default=None, description="Last date wanted, inclusive; omit for the end of the published period.")
    limit: int = Field(default=3, ge=1, le=60, description="1 for the next date only, up to 60 for a whole year.")
    release_id: str | None = None

    @model_validator(mode="after")
    def one_key(self) -> "LookupRequest":
        if (self.postal_code is None) == (self.zone is None):
            raise ValueError("give exactly one of postal_code and zone, the one the offer's requires names")
        return self


class LookupEvent(Strict):
    date: date
    label: str
    location: str | None = Field(default=None, description="Where the event happens, when the publisher names a place.")


class LookupSource(Strict):
    url: str
    sha256: str
    bytes: int
    downloaded_on: date


class LookupProvenance(Strict):
    publisher: str
    publisher_url: str = Field(description="The publisher's page for the dataset; cite it.")
    licence: str
    sources: list[LookupSource]
    period: Period
    dataset_version: str


class LookupResult(Strict):
    release_id: str
    dataset_id: str
    dataset_version: str
    type: str
    label: str
    status: Status = Field(description="SUPPORTED with at least one event, else OUT_OF_COVERAGE with a gap.")
    as_of: date
    events: list[LookupEvent]
    truncated: bool = Field(description="More dates than limit fell into the range.")
    provenance: LookupProvenance
    gaps: list[CoverageGap]
    guidance_for_caller: str | None = None
    limitations: list[str]


class ConceptResolution(Strict):
    concept_id: str
    status: Status
    answering_jurisdiction: str | None = None
    review_status: ReviewStatus | None = Field(default=None, description=(
        REVIEW_STATUS_DESCRIPTION + " Stated here once when every fact of the concept shares it, together with "
        "reviewed_on and reviewed_by; absent when the facts differ, and then each fact states its own."))
    reviewed_on: date | None = Field(default=None, description="Date of the human review shared by every fact.")
    reviewed_by: str | None = Field(default=None, description="Person who confirmed every fact of the concept.")
    basis: str | None = Field(default=None, description=(
        BASIS_DESCRIPTION + " Stated here once when every served fact of the concept shares it; absent when the "
        "facts differ, and then each fact states its own."))
    facts: list[Fact] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list, description=(
        "One entry per cited page: the page whose excerpts carry the strongest basis first (the law before a FAQ, a "
        "FAQ before a portal summary), then in the order the facts first cite it."))
    missing_context: list[MissingContext] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)
    not_served: list[str] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "What users commonly ask about this concept that the release does not publish (for example a fee, a document "
        "list or live availability). Say that the service does not publish it and point to the cited page; do not "
        "fill it in from general knowledge. Omitted when empty."))
    lookups: list[LookupOffer] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "Datasets served behind this concept by a registered connector, for a date the facts cannot give (the next "
        "collection day for a postal code). Call lookup with the dataset_id and the fields it requires; never derive "
        "such a date from the facts. Omitted when no dataset stands behind the concept for the resolved place."))


def page_citations(cited: list[tuple[str, dict]]) -> list[Citation]:
    """Group (evidence_id, page fields) pairs into one citation per page, in first-cited order.

    A page is the same when every citation field matches, so two saved copies of one URL with different access
    dates stay apart.
    """
    pages: dict[tuple, Citation] = {}
    for evidence_id, page in cited:
        key = (page["url"], page["source_title"], page["publisher"], page["language"], page["accessed_on"],
               page.get("level"), page.get("jurisdiction"))
        if key not in pages:
            pages[key] = Citation(evidence_ids=[], **page)
        if evidence_id not in pages[key].evidence_ids:
            pages[key].evidence_ids.append(evidence_id)
    return list(pages.values())


def shared_basis(facts: list[Fact]) -> tuple[list[Fact], str | None]:
    """Move the basis label to the concept when every fact carries the same one; facts without a basis stay as they are."""
    labels = {fact.basis for fact in facts}
    if len(labels) != 1 or labels == {None}:
        return facts, None
    return [fact.model_copy(update=dict(basis=None)) for fact in facts], labels.pop()


def shared_review(facts: list[Fact]) -> tuple[list[Fact], dict]:
    """Move the review fields to the concept when every fact carries the same ones.

    Returns the facts, without their review fields when they were moved, and the fields for the concept (empty
    when the facts differ).
    """
    reviews = {(fact.review_status, fact.reviewed_on, fact.reviewed_by) for fact in facts}
    if len(reviews) != 1:
        return facts, {}
    status, reviewed_on, reviewed_by = reviews.pop()
    if status is None:
        return facts, {}
    cleared = [fact.model_copy(update=dict(review_status=None, reviewed_on=None, reviewed_by=None)) for fact in facts]
    return cleared, dict(review_status=status, reviewed_on=reviewed_on, reviewed_by=reviewed_by)


class RequiredUserFact(Strict):
    name: str
    status: str
    instruction: str


class DecisionRule(Strict):
    description: str
    steps: list[str]


class ResolveResult(Strict):
    release_id: str
    status: Status
    as_of: date
    executed_scope: ExecutedScope
    freshness: FreshnessPolicy
    results: list[ConceptResolution]
    required_user_facts: list[RequiredUserFact] = Field(default_factory=list)
    decision_rule: DecisionRule | None = None
    guidance_for_caller: str | None = None
    limitations: list[str]


# --- get_evidence -----------------------------------------------------------


class GetEvidenceRequest(Strict):
    evidence_ids: list[str] = Field(min_length=1, max_length=5, description="Evidence IDs exactly as returned by resolve.")
    release_id: str | None = None


class Evidence(Strict):
    evidence_id: str
    source_title: str
    publisher: str
    level: str | None = Field(default=None, description=LEVEL_DESCRIPTION)
    jurisdiction: str | None = Field(default=None, description=INSTITUTION_JURISDICTION_DESCRIPTION)
    basis: str | None = Field(default=None, description=BASIS_DESCRIPTION)
    url: str
    language: str
    accessed_on: date
    original_excerpt: str


class GetEvidenceResult(Strict):
    release_id: str
    evidence: list[Evidence]
    limitations: list[str]


# --- errors -----------------------------------------------------------------


class ValidationIssue(Strict):
    path: str
    message: str


class ErrorBody(Strict):
    code: ErrorCode
    issues: list[ValidationIssue]


class ToolError(Strict):
    error: ErrorBody


TOOL_DESCRIPTIONS = {
    "get_coverage": (
        "Discover what this server covers. A question normally needs two calls in total: search, then resolve. "
        "Call get_coverage with no arguments only when you are unsure whether the question is in scope at all: the "
        "root page (about 5 KB) returns the active release_id, a scope_statement, an out_of_scope list, the covered "
        "jurisdictions and languages, the query languages for search, the freshness window and the topics. If the question matches out_of_scope "
        "or lies outside the scope_statement (another topic, another country), follow out_of_scope_response and "
        "make no further calls. With parent_id set to a topic_id it lists that topic's concepts with their "
        "jurisdictions and required context fields; search is the shorter way to the same concept_ids."),
    "search": (
        "Find ranked published concepts for a question or key terms, in ONE call per user question: send one query, "
        "never two searches side by side and never the same question again in another language or wording, which "
        "rarely finds other concepts. Lexical search matches only the server's query "
        "languages (named in the server instructions and in get_coverage query_languages; English and German unless "
        "named otherwise): send a question in one of them as asked, in one search and not again in another query "
        "language, and translate the key terms of a question in any other language into the first one named before "
        "searching. Optional local semantic search can also find "
        "concepts in other languages, less reliably. When the user's canton or municipality is known, give it as "
        "jurisdiction, as for resolve: the hits are then the concepts that can apply there, and a concept published "
        "for other places only is named in published_elsewhere instead (do not resolve it; if no hit answers the "
        "question, say that this service does not publish it for the user's place). The result names retrieval_mode "
        "and any fallback. An empty list means no candidates matched this retrieval method; it does not prove the "
        "subject is outside coverage. matched_count and truncated describe the selected candidates, not completeness "
        "for the question. Scores rank candidates, not confidence or applicability; they include a small prior for "
        "the basis of a concept's facts (the law above guidance above a portal summary) and for their review, never "
        "for the level of the publisher. Each hit carries concept_id, label, description, "
        "jurisdictions, required_context and context_schema (the allowed values of every context field with their "
        "meaning). Select the relevant concepts and supply the known jurisdiction and context to resolve, which "
        "determines which published facts match. A search hit only says the concept is a retrieval candidate. "
        "match_strength says whether the question's distinctive words reached a published concept at all: on strong, "
        "resolve the relevant hits; on weak or none, the hits rest on incidental words or a nearby subject and the "
        "result carries the scope_statement, so compare the question with it and decline in this same turn, without "
        "calling get_coverage, unless a hit is clearly the question's subject."),
    "resolve": (
        "Return the published facts, evidence citations and, where published, the user facts still needed and a "
        "decision rule for up to five concept_ids in ONE call. Give where the user lives inside the jurisdiction "
        "argument, at the most specific level you know, in the words the user used: {\"concept_ids\": [...], "
        "\"jurisdiction\": {\"canton\": \"Zurich\", \"city\": \"Wallisellen\"}}. Names in English or the local language and codes are both accepted; the server turns "
        "them into codes and echoes what it understood in executed_scope, where not_recognised names a part it "
        "could not place (the request then ran for the broader place). A federal concept "
        "answers for any canton, a cantonal concept for its municipalities, never upward or sideways. Give as_of "
        "(normally today) and every required context field of each concept, derived from the question (the "
        "search hits list the allowed values). Per concept the status is SUPPORTED, NEEDS_CONTEXT "
        "(missing_context names the fields; derive them before asking the user), OUT_OF_COVERAGE (gaps name the "
        "dimension and the published values to use instead; say the request is not covered) or STALE (the facts "
        "say what the pages published on the snapshot date, not what holds on as_of: present them as published "
        "then and tell the user to check the page; never resolve again with an earlier as_of to avoid it). The "
        "facts and citations in the result are enough to answer: build the answer from the statements, cite only "
        "the returned URLs, add no document, fee, contact, link or procedure the statements do not contain (not_served "
        "names what the release does not publish), collect the required_user_facts before computing any date, and "
        "follow guidance_for_caller, including what to tell the user about review status and translation. Do not "
        "call search or get_evidence again unless the user asks for a verbatim quote. Citations list each cited "
        "page once with the evidence_ids that rest on it, the publisher, its level (federal, cantonal, municipal) "
        "and the jurisdiction it speaks for, the page with the strongest basis first. Every served fact states its "
        "basis (on the fact, or once on the concept when all its facts share it): what the cited excerpt is, "
        "such as 'Federal act: AIG, SR 142.20, Art. 12', 'Cantonal authority guidance' or 'Portal summary of "
        "federal rules', whichever page carries it; name it to the user when it matters, and prefer the law's "
        "excerpt where a summary differs from it. Every concept with facts states review_status, once for "
        "all its facts or, when they differ, on each fact: 'human-reviewed' means a named person confirmed the "
        "statement against its excerpt, any other value means no person has. Say so when it matters to the user, and for a critical question set "
        "reviewed_only to withhold unreviewed statements rather than pass them on unqualified."),
    "get_evidence": (
        "Read the full original excerpt, publisher (with its level and jurisdiction), the basis of the excerpt, "
        "language and citation URL for up to five evidence_ids, exactly as returned by resolve (or fact_ids, "
        "which return the evidence of that fact). Only needed to quote the source verbatim; resolve already "
        "returns the statements and citations."),
    "lookup": (
        "Read the published dates of a dataset a resolve result offered in lookups, for one four-digit postal code: "
        "for example the next collection days of a kind of waste. The facts of the concept give the rule; the "
        "dataset gives the dates, taken unchanged from the publisher's open-data file and not reviewed by a "
        "person. Send the dataset_id of the offer, the postal code the user gave (ask for it when the conversation "
        "has not given it; never guess one from the municipality), as_of (normally today), and limit 1 for the "
        "next date only, or start and end for a range such as a month. SUPPORTED returns the dates oldest first; "
        "OUT_OF_COVERAGE names the gap: a postal code the dataset does not hold (published_values lists the ones it "
        "holds), a range outside the published period (typically next year before its file exists), no date left "
        "in the range, or a connector that is not running (the facts are unaffected). State the dates as published "
        "by the named publisher for that postal code, cite provenance.publisher_url, and follow "
        "guidance_for_caller."),
}

TOOL_CONTRACTS = {
    "get_coverage": (GetCoverageRequest, CoverageRoot | CoverageTopic),
    "search": (SearchRequest, SearchResult),
    "resolve": (ResolveRequest, ResolveResult),
    "get_evidence": (GetEvidenceRequest, GetEvidenceResult),
}

# Served only while a dataset connector is registered; part of the schema bundle, not of every server's tool list.
CONNECTOR_TOOL_CONTRACTS = {
    "lookup": (LookupRequest, LookupResult),
}


def tool_input_schema(model):
    """JSON Schema of a request model with local definitions inlined.

    Some clients (for example XML-style tool parsers on small models) read a
    property's type directly and do not resolve references, so a bare $ref can
    turn an object argument into a string. Inlining keeps every tool argument
    self-describing without changing the models.
    """
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value, active=()):
        if isinstance(value, list):
            return [expand(item, active) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if reference is None:
            return {key: expand(item, active) for key, item in value.items()}
        if not reference.startswith("#/$defs/") or reference in active:
            raise ValueError("Tool input schemas require acyclic local definitions")
        name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        resolved = expand(definitions[name], (*active, reference))
        siblings = expand({key: item for key, item in value.items() if key != "$ref"}, active)
        if resolved.keys() & siblings.keys():
            return {**resolved, "allOf": [*resolved.get("allOf", []), siblings]}
        return {**resolved, **siblings}

    return expand(schema)


def tool_output_schema(result_type):
    return {"type": "object", **TypeAdapter(result_type | ToolError).json_schema()}


def schema_bundle():
    return {
        "schema_version": SCHEMA_VERSION,
        "tools": {
            name: {"description": TOOL_DESCRIPTIONS[name],
                   "input": tool_input_schema(request),
                   "output": tool_output_schema(result)}
            for name, (request, result) in {**TOOL_CONTRACTS, **CONNECTOR_TOOL_CONTRACTS}.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the JSON Schema bundle of the tool contracts.")
    parser.add_argument("--output", help="Write the bundle to this file instead of stdout.")
    parser.add_argument("--check", help="Exit 1 if this file differs from the current bundle.")
    args = parser.parse_args(argv)
    text = json.dumps(schema_bundle(), indent=2, ensure_ascii=True) + "\n"
    if args.check:
        with open(args.check, encoding="utf-8") as handle:
            if handle.read() != text:
                print(f"{args.check} is out of date; regenerate it with --output.")
                return 1
        print(f"{args.check} is current.")
        return 0
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"Wrote {args.output}")
        return 0
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
