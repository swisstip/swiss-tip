"""The compiled knowledge graph a pack's curation names, loaded for the pack build to embed.

The graph is compiled and validated on its own (`swisstip-graph compile`); the pack build takes `graph.json` as it
is, the way it takes the place register, and the release validator checks it again against the pack's place register
and the topics' `graph_nodes`. A changed `graph.json` changes the build's inputs, so the next pipeline run rebuilds.
"""

from pathlib import Path

from swisstip.core.graph import GraphInvalid, load_graph, validate_graph
from swisstip.core.release import KnowledgeGraph

from .curation import Curation


def graph_file(curation: Curation, curation_path: Path) -> Path | None:
    if not curation.knowledge_graph:
        return None
    return (Path(curation_path).resolve().parent / curation.knowledge_graph).resolve()


def graph_for(curation: Curation, curation_path: Path) -> KnowledgeGraph | None:
    path = graph_file(curation, curation_path)
    if path is None:
        return None
    if not path.is_file():
        raise GraphInvalid([f"the curation names the knowledge graph {path}, which does not exist; compile it first"])
    graph = load_graph(path)
    issues = validate_graph(graph)
    if issues:
        raise GraphInvalid(issues)
    return graph
