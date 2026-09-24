"""Serving the knowledge graph: the orientation a caller reads before it searches (get_knowledge_graph).

Selection is deterministic and needs no model. The question's words are matched against every domain the graph
knows, each word weighted by how rare it is across the domains and counted once at the strongest field it appears in
(label, names and keywords above the names of the roles and pitfalls linked to the domain, above the summary), as
search ranks concepts. The best domains, at most three, are expanded by one hop: who sets the rules, who carries them
out and decides, where a person turns first, the laws and their authoritative source, the pitfalls. An edge that
names a place is served by containment like a fact, for that place and the places inside it; a role is resolved to
the institution that plays it at the most specific level the request's place reaches. The result says whether the
answer depends on the canton or the municipality (`place_dependence`), what to search for next, and which topics of
the release publish facts for the domains. It stays within a byte budget by dropping the least specific links first.
Design: docs/architecture/knowledge-graph.md.
"""

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date

from pydantic import Field

from swisstip.core.contracts import (DomainSummary, ExecutedScope, GetKnowledgeGraphRequest, GraphEdgeOut, GraphNodeOut,
                                     Jurisdiction, KnowledgeGraphResult, NextSearch, PlaceDependence)
from swisstip.core.graph import contains
from swisstip.core.places import PlaceIndex, Scope, level_of
from swisstip.core.release import GraphEdge, GraphNode, KnowledgeGraph, Strict, Topic

from .semantic import SemanticError, _digest, _vectors
from .service import tokens

FIELD_WEIGHTS = {"label": 3.0, "names": 3.0, "keywords": 3.0, "linked": 2.0, "summary": 1.0}
ANCHOR_FIELDS = frozenset({"label", "names", "keywords", "linked"})
LINKING_RELATIONS = ("executed_by", "decided_by", "approved_by", "first_contact", "pitfall")
DEPENDENCE_RELATIONS = ("executed_by", "decided_by", "first_contact", "varies_by")
MAX_DOMAINS = 3
# The country's own names, which a question uses for the place and not the subject, like a canton's name.
COUNTRY_WORDS = "Switzerland Swiss Schweiz schweizerisch Suisse suisse Svizzera svizzero Svizra"
# The words of a place's label that are not its name ("City of Zurich", "Canton of Ticino"): they stay subject words.
# The parts of the canton names that are also ordinary words or stem like one (Appenzell "Interno" and international,
# Basel-"Land", the "Confederation" and eidgenössisch) stay subject words too; so do the two-letter codes.
PLACE_GENERIC_WORDS = ("city town canton commune municipality Stadt Kanton Gemeinde Bezirk ville canton commune "
                       "città cantone comune country land campagne campagna inner outer interno esterno intérieures "
                       "extérieures Confederation Eidgenossenschaft eidgenössisch confédération confederazione saint "
                       "sankt san")
DOMAIN_SCORE_SHARE = 0.5
BYTE_BUDGET = 8000
# Embedding matching, when the server has a local embedder (hybrid search): the question against one text per domain.
GRAPH_QUERY_PREFIX = "Instruct: Given a question, retrieve the Swiss public-administration domain it concerns.\nQuery: "
SEMANTIC_FLOOR = 0.45
SEMANTIC_STRONG = 0.6
FUSION_K = 5
# The order a caller needs a domain's links in: where to turn and who carries it out first (with the office that plays
# the role at the user's place right after its role), then who sets the rules, what differs, the traps and the law.
# The budget drops links from the end, the best-matched domain's last.
RELATION_ORDER = ("first_contact", "executed_by", "decided_by", "approved_by", "instance", "rules_set_by", "varies_by",
                  "pitfall", "legal_basis", "authoritative_source", "see_also", "published_by", "governed_by", "part_of")
