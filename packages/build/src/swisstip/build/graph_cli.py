"""Knowledge graph commands: Derive from packs, and compile `graph.yaml` to `graph.json`.

    swisstip-graph derive  graphs/<graph>/graph.yaml            # preview what Derive would change
    swisstip-graph derive  graphs/<graph>/graph.yaml --apply    # write it into graph.yaml
    swisstip-graph compile graphs/<graph>/graph.yaml --text .local/graph-<graph>/text [--update-citations]
    swisstip-graph merge   graphs/<graph>/graph.yaml PROPOSAL.yaml...   # merge reader agents' proposals
    swisstip-graph embed   releases/<pack>                       # put the compiled graph into the pack's release

Paths are resolved against the packs repository, which is the folder two levels above `graph.yaml`
(`graphs/<graph>/graph.yaml`) unless `--packs-dir` names it. `compile` writes `graph.json` and `graph-report.json`
next to `graph.yaml` and refuses a graph that does not validate.

`embed` is for a pack whose run is not at hand: it puts the `graph.json` the pack's curation names into the pack's
current `release.json`, with the topics' `graph_nodes`, under the next release ID, and validates the result. Facts,
evidence and documents stay byte for byte as the last build wrote them. A pack whose run is at hand is rebuilt
instead (`swisstip-build <pack>`), which embeds the graph the same way. Either way the release changes, so its
readiness record no longer matches: re-accept and attest it.
"""

import argparse
import copy
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from swisstip.core.graph import GraphInvalid, dump_graph
from swisstip.core.release import Topic, content_hash, dump_release, load_release
from swisstip.core.validation import validate_release

from .curation import load_curation
from .graph_embed import graph_for

from .graph_build import build_graph, graph_places
from .graph_curation import load_graph_curation, save_graph_curation
from .graph_derive import apply_derivation, derive
from .graph_merge import load_proposals, merge_proposals
from .places import load_place_register
from .release_build import BuildError


def packs_root(curation_path: Path, packs_dir: Path | None) -> Path:
    return Path(packs_dir).resolve() if packs_dir else Path(curation_path).resolve().parent.parent.parent


def run_derive(curation_path: Path, root: Path, apply: bool) -> dict:
    curation = load_graph_curation(curation_path)
    register = None
    if curation.place_register:
        register = load_place_register((Path(curation_path).resolve().parent / curation.place_register).resolve())
    target = curation if apply else copy.deepcopy(curation)
    diff = apply_derivation(target, derive(target, root, register))
    if apply and (diff.added or diff.updated):
        save_graph_curation(curation_path, target)
    return dict(applied=apply, **diff.as_dict())


def run_merge(curation_path: Path, root: Path, proposals: list[Path], text_dir: Path | None) -> dict:
    curation = load_graph_curation(curation_path)
    text_ids = None
    if text_dir is not None and (Path(text_dir) / "index.json").is_file():
        text_ids = {entry["document_id"] for entry in json.loads((Path(text_dir) / "index.json").read_text(encoding="utf-8"))}
    report = merge_proposals(curation, load_proposals(proposals), root, text_ids)
    save_graph_curation(curation_path, curation)
    return report.as_dict()


