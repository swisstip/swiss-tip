"""Compile a knowledge graph: `graphs/<graph>/graph.yaml` and its text dataset to a validated, hashed `graph.json`.

Citations resolve as a pack's do. A block range of the graph's own text dataset is cut to an exact excerpt,
attributed to its institution and given its basis by the same functions and rules as a release
(`swisstip.build.release_build`), and relocated through its anchor when the page was fetched anew. A reference to a
pack's evidence is copied from that pack's release, whose build already verified it, after its excerpt hash is
checked. A node or edge whose citation cannot be resolved is dropped with the reason, like a fact; an edge whose
node was dropped goes with it. A name that occurs in none of a node's excerpts fails the compile, as a source term
does. Nothing is guessed. Design: docs/architecture/knowledge-graph.md.

    swisstip-graph compile graphs/<graph>/graph.yaml --text .local/graph-<graph>/text
"""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from swisstip.core.graph import graph_hash, review_counts, validate_graph
from swisstip.core.release import (EvidenceRecord, Freshness, GraphEdge, GraphNode, Institution, KnowledgeGraph, Release,
                                   SourceDocument, load_release, sha256_text)
from swisstip.extraction.anchors import make_anchor

from .curation import Anchor
from .graph_curation import GraphCitation, GraphCuration
from .places import load_place_register
from .release_build import (BuildError, TextDataset, accessed_on, basis_for, institution_for, publisher_for,
                            record_language, resolve_citation)
from .source_terms import missing_source_terms

DROPPING_OUTCOMES = {"dropped", "changed", "ambiguous"}
# The skeleton's placeholder: a node that still carries it was named but never read, and is never compiled.
PLACEHOLDER = "(to be written"


def graph_places(curation: GraphCuration, curation_path: Path) -> set[str] | None:
    """The codes of the place register the graph names, or None when it names none."""
    if not curation.place_register:
        return None
    register = load_place_register((Path(curation_path).resolve().parent / curation.place_register).resolve())
    return {place.code for place in register.places}


