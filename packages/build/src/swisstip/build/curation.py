"""The curation file of a pack: what the expert (or a migration) writes, read by the build.

`releases/<pack>/curation.yaml` holds the release metadata, the context field
definitions, the institution registry, the page defaults for the basis of
excerpts, the ranking policy, the place files to embed, the topics and the
concepts with their facts.
Each fact cites one or more block ranges of the pack's text dataset; the
build resolves them to exact excerpts. An `anchor` on a citation is written
by the build (or a migration) and lets the build find the excerpt again after
the page was downloaded anew. A `basis` on a citation says what the excerpt
is when the page default does not: the law quoted on an authority's page.
"""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from swisstip.core.release import (BasisKind, ContextFieldSpec, DecisionRule, InstitutionBody, InstitutionLevel, Provenance,
                                   RankingPolicy, RequiredUserFact)

from . import CURATION_SCHEMA_VERSION


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Anchor(Strict):
    """What the excerpt looked like when it was last resolved; see swisstip.extraction.anchors."""

    source_url: str
    document_id: str
    content_sha256: str
    raw_sha256: str
    block_ids: list[str]
    block_hashes: list[str]
    heading_path: list[str]
    start: int
    end: int
    excerpt: str


class BasisSpec(Strict):
    """What an excerpt is, as the curator states it; `level` defaults to the level of the page's institution."""

    level: InstitutionLevel | None = None
    kind: BasisKind
    norm: str | None = Field(default=None, description="Abbreviation, SR or LS number and article; required for act, ordinance, treaty and directive.")
    refers_to: str | None = Field(default=None, description="A norm the excerpt names without reproducing it.")


class CitationRef(Strict):
    document_id: str
    first_block: int = Field(ge=1)
    last_block: int | None = Field(default=None, ge=1)
    anchor: Anchor | None = None
    basis: BasisSpec | None = Field(default=None, description="Overrides the page default of `page_basis` for this excerpt.")


class CuratedInstitution(Strict):
    institution_id: str
    name: str
    native_name: str | None = None
    level: InstitutionLevel
    body: InstitutionBody
    jurisdiction: str
    urls: list[str] = Field(min_length=1, description=(
        "Attribution rules: a host, or a host plus path prefix; the longest rule a page's source URL falls under wins."))


class CuratedFact(Strict):
    fact_id: str
    statement: str
    language: str = "en"
    jurisdiction: str = "CH"
    condition: dict[str, str] | None = None
    valid_from: date | None = None
    valid_through: date | None = None
    provenance: Provenance
    evidence: list[CitationRef] = Field(min_length=1)


class CuratedConcept(Strict):
    concept_id: str
    topic_id: str
    label: str
    description: str
    aliases: list[str] = Field(default_factory=list, description="Authored search terms in any language.")
    questions: list[str] = Field(default_factory=list)
    source_terms: list[str] = Field(default_factory=list, description=(
        "Terms taken verbatim from the cited excerpts, in the language of the source; the build verifies that each "
        "occurs in the concept's evidence and merges them into the released aliases."))
    required_context: list[str] = Field(default_factory=list)
    required_user_facts: list[RequiredUserFact] = Field(default_factory=list)
    decision_rule: DecisionRule | None = None
    notes: list[str] = Field(default_factory=list)
    not_served: list[str] = Field(default_factory=list, description=(
        "Questions about this concept the cited pages do not answer, stated as short noun phrases; served with resolve."))
    facts: list[CuratedFact] = Field(min_length=1)


class CuratedTopic(Strict):
    topic_id: str
    title: str
    description: str
    graph_nodes: list[str] = Field(default_factory=list, description=(
        "Domain nodes of the knowledge graph this topic publishes facts for; Derive and the graph tool follow them."))