def run_compile(curation_path: Path, root: Path, text_dir: Path | None, update_citations: bool) -> dict:
    curation = load_graph_curation(curation_path)
    graph, report = build_graph(curation, text_dir, root, places=graph_places(curation, curation_path),
                                update_citations=update_citations)
    folder = Path(curation_path).resolve().parent
    (folder / "graph.json").write_text(dump_graph(graph), encoding="utf-8", newline="\n")
    (folder / "graph-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                                              newline="\n")
    if update_citations:
        save_graph_curation(curation_path, curation)
    return {key: value for key, value in report.items() if key != "items"}


def bumped_release_id(existing: str, pack: str, today: date) -> str:
    """Same day: the next version; another day: v1 of today (the knowledge builder's rule)."""
    match = re.fullmatch(rf"{re.escape(pack)}-(\d{{4}}-\d{{2}}-\d{{2}})-v(\d+)", existing)
    if match and match[1] == today.isoformat():
        return f"{pack}-{today.isoformat()}-v{int(match[2]) + 1}"
    return f"{pack}-{today.isoformat()}-v1"


def run_embed(pack_dir: Path) -> dict:
    pack_dir = Path(pack_dir)
    curation = load_curation(pack_dir / "curation.yaml")
    graph = graph_for(curation, pack_dir / "curation.yaml")
    if graph is None:
        raise ValueError(f"{pack_dir / 'curation.yaml'} names no knowledge_graph")
    release = load_release(pack_dir / "release.json")
    bridges = {topic.topic_id: topic.graph_nodes for topic in curation.topics}
    release.topics = [Topic(**{**topic.model_dump(), "graph_nodes": bridges.get(topic.topic_id, [])})
                      for topic in release.topics]
    release.knowledge_graph = graph
    previous = release.manifest.release_id
    release.manifest.release_id = bumped_release_id(previous, release.manifest.pack, date.today())
    release.manifest.created_at = datetime.now(UTC)
    release.manifest.content_sha256 = content_hash(release)
    issues = validate_release(release)
    if issues:
        raise GraphInvalid(issues)
    (pack_dir / "release.json").write_text(dump_release(release), encoding="utf-8", newline="\n")
    return dict(pack=release.manifest.pack, previous_release_id=previous, release_id=release.manifest.release_id,
                graph_id=graph.graph_id, graph_sha256=graph.content_sha256, nodes=len(graph.nodes), edges=len(graph.edges),
                bridged_topics={topic_id: nodes for topic_id, nodes in bridges.items() if nodes},
                content_sha256=release.manifest.content_sha256,
                next_steps="re-run the acceptance suite and attest the release (swisstip-build <pack> --from accept --until ready)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="swisstip-graph", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    derive_parser = commands.add_parser("derive", help="derive graph items from the packs")
    derive_parser.add_argument("graph", type=Path, help="graphs/<graph>/graph.yaml")
    derive_parser.add_argument("--apply", action="store_true", help="write the derivation into graph.yaml")
    derive_parser.add_argument("--packs-dir", type=Path)
    compile_parser = commands.add_parser("compile", help="compile graph.yaml to graph.json")
    compile_parser.add_argument("graph", type=Path, help="graphs/<graph>/graph.yaml")
    compile_parser.add_argument("--text", type=Path, help="the graph's text dataset (.local/graph-<graph>/text)")
    compile_parser.add_argument("--update-citations", action="store_true",
                                help="rewrite relocated citations and anchors in graph.yaml")
    compile_parser.add_argument("--packs-dir", type=Path)
    merge_parser = commands.add_parser("merge", help="merge reader proposals into graph.yaml")
    merge_parser.add_argument("graph", type=Path, help="graphs/<graph>/graph.yaml")
    merge_parser.add_argument("proposals", type=Path, nargs="+", help="<group>-proposal.yaml files")
    merge_parser.add_argument("--text", type=Path, help="the graph's text dataset, to check text references")
    merge_parser.add_argument("--packs-dir", type=Path)
    embed_parser = commands.add_parser("embed", help="put the compiled graph into a pack's current release")
    embed_parser.add_argument("pack_dir", type=Path, help="releases/<pack>")
    args = parser.parse_args(argv)
    try:
        if args.command == "embed":
            print(json.dumps(run_embed(args.pack_dir), ensure_ascii=False, indent=2))
            return 0
    except (BuildError, GraphInvalid, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    root = packs_root(args.graph, args.packs_dir)
    try:
        if args.command == "derive":
            result = run_derive(args.graph, root, args.apply)
        elif args.command == "merge":
            result = run_merge(args.graph, root, args.proposals, args.text)
        else:
            result = run_compile(args.graph, root, args.text, args.update_citations)
    except (BuildError, GraphInvalid, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
