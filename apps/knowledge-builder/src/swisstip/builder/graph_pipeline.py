"""The build pipeline of a knowledge graph: its catalogue to a compiled, checked `graph.json` in seven stages.

A graph lives beside the packs, in `graphs/<graph>/` of the packs repository (catalogue `sources.json`, curation
`graph.yaml`, orientation checks `checks.yaml`, and the compiled `graph.json`), with its run in `.local/graph-<graph>/`.
The first four stages are the pack's own, unchanged; the pack build then embeds `graph.json` wherever a pack's
curation names it.

| Stage | Does | Skipped when |
| --- | --- | --- |
| acquire, gaps, extract, validate-text | as for a pack, on the graph's catalogue and run | as for a pack |
| derive | Previews what Derive from packs would add or update in `graph.yaml`; writes it only with `apply_derive` | never |
| compile | Compiles `graph.yaml` to `graph.json` and `graph-report.json`, relocating citations; fails on an invalid graph | graph.yaml, the text index, the cited pack releases and the place register unchanged since the last compile |
| check | Replays `checks.yaml` with the server's code and writes `checks-report.json`; a failing blocking case fails | no checks file |

Every run writes `graphs/<graph>/pipeline-report.json`.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import yaml

from swisstip.build.graph_build import build_graph, graph_places
from swisstip.build.graph_cli import run_derive
from swisstip.build.graph_curation import load_graph_curation, save_graph_curation
from swisstip.build.places import PlaceFileError, load_place_register
from swisstip.build.release_build import BuildError
from swisstip.core.graph import GraphInvalid, dump_graph, load_graph
from swisstip.core.places import PlaceIndex
from swisstip.runtime.graph import GraphChecks, GraphIndex, check_graph

from .pipeline import Pipeline, StageError, sha256_file

GRAPH_STAGES = ("acquire", "gaps", "extract", "validate-text", "derive", "compile", "check")


class GraphPipeline(Pipeline):
    stage_names = GRAPH_STAGES

    def __init__(self, root: Path, graph: str, *, apply_derive: bool = False, **options):
        super().__init__(root, f"graph-{graph}", **options)
        self.graph = graph
        self.pack_dir = self.root / "graphs" / graph
        self.catalogue = self.pack_dir / "sources.json"
        self.markdown = self.pack_dir / "sources.md"
        self.curation = self.pack_dir / "graph.yaml"
        self.checks_path = self.pack_dir / "checks.yaml"
        self.graph_path = self.pack_dir / "graph.json"
        self.report_path = self.pack_dir / "pipeline-report.json"
        self.apply_derive = apply_derive
        self.previous = json.loads(self.report_path.read_text(encoding="utf-8")) if self.report_path.is_file() else {}

    def handlers(self) -> dict:
        return {"acquire": self.acquire, "gaps": self.gaps, "extract": self.extract, "validate-text": self.validate_text,
                "derive": self.derive, "compile": self.compile, "check": self.check}

    def derive(self) -> tuple[str, dict]:
        if not self.curation.is_file():
            return "skipped", dict(reason=f"no graph curation at {self.curation}")
        try:
            diff = run_derive(self.curation, self.root, self.apply_derive)
        except (BuildError, PlaceFileError, OSError, ValueError) as exc:
            raise StageError(str(exc)) from exc
        return "ran", dict(applied=diff["applied"], added=len(diff["added"]), updated=len(diff["updated"]),
                           kept=len(diff["kept"]), stale=diff["stale"], notes=diff["notes"])

    def compile_inputs(self) -> str:
        """graph.yaml, the text index, every pack release Derive's citations point to, and the place register."""
        curation = load_graph_curation(self.curation)
        parts = [sha256_file(self.curation)]
        index = self.text / "index.json"
        if index.is_file():
            parts.append(sha256_file(index))
        for pack in sorted(curation.packs):
            release = self.root / "releases" / pack / "release.json"
            if release.is_file():
                parts.append(sha256_file(release))
        if curation.place_register:
            parts.append(sha256_file((self.curation.parent / curation.place_register).resolve()))
        return hashlib.sha256("".join(parts).encode()).hexdigest()

    def compile(self) -> tuple[str, dict]:
        if not self.curation.is_file():
            return "skipped", dict(reason=f"no graph curation at {self.curation}")
        previous = next((s for s in self.previous.get("stages", []) if s["stage"] == "compile" and s.get("inputs_sha256")), None)
        inputs = self.compile_inputs()
        if previous and self.graph_path.is_file() and previous["inputs_sha256"] == inputs and not self.update_curation:
            return "skipped", dict(reason="graph.yaml, text index, pack releases and place register unchanged",
                                   inputs_sha256=inputs)
        curation = load_graph_curation(self.curation)
        text = self.text if (self.text / "index.json").is_file() else None
        try:
            graph, report = build_graph(curation, text, self.root, places=graph_places(curation, self.curation),
                                        update_citations=self.update_curation)
        except (BuildError, GraphInvalid, PlaceFileError) as exc:
            raise StageError(str(exc)) from exc
        self.graph_path.write_text(dump_graph(graph), encoding="utf-8", newline="\n")
        (self.pack_dir / "graph-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                        encoding="utf-8", newline="\n")
        if self.update_curation:
            save_graph_curation(self.curation, curation)
        return "ran", dict(graph_id=graph.graph_id, nodes=report["nodes"], edges=report["edges"],
                           review_statuses=report["review_statuses"], citation_outcomes=report["citation_outcomes"],
                           dropped=len(report["dropped"]), content_sha256=graph.content_sha256,
                           inputs_sha256=self.compile_inputs())

    def check(self) -> tuple[str, dict]:
        if not self.checks_path.is_file():
            return "skipped", dict(reason="the graph has no checks.yaml")
        if not self.graph_path.is_file():
            raise StageError("no graph.json; run the compile stage first")
        graph = load_graph(self.graph_path)
        checks = GraphChecks.model_validate(yaml.safe_load(self.checks_path.read_text(encoding="utf-8")))
        curation = load_graph_curation(self.curation)
        register = None
        if curation.place_register:
            register = load_place_register((self.curation.parent / curation.place_register).resolve())
        report = check_graph(GraphIndex(graph), PlaceIndex(register, ["CH"]), checks, date.today())
        (self.pack_dir / "checks-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8", newline="\n")
        if report["failed_blocking"]:
            raise StageError(f"blocking orientation checks failed: {', '.join(report['failed_blocking'])}; "
                             f"see {self.pack_dir / 'checks-report.json'}")
        return "ran", dict(cases=report["cases"], passed=report["passed"])
