"""The knowledge graph: the orientation a caller reads before it searches.

A graph says which level of the state sets the rules of a domain, who carries
them out, which office a person deals with in their canton or commune, which
law and which source are authoritative, and what trips a generic answer up.
Every node summary and every edge is a claim resting on exact excerpts, with
provenance and review status like a fact. The relation vocabulary is code: a
new domain, canton or source is data, a new kind of link is a code change.

The models live in swisstip.core.release (a release embeds a graph); this
module holds the vocabulary, the hash and the validator, which the compiler
and the server both run and which fail closed. Design:
docs/architecture/knowledge-graph.md.
"""

import json
import re
from datetime import timedelta
from pathlib import Path

from .basis import level_of_jurisdiction
from .release import GraphNode, GraphNodeKind, GraphRelation, KnowledgeGraph, Release, sha256_text

EVIDENCE_PREFIX = "graph-"
NODE_ID = re.compile(r"^[a-z]+\.[a-z0-9][a-z0-9._-]*$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
JURISDICTION = re.compile(r"^CH(?:-[A-Z]{2}(?:-\d{1,4})?)?$")
LANGUAGE = re.compile(r"^[a-z]{2,3}$")
SUMMARY_LIMIT = 400
STATEMENT_LIMIT = 300

# relation: (kinds it may start from, kinds it may point to), and what it means for a caller.
RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], str]] = {
    "rules_set_by": (frozenset({"domain"}), frozenset({"level", "institution"}),
                     "the level or body that makes the rules of the domain"),
    "executed_by": (frozenset({"domain"}), frozenset({"role", "institution", "level"}),
                    "who carries the rules out"),
    "decided_by": (frozenset({"domain"}), frozenset({"role", "institution", "level"}),
                   "who takes the decision"),
    "approved_by": (frozenset({"domain"}), frozenset({"role", "institution"}),
                    "who must approve on top of the deciding body"),
    "first_contact": (frozenset({"domain"}), frozenset({"role", "institution"}),
                      "where a person turns first"),
    "legal_basis": (frozenset({"domain", "role", "level", "principle"}), frozenset({"law"}),
                    "the law the domain or body rests on"),
    "authoritative_source": (frozenset({"domain", "law", "role", "level"}), frozenset({"source"}),
                             "where the binding or most exact text is published"),
    "published_by": (frozenset({"domain"}), frozenset({"institution"}),
                     "an institution whose pages the release cites for the domain"),
    "varies_by": (frozenset({"domain"}), frozenset({"level"}),
                  "the rule or procedure differs from one canton or commune to the next"),
    "pitfall": (frozenset({"domain"}), frozenset({"pitfall"}),
                "a mistake a generic answer makes"),
    "see_also": (frozenset({"domain"}), frozenset({"domain"}),
                 "a related domain a question often touches"),
    "instance": (frozenset({"role"}), frozenset({"institution"}),
                 "the institution that plays the role for a place"),
    "part_of": (frozenset({"place", "institution", "level"}), frozenset({"place", "level"}),
                "containment"),
    "governed_by": (frozenset({"level"}), frozenset({"principle"}),
                    "a constitutional principle the level works under"),
}
# Kinds that name a place: an institution speaks for one, a place is one.
PLACED_KINDS = frozenset({"place", "institution"})
# Links a place register already grounds: a canton lies in the country, a commune in its canton.
UNCITED_RELATIONS = frozenset({"part_of"})


