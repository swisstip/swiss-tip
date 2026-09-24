# swisstip-build

**Last update:** 24 September 2026

The build side of a pack: read `releases/<pack>/curation.yaml`, resolve every
cited block range against the pack's text dataset, pin it with hashes,
relocate citations whose page was downloaded anew, validate, and write
`release.json` with a `build-report.json` next to it. Depends on
`swisstip-core`, `swisstip-extraction` and PyYAML. The serving side never
imports it. `swisstip.build.acceptance` reads and writes the pack's
acceptance suite (`acceptance.yaml`, models in `swisstip.core.acceptance`),
which the knowledge builder's `accept` stage replays, and the regression
pack that adds `regression.yaml`; `swisstip.build.case_catalogue` renders
both with the latest committed results into the pack's `test-cases.md`; see
[docs/architecture/acceptance-gate.md](../../docs/architecture/acceptance-gate.md).

```shell
./.venv/Scripts/python.exe -m pip install -e packages/build
./.venv/Scripts/python.exe -m unittest discover -s packages/build/tests
./.venv/Scripts/python.exe -m swisstip.build.build_cli --curation releases/<pack>/curation.yaml --text .local/<pack>/text --release-id <release-id> --output releases/<pack>/release.json
```

| Option | Effect |
| --- | --- |
| `--curation FILE` | The pack's curation file |
| `--text DIR` | Text dataset of the pack's run |
| `--release-id ID` | Identity of the release; choose a new one when the content changes |
| `--output FILE` | `release.json` to write; the report goes next to it |
| `--update-curation` | Write relocated citations and fresh anchors back into the curation file |

A curation file can name two place files, relative to itself
(`place_register`, written by `swisstip-places` of the ingestion package, and
`place_aliases`, written by hand). `swisstip.build.places` merges them into
the release's place register, which lets a caller name a canton or a city
instead of its code; an alias for a code the register does not list stops the
build. The packs of one country share the files under `config/places/`.

Exit code nonzero when a fact was dropped; the release is still written. The
curation format, the relocation rules and the build report are described in
[docs/architecture/release-format.md](../../docs/architecture/release-format.md).

## Knowledge graph

`swisstip-graph` compiles and maintains a knowledge graph
(`graphs/<graph>/graph.yaml` of the packs repository):

```shell
swisstip-graph derive  graphs/ch/graph.yaml [--apply]          # places, institutions, laws, links from the packs
swisstip-graph merge   graphs/ch/graph.yaml PROPOSAL.yaml...   # reader agents' proposals, deterministically
swisstip-graph compile graphs/ch/graph.yaml --text .local/graph-ch/text
swisstip-graph embed   releases/<pack>                          # the compiled graph into a pack's release
```

Modules: `graph_curation` (the curation model), `graph_build` (the compiler,
reusing the release build's citation, institution and basis functions),
`graph_derive`, `graph_merge`, `graph_embed` (for the pack build) and
`graph_cli`. A pack's curation names the compiled graph with
`knowledge_graph:` and bridges its topics with `graph_nodes`; the build embeds
the graph. Design: [knowledge-graph.md](../../docs/architecture/knowledge-graph.md).

## Dataset bundles

```shell
./.venv/Scripts/python.exe -m swisstip.build.dataset_cli --packs-dir ../swiss-tip-mvp --pack <pack> --dataset <dataset_id>
```

`swisstip.build.datasets` builds the bundle a dataset connector serves
(`swiss-tip-dataset/v1`, models and validator in `swisstip.core.datasets`)
from `datasets/<pack>/<dataset_id>/dataset.yaml`
(`swiss-tip-dataset-curation/v1`: the binding to a pack and a concept, the
publisher and licence, the sources, the `csv-columns` importer with its
column mapping, the published period). The command downloads every source
it does not find under `.local/<pack>/datasets/<dataset_id>/`, the one step
with network, pins its hash in the curation file on the first download,
imports the rows, validates and writes `dataset.json` and
`build-report.json` next to the curation file. A downloaded file whose
bytes differ from the pin stops the build; `--refresh` accepts the
publisher's new file and re-pins it, `--offline` never downloads, and
`--version` raises the `v<n>` of the dataset version for a rebuild of the
same download. Design and status:
[docs/architecture/dataset-connectors.md](../../docs/architecture/dataset-connectors.md).

## Curation coverage

```shell
./.venv/Scripts/python.exe -m swisstip.build.coverage_cli --release releases/<pack>/release.json --text .local/<pack>/text --curation releases/<pack>/curation.yaml --dispositions releases/<pack>/curation-coverage.yaml --catalogue .local/<pack>/catalogue.json --output releases/<pack>/curation-coverage.json
```

`swisstip.build.coverage` joins the text dataset with the release and asks,
for every content section of every curation candidate, whether a fact cites
it or a curator dispositioned it in `curation-coverage.yaml`
(`swiss-tip-curation-coverage/v1`: `kind`, `reason`, `author`, `date`, and a
`document_id` with optional `section_ids` or a `url_prefix` rule with its
`known_documents`; `out_of_scope` names a phrase of the manifest's
`out_of_scope`, `deferred` a `reaffirm_by` date). The report lists every
open section with its heading path and block range, the findings (`stale`,
`expired`, `unknown_out_of_scope_entry`, `new_under_rule`, `unused_rule`) and
a roll-up per catalogue source. A section whose text recurs on
`boilerplate_min_pages` or more candidate pages of one host (a curation
field, default 5, never below 3; the count is per host because the same
sentence on two authorities' sites is two authorities saying it) is
boilerplate: not asked for, but traced. `repeated_sections`
names, per repeated text and host, the page or pages where a fact cites at least half
of its characters (`cited_nowhere`, `cited_once`, `cited_on_several_pages`),
looking at every cited record including blocks the section rules exclude as
furniture, because the canonical citation of a contact card is usually under
the "Kontakt" heading of the office's own page. Exit code 1 only when the report is not
clean and the curation says `coverage_policy: enforce`; `report`, the
default, writes it and exits 0. The knowledge builder runs it as the
`coverage` stage. Module docstring: why the release validator alone could
not find a saved page that no fact cites.
