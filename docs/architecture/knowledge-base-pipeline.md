# Knowledge base pipeline

**Last update:** 24 September 2026

How a knowledge base (a pack under `releases/<pack>/`) goes from an idea to
a published release, and how an existing pack is extended. Each step names
the command and the document that describes it in detail; this page only
orders them. It is the route the `mvp-zurich` and `mvp-wallisellen`
packs and their extensions follow.

| Step | Name | Output |
| ---: | --- | --- |
| 1 | Scope and questions | `docs/product/<pack>-user-acceptance-tests.md` |
| 2 | Catalogue | `releases/<pack>/sources.json`, `sources.md` |
| 3 | Acquire | `.local/<pack>/pages/`, `plan.json`, `gap-report.md` |
| 4 | Extract | `.local/<pack>/text/` |
| 5 | Curate | `releases/<pack>/curation.yaml` |
| 6 | Build and accept | `release.json`, `build-report.json`, `acceptance-report.json` |
| 7 | Human review | review statuses in `curation.yaml`, rebuilt `release.json` |
| 8 | Semantic index | `semantic-index.json` |
| 9 | Regression and live runs | `regression-report.json`, `acceptance-answers.json` |
| 10 | Readiness | `readiness.json` |
| 11 | Documents | `COVERAGE.md`, `LIMITATIONS.md`, `releases/README.md`, the pack README |
| 12 | Commit | Git |
| 13 | Publish | container images on ghcr.io, GitHub release `kb/<release_id>` |

Steps 1 to 5 happen once per idea or extension. Steps 6 to 10 repeat after
every change to the curation file, and step 10 invalidates itself whenever
`release.json` changes by a single byte, so it comes last. The commands use
the Windows virtual environment path; on macOS or Linux replace
`.venv/Scripts/` with `.venv/bin/`.

## 1. Scope and questions

- Decide what the pack or extension covers, at which levels of the state
  (federal, cantonal, municipal) and in which languages. The product
  context is the [functional specification](../product/functional-specification.md).
- Write the user questions first, with the expected answer and the trap
  each one sets, in the pack's acceptance-test document
  ([user-acceptance-tests.md](../product/user-acceptance-tests.md) for
  `mvp-zurich`, [wallisellen-user-acceptance-tests.md](../product/wallisellen-user-acceptance-tests.md)
  for `mvp-wallisellen`). They decide which pages the catalogue needs, and
  they become the acceptance suite in step 6.
- A new pack gets a folder name (`releases/<pack>/`). An extension of an
  existing pack gets new topics in the same folder.

## 2. Catalogue

