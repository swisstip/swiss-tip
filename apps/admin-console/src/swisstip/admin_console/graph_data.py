"""The knowledge graph as a unit of the console: its files, its excerpts, and the one way the console writes it.

A graph lives in `graphs/<graph>/` of the packs repository (`graph.yaml`, `graph.json`, `sources.json`,
`checks.yaml` and the reports) with its run and the console's working state in `.local/graph-<graph>/`. The console
reads the same files the graph pipeline writes and writes only `graph.yaml`, through `write_graph`: load, change,
validate through the graph curation model, compile into memory against the text dataset and the pack releases,
save, audit. A write that would not compile is refused with the reasons, so the console can never leave a graph the
pipeline would refuse. Design: docs/architecture/admin-console.md, section 4.10.
"""

import json
from datetime import date
from pathlib import Path

import yaml
from pydantic import ValidationError

from swisstip.build.graph_build import PackReleases, build_graph, graph_places
from swisstip.build.graph_curation import GraphCuration, load_graph_curation, save_graph_curation
from swisstip.build.places import PlaceFileError
from swisstip.build.release_build import BuildError
from swisstip.core.graph import GraphInvalid
from swisstip.core.release import KnowledgeGraph

from .data import CachedFile, TextDataset, sha256_bytes
from .writes import LOCKS, WriteRefused, append_audit, check_unchanged, field_errors, pack_file_lock

UNREVIEWED = ("assistant-authored-unreviewed", "automatically-derived-unreviewed", "model-candidate-automated-review")


def parse_graph_curation(text: str) -> GraphCuration:
    return GraphCuration.model_validate(yaml.safe_load(text))


def graph_names(root: Path) -> list[str]:
    folder = Path(root) / "graphs"
    return sorted(child.name for child in folder.iterdir() if (child / "graph.yaml").is_file()) if folder.is_dir() else []


class GraphData:
    """Every file of one graph. `pack` is the graph's run name, so jobs, locks and the audit log treat it as a unit."""

    def __init__(self, root: Path, graph: str):
        self.root = Path(root).resolve()
        self.graph = graph
        self.pack = f"graph-{graph}"
        self.graph_dir = self.root / "graphs" / graph
        self.curation_path = self.graph_dir / "graph.yaml"
        self.compiled_path = self.graph_dir / "graph.json"
        self.catalogue_path = self.graph_dir / "sources.json"
        self.checks_path = self.graph_dir / "checks.yaml"
        self.run_dir = self.root / ".local" / self.pack
        self.text_dir = self.run_dir / "text"
        self.console_dir = self.run_dir / "console"
        self.curation = CachedFile(self.curation_path, parse_graph_curation)
        self.compiled = CachedFile(self.compiled_path, KnowledgeGraph.model_validate_json)
        self.report = CachedFile(self.graph_dir / "graph-report.json")
        self.pipeline_report = CachedFile(self.graph_dir / "pipeline-report.json")
        self.checks_report = CachedFile(self.graph_dir / "checks-report.json")
        self.dataset = TextDataset(self.text_dir)
        self.packs = PackReleases(self.root)

    # --- reading ----------------------------------------------------------------------------------------------------

    def items(self) -> dict[str, object]:
        curation = self.curation.current()
        if curation is None:
            return {}
        return {**{n.node_id: n for n in curation.nodes}, **{e.edge_id: e for e in curation.edges}}

    def excerpt(self, citation) -> dict:
        """What a citation shows a reviewer: the excerpt, its page and where it comes from."""
        if citation.pack is not None:
            reference = citation.pack
            try:
                release = self.packs.release(reference.pack)
            except BuildError as exc:
                return dict(label=f"{reference.pack}:{reference.evidence_id}", excerpt=None, problem=str(exc))
            record = next((e for e in release.evidence if e.evidence_id == reference.evidence_id), None)
            if record is None:
                return dict(label=f"{reference.pack}:{reference.evidence_id}", excerpt=None,
                            problem=f"not in {release.manifest.release_id}")
            return dict(label=f"{reference.pack}:{reference.evidence_id}", excerpt=record.original_excerpt, url=record.url,
                        publisher=record.publisher, basis=record.basis.label if record.basis else None,
                        problem=None if record.excerpt_sha256 == reference.excerpt_sha256 else
                        "the pack's excerpt changed since this citation was written")
        reference = citation.text
        record = self.dataset.record(reference.document_id)
        label = f"{reference.document_id} blocks {reference.first_block}-{reference.last_block or reference.first_block}"
        if record is None:
            return dict(label=label, excerpt=None, problem="the record is not in the graph's text dataset")
        blocks = record["blocks"][reference.first_block - 1:(reference.last_block or reference.first_block)]
        return dict(label=label, excerpt="\n\n".join(b["text"] for b in blocks), url=record["source_url"],
                    publisher=None, basis=None, document_id=reference.document_id, first_block=reference.first_block,
                    problem=None if blocks else "the block range lies outside the record")

    def bridges(self) -> dict[str, list[str]]:
        """Domain node to the pack topics that publish facts for it, read from the packs' curation files."""
        result: dict[str, list[str]] = {}
        curation = self.curation.current()
        for pack in curation.packs if curation else []:
            path = self.root / "releases" / pack / "curation.yaml"
            if not path.is_file():
                continue
            for topic in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("topics") or []:
                for node_id in topic.get("graph_nodes") or []:
                    result.setdefault(node_id, []).append(f"{pack}/{topic['topic_id']}")
        return result

    def card(self) -> dict:
        curation = self.curation.current()
        compiled = self.compiled.current()
        report = self.report.current() or {}
        checks = self.checks_report.current() or {}
        statuses: dict[str, int] = {}
        for item in self.items().values():
            statuses[item.provenance.review_status] = statuses.get(item.provenance.review_status, 0) + 1
        return dict(graph=self.graph, curation_error=self.curation.error,
                    nodes=len(curation.nodes) if curation else 0, edges=len(curation.edges) if curation else 0,
                    review=statuses, reviewed=statuses.get("human-reviewed", 0),
                    compiled=None if compiled is None else dict(
                        nodes=len(compiled.nodes), edges=len(compiled.edges), created_at=compiled.created_at.isoformat()[:19],
                        content_sha256=compiled.content_sha256, stale_from=compiled.freshness.stale_from.isoformat(),
                        snapshot_date=compiled.freshness.snapshot_date.isoformat()),
                    dropped=len(report.get("dropped", [])), checks=checks and dict(
                        cases=checks.get("cases"), passed=checks.get("passed"), failed=checks.get("failed_blocking")),
                    compiled_is_current=bool(compiled and curation and self.compiled_path.stat().st_mtime
                                             >= self.curation_path.stat().st_mtime))