# The links that say where the user turns: every matched domain keeps them, with the office at the user's place,
# before any domain's laws, sources and pitfalls, since the second-best domain is often the subject.
TURN_TO = frozenset({"first_contact", "executed_by", "decided_by", "approved_by"})
RELATION_CAP = {"legal_basis": 4, "published_by": 3, "see_also": 2}
LEVEL_WORD = {"municipal": "municipality", "cantonal": "canton"}
DEPENDENCE_RANK = {"none": 0, "canton": 1, "municipality": 2}

GUIDANCE = ("Orientation from the knowledge graph, not citable evidence. In your next turn call search with "
            "next_search.query, the question as asked, and do not add next_search.terms or other words from the graph "
            "to it; the terms are the official names to recognise the right office and concepts in the results. Then "
            "resolve the relevant concepts with the user's place, when known; answer only from resolve's facts and cite "
            "only its URLs. Use the graph to understand who decides and where the user turns, not as a source to quote.")
GUIDANCE_ROOT = ("The root page of the knowledge graph: the levels of the state, the principles they work under and the "
                 "domains the graph knows. Call get_knowledge_graph with the user's question and place for the domains "
                 "that concern it.")
GUIDANCE_PLACE = (" The answer depends on the user's {level}: derive it from what the user said (the place of work is "
                  "not the place of residence) and ask only if it cannot be derived. You may search now, but resolve no "
                  "cantonal or municipal concept before the place is known, and never assume one.")
GUIDANCE_NOT_COVERED = (" This release publishes no facts on {domains}: tell the user that this service does not cover it, "
                        "name the authoritative source only as where to look, and do not answer from general knowledge "
                        "as if it were grounded.")
GUIDANCE_WEAK = (" The question's words reached no domain of the graph clearly; the nodes rest on incidental words. "
                 "Search once with the question before declining.")
GUIDANCE_NONE = (" The question's words reached no domain of the graph. Search once with the question; if that finds "
                 "nothing either, decline.")
GUIDANCE_STALE = (" The graph's sources were saved on {snapshot}, more than {days} days ago: offices and procedures may "
                  "have changed, so point the user to the cited pages.")
ASK = {"municipality": "In which municipality does the user live (the political commune, not the place of work)?",
       "canton": "In which canton does the user live?"}


class GraphCase(Strict):
    """One orientation check of a graph's `checks.yaml`: a request and what the result must contain."""

    case_id: str
    question: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    jurisdiction: Jurisdiction = Field(default_factory=Jurisdiction)
    expect_nodes: list[str] = Field(default_factory=list)
    expect_absent: list[str] = Field(default_factory=list)
    expect_place_dependence: str | None = None
    expect_match: str | None = None
    blocking: bool = True


class GraphChecks(Strict):
    schema_version: str = "swiss-tip-graph-checks/v1"
    graph: str
    cases: list[GraphCase]


def place_names(places: PlaceIndex | None) -> list[str]:
    """The names of the country and the cantons of a place register, which say where the user is and not what the
    question is about. Communes are left out: many of their names are ordinary words (Wald, Egg, Au)."""
    if places is None:
        return []
    return [name for place in places.places.values() if level_of(place.code) < 2 for name in (place.name, *place.aliases)]


@dataclass
class Ranking:
    chosen: list[tuple[float, str]]
    strength: str
    mode: str
    best_cosine: float | None = None
    error: str | None = None


