"""Derive from packs: the part of a knowledge graph that follows from data the packs already hold.

Pure functions, no model, no network. From the place register: the country, its cantons and the municipalities an
institution speaks for, with `part_of` links. From each pack's release: its institutions (law collections and portals
become `source` nodes, the rest `institution` nodes), the laws its excerpts rest on (parsed from the basis norm,
"AIG, SR 142.20, Art. 12" becomes `law.aig`), `authoritative_source` from a law to the collection that publishes it,
and, for every domain a pack topic bridges to (`graph_nodes` in the pack's curation), `legal_basis` to the laws and
`published_by` to the institutions its facts cite. The curator's `role_rules` add `instance` links from a role to
the institutions that play it for their place. Every derived claim cites the pack excerpt it follows from.

`derive` returns a diff against the curation; `apply_derivation` writes it. An item a person or an agent wrote, or
anyone reviewed, is never changed: only items still marked as Derive's own (author `derive`, status
`automatically-derived-unreviewed`) are added or updated. Running it twice changes nothing the second time.
"""

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from swisstip.core.basis import LEVEL_WORD
from swisstip.core.release import EvidenceRecord, Institution, PlaceRegister, Provenance, Release

from .graph_build import PackReleases
from .graph_curation import CuratedEdge, CuratedNode, GraphCitation, GraphCuration, PackEvidenceRef

AUTHOR = "derive"
STATUS = "automatically-derived-unreviewed"
SOURCE_BODIES = {"law_collection", "portal"}
NORM_TAIL = re.compile(r",\s*(?:Art\.|art\.|Annex|Anhang|Rz\b|title\b|§|cpv\.|Abs\.)")
NORM_WITH_NUMBER = re.compile(r"(?P<abbreviation>.+?),\s*(?P<number>(?:SR|RS|LS)\s*[\d.]+)$")
BODY_WORD = {"law_collection": "law collection", "portal": "information portal", "public_law_body": "public-law body",
             "administration": "authority"}


def slug(text: str) -> str:
    folded = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "é": "e", "è": "e", "à": "a"}))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


@dataclass(frozen=True)
class LawIdentity:
    node_id: str
    label: str
    number: str | None


def law_identity(norm: str, level: str, jurisdiction: str) -> LawIdentity:
    """The law a norm names, without its article: "AIG, SR 142.20, Art. 12" is `law.aig` (SR 142.20); a cantonal
    or municipal law carries its place in the identifier, since two cantons use the same abbreviations."""
    identity = NORM_TAIL.split(norm, maxsplit=1)[0].strip().rstrip(",")
    match = NORM_WITH_NUMBER.fullmatch(identity)
    abbreviation, number = (match["abbreviation"].strip(), match["number"]) if match else (identity, None)
    if number is None and re.fullmatch(r"[A-Za-z/-]+ [\d.]+[\d]", identity):
        number = identity
    suffix = "" if level == "federal" else "-" + jurisdiction.lower()
    label = f"{abbreviation} ({number})" if number and number != abbreviation else abbreviation
    return LawIdentity(node_id=f"law.{slug(abbreviation)}{suffix}", label=label, number=number)


def place_label(code: str, names: dict[str, str]) -> str:
    name = names.get(code, code)
    depth = code.count("-")
    return "Switzerland" if depth == 0 else f"Canton of {name} ({code})" if depth == 1 else f"{name} ({code})"


def derived_provenance(source: str) -> Provenance:
    return Provenance(kind="source-section", review_status=STATUS, author=AUTHOR, source=source)


def pack_citation(pack: str, item: EvidenceRecord) -> GraphCitation:
    return GraphCitation(pack=PackEvidenceRef(pack=pack, evidence_id=item.evidence_id, excerpt_sha256=item.excerpt_sha256))


def pack_topic_bridges(root: Path, pack: str) -> dict[str, list[str]]:
    """Topic to domain nodes, from the pack's curation file (the release carries them only after a rebuild)."""
    path = Path(root) / "releases" / pack / "curation.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {topic["topic_id"]: list(topic.get("graph_nodes") or []) for topic in data.get("topics") or []}


@dataclass
class Derivation:
    nodes: dict[str, CuratedNode] = field(default_factory=dict)
    edges: dict[str, CuratedEdge] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def node(self, node: CuratedNode) -> None:
        self.nodes.setdefault(node.node_id, node)

    def edge(self, edge: CuratedEdge) -> None:
        self.edges.setdefault(edge.edge_id, edge)