class Curation(Strict):
    schema_version: Literal["swiss-tip-curation/v1"] = CURATION_SCHEMA_VERSION
    pack: str
    title: str
    scope_statement: str
    out_of_scope: list[str]
    out_of_scope_response: str
    limitations: list[str] = Field(default_factory=list)
    freshness_max_age_days: int = Field(default=60, ge=1)
    publishers: dict[str, str] = Field(default_factory=dict, description=(
        "Publisher name by host name of the cited pages; the fallback when no institution rule matches a page."))
    institutions: list[CuratedInstitution] = Field(default_factory=list, description=(
        "Who publishes the cited pages; a page matching no rule falls back to its catalogue entry."))
    page_basis: dict[str, BasisSpec] = Field(default_factory=dict, description=(
        "Default basis of every excerpt of a page, by host or host plus path prefix; a citation's own basis wins, "
        "and a page without a rule defaults to guidance at the institution's level."))
    page_languages: dict[str, str] = Field(default_factory=dict, description=(
        "Language of the cited pages whose text record declares none and has no hint (a PDF without /Lang), by host "
        "or host plus path prefix; the longest rule wins. A record's own language always wins."))
    ranking_policy: RankingPolicy | None = Field(default=None, description="Copied into the manifest; the defaults when absent.")
    place_register: str | None = Field(default=None, description=(
        "Place file of the pack's country (written by swisstip-places), relative to the curation file; the build embeds "
        "it so that callers can name a canton or a city instead of its code. A release without one accepts codes only."))
    place_aliases: str | None = Field(default=None, description=(
        "Hand-written aliases for the place file (the country's entry, other-language names, generic words), relative "
        "to the curation file."))
    knowledge_graph: str | None = Field(default=None, description=(
        "Compiled knowledge graph (`graphs/<graph>/graph.json` of the packs repository), relative to the curation "
        "file; the build embeds it so that the server offers get_knowledge_graph."))
    question_languages: list[Literal["en", "de"]] = Field(default_factory=list, description=(
        "Languages in which every concept needs at least one sample question (the pack's query languages); the build "
        "tells a question's language from its function words and refuses a concept that lacks one. Empty: no check."))
    context_fields: dict[str, ContextFieldSpec] = Field(default_factory=dict)
    coverage_policy: Literal["report", "enforce"] = Field(default="report", description=(
        "What the build does with content sections of candidate records that no fact cites and no disposition in "
        "curation-coverage.yaml names: `report` writes curation-coverage.json and goes on, `enforce` fails the build. "
        "Not copied into the release."))
    boilerplate_min_pages: int = Field(default=5, ge=3, description=(
        "A content section whose text stands on this many candidate pages of the same host is site boilerplate: the "
        "coverage stage does not ask for a disposition of it and instead reports the page where a fact cites it. Never "
        "below 3: two pages sharing a paragraph is a duplicate to disposition, not furniture. Not copied into the release."))
    topics: list[CuratedTopic] = Field(min_length=1)
    concepts: list[CuratedConcept] = Field(min_length=1)


def load_curation(path: Path) -> Curation:
    return parse_curation(Path(path).read_text(encoding="utf-8"))


def parse_curation(text: str) -> Curation:
    return Curation.model_validate(yaml.safe_load(text))


class CurationDumper(yaml.SafeDumper):
    """Writes multi-line strings (excerpts) as literal blocks, one source line per line.

    In a quoted or plain scalar every line break of the value costs an empty
    line in the file; a literal block keeps the excerpt readable. PyYAML falls
    back to quoting a value a literal block cannot hold (trailing spaces).
    """


def represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


CurationDumper.add_representer(str, represent_str)


def dump_curation(curation: Curation) -> str:
    data = curation.model_dump(mode="json", exclude_none=True)
    return yaml.dump(data, Dumper=CurationDumper, allow_unicode=True, sort_keys=False, width=110)


def save_curation(path: Path, curation: Curation) -> None:
    Path(path).write_text(dump_curation(curation), encoding="utf-8", newline="\n")
