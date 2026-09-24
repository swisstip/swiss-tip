"""The knowledge release: one versioned, hashed JSON file per pack.

Section 4.2 of the functional specification: a catalog (topics and concepts
with labels, descriptions and aliases), facts (short statements with a
jurisdiction, an optional condition, a validity window and evidence
references), evidence (exact excerpts of saved pages with offsets, URL,
publisher, language, access date and hashes) and a manifest (release ID,
freshness policy, scope statement, covered jurisdictions and languages,
content hash). Every fact carries its provenance kind and review status, so a
caller can tell a curated statement from unreviewed material.

Since 16 September 2026 a release also names, per page, the institution that
published it (level of the state, jurisdiction) and, per excerpt, its basis:
whether the excerpt is the text of an act, an ordinance or a treaty, a
directive, an authority's own guidance, a directory entry or a portal
summary. The fields are optional and left out of the dump when absent, so
releases built before them keep their bytes and their content digest.

Since 18 September 2026 a release can carry a place register: the official
names of the country, its cantons and its municipalities with their
jurisdiction codes, and the other spellings accepted for them. The server
reads it to turn the place a caller names ("Wallisellen", "Kanton Zürich")
into the code the facts carry. It decides which facts a request reaches, so
it is part of the content digest; a release without one accepts codes only.
"""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import RELEASE_SCHEMA_VERSION

ProvenanceKind = Literal["curated-statement", "source-section", "model-candidate"]
ReviewStatus = Literal["human-reviewed", "assistant-authored-unreviewed", "automatically-derived-unreviewed",
                       "model-candidate-automated-review"]
InstitutionLevel = Literal["federal", "cantonal", "municipal"]
InstitutionBody = Literal["administration", "law_collection", "portal", "public_law_body"]
BasisKind = Literal["act", "ordinance", "treaty", "directive", "guidance", "directory", "summary"]


def absent(value) -> bool:
    return value is None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextFieldSpec(Strict):
    type: Literal["string"] = "string"
    enum: list[str] | None = None
    description: str


class Freshness(Strict):
    snapshot_date: date = Field(description="Latest access date of the cited pages.")
    max_age_days: int = Field(ge=1)
    stale_from: date


class Institution(Strict):
    """Who published a page: an office of the administration, the law collection, a portal or a public-law body."""

    institution_id: str
    name: str = Field(description="English display name; served as the publisher of the institution's pages.")
    native_name: str | None = Field(default=None, exclude_if=absent, description="The institution's own name in its language.")
    level: InstitutionLevel = Field(description="The tier of the state the institution belongs to.")
    body: InstitutionBody
    jurisdiction: str = Field(description="Whom the institution speaks for: CH, a canton code or a municipality code.")


class Place(Strict):
    """A country, canton or municipality a caller can name; its level follows from the code."""

    code: str = Field(description="Jurisdiction code: CH, a canton (CH-ZH) or a municipality (CH-ZH-261).")
    name: str = Field(description="The official name as the register spells it; the name the server echoes.")
    aliases: list[str] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "Other spellings accepted on input (English and other-language names); never served."))


class PlaceRegister(Strict):
    """The places of the release's country, taken from an official register, with where and when it was read."""

    title: str
    publisher: str
    url: str
    accessed_on: date
    raw_sha256: str = Field(description="Hash of the register response the places were read from.")
    generic_words: list[str] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "Words a caller may put around a name without changing the place: 'Canton of', 'Stadt', 'Gemeinde'."))
    places: list[Place] = Field(min_length=1)


class Basis(Strict):
    """What an excerpt is, independent of the page that carries it."""

    level: InstitutionLevel = Field(description="Level of the body that enacted the norm or wrote the text.")
    kind: BasisKind
    norm: str | None = Field(default=None, exclude_if=absent, description=(
        "Identity of the norm (abbreviation, SR or LS number, article); required for act, ordinance, treaty and directive."))
    refers_to: str | None = Field(default=None, exclude_if=absent, description=(
        "A norm the excerpt names without reproducing it, for example the article a FAQ answer rests on."))
    label: str = Field(description="The served label, composed from the other fields by swisstip.core.basis.basis_label.")


class RankingPolicy(Strict):
    """Internal ranking weights: a prior on search scores from the basis of a concept's facts and their review."""

    floor: float = Field(default=0.7, ge=0, le=1, description="The prior spans floor to 1: score times floor + (1 - floor) * authority.")
    aggregate: Literal["max", "mean"] = Field(default="max", description="How a concept's authority follows from its facts.")
    basis_kind: dict[str, float] = Field(description="Weight per basis kind.")
    provenance_kind: dict[str, float] = Field(description="Weight per provenance kind of a statement.")
    review_status: dict[str, float] = Field(description="Weight per review status of a statement.")

    @model_validator(mode="after")
    def weights_are_known_and_positive(self) -> "RankingPolicy":
        for table, allowed in ((self.basis_kind, get_args(BasisKind)), (self.provenance_kind, get_args(ProvenanceKind)),
                               (self.review_status, get_args(ReviewStatus))):
            for key, weight in table.items():
                if key not in allowed:
                    raise ValueError(f"unknown ranking key {key!r}")
                if not 0 < weight <= 1:
                    raise ValueError(f"ranking weight of {key!r} must lie in (0, 1], not {weight}")
        return self


class SourceDocument(Strict):
    document_id: str
    source_url: str
    document_url: str
    version_uri: str | None = None
    title: str
    publisher: str
    institution_id: str | None = Field(default=None, exclude_if=absent, description="The institution that published the page.")
    language: str
    accessed_on: date
    raw_sha256: str
    content_sha256: str