class GraphSemantic:
    """Embedding matching of questions to the graph's domains, with the local embedder of hybrid search.

    The domain vectors are made on first use, in one request, from the graph the server holds, so there is no index
    file to build or attest; `digest` records the model digest they were made with."""

    def __init__(self, embedder, floor: float = SEMANTIC_FLOOR, strong: float = SEMANTIC_STRONG):
        self.embedder, self.floor, self.strong = embedder, floor, strong
        self.vectors: dict[str, list[float]] | None = None
        self.digest: str | None = None

    def prepare(self, index: "GraphIndex") -> None:
        if self.vectors is not None:
            return
        try:
            digest = _digest(self.embedder.model_digest())
            texts = [index.domain_text(d) for d in index.domains]
            vectors = _vectors(self.embedder.embed(texts), len(texts))
        except SemanticError:
            raise
        except Exception as exc:
            raise SemanticError(f"Graph embedding failed ({type(exc).__name__}).") from exc
        self.vectors = {d.node_id: v for d, v in zip(index.domains, vectors)}
        self.digest = digest

    def scores(self, question: str, index: "GraphIndex") -> list[tuple[float, str]]:
        """The cosine of every domain, best first: one embedding request (after the first)."""
        self.prepare(index)
        try:
            vector = _vectors(self.embedder.embed([GRAPH_QUERY_PREFIX + question]), 1, len(next(iter(self.vectors.values()))))[0]
        except SemanticError:
            raise
        except Exception as exc:
            raise SemanticError(f"Graph embedding failed ({type(exc).__name__}).") from exc
        return sorted(((max(-1.0, min(1.0, sum(a * b for a, b in zip(vector, v)))), domain_id)
                       for domain_id, v in self.vectors.items()), key=lambda item: (-item[0], item[1]))


