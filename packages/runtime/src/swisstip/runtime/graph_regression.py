"""Replay a pack's acceptance and regression questions against its knowledge graph: the graph's own regression.

The acceptance suite checks search, resolve and the facts, and never calls `get_knowledge_graph`. This replay asks
the graph every question of the same suites, as a caller following the instructions would (the question as asked,
or the prepared key terms of a question in a language the release does not advertise, and the place of the search
step), and judges the orientation against what the suites already expect, so it needs no expectations of its own:

- a case whose search expects a concept (`subject`) expects the graph to match a domain its topic bridges to
  (`graph_nodes`), among the first `within` domains, and to name the topic in `covered_topics`; a topic bridged to no
  domain is reported as `unbridged` and not judged;
- a case whose search expects only a weak or empty match (`decline`) expects the graph not to point the caller at a
  covered topic with a strong match, since a caller would then resolve it.

It uses no model and no network; the result carries the domains ranked, the match strength and the size of every
answer, so two graphs or two matching methods can be compared case by case.
"""

from collections import Counter
from datetime import UTC, datetime
from statistics import median

from swisstip.core.acceptance import AcceptanceFile, Case, SearchStep
from swisstip.core.contracts import GetKnowledgeGraphRequest, KnowledgeGraphResult

from .service import ReleaseService

GRAPH_REGRESSION_SCHEMA_VERSION = "swiss-tip-graph-regression/v1"
QUESTION_LIMIT = 500


def graph_query(case: Case, step: SearchStep) -> str:
    """What a caller sends to the graph: the question as asked, or the key terms a caller searches with when the
    release does not advertise the question's language."""
    return (step.query if step.translated else case.question)[:QUESTION_LIMIT]


def case_kind(case: Case) -> tuple[str, SearchStep] | None:
    searches = [step.search for step in case.steps if step.search is not None]
    subject = next((s for s in searches if s.expect_concept is not None), None)
    if subject is not None:
        return "subject", subject
    decline = next((s for s in searches if s.expect_strength in ("weak", "none")), None)
    return ("decline", decline) if decline is not None else None


def check_graph_case(service: ReleaseService, case: Case, within: int) -> dict | None:
    kind = case_kind(case)
    if kind is None:
        return None
    kind, step = kind
    query = graph_query(case, step)
    # The tool first: it attaches the service's embedder to the graph, which the ranking below then uses too.
    result = service.get_knowledge_graph(GetKnowledgeGraphRequest(question=query, jurisdiction=step.jurisdiction))
    ranking = service.graph_index.ranking(query)
    if not isinstance(result, KnowledgeGraphResult):
        return dict(case_id=case.case_id, blocking=case.blocking, kind=kind, query=query, passed=False,
                    issues=[f"the graph refused the request: {result}"])
    domains = [domain_id for _, domain_id in ranking.chosen]
    outcome = dict(case_id=case.case_id, blocking=case.blocking, kind=kind, query=query, mode=ranking.mode,
                   best_cosine=None if ranking.best_cosine is None else round(ranking.best_cosine, 4),
                   match_strength=result.match_strength, domains=domains, covered_topics=result.covered_topics,
                   bytes=len(result.model_dump_json(exclude_none=True).encode()))
    issues: list[str] = []
    if kind == "subject":
        concept = service.concepts.get(step.expect_concept)
        topic = service.topics.get(concept.topic_id) if concept is not None else None
        expected = list(topic.graph_nodes) if topic is not None else []
        outcome.update(concept=step.expect_concept, topic=topic.topic_id if topic else None, expected_domains=expected)
        if not expected:
            return dict(outcome, judged=False, passed=None, issues=[f"topic {outcome['topic']} bridges to no domain"])
        position = next((i for i, d in enumerate(domains) if d in expected), None)
        outcome.update(position=position, first=position == 0)
        if position is None or position >= within:
            issues.append(f"no domain of {outcome['topic']} ({', '.join(expected)}) among {domains[:within] or 'none'}")
        if outcome["topic"] not in result.covered_topics:
            issues.append(f"covered_topics {result.covered_topics} lacks {outcome['topic']}")
    elif result.match_strength == "strong" and result.covered_topics:
        issues.append(f"strong match on covered topics {result.covered_topics} for a question the release declines")
    return dict(outcome, judged=True, passed=not issues, issues=issues)


def graph_regression(service: ReleaseService, suite: AcceptanceFile, within: int = 3) -> dict:
    """The graph's orientation for every case of `suite` that has a search step to derive an expectation from; the
    matching is hybrid when the service has a local embedder (`semantic_search`), lexical otherwise."""
    if service.graph_index is None:
        raise ValueError("the release carries no knowledge graph (or it was switched off)")
    results = [r for r in (check_graph_case(service, case, within) for case in suite.cases) if r is not None]
    judged = [r for r in results if r["judged"]]

    def tally(kind: str) -> dict:
        cases = [r for r in judged if r["kind"] == kind]
        counts = dict(cases=len(cases), passed=sum(1 for r in cases if r["passed"]))
        if kind == "subject":
            counts["first"] = sum(1 for r in cases if r.get("first"))
        return counts

    graph = service.graph_index.graph
    sizes = [r["bytes"] for r in results if "bytes" in r]
    return dict(schema_version=GRAPH_REGRESSION_SCHEMA_VERSION, pack=suite.pack, release_id=service.release_id,
                graph_id=graph.graph_id, graph_sha256=graph.content_sha256, suite_sha256=suite.digest(),
                checked_at=datetime.now(UTC).isoformat(), within=within,
                modes=dict(Counter(r.get("mode") for r in results if r.get("mode"))),
                cases=len(results), judged=len(judged), passed=sum(1 for r in judged if r["passed"]),
                subject=tally("subject"), decline=tally("decline"),
                unbridged=sorted({r["topic"] for r in results if not r["judged"] and r.get("topic")}),
                failed_blocking=[r["case_id"] for r in judged if r["blocking"] and not r["passed"]],
                quarantined_passing=[r["case_id"] for r in judged if not r["blocking"] and r["passed"]],
                max_bytes=max(sizes, default=0), median_bytes=int(median(sizes)) if sizes else 0, results=results)