class PackReleases:
    """The pack releases Derive's citations point to, loaded once each from `<root>/releases/<pack>/release.json`."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._releases: dict[str, Release] = {}

    def release(self, pack: str) -> Release:
        if pack not in self._releases:
            path = self.root / "releases" / pack / "release.json"
            if not path.is_file():
                raise BuildError(f"the graph cites pack {pack}, but {path} does not exist")
            self._releases[pack] = load_release(path)
        return self._releases[pack]


class Compiler:
    def __init__(self, curation: GraphCuration, dataset: TextDataset | None, packs: PackReleases, update_citations: bool):
        self.curation, self.dataset, self.packs, self.update_citations = curation, dataset, packs, update_citations
        self.documents: dict[str, SourceDocument] = {}
        self.institutions: dict[str, Institution] = {}
        self.evidence: dict[str, EvidenceRecord] = {}
        self.outcomes: Counter = Counter()

    def cite(self, owner: str, citations: list[GraphCitation]) -> tuple[list[str] | None, list[dict]]:
        """The evidence IDs of the owner's citations, or None with the reasons when one cannot be resolved."""
        evidence_ids, report = [], []
        for number, citation in enumerate(citations, 1):
            if citation.pack is not None:
                evidence_id, entry = self.cite_pack(citation)
            else:
                evidence_id, entry = self.cite_text(owner, number, citation)
            report.append(entry)
            if evidence_id is None:
                return None, report
            evidence_ids.append(evidence_id)
        return evidence_ids, report

    def cite_pack(self, citation: GraphCitation) -> tuple[str | None, dict]:
        reference = citation.pack
        release = self.packs.release(reference.pack)
        record = next((e for e in release.evidence if e.evidence_id == reference.evidence_id), None)
        entry = dict(pack=reference.pack, evidence_id=reference.evidence_id, release_id=release.manifest.release_id)
        if record is None:
            self.outcomes["dropped"] += 1
            return None, dict(entry, outcome="dropped", reason=f"{reference.evidence_id} is not in {release.manifest.release_id}")
        if record.excerpt_sha256 != reference.excerpt_sha256:
            self.outcomes["changed"] += 1
            return None, dict(entry, outcome="changed", reason="the pack's excerpt changed since the reference was written")
        self.outcomes["pack"] += 1
        document = next(d for d in release.documents if d.document_id == record.document_id)
        self.add_document(document)
        if record.institution_id:
            self.add_institution(next(i for i in release.institutions if i.institution_id == record.institution_id))
        evidence_id = f"graph-{reference.pack}-{record.evidence_id}"
        self.evidence.setdefault(evidence_id, record.model_copy(update=dict(evidence_id=evidence_id)))
        return evidence_id, dict(entry, outcome="pack")

    def cite_text(self, owner: str, number: int, citation: GraphCitation) -> tuple[str | None, dict]:
        reference = citation.text
        if self.dataset is None:
            raise BuildError(f"{owner} cites {reference.document_id}, but no text dataset was given")
        record, resolution = resolve_citation(self.dataset, reference)
        self.outcomes[resolution["outcome"]] += 1
        entry = dict(document_id=reference.document_id, **{k: v for k, v in resolution.items() if k != "excerpt"})
        if resolution["outcome"] in DROPPING_OUTCOMES:
            return None, entry
        institution, _ = institution_for(self.curation, record)
        if institution is None and self.curation.institutions:
            raise BuildError(f"No institution matches {record['source_url']}; add a rule to the graph's institutions")
        if institution is not None:
            self.add_institution(institution)
        document_id = record["document_id"]
        if document_id not in self.documents:
            self.add_document(SourceDocument(
                document_id=document_id, source_url=record["source_url"], document_url=record["document_url"],
                version_uri=record.get("version_uri"), title=record.get("title") or record["source_url"],
                publisher=institution.name if institution else publisher_for(self.curation, record),
                institution_id=institution.institution_id if institution else None,
                language=record_language(record, self.curation.page_languages), accessed_on=accessed_on(record),
                raw_sha256=record["acquisition"]["raw_sha256"], content_sha256=record["content_sha256"]))
        document = self.documents[document_id]
        basis, basis_rule, warnings = basis_for(self.curation, reference, record, institution, owner, resolution["block_ids"])
        entry.update(basis=basis.label if basis else None, basis_rule=basis_rule, basis_warnings=warnings)
        evidence_id = f"graph-{owner.split(' ', 1)[1]}-{number}"
        self.evidence[evidence_id] = EvidenceRecord(
            evidence_id=evidence_id, document_id=document_id, source_title=document.title, publisher=document.publisher,
            institution_id=document.institution_id, url=record["document_url"], language=document.language,
            accessed_on=document.accessed_on, start_offset=resolution["start"], end_offset=resolution["end"],
            original_excerpt=resolution["excerpt"], excerpt_sha256=sha256_text(resolution["excerpt"]),
            block_ids=resolution["block_ids"], content_sha256=record["content_sha256"],
            raw_sha256=record["acquisition"]["raw_sha256"], basis=basis)
        first = int(resolution["block_ids"][0].rsplit(":b", 1)[1])
        last = int(resolution["block_ids"][-1].rsplit(":b", 1)[1])
        if self.update_citations and resolution["outcome"] != "same-snapshot":
            reference.document_id, reference.first_block, reference.last_block = document_id, first, last
        if self.update_citations or reference.anchor is None:
            reference.anchor = Anchor(**make_anchor(record, first, last))
        return evidence_id, entry

    def add_document(self, document: SourceDocument) -> None:
        known = self.documents.setdefault(document.document_id, document)
        if known != document:
            raise BuildError(f"document {document.document_id} differs between two of the graph's sources")

    def add_institution(self, institution: Institution) -> None:
        known = self.institutions.setdefault(institution.institution_id, institution)
        if known != institution:
            raise BuildError(f"institution {institution.institution_id} differs between the graph and a pack; "
                             "give one of them another identifier")


