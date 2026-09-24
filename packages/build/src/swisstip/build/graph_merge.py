"""Merge reader proposals into a graph's curation: the step between the agents' reading and the compile.

A proposal (`graph-reader` agents, one file per domain group) lists nodes and edges whose `evidence` are strings:
`<pack>:<evidence_id>` for an excerpt a pack's release carries, `text:<document_id>:<block>` or
`text:<document_id>:<first>-<last>` for blocks of the graph's own text dataset. The merge turns them into citations
(the pack reference carries the excerpt's hash from the pack's release), and refuses nothing silently: a reference to
an excerpt that does not exist, an edge whose relation or endpoints the vocabulary does not allow, an edge to a node
no one proposed, each is left out and reported. A node the skeleton named is filled in by the first proposal that
supports it; later proposals add their citations and names. A node or edge a person wrote or reviewed, or that Derive
owns, is never changed. Skeleton nodes that no proposal supports are removed with their edges and reported as gaps,
so the compile sees only cited claims. Deterministic; no model.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from swisstip.core.graph import RELATIONS
from swisstip.core.release import Provenance

from .graph_build import PackReleases
from .graph_curation import CuratedEdge, CuratedNode, GraphCitation, GraphCuration, PackEvidenceRef
from .curation import CitationRef

SKELETON = "skeleton"
# A proposed node of these kinds that names an existing one (same label, name or keyword) is that node: agents read
# excerpts and do not know every identifier Derive gave the packs' institutions and laws.
FOLDED_KINDS = ("institution", "law", "source")
UNREVIEWED = "assistant-authored-unreviewed"


@dataclass
class MergeReport:
    nodes_filled: list[str] = field(default_factory=list)
    nodes_added: list[str] = field(default_factory=list)
    nodes_extended: list[str] = field(default_factory=list)
    nodes_folded: list[str] = field(default_factory=list)
    edges_added: list[str] = field(default_factory=list)
    edges_extended: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    unsupported_skeleton: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {name: value for name, value in self.__dict__.items()}


def citation(reference: str, packs: PackReleases, text_ids: set[str] | None) -> GraphCitation | str:
    """The citation a proposal's evidence string names, or the reason it names none."""
    if reference.startswith("text:"):
        parts = reference.split(":")
        if len(parts) != 3:
            return f"malformed text reference {reference!r}"
        _, document_id, blocks = parts
        if text_ids is not None and document_id not in text_ids:
            return f"{reference}: the graph's text dataset has no record {document_id}"
        first, _, last = blocks.partition("-")
        if not first.isdigit() or (last and not last.isdigit()):
            return f"malformed block range in {reference!r}"
        return GraphCitation(text=CitationRef(document_id=document_id, first_block=int(first),
                                              last_block=int(last) if last else None))
    pack, _, evidence_id = reference.partition(":")
    if not evidence_id:
        return f"malformed reference {reference!r}"
    try:
        release = packs.release(pack)
    except Exception as exc:  # an unknown pack is a rejected reference, not a failed merge
        return f"{reference}: {exc}"
    record = next((e for e in release.evidence if e.evidence_id == evidence_id), None)
    if record is None:
        return f"{reference}: not an evidence record of {release.manifest.release_id}"
    return GraphCitation(pack=PackEvidenceRef(pack=pack, evidence_id=evidence_id, excerpt_sha256=record.excerpt_sha256))


def spellings(node) -> set[str]:
    """The folded spellings a node is known by: its label, its names and its keywords."""
    return {" ".join(text.casefold().split()) for text in [node.label, *node.names.values(), *node.keywords] if text}


def same_node(raw: dict, nodes: dict) -> str | None:
    """The existing node a proposed institution, law or source names, when exactly one does."""
    if raw.get("kind") not in FOLDED_KINDS:
        return None
    # The proposal's own label and names only, against the nodes Derive made from the packs' registries (their
    # English and native names): a shared keyword ("Stadt Wallisellen") names a place, not an institution.
    proposed = {" ".join(str(text).casefold().split()) for text in [raw.get("label"), *(raw.get("names") or {}).values()] if text}
    matches = [node_id for node_id, node in nodes.items()
               if node.kind == raw["kind"] and node.provenance.author == "derive" and proposed & spellings(node)]
    return matches[0] if len(matches) == 1 else None


def owned_by_merge(item) -> bool:
    """Items the merge may change: the skeleton's and the agents' own, unreviewed."""
    provenance = item.provenance
    return provenance.review_status == UNREVIEWED and provenance.author != "derive" and not provenance.reviewed_by