class GraphInvalid(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__(f"{len(issues)} graph issue(s): " + "; ".join(issues[:5]))
        self.issues = issues


def contains(outer: str, inner: str) -> bool:
    """Jurisdiction containment, as for facts: CH contains every canton, a canton its municipalities."""
    return inner == outer or inner.startswith(outer + "-")


def graph_hash(graph: KnowledgeGraph) -> str:
    body = dict(nodes=[n.model_dump(mode="json") for n in graph.nodes],
                edges=[e.model_dump(mode="json") for e in graph.edges],
                institutions=[i.model_dump(mode="json") for i in graph.institutions],
                documents=[d.model_dump(mode="json") for d in graph.documents],
                evidence=[e.model_dump(mode="json") for e in graph.evidence])
    return sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def review_counts(graph: KnowledgeGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in [*graph.nodes, *graph.edges]:
        counts[item.provenance.review_status] = counts.get(item.provenance.review_status, 0) + 1
    return dict(sorted(counts.items()))


def is_uncited_link(edge, nodes: dict[str, GraphNode]) -> bool:
    start, end = nodes.get(edge.from_id), nodes.get(edge.to_id)
    return (edge.relation in UNCITED_RELATIONS and start is not None and end is not None
            and start.kind in ("place", "level") and end.kind in ("place", "level"))


def validate_graph(graph: KnowledgeGraph, places: set[str] | None = None) -> list[str]:
    """Every rule a compiled graph must meet. `places`, when given, is the set of codes of the place register the
    graph is served with: every place a node or an edge names must be in it."""
    issues: list[str] = []
    if graph_hash(graph) != graph.content_sha256:
        issues.append("content_sha256 does not match the graph body")
    freshness = graph.freshness
    if freshness.stale_from != freshness.snapshot_date + timedelta(days=freshness.max_age_days):
        issues.append("freshness.stale_from is not snapshot_date plus max_age_days")
    if review_counts(graph) != dict(sorted(graph.review_statuses.items())):
        issues.append("review_statuses differ from the nodes and edges")
    for name, identifiers in (("edges", [e.edge_id for e in graph.edges]),
                              ("institutions", [i.institution_id for i in graph.institutions]),
                              ("documents", [d.document_id for d in graph.documents]),
                              ("evidence", [e.evidence_id for e in graph.evidence])):
        if len(set(identifiers)) != len(identifiers):
            issues.append(f"duplicate identifiers in {name}")
        for identifier in identifiers:
            if not IDENTIFIER.match(identifier):
                issues.append(f"malformed identifier in {name}: {identifier!r}")
    node_ids = [n.node_id for n in graph.nodes]
    if len(set(node_ids)) != len(node_ids):
        issues.append("duplicate identifiers in nodes")
    nodes = {n.node_id: n for n in graph.nodes}
    evidence = {e.evidence_id: e for e in graph.evidence}
    documents = {d.document_id: d for d in graph.documents}
    institutions = {i.institution_id: i for i in graph.institutions}
    cited: set[str] = set()

    def check_place(owner: str, code: str | None) -> None:
        if code is None:
            return
        if not JURISDICTION.match(code):
            issues.append(f"{owner} has a malformed place {code!r}")
        elif places is not None and code not in places:
            issues.append(f"{owner} names place {code}, which the place register does not list")

    def check_evidence(owner: str, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            if evidence_id not in evidence:
                issues.append(f"{owner} references unknown evidence {evidence_id}")
            cited.add(evidence_id)

    for node in graph.nodes:
        owner = f"node {node.node_id}"
        if not NODE_ID.match(node.node_id) or not node.node_id.startswith(node.kind + "."):
            issues.append(f"{owner} is not named `{node.kind}.<slug>`")
        if not node.label.strip():
            issues.append(f"{owner} has no label")
        if not node.summary.strip():
            issues.append(f"{owner} has no summary")
        elif len(node.summary) > SUMMARY_LIMIT:
            issues.append(f"{owner} summary is longer than {SUMMARY_LIMIT} characters")
        for language, name in node.names.items():
            if not LANGUAGE.match(language) or not name.strip():
                issues.append(f"{owner} has a malformed name {language}={name!r}")
        check_place(owner, node.place)
        if node.kind in PLACED_KINDS and node.place is None:
            issues.append(f"{owner} is a {node.kind} without a place")
        if node.place is not None and JURISDICTION.match(node.place) and node.kind in PLACED_KINDS:
            expected = level_of_jurisdiction(node.place)
            if node.level is not None and node.level != expected:
                issues.append(f"{owner} is {node.level} but its place {node.place} is {expected}")
        if node.kind == "place" and node.place and node.node_id != "place." + node.place.lower():
            issues.append(f"{owner} should be named place.{node.place.lower()}")
        if node.kind != "place" and not node.evidence_ids:
            issues.append(f"{owner} cites no evidence")
        check_evidence(owner, node.evidence_ids)
    seen_links: set[tuple] = set()
    for edge in graph.edges:
        owner = f"edge {edge.edge_id}"
        start, end = nodes.get(edge.from_id), nodes.get(edge.to_id)
        if start is None:
            issues.append(f"{owner} starts at unknown node {edge.from_id}")
        if end is None:
            issues.append(f"{owner} points to unknown node {edge.to_id}")
        if edge.from_id == edge.to_id:
            issues.append(f"{owner} links a node to itself")
        allowed_from, allowed_to, _ = RELATIONS[edge.relation]
        if start is not None and start.kind not in allowed_from:
            issues.append(f"{owner}: {edge.relation} cannot start at a {start.kind}")
        if end is not None and end.kind not in allowed_to:
            issues.append(f"{owner}: {edge.relation} cannot point to a {end.kind}")
        link = (edge.from_id, edge.relation, edge.to_id, edge.place)
        if link in seen_links:
            issues.append(f"{owner} repeats a link another edge states")
        seen_links.add(link)
        if not edge.statement.strip():
            issues.append(f"{owner} has an empty statement")
        elif len(edge.statement) > STATEMENT_LIMIT:
            issues.append(f"{owner} statement is longer than {STATEMENT_LIMIT} characters")
        check_place(owner, edge.place)
        if edge.relation == "instance":
            if edge.place is None:
                issues.append(f"{owner} is an instance without a place")
            elif end is not None and end.place is not None and end.place != edge.place:
                issues.append(f"{owner} holds for {edge.place} but its institution speaks for {end.place}")
        if not edge.evidence_ids and not is_uncited_link(edge, nodes):
            issues.append(f"{owner} cites no evidence")
        check_evidence(owner, edge.evidence_ids)
    used_documents = set()
    for item in graph.evidence:
        owner = f"evidence {item.evidence_id}"
        if not item.evidence_id.startswith(EVIDENCE_PREFIX):
            issues.append(f"{owner} does not start with {EVIDENCE_PREFIX!r}")
        if item.evidence_id not in cited:
            issues.append(f"{owner} is not cited by any node or edge")
        if item.document_id not in documents:
            issues.append(f"{owner} references unknown document {item.document_id}")
        used_documents.add(item.document_id)
        if item.end_offset <= item.start_offset or len(item.original_excerpt) != item.end_offset - item.start_offset:
            issues.append(f"{owner} offsets do not match its excerpt length")
        if sha256_text(item.original_excerpt) != item.excerpt_sha256:
            issues.append(f"{owner} excerpt hash mismatch")
        if item.institution_id is not None and item.institution_id not in institutions:
            issues.append(f"{owner} names unknown institution {item.institution_id}")
        document = documents.get(item.document_id)
        if document and (document.content_sha256 != item.content_sha256 or document.raw_sha256 != item.raw_sha256):
            issues.append(f"{owner} hashes differ from document {document.document_id}")
    for document in graph.documents:
        if document.document_id not in used_documents:
            issues.append(f"document {document.document_id} is not cited")
        if document.accessed_on > freshness.snapshot_date:
            issues.append(f"document {document.document_id} was accessed after the snapshot date")
        if document.institution_id is not None and document.institution_id not in institutions:
            issues.append(f"document {document.document_id} names unknown institution {document.institution_id}")
    for institution in graph.institutions:
        check_place(f"institution {institution.institution_id}", institution.jurisdiction)
    if graph.serve == "reviewed":
        for item in [*graph.nodes, *graph.edges]:
            if item.provenance.review_status != "human-reviewed":
                issues.append(f"{getattr(item, 'node_id', None) or item.edge_id} is served unreviewed "
                              "although the graph serves reviewed items only")
    return issues


def check_embedded_graph(release: Release) -> list[str]:
    """The graph a release carries: valid against the release's place register, and bridged from its topics."""
    graph = release.knowledge_graph
    topics_with_bridges = [t for t in release.topics if t.graph_nodes]
    if graph is None:
        return [f"topic {t.topic_id} names graph nodes but the release carries no graph" for t in topics_with_bridges]
    places = {p.code for p in release.place_register.places} if release.place_register else None
    issues = [f"knowledge_graph: {issue}" for issue in validate_graph(graph, places)]
    nodes = {n.node_id: n for n in graph.nodes}
    for topic in topics_with_bridges:
        for node_id in topic.graph_nodes:
            node = nodes.get(node_id)
            if node is None or node.kind != "domain":
                issues.append(f"topic {topic.topic_id} names {node_id}, which is not a domain of the graph")
    clashes = {e.evidence_id for e in release.evidence} & {e.evidence_id for e in graph.evidence}
    if clashes:
        issues.append(f"knowledge_graph evidence identifiers clash with the release's: {', '.join(sorted(clashes)[:5])}")
    return issues


def assert_valid_graph(graph: KnowledgeGraph, places: set[str] | None = None) -> None:
    issues = validate_graph(graph, places)
    if issues:
        raise GraphInvalid(issues)


def dump_graph(graph: KnowledgeGraph) -> str:
    return json.dumps(graph.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def load_graph(path: Path) -> KnowledgeGraph:
    return KnowledgeGraph.model_validate_json(Path(path).read_text(encoding="utf-8"))


def node_kinds() -> tuple[str, ...]:
    return GraphNodeKind.__args__


def relations() -> tuple[str, ...]:
    return GraphRelation.__args__