- `releases/<pack>/sources.json`: one entry per source with start URL, host
  and path allowlist, authority, jurisdiction, language, scan sets and crawl
  budget. `sources.md` lists explicit pages (seeds and their subpages,
  PDFs). URLs and planning metadata only, no content
  ([releases README](../../releases/README.md#what-the-catalogues-contain)).
- Bump the catalogue version (`draft-N`) with every change. A run directory
  is bound to the catalogue it was planned from, so a changed catalogue
  needs a new run: keep the old one under another name
  (`.local/<pack>-run-<date>-draft-N/`) and plan a fresh `.local/<pack>/`.
  Saved pages worth keeping are copied by a one-time script under
  `.local/scripts/`.
- The admin console's sources screen edits the catalogue too.

## 3. Acquire

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --until gaps --download --workers 4
```

- Plans the run and saves every target byte for byte under `.local/<pack>/`
  (never under `releases/`). The bounded downloader follows no link beyond
  the catalogue.
- Read `gap-report.md`: dead links, application shells and access denials
  are catalogue decisions, not retries. Soft error pages served with HTTP
  200 (the ch.ch case) are classified `soft-error-page`.
- Design: [ingestion README](../../packages/ingestion/README.md).

## 4. Extract

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --until validate-text --workers 4
```

- Writes the text dataset under `.local/<pack>/text/`: labelled blocks with
  code-point offsets and hashes, and a Markdown reading view per record.
  No model is involved.
- When a publisher puts content in page components the extractor does not
  read yet (the City of Zurich's `stzh-contact` and `stzh-datatable` are
  such components), extend the extractor with tests and raise
  `EXTRACTOR_VERSION`.
- A PDF whose page is an image has no text (no OCR); a PDF without a
  declared language needs a `page_languages` rule in step 5.
- Design: [extraction.md](extraction.md).

## 5. Curate

The curation file `releases/<pack>/curation.yaml` holds topics, concepts,
facts and their citations as block ranges of the reading views. Format:
[release-format.md](release-format.md), section 3.

Three routes write it:

| Route | State |
| --- | --- |
| The facts are written by reading the reading views: by a person, or by the assistant in the Claude Code conversation, facts `assistant-authored-unreviewed` | Used for the served releases and their extensions |
| `swisstip-concepts` proposes candidates with a model (extraction, review and basis calls), `swisstip-concepts-package` appends them as `model-candidate` facts ([concept-extraction.md](concept-extraction.md); the Claude Code route without an API key is in [agent-operated-curation.md](../product/agent-operated-curation.md)) | Implemented and tested offline |
| The admin console's curation workbench ([admin-console README](../../apps/admin-console/README.md)) | Implemented; used for review (step 7) rather than authoring |

What the curation must carry besides the facts:

- the manifest: `scope_statement`, `out_of_scope` and the served
  `limitations` (every caller reads them; they must match `LIMITATIONS.md`);
- the `institutions` registry and `page_basis` rules, and the `basis` of
  citations that differ from the page rule
  ([institutions-and-provenance-weights.md](institutions-and-provenance-weights.md)).
  The basis can be classified with the prompt
  `packages/concepts/src/swisstip/concepts/prompts/basis_classification_v1.md` by subagents
  over per-page packets, as for `mvp-wallisellen`; the outcome goes to
  `releases/<pack>/basis-review.md`;
- aliases in the users' own words, and `source_terms` copied verbatim from
  the cited excerpts, never translated.
  `python -m swisstip.build.source_terms releases/<pack>/release.json`
  lists the candidates after a first build;
- sample `questions` in every query language the pack lists in
  `question_languages` (the build refuses a concept without one), written
  from the concept's facts and never copied from a regression case; each
  added word moves the rarity weights, so replay the regression pack after
  adding them;
- `page_languages` for records without a language;
- `valid_through` on dated facts (collection dates, fees for a year);
- `place_register` and `place_aliases`: the place files of the pack's
  country under `config/places/`, which the build embeds so that a caller
  names a canton or a city instead of its code
  ([release-format.md](release-format.md), "Place register"). The Swiss
  files exist; `swisstip-places --output config/places/ch-register.json`
  fetches the register of municipalities again, which is due after the
  mergers of each 1 January, and the next build embeds it under a new
  release ID.

## 6. Build and accept

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack>
```

- Runs `build`, `validate-release`, `health` and `accept` (the default
  `--until`). The build resolves every citation, pins it with hashes,
  verifies the source terms and refuses an invalid release. The release ID
  is bumped (`<pack>-<date>-vN`) when the inputs changed.
- `build-report.json` must list no dropped fact.
- `releases/<pack>/acceptance.yaml` turns each question of step 1 into a
  case: the search and resolve requests, the expected status and the claims
  the served facts must carry, each with the phrase its excerpt must
  contain. Add declined questions (`DECLINE-`, `SEARCH-DECLINE-`) that
  assert `OUT_OF_COVERAGE` and a gap on the server, not an answer. A
  blocking case that fails stops the pipeline.
- Design: [release-format.md](release-format.md) section 4,
  [acceptance-gate.md](acceptance-gate.md) sections 2 and 3.

## 7. Human review

- A person confirms, corrects or rejects every new fact in the admin
  console's review queue (filter `review_status`), which sets
  `human-reviewed` and `reviewed_by`:

  ```shell
  ./.venv/Scripts/python.exe -m swisstip.admin_console.app --actor "<name>"
  ```

- Readiness does not require reviewed facts, but `LIMITATIONS.md` and the
  served `limitations` must state the review status in absolute numbers,
  including how many facts were confirmed in bulk groups.
- Update the served limitations line, then run step 6 again. The rebuilt
  release gets a new version and content digest.

## 8. Semantic index

```shell
./.venv/Scripts/python.exe -m swisstip.runtime.search_cli index \
  --release releases/<pack>/release.json \
  --output .local/semantic-search/index.json \
  --model qwen3-embedding:0.6b --base-url http://127.0.0.1:11434 --timeout 30
```

- Needs local Ollama with `qwen3-embedding:0.6b` at the digest the images
  use. Validate the candidate, then replace
  `releases/<pack>/semantic-index.json`. The index is bound to the release
  ID and content digest; a stale one makes the server fall back to lexical
  search and fails the pack image build.
- Details: [runtime README](../../packages/runtime/README.md#rebuild-an-index).

## 9. Regression and live runs

```shell
./.venv/Scripts/python.exe scripts/test/regression/run_regression.py --pack <pack>
```

- `releases/<pack>/regression.yaml` adds breadth beyond the acceptance
  document: plain questions in the users' languages, noisy questions and
  declined ones. Cases added for an extension follow the pack's existing
  ratio of cases per fact. A case that fails in the hybrid run is committed
  with `blocking: false` and a `quarantine_reason`. The runner replays both
  suites lexically and hybrid and writes `regression-report.json`, which the
  committed-pack test requires to be current
  ([acceptance-gate.md](acceptance-gate.md), section 10).
- Live caller runs through the OpenCode harness
  ([harness README](../../scripts/test/mock-mcp/README.md)) are graded per
  case, and `--answers` writes `acceptance-answers.json` for gate G5. The
  gate is `advisory` for `mvp-zurich`, so these runs are optional for
  readiness and are often done after publication.

## 10. Readiness

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --from accept --until ready --attested-by "<name>"
```

- Runs gates G1 to G6 on the current files and writes `readiness.json`,
  bound to the bytes of `release.json` and the suite digest
  ([acceptance-gate.md](acceptance-gate.md), section 5). The server with
  `--require-ready`, the container build and the committed-pack test all
  refuse a release without a matching record.
- Any later change to `release.json`, including a reworded limitation,
  needs this step again. So does a change to `acceptance.yaml`.

## 11. Documents

In the same change as the release, with every number read from the
release and its reports ([AGENTS.md](../../AGENTS.md), "Coverage and
limitations documents"):

- `COVERAGE.md` and `LIMITATIONS.md` of the packs repository for the pack
  they describe;
  the admin console's release screen renders both from the release for
  comparison;
- the pack's row in [releases/README.md](../../releases/README.md);
- the image README `releases/<pack>/README.md` (where the pack has one),
  which quotes the release ID, digest and scope;
- the acceptance-test document (new cases, corrected expectations), and
  `TODO.md` with the open items;
- the demo welcome panel `docker/demo-opencode/welcome.json` when the
  sample questions should change; they must come from acceptance cases;
- release notes, if wanted, under `.local/release-notes/`.

## 12. Commit

The lead commits. The commit carries the curation, the release and all its
reports (`release.json`, `build-report.json`, `acceptance-report.json`,
`regression-report.json`, `readiness.json`, `semantic-index.json`,
`pipeline-report.json`) and the documents. The run directory stays in
`.local/`.

## 13. Publish

- Run the workflow `container-images.yml` of the packs repository on GitHub
  (manual, selection `mvp-zurich`, `mvp-wallisellen`, `packs` or `all`, with
  push). It builds and tests the pack image and the slim image, on the
  both MCP images this repository's workflow of the same name
  pushed before, and pushes them to `ghcr.io/<owner>/swiss-tip` under the
  release ID and pack tags.
  [scripts/container/build_image.py](../../scripts/container/build_image.py)
  builds the same image locally
  ([docker README](../../docker/README.md)).
- Verify the published image as the jury pulls it:

  ```shell
  ./.venv/Scripts/python.exe scripts/test/container/run_image_test.py \
    --image ghcr.io/<owner>/swiss-tip:<release_id> --require-public
  ```

- Create the GitHub release `kb/<release_id>` with the same release files
  as the previous `kb/` release (among them `release.json`,
  `readiness.json` and `semantic-index.json`) and the pack README as
  assets, so the release can be served without the image or a clone.
- Point the tags in `run_image_test.py`, the server README and the docker
  README at the new release.
- PyPI (`pypi-packages.yml`, triggered by a `v*` tag) is only needed when
  the code of the packages changed; a new release alone needs no new
  package version. A new package version also needs this repository's
  `container-images.yml` (selection `all`, with that version) before the
  packs are rebuilt on it.

## The knowledge graph

The orientation graph that `get_knowledge_graph` serves is built beside the
packs, in `graphs/<graph>/`, with the same stages and its run in
`.local/graph-<graph>/`; the design and every command are in
[knowledge-graph.md](knowledge-graph.md), and the agent route in
[agent-built-knowledge-graph.md](../product/agent-built-knowledge-graph.md).
In short: `swisstip-build <graph> --graph --packs-dir ../swiss-tip-mvp`
runs acquire, gaps, extract, validate-text, derive (a preview unless
`--apply-derive`), compile and check. A pack embeds the compiled graph when its
curation names it (`knowledge_graph:`), in its build stage, or with
`swisstip-graph embed releases/<pack>` when its run is not at hand; the pack's
release then changes and is accepted and attested again.

Source etiquette applies to the graph's pages as to a pack's: the acquire
stage obeys robots.txt unless the run passes `--no-obey-robots` or the
operator sets `SWISSTIP_OBEY_ROBOTS=0` (README, "Source etiquette").

## A new pack, beyond an extension

Steps 1 to 13 apply unchanged. In addition:

- a `docker/<pack>/Dockerfile` for the all-in-one pack image, and the pack
  in the selection of the packs repository's `container-images.yml` (the slim image takes any pack
  through `--build-arg PACK=<pack>`);
- the server serves the pack named by
  `--release releases/<pack>/release.json` (it has no default pack);
- `COVERAGE.md` and `LIMITATIONS.md` describe only the pack they name; the
  other pack's scope, counts and review status go into its row of the
  releases README and its served manifest;
- a pack for another country needs that country's place files: a place
  file in the `swiss-tip-places/v1` format from its official register
  (`swisstip-places` reads the Swiss one only) and an alias file with the
  country's entry. The level names of its jurisdiction parts (`state`) are
  already accepted on the `canton` field; the jurisdiction codes of the
  release validator are Swiss (`CH`, `CH-ZH`, `CH-ZH-261`) and would have
  to be widened.
