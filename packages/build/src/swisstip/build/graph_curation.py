"""The curation file of a knowledge graph: what agents, the provider route and the curator write, read by the compiler.

`graphs/<graph>/graph.yaml` in the packs repository holds the graph's metadata, the institution registry and page
rules of its own sources (the same fields and rules as a pack's curation), the role rules Derive applies, and the
nodes and edges. Every node and edge cites excerpts like a fact does: block ranges of the graph's own text dataset
(`CitationRef`), or, for what Derive takes from a pack, an evidence record of that pack's release
(`PackEvidenceRef`). The compiler resolves both into `graph.json`. Design: docs/architecture/knowledge-graph.md.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from swisstip.core.release import GraphNodeKind, GraphRelation, InstitutionLevel, Provenance

from .curation import BasisSpec, CitationRef, CuratedInstitution, CurationDumper, Strict

GRAPH_CURATION_SCHEMA_VERSION = "swiss-tip-graph-curation/v1"


class PackEvidenceRef(Strict):
    """An excerpt a pack's release already carries, verified there; the compiler copies it into the graph."""

    pack: str
    evidence_id: str
    excerpt_sha256: str = Field(description="The excerpt's hash when the reference was written; a changed excerpt fails the compile.")


class GraphCitation(Strict):
    """Exactly one of `text` (a block range of the graph's own text dataset) and `pack`."""

    text: CitationRef | None = None
    pack: PackEvidenceRef | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "GraphCitation":
        if (self.text is None) == (self.pack is None):
            raise ValueError("a graph citation names either a text block range or a pack evidence record")
        return self


class CuratedNode(Strict):
    node_id: str
    kind: GraphNodeKind
    label: str
    names: dict[str, str] = Field(default_factory=dict, description=(
        "Names in the languages of the sources, by ISO 639 code; the compiler checks each against the cited excerpts."))
    summary: str
    keywords: list[str] = Field(default_factory=list)
    level: InstitutionLevel | None = None
    place: str | None = None
    hosts: list[str] = Field(default_factory=list)
    sr_number: str | None = None
    provenance: Provenance
    evidence: list[GraphCitation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CuratedEdge(Strict):
    edge_id: str
    from_id: str
    relation: GraphRelation
    to_id: str
    statement: str
    place: str | None = None
    provenance: Provenance
    evidence: list[GraphCitation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GraphCuration(Strict):
    schema_version: Literal["swiss-tip-graph-curation/v1"] = GRAPH_CURATION_SCHEMA_VERSION
    graph: str
    title: str
    freshness_max_age_days: int = Field(default=90, ge=1)
    serve: Literal["all", "reviewed"] = Field(default="all", description=(
        "`reviewed` compiles only what a person confirmed; `all` serves every item with its review status."))
    place_register: str | None = Field(default=None, description=(
        "Place file of the graph's country, relative to this file; every place a node or edge names must be in it."))
    publishers: dict[str, str] = Field(default_factory=dict)
    institutions: list[CuratedInstitution] = Field(default_factory=list, description=(
        "Who publishes the graph's own sources, with the same attribution rules as a pack's curation."))
    page_basis: dict[str, BasisSpec] = Field(default_factory=dict)
    page_languages: dict[str, str] = Field(default_factory=dict)
    packs: list[str] = Field(default_factory=list, description=(
        "The packs Derive reads (`releases/<pack>/curation.yaml` and `release.json`)."))
    role_rules: dict[str, list[str]] = Field(default_factory=dict, description=(
        "Role node to institution-ID patterns (`*` is a wildcard): Derive links each matching institution as the "
        "role's instance for the place it speaks for."))
    nodes: list[CuratedNode] = Field(min_length=1)
    edges: list[CuratedEdge] = Field(default_factory=list)


def load_graph_curation(path: Path) -> GraphCuration:
    return GraphCuration.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def dump_graph_curation(curation: GraphCuration) -> str:
    data = curation.model_dump(mode="json", exclude_none=True)
    return yaml.dump(data, Dumper=CurationDumper, allow_unicode=True, sort_keys=False, width=110)


def save_graph_curation(path: Path, curation: GraphCuration) -> None:
    Path(path).write_text(dump_graph_curation(curation), encoding="utf-8", newline="\n")