class GraphIndex:
    def __init__(self, graph: KnowledgeGraph, topics: list[Topic] | None = None, place_names: list[str] = ()):
        self.graph = graph
        self.semantic: GraphSemantic | None = None
        self.nodes: dict[str, GraphNode] = {n.node_id: n for n in graph.nodes}
        self.out_edges: dict[str, list[GraphEdge]] = {n.node_id: [] for n in graph.nodes}
        for edge in graph.edges:
            self.out_edges[edge.from_id].append(edge)
        self.urls = {e.evidence_id: e.url for e in graph.evidence}
        self.domains = [n for n in graph.nodes if n.kind == "domain"]
        self.bridges: dict[str, list[str]] = {}
        for topic in topics or []:
            for node_id in topic.graph_nodes:
                self.bridges.setdefault(node_id, []).append(topic.topic_id)
        self.fields = {domain.node_id: self.domain_fields(domain) for domain in self.domains}
        frequency = Counter(token for fields in self.fields.values() for token in set().union(*fields.values()))
        count = max(len(self.domains), 1)
        self.rarity = {token: math.log(1 + count / seen) for token, seen in frequency.items()}
        # A place in the question ("in Zurich") says where, not what: it would match every domain whose offices carry
        # the place's name (SVA Zürich, Steueramt Zürich) and turn an off-topic question into a strong match.
        self.place_tokens = tokens(" ".join([COUNTRY_WORDS, *place_names, *(
            " ".join([n.label, *n.names.values()]) for n in graph.nodes if n.kind == "place")]))
        self.place_tokens = {t for t in self.place_tokens - tokens(PLACE_GENERIC_WORDS) if len(t) > 2}

    def domain_fields(self, domain: GraphNode) -> dict[str, set[str]]:
        linked = [self.nodes[e.to_id] for e in self.out_edges[domain.node_id] if e.relation in LINKING_RELATIONS]
        return {"label": tokens(domain.label), "names": tokens(" ".join(domain.names.values())),
                "keywords": tokens(" ".join(domain.keywords)),
                "linked": tokens(" ".join(" ".join([n.label, *n.names.values(), *n.keywords]) for n in linked)),
                "summary": tokens(domain.summary)}

    # --- selection -----------------------------------------------------------

    def rank(self, question: str) -> tuple[list[tuple[float, str]], str]:
        """The chosen domains, best first, and the match strength (see `ranking`)."""
        ranking = self.ranking(question)
        return ranking.chosen, ranking.strength

    def ranking(self, question: str) -> "Ranking":
        """Lexical ranking, fused with embedding ranking when the index has an embedder.

        Fused, a domain scores 1/(k + rank) in each list it is in, the embedding list holding only the domains at or
        above the floor. The embedding score also sets the strength: strong at or above SEMANTIC_STRONG, and below
        the floor a lexical match is at most weak, so a rare word alone ("limit", "Zurich") no longer makes an
        off-topic question strong. When the embedder fails the lexical ranking stands, as search falls back."""
        lexical, strength = self.lexical_rank(question)
        if self.semantic is None:
            return Ranking(lexical, strength, "lexical")
        try:
            scored = self.semantic.scores(question, self)
        except SemanticError as exc:
            return Ranking(lexical, strength, "lexical-fallback", error=str(exc))
        best_cosine = scored[0][0] if scored else None
        lexical_all = self.lexical_scores(question)[0]
        fused: Counter = Counter()
        for position, (_, domain_id) in enumerate(lexical_all):
            fused[domain_id] += 1 / (FUSION_K + position + 1)
        for position, (cosine, domain_id) in enumerate(scored):
            if cosine >= self.semantic.floor:
                fused[domain_id] += 1 / (FUSION_K + position + 1)
        ranked = sorted(((score, domain_id) for domain_id, score in fused.items()), key=lambda item: (-item[0], item[1]))
        if best_cosine is not None and best_cosine >= self.semantic.strong:
            strength = "strong"
        elif best_cosine is None or best_cosine < self.semantic.floor:
            strength = "weak" if lexical else "none"
        elif not lexical:
            strength = "weak"
        chosen = [item for item in ranked if item[0] >= DOMAIN_SCORE_SHARE * ranked[0][0]][:MAX_DOMAINS] if ranked else []
        return Ranking(chosen, strength, "hybrid", best_cosine=best_cosine)

    def lexical_rank(self, question: str) -> tuple[list[tuple[float, str]], str]:
        """Domains by lexical score, and the match strength: strong when the best domain matched a word at its label,
        names, keywords or linked roles; weak when only its summary did; none without a match."""
        ranked, anchored = self.lexical_scores(question)
        if not ranked:
            return [], "none"
        best = ranked[0][0]
        chosen = [item for item in ranked if item[0] >= DOMAIN_SCORE_SHARE * best][:MAX_DOMAINS]
        return chosen, "strong" if anchored[ranked[0][1]] else "weak"

    def lexical_scores(self, question: str) -> tuple[list[tuple[float, str]], dict[str, bool]]:
        query = tokens(question) - self.place_tokens
        ranked, anchored = [], {}
        for domain_id, fields in self.fields.items():
            score, anchor = 0.0, False
            for token in query:
                weights = [FIELD_WEIGHTS[name] for name, words in fields.items() if token in words]
                if weights:
                    score += self.rarity.get(token, 0.0) * max(weights)
                    anchor = anchor or any(token in fields[name] for name in ANCHOR_FIELDS)
            if score > 0:
                ranked.append((score, domain_id))
                anchored[domain_id] = anchor
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked, anchored

    def domain_text(self, domain: GraphNode) -> str:
        """The embedding input of a domain: what it is called, where a person turns, and what it covers."""
        linked = [self.nodes[e.to_id] for e in self.out_edges[domain.node_id] if e.relation in LINKING_RELATIONS]
        return "\n".join([f"Domain: {domain.label}", "Names: " + "; ".join(domain.names.values()),
                          "Keywords: " + "; ".join(domain.keywords),
                          "Offices and pitfalls: " + "; ".join(dict.fromkeys(
                              " / ".join([n.label, *n.names.values()]) for n in linked)),
                          f"Summary: {domain.summary}"])

    # --- expansion -----------------------------------------------------------

    @staticmethod
    def applies(edge: GraphEdge, place: str) -> bool:
        return edge.place is None or contains(edge.place, place)

    def expand(self, start: list[str], place: str, reviewed_only: bool) -> tuple[list[GraphNode], list[GraphEdge]]:
        """The start nodes and their links, each link with its rank in `self.rank_of`: where the user turns (with the
        office at their place) for every start node first, then the other links by start position and relation."""
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        self.rank_of: dict[str, tuple] = {}

        def keep(item) -> bool:
            return not reviewed_only or item.provenance.review_status == "human-reviewed"

        def add_node(node_id: str) -> bool:
            node = self.nodes.get(node_id)
            if node is None or not keep(node):
                return False
            nodes.setdefault(node_id, node)
            return True

        def add_edge(edge: GraphEdge, rank: tuple) -> None:
            if keep(edge) and edge not in edges and add_node(edge.to_id):
                edges.append(edge)
                self.rank_of[edge.edge_id] = rank

        for position, node_id in enumerate(start):
            if not add_node(node_id):
                continue
            counts: Counter = Counter()
            for edge in sorted(self.out_edges[node_id], key=lambda e: RELATION_ORDER.index(e.relation)):
                if not self.applies(edge, place) or counts[edge.relation] >= RELATION_CAP.get(edge.relation, 99):
                    continue
                counts[edge.relation] += 1
                relation = RELATION_ORDER.index(edge.relation)
                tier = 0 if edge.relation in TURN_TO else 1
                add_edge(edge, (tier, position, relation, 0))
                target = self.nodes[edge.to_id]
                if target.kind == "role":
                    for instance in self.instances(target.node_id, place):
                        add_edge(instance, (tier, position, relation, 1))
                if target.kind == "law":
                    for source in [e for e in self.out_edges[target.node_id] if e.relation == "authoritative_source"][:1]:
                        add_edge(source, (1, position, RELATION_ORDER.index("authoritative_source"), 1))
        for code in self.place_chain(place):
            add_node(f"place.{code.lower()}")
        return list(nodes.values()), edges

    def instances(self, role_id: str, place: str) -> list[GraphEdge]:
        """The institutions that play the role at the most specific level the place reaches."""
        candidates = [e for e in self.out_edges[role_id] if e.relation == "instance" and self.applies(e, place)]
        if not candidates:
            return []
        depth = max((e.place or "CH").count("-") for e in candidates)
        return [e for e in candidates if (e.place or "CH").count("-") == depth][:2]

    @staticmethod
    def place_chain(place: str) -> list[str]:
        """The user's canton and municipality, broadest first; nothing for the country alone."""
        parts = place.split("-")
        return ["-".join(parts[:depth]) for depth in range(2, len(parts) + 1)]

    def dependence(self, domain_ids: list[str], scope: Scope | None) -> PlaceDependence | None:
        best, reason = "none", None
        for domain_id in domain_ids:
            for edge in self.out_edges.get(domain_id, []):
                if edge.relation not in DEPENDENCE_RELATIONS:
                    continue
                level = LEVEL_WORD.get(self.nodes[edge.to_id].level or "")
                if level and DEPENDENCE_RANK[level] > DEPENDENCE_RANK[best]:
                    best, reason = level, edge.statement
        if not domain_ids:
            return None
        known = (best == "none" or (best == "canton" and scope is not None and scope.canton_code is not None)
                 or (best == "municipality" and scope is not None and scope.municipality_id is not None))
        return PlaceDependence(depends_on=best, known=known, reason=reason, ask=None if known else ASK[best])

    def next_search(self, question: str | None, domain_ids: list[str], nodes: list[GraphNode],
                    jurisdiction: Jurisdiction) -> NextSearch | None:
        if not domain_ids:
            return None
        # The terms name the best-matched domain and its offices, to recognise them in search's results; they are not
        # added to the query, since a second domain's words pull search off the subject.
        terms: list[str] = []
        best = domain_ids[0]
        reached = {e.to_id for e in self.out_edges[best] if e.relation in ("first_contact", "executed_by")}
        for node in [self.nodes[best]] + [n for n in nodes if n.node_id in reached]:
            for term in [node.names.get("de"), node.names.get("en")]:
                if term and term not in terms and term.casefold() not in (question or "").casefold():
                    terms.append(term)
        query = question.strip() if question else " ".join(self.nodes[d].label for d in domain_ids)
        given = jurisdiction if (jurisdiction.country or jurisdiction.canton or jurisdiction.city) else None
        return NextSearch(query=query, terms=terms[:6], jurisdiction=given)

    # --- the tool ------------------------------------------------------------

    def orient(self, request: GetKnowledgeGraphRequest, scope: Scope | None, release_id: str, today: date,
               executed_scope: ExecutedScope | None = None) -> KnowledgeGraphResult:
        place = (scope.code if scope is not None else None) or "CH"
        if not request.question and not request.node_ids:
            return self.root(release_id, today)
        ranking = self.ranking(request.question) if request.question else Ranking([], None, "none")
        ranked, strength = ranking.chosen, ranking.strength
        domain_ids = [domain_id for _, domain_id in ranked]
        start = list(dict.fromkeys([*request.node_ids, *domain_ids]))
        nodes, edges = self.expand(start, place, request.reviewed_only)
        domains = [d for d in start if d in self.nodes and self.nodes[d].kind == "domain"]
        covered = sorted({topic for d in domains for topic in self.bridges.get(d, [])})
        dependence = self.dependence(domains, scope)
        guidance = GUIDANCE
        if strength == "weak":
            guidance += GUIDANCE_WEAK
        elif strength == "none":
            guidance += GUIDANCE_NONE
        if dependence is not None and not dependence.known:
            guidance += GUIDANCE_PLACE.format(level=dependence.depends_on)
        if domains and not covered:
            guidance += GUIDANCE_NOT_COVERED.format(domains=", ".join(self.nodes[d].label.lower() for d in domains))
        guidance += self.stale_note(today)
        result = KnowledgeGraphResult(
            release_id=release_id, graph_id=self.graph.graph_id, executed_scope=executed_scope, match_strength=strength,
            nodes=[], edges=[], covered_topics=covered, place_dependence=dependence,
            next_search=self.next_search(request.question, domains, nodes, request.jurisdiction),
            guidance_for_caller=guidance, limitations=self.limitations())
        if ranking.mode == "lexical-fallback":
            result.limitations.append("Domains were matched by words only: the local embedding model was unavailable.")
        return self.fit(result, nodes, edges, protected=set(domains) | set(request.node_ids))

    def root(self, release_id: str, today: date) -> KnowledgeGraphResult:
        nodes = [n for n in self.graph.nodes if n.kind in ("level", "principle")]
        ids = {n.node_id for n in nodes}
        edges = [e for e in self.graph.edges if e.from_id in ids and e.to_id in ids]
        result = KnowledgeGraphResult(
            release_id=release_id, graph_id=self.graph.graph_id, nodes=[], edges=[],
            domains=[DomainSummary(node_id=d.node_id, label=d.label, covered=d.node_id in self.bridges) for d in self.domains],
            guidance_for_caller=GUIDANCE_ROOT + self.stale_note(today), limitations=self.limitations())
        return self.fit(result, nodes, edges, protected=ids)

    def stale_note(self, today: date) -> str:
        freshness = self.graph.freshness
        if today < freshness.stale_from:
            return ""
        return GUIDANCE_STALE.format(snapshot=freshness.snapshot_date.isoformat(), days=freshness.max_age_days)

    def limitations(self) -> list[str]:
        counts = self.graph.review_statuses
        total = sum(counts.values())
        reviewed = counts.get("human-reviewed", 0)
        return [f"Orientation, not evidence: the graph's statements summarise official pages; {reviewed} of its {total} "
                "nodes and edges have been confirmed by a person, the rest are assistant-authored or derived and unreviewed."]

    def fit(self, result: KnowledgeGraphResult, nodes: list[GraphNode], edges: list[GraphEdge],
            protected: set[str]) -> KnowledgeGraphResult:
        """Fill the result and fit it to the byte budget, shortening before cutting: first without the summaries of
        the nodes other than the matched and requested ones (a caller asks for a node by its ID), then without the
        edges' source URLs (the caller cites resolve's), and only then by dropping the least specific links; an
        unlinked node goes with its last edge, the matched domains stay."""
        statuses = Counter(item.provenance.review_status for item in [*nodes, *edges])
        common = statuses.most_common(1)[0][0] if statuses else None
        ranks = getattr(self, "rank_of", {})
        edges = sorted(edges, key=lambda e: ranks.get(e.edge_id, (99, 99, RELATION_ORDER.index(e.relation), 0)))
        brevity = 0
        while True:
            linked = {e.from_id for e in edges} | {e.to_id for e in edges}
            kept = [n for n in nodes if n.node_id in protected or n.node_id in linked or n.kind == "place"]
            result.nodes = [self.node_out(n, common, summary=brevity == 0 or n.node_id in protected) for n in kept]
            result.edges = [self.edge_out(e, common, source=brevity < 2) for e in edges]
            result.review_status = common
            size = len(json.dumps(result.model_dump(mode="json", exclude_none=True), ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"))
            if size <= BYTE_BUDGET or not edges:
                return result
            if brevity < 2:
                brevity += 1
                continue
            edges = edges[:-1]

    def url(self, evidence_ids: list[str]) -> str | None:
        return self.urls.get(evidence_ids[0]) if evidence_ids else None

    def node_out(self, node: GraphNode, common: str | None, summary: bool = True) -> GraphNodeOut:
        status = node.provenance.review_status
        # The edges carry the citations; a node's own source would repeat one of them for most nodes.
        return GraphNodeOut(node_id=node.node_id, kind=node.kind, label=node.label, names=node.names,
                            summary=node.summary if summary else None,
                            level=node.level, place=node.place, review_status=None if status == common else status)

    def edge_out(self, edge: GraphEdge, common: str | None, source: bool = True) -> GraphEdgeOut:
        status = edge.provenance.review_status
        return GraphEdgeOut(from_id=edge.from_id, relation=edge.relation, to_id=edge.to_id, statement=edge.statement,
                            place=edge.place, source_url=self.url(edge.evidence_ids) if source else None,
                            review_status=None if status == common else status)


def check_graph(index: GraphIndex, place_index: PlaceIndex, checks: GraphChecks, today: date | None = None) -> dict:
    """Replay the orientation cases with the code the server answers with; a blocking case that fails fails the check."""
    results = []
    for case in checks.cases:
        request = GetKnowledgeGraphRequest(question=case.question, node_ids=case.node_ids, jurisdiction=case.jurisdiction)
        place = case.jurisdiction
        scope = place_index.resolve(place.country, place.canton, place.city) if (place.country or place.canton or place.city) else None
        result = index.orient(request, scope, "check", today or date.today())
        served = {n.node_id for n in result.nodes}
        problems = [f"missing {node_id}" for node_id in case.expect_nodes if node_id not in served]
        problems += [f"unexpected {node_id}" for node_id in case.expect_absent if node_id in served]
        dependence = result.place_dependence.depends_on if result.place_dependence else None
        if case.expect_place_dependence is not None and dependence != case.expect_place_dependence:
            problems.append(f"place_dependence {dependence}, expected {case.expect_place_dependence}")
        if case.expect_match is not None and result.match_strength != case.expect_match:
            problems.append(f"match_strength {result.match_strength}, expected {case.expect_match}")
        results.append(dict(case_id=case.case_id, passed=not problems, blocking=case.blocking, problems=problems))
    failed = [r["case_id"] for r in results if not r["passed"] and r["blocking"]]
    return dict(graph_id=index.graph.graph_id, content_sha256=index.graph.content_sha256, cases=len(results),
                passed=sum(r["passed"] for r in results), failed_blocking=failed, results=results)