def derive(curation: GraphCuration, root: Path, register: PlaceRegister | None) -> Derivation:
    result = Derivation()
    names = {place.code: place.name for place in register.places} if register else {}
    authored = {node.node_id: node for node in curation.nodes}
    packs = PackReleases(root)
    needed_places = {"CH"} | {code for code in names if code.count("-") == 1}
    releases: list[tuple[str, Release]] = []
    for pack in curation.packs:
        release = packs.release(pack)
        releases.append((pack, release))
    for pack, release in releases:
        source = f"derive from {pack} {release.manifest.release_id}"
        first_evidence: dict[str, EvidenceRecord] = {}
        for item in release.evidence:
            if item.institution_id:
                first_evidence.setdefault(item.institution_id, item)
        institution_nodes = {}
        for institution in release.institutions:
            item = first_evidence.get(institution.institution_id)
            if item is None:
                continue
            node = institution_node(institution, item, pack, source, names)
            institution_nodes[institution.institution_id] = node
            result.node(node)
            needed_places.add(institution.jurisdiction)
        institutions = {i.institution_id: i for i in release.institutions}
        laws: dict[str, tuple[LawIdentity, EvidenceRecord]] = {}
        for item in release.evidence:
            basis = item.basis
            if basis is None or not basis.norm or basis.kind not in ("act", "ordinance", "treaty", "directive"):
                continue
            institution = institutions.get(item.institution_id or "")
            jurisdiction = "CH" if basis.level == "federal" else (institution.jurisdiction if institution else "CH")
            law = law_identity(basis.norm, basis.level, jurisdiction)
            if law.node_id in laws:
                continue
            laws[law.node_id] = (law, item)
            kind_word = "international agreement" if basis.kind == "treaty" else basis.kind
            result.node(CuratedNode(
                node_id=law.node_id, kind="law", label=law.label, level=basis.level,
                place=None if basis.level == "federal" else jurisdiction, sr_number=law.number,
                summary=f"{LEVEL_WORD[basis.level]} {kind_word} cited as {law.label}.",
                keywords=[law.label.split(" (")[0]], provenance=derived_provenance(source),
                evidence=[pack_citation(pack, item)]))
            collection = institution_nodes.get(item.institution_id or "")
            if collection is not None and collection.kind == "source":
                result.edge(CuratedEdge(
                    edge_id=f"{law.node_id}.authoritative_source.{collection.node_id}", from_id=law.node_id,
                    relation="authoritative_source", to_id=collection.node_id,
                    statement=f"The text of {law.label} is published by {collection.label}.",
                    provenance=derived_provenance(source), evidence=[pack_citation(pack, item)]))
        bridges = pack_topic_bridges(root, pack)
        facts_by_topic: dict[str, list] = {}
        concept_topic = {c.concept_id: c.topic_id for c in release.concepts}
        for fact in release.facts:
            facts_by_topic.setdefault(concept_topic.get(fact.concept_id, ""), []).append(fact)
        evidence = {e.evidence_id: e for e in release.evidence}
        for topic_id, domains in bridges.items():
            for domain_id in domains:
                domain = authored.get(domain_id)
                if domain is None or domain.kind != "domain":
                    result.notes.append(f"{pack} topic {topic_id} names {domain_id}, which the graph has no domain for")
                    continue
                for fact in facts_by_topic.get(topic_id, []):
                    for evidence_id in fact.evidence_ids:
                        item = evidence[evidence_id]
                        link_domain(result, domain, item, pack, source, institutions, institution_nodes, names)
    for role_id, patterns in curation.role_rules.items():
        role = authored.get(role_id)
        if role is None or role.kind != "role":
            result.notes.append(f"role_rules name {role_id}, which the graph has no role for")
            continue
        for node in list(result.nodes.values()):
            institution_id = node.node_id.split(".", 1)[1]
            if node.kind != "institution" or not any(fnmatchcase(institution_id, p) for p in patterns):
                continue
            result.edge(CuratedEdge(
                edge_id=f"{role_id}.instance.{node.node_id}", from_id=role_id, relation="instance", to_id=node.node_id,
                place=node.place, statement=f"For {place_label(node.place, names)}, the {role.label.lower()} is {node.label}.",
                provenance=derived_provenance(node.provenance.source), evidence=list(node.evidence)))
    for code in sorted(needed_places):
        if register is not None and code not in names:
            result.notes.append(f"place {code} is not in the place register")
            continue
        result.node(CuratedNode(
            node_id=f"place.{code.lower()}", kind="place", label=place_label(code, names), place=code,
            level="federal" if code == "CH" else "cantonal" if code.count("-") == 1 else "municipal",
            summary=place_summary(code, names), keywords=[names.get(code, code), code],
            provenance=derived_provenance("derive from the place register")))
        if code != "CH":
            parent = code.rsplit("-", 1)[0]
            needed = parent in needed_places or parent == "CH"
            if needed:
                result.edge(CuratedEdge(
                    edge_id=f"place.{code.lower()}.part_of.place.{parent.lower()}", from_id=f"place.{code.lower()}",
                    relation="part_of", to_id=f"place.{parent.lower()}",
                    statement=f"{place_label(code, names)} lies in {place_label(parent, names)}.",
                    provenance=derived_provenance("derive from the place register")))
    return result