def merge_proposals(curation: GraphCuration, proposals: list[tuple[str, dict]], root: Path,
                    text_ids: set[str] | None = None, drop_unsupported: bool = True) -> MergeReport:
    """Merge (author, proposal) pairs into the curation in place; `text_ids` are the documents of the graph's text
    dataset, when it exists."""
    report = MergeReport()
    packs = PackReleases(root)
    nodes = {node.node_id: node for node in curation.nodes}
    edges = {edge.edge_id: edge for edge in curation.edges}
    # The same claim under another identifier (Derive names its edges without the place) is the same edge.
    links = {(edge.from_id, edge.relation, edge.to_id, edge.place): edge for edge in curation.edges}

    def cite(owner: str, references: list) -> list[GraphCitation]:
        result = []
        for reference in references or []:
            found = citation(str(reference), packs, text_ids)
            if isinstance(found, str):
                report.rejected.append(f"{owner}: {found}")
            elif found not in result:
                result.append(found)
        return result

    aliases: dict[str, str] = {}
    for author, proposal in proposals:
        report.gaps.extend(f"{author}: {gap}" for gap in proposal.get("gaps") or [])
        provenance = Provenance(kind="curated-statement", review_status=UNREVIEWED, author=author)
        for raw in proposal.get("nodes") or []:
            node_id = raw.get("node_id", "")
            evidence = cite(f"node {node_id}", raw.get("evidence"))
            if not evidence:
                report.rejected.append(f"node {node_id}: no citable evidence")
                continue
            current = nodes.get(node_id)
            names = {k: v for k, v in (raw.get("names") or {}).items() if v}
            folded_into = same_node(raw, nodes) if current is None else None
            if folded_into is not None:
                aliases[node_id] = folded_into
                target = nodes[folded_into]
                target.evidence.extend(c for c in evidence if c not in target.evidence)
                target.names = {**names, **target.names}
                target.keywords = list(dict.fromkeys([*target.keywords, *(raw.get("keywords") or []), raw.get("label", "")]))
                report.nodes_folded.append(f"{node_id} -> {folded_into}")
                continue
            if current is None:
                try:
                    node = CuratedNode(node_id=node_id, kind=raw["kind"], label=raw["label"], names=names,
                                       summary=raw["summary"], keywords=list(raw.get("keywords") or []),
                                       level=raw.get("level"), place=raw.get("place"), provenance=provenance.model_copy(),
                                       evidence=evidence)
                except Exception as exc:
                    report.rejected.append(f"node {node_id}: {exc}")
                    continue
                nodes[node_id] = node
                curation.nodes.append(node)
                report.nodes_added.append(node_id)
            elif not owned_by_merge(current):
                report.kept.append(node_id)
            elif current.provenance.author == SKELETON:
                current.summary = raw.get("summary") or current.summary
                current.names = names
                current.keywords = list(dict.fromkeys([*current.keywords, *(raw.get("keywords") or [])]))
                current.level = raw.get("level") or current.level
                current.evidence = evidence
                current.provenance = provenance.model_copy()
                report.nodes_filled.append(node_id)
            else:
                current.evidence.extend(c for c in evidence if c not in current.evidence)
                current.names = {**names, **current.names}
                current.keywords = list(dict.fromkeys([*current.keywords, *(raw.get("keywords") or [])]))
                report.nodes_extended.append(node_id)
    for author, proposal in proposals:
        provenance = Provenance(kind="curated-statement", review_status=UNREVIEWED, author=author)
        for raw in proposal.get("edges") or []:
            start, relation, end, place = raw.get("from_id"), raw.get("relation"), raw.get("to_id"), raw.get("place")
            start, end = aliases.get(start, start), aliases.get(end, end)
            edge_id = f"{start}.{relation}.{end}" + (f".{place.lower()}" if place else "")
            if relation not in RELATIONS:
                report.rejected.append(f"edge {edge_id}: unknown relation")
                continue
            if start not in nodes or end not in nodes:
                report.rejected.append(f"edge {edge_id}: an endpoint is not in the graph")
                continue
            allowed_from, allowed_to, _ = RELATIONS[relation]
            if nodes[start].kind not in allowed_from or nodes[end].kind not in allowed_to:
                report.rejected.append(f"edge {edge_id}: {relation} cannot link a {nodes[start].kind} to a {nodes[end].kind}")
                continue
            evidence = cite(f"edge {edge_id}", raw.get("evidence"))
            if not evidence:
                report.rejected.append(f"edge {edge_id}: no citable evidence")
                continue
            current = edges.get(edge_id) or links.get((start, relation, end, place))
            if current is None:
                edge = CuratedEdge(edge_id=edge_id, from_id=start, relation=relation, to_id=end, statement=raw.get("statement", ""),
                                   place=place, provenance=provenance.model_copy(), evidence=evidence)
                edges[edge_id] = edge
                links[(start, relation, end, place)] = edge
                curation.edges.append(edge)
                report.edges_added.append(edge_id)
            elif owned_by_merge(current):
                current.evidence.extend(c for c in evidence if c not in current.evidence)
                report.edges_extended.append(edge_id)
            else:
                report.kept.append(edge_id)
    if drop_unsupported:
        unsupported = {n.node_id for n in curation.nodes if n.provenance.author == SKELETON and not n.evidence}
        role_rules = set(curation.role_rules)
        report.unsupported_skeleton = sorted(unsupported)
        curation.nodes = [n for n in curation.nodes if n.node_id not in unsupported]
        curation.edges = [e for e in curation.edges if e.from_id not in unsupported and e.to_id not in unsupported]
        curation.role_rules = {role: patterns for role, patterns in curation.role_rules.items() if role not in unsupported}
        report.gaps.extend(f"skeleton node {node_id} has no citable evidence" for node_id in sorted(unsupported & role_rules))
    return report


def load_proposals(paths: list[Path]) -> list[tuple[str, dict]]:
    return [(f"graph-reader agent ({Path(path).name.removesuffix('-proposal.yaml')})",
             yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}) for path in paths]