def build_graph(curation: GraphCuration, text_dir: Path | None, packs_root: Path, *, places: set[str] | None = None,
                created_at: datetime | None = None, update_citations: bool = False) -> tuple[KnowledgeGraph, dict]:
    """`packs_root` holds `releases/<pack>/release.json` for the pack citations; `places` are the codes of the
    graph's place register (`graph_places`). With `update_citations` the citations of the curation model are
    rewritten in place to what they resolved to, anchors included, for the caller to save."""
    dataset = TextDataset(text_dir) if text_dir is not None and Path(text_dir, "index.json").is_file() else None
    compiler = Compiler(curation, dataset, PackReleases(packs_root), update_citations)
    placeholders = [n.node_id for n in curation.nodes if n.summary.strip().startswith(PLACEHOLDER)]
    if placeholders:
        raise BuildError(f"{len(placeholders)} node(s) still carry the skeleton's placeholder summary; merge the "
                         f"readers' proposals or remove them first: {', '.join(placeholders[:8])}")
    withheld, dropped, name_issues = [], [], []
    nodes: list[GraphNode] = []
    report_items = []
    for curated in curation.nodes:
        if curation.serve == "reviewed" and curated.provenance.review_status != "human-reviewed":
            withheld.append(curated.node_id)
            continue
        evidence_ids, citations = compiler.cite(f"node {curated.node_id}", curated.evidence)
        report_items.append(dict(node_id=curated.node_id, citations=citations))
        if evidence_ids is None:
            dropped.append(dict(node_id=curated.node_id, reason="a citation could not be resolved", citations=citations))
            continue
        excerpts = [compiler.evidence[e].original_excerpt for e in evidence_ids]
        missing = missing_source_terms(list(curated.names.values()), excerpts) if excerpts else []
        if missing:
            name_issues.append(f"{curated.node_id}: {missing}")
        nodes.append(GraphNode(node_id=curated.node_id, kind=curated.kind, label=curated.label, names=curated.names,
                               summary=curated.summary, keywords=curated.keywords, level=curated.level,
                               place=curated.place, hosts=curated.hosts, sr_number=curated.sr_number,
                               evidence_ids=evidence_ids, provenance=curated.provenance))
    if name_issues:
        raise BuildError("Names must occur verbatim in the node's cited excerpts; not found: " + "; ".join(name_issues))
    kept = {node.node_id for node in nodes}
    edges: list[GraphEdge] = []
    for curated in curation.edges:
        if curation.serve == "reviewed" and curated.provenance.review_status != "human-reviewed":
            withheld.append(curated.edge_id)
            continue
        if curated.from_id not in kept or curated.to_id not in kept:
            dropped.append(dict(edge_id=curated.edge_id, reason="one of its nodes is not in the graph"))
            continue
        evidence_ids, citations = compiler.cite(f"edge {curated.edge_id}", curated.evidence)
        report_items.append(dict(edge_id=curated.edge_id, citations=citations))
        if evidence_ids is None:
            dropped.append(dict(edge_id=curated.edge_id, reason="a citation could not be resolved", citations=citations))
            continue
        edges.append(GraphEdge(edge_id=curated.edge_id, from_id=curated.from_id, relation=curated.relation,
                               to_id=curated.to_id, statement=curated.statement, place=curated.place,
                               evidence_ids=evidence_ids, provenance=curated.provenance))
    snapshot = max((d.accessed_on for d in compiler.documents.values()), default=None) or date.today()
    graph = KnowledgeGraph(
        graph_id=curation.graph, title=curation.title, created_at=created_at or datetime.now(UTC),
        freshness=Freshness(snapshot_date=snapshot, max_age_days=curation.freshness_max_age_days,
                            stale_from=snapshot + timedelta(days=curation.freshness_max_age_days)),
        serve=curation.serve, review_statuses={}, nodes=nodes, edges=edges,
        institutions=sorted(compiler.institutions.values(), key=lambda i: i.institution_id),
        documents=sorted(compiler.documents.values(), key=lambda d: d.document_id),
        evidence=sorted(compiler.evidence.values(), key=lambda e: e.evidence_id), content_sha256="0" * 64)
    graph.review_statuses = review_counts(graph)
    graph.content_sha256 = graph_hash(graph)
    issues = validate_graph(graph, places)
    if issues:
        raise BuildError("Compiled graph does not validate: " + "; ".join(issues[:8]))
    report = dict(graph_id=graph.graph_id, compiled_at=graph.created_at.isoformat(), content_sha256=graph.content_sha256,
                  nodes=len(nodes), edges=len(edges), documents=len(graph.documents), evidence=len(graph.evidence),
                  institutions=len(graph.institutions), node_kinds=dict(Counter(n.kind for n in nodes)),
                  relations=dict(Counter(e.relation for e in edges)), review_statuses=graph.review_statuses,
                  citation_outcomes=dict(compiler.outcomes), withheld=withheld, dropped=dropped,
                  snapshot_date=snapshot.isoformat(), text_dataset=str(Path(text_dir).resolve()) if dataset else None,
                  items=report_items)
    return graph, report