def place_summary(code: str, names: dict[str, str]) -> str:
    if code == "CH":
        return "The Swiss Confederation: federal law applies in every canton and municipality."
    if code.count("-") == 1:
        return f"{place_label(code, names)}, one of the 26 cantons; cantonal law applies in its municipalities."
    return f"The municipality {place_label(code, names)} in {place_label(code.rsplit('-', 1)[0], names)}."


def institution_node(institution: Institution, item: EvidenceRecord, pack: str, source: str,
                     names: dict[str, str]) -> CuratedNode:
    kind = "source" if institution.body in SOURCE_BODIES else "institution"
    host = item.url.split("/")[2] if "://" in item.url else None
    keywords = [institution.native_name] if institution.native_name and institution.native_name != institution.name else []
    return CuratedNode(
        node_id=f"{kind}.{institution.institution_id}", kind=kind, label=institution.name, level=institution.level,
        place=institution.jurisdiction if kind == "institution" else None, hosts=[host] if host else [],
        summary=(f"{institution.name}: the {institution.level} {BODY_WORD[institution.body]} "
                 f"for {place_label(institution.jurisdiction, names)}."),
        keywords=keywords, provenance=derived_provenance(source), evidence=[pack_citation(pack, item)])


def link_domain(result: Derivation, domain: CuratedNode, item: EvidenceRecord, pack: str, source: str,
                institutions: dict[str, Institution], institution_nodes: dict[str, CuratedNode],
                names: dict[str, str]) -> None:
    basis = item.basis
    institution = institutions.get(item.institution_id or "")
    if basis is not None and basis.norm and basis.kind in ("act", "ordinance", "treaty", "directive"):
        jurisdiction = "CH" if basis.level == "federal" else (institution.jurisdiction if institution else "CH")
        law = law_identity(basis.norm, basis.level, jurisdiction)
        result.edge(CuratedEdge(
            edge_id=f"{domain.node_id}.legal_basis.{law.node_id}", from_id=domain.node_id, relation="legal_basis",
            to_id=law.node_id, place=None if basis.level == "federal" else jurisdiction,
            statement=f"{domain.label} rests in part on {law.label}.", provenance=derived_provenance(source),
            evidence=[pack_citation(pack, item)]))
    node = institution_nodes.get(item.institution_id or "")
    if node is not None and node.kind == "institution":
        result.edge(CuratedEdge(
            edge_id=f"{domain.node_id}.published_by.{node.node_id}", from_id=domain.node_id, relation="published_by",
            to_id=node.node_id, place=node.place,
            statement=f"{node.label} publishes on {domain.label.lower()} for {place_label(node.place, names)}.",
            provenance=derived_provenance(source), evidence=[pack_citation(pack, item)]))


def untouched(item) -> bool:
    return item.provenance.author == AUTHOR and item.provenance.review_status == STATUS


@dataclass
class Diff:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(added=self.added, updated=self.updated, kept=self.kept, stale=self.stale, notes=self.notes)


def apply_derivation(curation: GraphCuration, derivation: Derivation) -> Diff:
    """Write the derivation into the curation (in place) and return what changed; call on a copy for a preview."""
    diff = Diff(notes=list(derivation.notes))
    for existing_items, derived_items in ((curation.nodes, derivation.nodes), (curation.edges, derivation.edges)):
        position = {getattr(item, "node_id", None) or item.edge_id: index for index, item in enumerate(existing_items)}
        for item_id, derived in derived_items.items():
            if item_id not in position:
                existing_items.append(derived)
                diff.added.append(item_id)
                continue
            current = existing_items[position[item_id]]
            if not untouched(current):
                diff.kept.append(item_id)
            elif current != derived:
                existing_items[position[item_id]] = derived
                diff.updated.append(item_id)
        for item_id, index in position.items():
            if item_id not in derived_items and untouched(existing_items[index]):
                diff.stale.append(item_id)
    return diff