class Topic(Strict):
    topic_id: str
    title: str
    description: str


class RequiredUserFact(Strict):
    name: str
    status: str
    instruction: str


class DecisionRule(Strict):
    description: str
    steps: list[str]


class Concept(Strict):
    concept_id: str
    topic_id: str
    label: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    jurisdictions: list[str] = Field(description="Sorted jurisdictions of the concept's facts.")
    required_context: list[str] = Field(default_factory=list)
    context_schema: dict[str, ContextFieldSpec] = Field(default_factory=dict)
    fact_ids: list[str] = Field(min_length=1)
    required_user_facts: list[RequiredUserFact] = Field(default_factory=list)
    decision_rule: DecisionRule | None = None
    notes: list[str] = Field(default_factory=list)
    # Left out of the dump (and so of the content hash) when empty, so releases built before the field existed keep
    # their bytes and digest.
    not_served: list[str] = Field(default_factory=list, exclude_if=lambda value: not value, description=(
        "What a user is likely to ask about this concept that the release does not publish (a fee, a document list, "
        "live availability); served with resolve so a caller says so instead of filling the gap from memory."))


class Provenance(Strict):
    kind: ProvenanceKind
    review_status: ReviewStatus
    author: str
    source: str | None = Field(default=None, description="Where the statement came from, for example a predecessor release.")
    reviewed_on: date | None = None
    reviewed_by: str | None = Field(default=None, description="Person who confirmed the statement; required when review_status is human-reviewed.")
    notes: list[str] = Field(default_factory=list)
    basis: Basis | None = Field(default=None, exclude_if=absent, description=(
        "The strongest basis among the fact's cited excerpts, set by the build; what the statement rests on."))

    @model_validator(mode="after")
    def reviewer_is_named(self) -> "Provenance":
        if self.review_status == "human-reviewed" and not (self.reviewed_by or "").strip():
            raise ValueError("review_status 'human-reviewed' requires a non-empty reviewed_by")
        return self


class FactRecord(Strict):
    fact_id: str
    concept_id: str
    statement: str
    language: str = "en"
    jurisdiction: str
    condition: dict[str, str] | None = None
    valid_from: date | None = None
    valid_through: date | None = None
    evidence_ids: list[str] = Field(min_length=1)
    provenance: Provenance


class EvidenceRecord(Strict):
    evidence_id: str
    document_id: str
    source_title: str
    publisher: str
    institution_id: str | None = Field(default=None, exclude_if=absent, description="The institution of the cited page.")
    url: str
    language: str
    accessed_on: date
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    original_excerpt: str
    excerpt_sha256: str
    block_ids: list[str] = Field(min_length=1)
    content_sha256: str = Field(description="Normalized text hash of the cited text record.")
    raw_sha256: str = Field(description="Hash of the saved response the record was extracted from.")
    basis: Basis | None = Field(default=None, exclude_if=absent, description="What this excerpt is.")


class Manifest(Strict):
    schema_version: Literal["swiss-tip-release/v1"] = RELEASE_SCHEMA_VERSION
    release_id: str
    pack: str
    title: str
    created_at: datetime
    scope_statement: str
    out_of_scope: list[str]
    out_of_scope_response: str
    jurisdictions: list[str]
    languages: list[str]
    freshness: Freshness
    provenance_kinds: dict[str, int]
    review_statuses: dict[str, int]
    institution_levels: dict[str, int] = Field(default_factory=dict, exclude_if=lambda value: not value,
                                               description="Cited documents per level of their institution.")
    basis_kinds: dict[str, int] = Field(default_factory=dict, exclude_if=lambda value: not value,
                                        description="Facts per kind of their strongest basis.")
    ranking_policy: RankingPolicy | None = Field(default=None, exclude_if=absent,
                                                  description="The weights the server ranks with; never served.")
    limitations: list[str]
    content_sha256: str = Field(description="SHA-256 over the canonical JSON of documents, topics, concepts, facts and evidence.")


class Release(Strict):
    manifest: Manifest
    documents: list[SourceDocument]
    topics: list[Topic]
    concepts: list[Concept]
    facts: list[FactRecord]
    evidence: list[EvidenceRecord]
    institutions: list[Institution] = Field(default_factory=list, exclude_if=lambda value: not value,
                                            description="The institutions the documents name; absent in older releases.")
    place_register: PlaceRegister | None = Field(default=None, exclude_if=absent, description=(
        "The places a caller can name instead of a jurisdiction code; absent in older releases, which accept codes only."))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(release: Release) -> str:
    body = dict(documents=[d.model_dump(mode="json") for d in release.documents],
                topics=[t.model_dump(mode="json") for t in release.topics],
                concepts=[c.model_dump(mode="json") for c in release.concepts],
                facts=[f.model_dump(mode="json") for f in release.facts],
                evidence=[e.model_dump(mode="json") for e in release.evidence])
    # Older releases have no institutions; the part enters the hash only when present, so their digests stand.
    if release.institutions:
        body["institutions"] = [i.model_dump(mode="json") for i in release.institutions]
    # The place register decides which code a named place becomes, and so which facts a request reaches.
    if release.place_register is not None:
        body["place_register"] = release.place_register.model_dump(mode="json")
    return sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def dump_release(release: Release) -> str:
    return json.dumps(release.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def load_release(path: Path) -> Release:
    return Release.model_validate_json(Path(path).read_text(encoding="utf-8"))


def release_schema() -> dict:
    return Release.model_json_schema()