class GraphConsole:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._graphs: dict[str, GraphData] = {}

    def names(self) -> list[str]:
        return graph_names(self.root)

    def graph(self, name: str) -> GraphData:
        if name not in self._graphs:
            if name not in self.names():
                raise KeyError(name)
            self._graphs[name] = GraphData(self.root, name)
        return self._graphs[name]


def dry_compile(graph: GraphData, curation: GraphCuration) -> dict:
    """Compile into memory with the pipeline's own code; a graph the pipeline would refuse refuses the write."""
    text = graph.text_dir if (graph.text_dir / "index.json").is_file() else None
    try:
        compiled, report = build_graph(curation, text, graph.root, places=graph_places(curation, graph.curation_path))
    except (BuildError, GraphInvalid, PlaceFileError) as exc:
        issues = exc.issues if isinstance(exc, GraphInvalid) else []
        raise WriteRefused(f"the graph would not compile: {exc}", issues[:10]) from exc
    return dict(nodes=len(compiled.nodes), edges=len(compiled.edges), dropped=report["dropped"])


def write_graph(graph: GraphData, mutate, actor: str, reason: str, *, expected_sha256: str | None = None,
                ids: list[str] | None = None) -> dict:
    """Load, change, validate, compile into memory, save, audit. Returns the dry compile's counts."""
    with pack_file_lock(graph), LOCKS.lock(graph.pack):
        if not graph.curation_path.is_file():
            raise WriteRefused(f"no graph curation at {graph.curation_path}")
        if graph.curation.error:
            raise WriteRefused(f"graph.yaml does not validate; fix it by hand first: {graph.curation.error}")
        check_unchanged(graph, graph.curation, graph.curation_path, expected_sha256)
        before = graph.curation.sha256
        curation = load_graph_curation(graph.curation_path)
        mutate(curation)
        try:
            curation = GraphCuration.model_validate(curation.model_dump(mode="python"))
        except ValidationError as exc:
            raise WriteRefused("graph.yaml would not validate", list(field_errors(exc).values()), field_errors(exc)) from exc
        report = dry_compile(graph, curation)
        save_graph_curation(graph.curation_path, curation)
        after = sha256_bytes(graph.curation_path.read_bytes())
        append_audit(graph, actor, graph.curation_path, before, after, reason, ids or [])
        graph.curation.current()
        return report


def record_rejection(graph: GraphData, item, actor: str, reason: str) -> None:
    path = graph.console_dir / "rejected.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(at=date.today().isoformat(), actor=actor, reason=reason, item=item.model_dump(mode="json"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
