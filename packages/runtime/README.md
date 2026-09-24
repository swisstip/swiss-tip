# Swiss TIP runtime

**Last update:** 24 September 2026

The release service implements coverage, concept search, scoped resolution
and evidence lookup over a validated release. Lexical search is the default
and requires no model or network. Resolution and evidence selection remain
deterministic when optional local semantic discovery is enabled.

## Lexical ranking

A query token counts once per concept, at the best field it appears in
(label and aliases weigh most, then example questions, description and fact
statements), multiplied by its rarity across the release's concepts. A hit
needs a match on the label, an alias or an example question. Function and
question words are stopwords. Case, diacritics and the ASCII spelling of
umlauts are folded away on both sides, so "Zürich", "Zuerich" and "Zurich",
or "Führerausweis" and "Fuehrerausweis", are one word, and words are cut to a
six-letter prefix stem. In a release of at least eight concepts, a
token found in at least three quarters of them (a municipal pack's own town
name) neither anchors nor scores a hit, and hits scoring below a fifth of the
best hit are dropped. On the committed Zurich query set this changed lexical
recall at three from 0.486 to 0.514 and at five from 0.543 to 0.571, with 8.2
instead of 10.1 matches per query; on the Wallisellen acceptance questions it
cut the matches per query from 16.3 (most questions matched every concept) to
2.8. The semantic index is unaffected; hybrid fusion ranks the shorter lexical
list.

Every score is then multiplied by the concept's prior,
`floor + (1 - floor) * authority` with the floor 0.7 by default: the
authority is the highest weight among the concept's facts, and a fact's
weight is the weight of the strongest basis among its excerpts (an act, an
ordinance or a treaty 1.0, a directive 0.95, guidance and a directory 0.9, a
portal summary 0.75) times the weight of its provenance kind and review
status (a reviewed curated statement 1.0, an unreviewed automatically
derived section 0.54). The weights come from the release manifest's
`ranking_policy`, or from `swisstip.core.basis` when a release has none; a
release without bases whose statements are reviewed ranks exactly as
before. In hybrid mode the same prior is applied to the semantic candidates'
similarity before rank fusion. The publisher's level never weighs. On the
release of 15 September the prior changed the top three of 3 of 35 fixture
queries and no acceptance case (design record: [institutions-and-provenance-weights.md](../../docs/architecture/institutions-and-provenance-weights.md), section 6).

## Match strength

Every `search` result says whether the question's distinctive words
reached a published concept at all (`match_strength`: `strong`, `weak`
or `none`), judged after rank fusion in both retrieval modes from raw
signals: the share and the rarity-weighted mass of the query tokens that
one concept's label, aliases or questions match, and, in hybrid mode,
the best raw cosine before the candidate cut. A weak or empty result
carries the release's scope statement and a guidance text that tells
the caller to decline in the same turn; the hits themselves are never
dropped. The rule, the thresholds and how they were measured are in
[tool-contracts.md](../../docs/architecture/tool-contracts.md), section
4.1; the pack check `scripts/test/packs/test_match_strength.py` replays the
measured questions on the committed Zurich release, so a rebuilt release that moves a question
over a threshold fails there.

## Jurisdiction in names

`resolve` takes where the user lives in the parts `country`, `canton` and
`city`, each a name in English or the local language or a code, and turns
them into the codes the facts carry with the release's place register
(`swisstip.core.places`). The country defaults to the one the release serves,
read from the manifest's jurisdictions; the `jurisdiction` description the
server sends with `tools/list` names it, and says so when a release carries
no register and reads codes only. `executed_scope` echoes the codes and the
register's official names; a part the register does not hold is listed in
`not_recognised`, the request runs for the broader place, and the guidance of
every status says which place the result is for. `answering_jurisdiction`
and the jurisdiction gap messages name a place next to its code. The field
names of schema v3 are still accepted.

## Search for a place

`search` takes the same `jurisdiction`, optionally. The concepts that can apply
at the place are ranked: those whose jurisdiction contains it, and those
below it, since a caller that gave the canton may not know the city yet. A
concept published for other places only is left out of `results` and, when
it would have been among the first `limit` hits, named in
`published_elsewhere`, with a guidance sentence that tells the caller not to
resolve it and to carry nothing over. In hybrid mode `SemanticSearch.scores`
returns every cosine from one embedding request and `cut` selects the
candidates twice, among every concept and among those that can apply. The
match strength stays that of the whole release. The rule and its measurement
on the regression pack are in
[tool-contracts.md](../../docs/architecture/tool-contracts.md), section 4.4.

## Institutions and basis on the wire

Citations and evidence name the publisher's `level` and `jurisdiction`, and
every served fact its `basis`, once on the concept when all its facts share
it: what the cited excerpt is (`Federal act: AIG, SR 142.20, Art. 12`,
`Cantonal authority guidance`, `Portal summary of federal rules`), whichever
page carries it. Citations list the page with the strongest basis first, and
a concept that serves a portal summary next to the law or an authority's
page gets one more guidance sentence naming the law's excerpt as the more
exact source. Releases built before these fields serve none of them.

## Caller guidance

`resolve` returns `guidance_for_caller` by status. `SUPPORTED`: answer from
the statements only, cite only the returned URLs and add nothing the
statements do not contain. `STALE`: the same, presenting the facts as
published on the snapshot date rather than as holding on `as_of`, without
resolving again with an earlier date. `OUT_OF_COVERAGE`: say the request is not
covered, and after `review_status_not_met` not to resolve again without
`reviewed_only` unless the user asks for unreviewed statements. When facts are
returned the guidance ends with what to tell the user (how many of them no
person has reviewed, and that English statements summarise pages in another
language and are not official translations) and asks for the whole answer in
the language of the user's question. Each concept
resolution also carries the concept's `not_served` list from the release when
it has one.

## Knowledge graph

`swisstip.runtime.graph` serves `get_knowledge_graph`: it matches the
question against the graph's domains (rarity-weighted, strongest field per
word), expands the best three by one hop, resolves each role to the
institution for the user's place by containment, states the place dependence
and the next search, and trims the result to 8 KB, least important links
first. `ReleaseService.tools()` lists the tool first for a release with a
graph, and the instructions and the other tools' descriptions then put it
first. `check_graph` replays a graph's orientation checks. Design:
[knowledge-graph.md](../../docs/architecture/knowledge-graph.md).

## Acceptance check

`swisstip.runtime.acceptance.check_acceptance` replays a pack's acceptance
suite (`releases/<pack>/acceptance.yaml`, models in `swisstip.core.acceptance`)
against a `ReleaseService` with no model or network and returns the report
the knowledge builder's `accept` stage writes; a resolve step's
`expect_basis` checks that at least one served fact of a concept rests on
the named basis (the law, a directive, an authority's guidance); the YAML loader lives in
`swisstip.build.acceptance`, so this package stays free of PyYAML. Format and
gate: [docs/architecture/acceptance-gate.md](../../docs/architecture/acceptance-gate.md).

## Optional local embedding index

A pack publishes its prebuilt index next to its release
(`releases/<pack>/semantic-index.json`): one vector per concept, built with
an Ollama embedding model such as `qwen3-embedding:0.6b`, and bound to that
release and to the model digest. The MVP packs and their indexes live in
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).

Use this index directly with the [MCP server](../../apps/mcp-server/README.md#optional-semantic-search)
or the benchmark below. Local Ollama must be running with that exact model
installed to embed each new query; the index contains document
vectors, not the model weights. Lexical search remains the default.

### Rebuild an index

The development CLI builds an index from published concept labels,
descriptions, aliases, example questions and fact statements. Its hashes tie
the index to the exact release, model digest and text-building version.
It requires an already installed Ollama embedding model. It does not pull
models. Only a loopback Ollama origin is accepted.

Regenerate the index when the release, model digest or indexed-text format
changes. Build a candidate under `.local/`, validate it against the intended
release, then replace the committed index as part of that release update.
Regeneration is an explicit command:

```shell
./.venv/Scripts/python.exe -m swisstip.runtime.search_cli index \
  --release releases/<pack>/release.json \
  --output .local/semantic-search/index.json \
  --model qwen3-embedding:0.6b --base-url http://127.0.0.1:11434 --timeout 30
```

On macOS or Linux replace `.venv/Scripts/` with `.venv/bin/`. Keep experimental
indexes and measurements under Git-ignored `.local/`. Embeddings rank
existing concept IDs. They do not establish that
a question is covered, resolve jurisdiction or replace the cited facts.

## Retrieval benchmark

The committed [query fixture](tests/fixtures/semantic-search-queries.json)
contains 42 novel questions: 35 supported and seven unsupported, across
English, German, French, Italian, Chinese and three Swiss German examples.
It was authored before live runs, with expected concept IDs tied to released
facts. It is an assistant-authored holdout, not independent human gold, a
language-quality certification or legal validation. Do not tune on this
fixture and continue calling it an unseen holdout; preserve each measured
dataset and its hash.

```shell
./.venv/Scripts/python.exe -m swisstip.runtime.search_cli benchmark \
  --release releases/<pack>/release.json \
  --index releases/<pack>/semantic-index.json \
  --cases packages/runtime/tests/fixtures/semantic-search-queries.json \
  --output .local/semantic-search/warm.json --repeats 3 --semantic-only
```

The fixture names the release it was authored for (`mvp-zurich-2026-09-13-v5`)
and is never edited, because the measurement records cite its hash. To run it
on a later release that still has the concept and fact identifiers it names, such
as the current release, add `--allow-release-change`; every
identifier is still validated and the report records both release IDs.

The main comparison is lexical versus hybrid search. `--semantic-only` adds
the embedding ranker alone. Every query requests five hits; the same ranking
is evaluated at three and five. The report includes per-query samples,
language aggregates, recall, recovery of every expected concept, first-hit
accuracy for cases with one expected concept, and empty/irrelevant results
for unsupported questions. Unsupported cases are excluded from recall.

Warm runs make one unscored warmup query and then measure actual search calls
without a query cache. `--cold --repeats 1` unloads the Ollama model before
each semantic or hybrid query and measures the following search, including
model loading. Unloading itself is excluded; OS caches are not flushed.
The wall-clock p50 and p95 use nearest-rank percentiles. These measurements
cover concept search only, not MCP transport or a caller's full answer.

Provider errors and lexical fallbacks are counted separately and excluded
from hybrid quality and successful latency aggregates. Their returned hits
and elapsed times remain in the samples. The command writes its report and
exits with status 2 if any query cannot execute its requested retrieval mode;
ordinary relevance misses are measurements and do not change the exit code.
The report records release, index, dataset and runtime-code hashes, model
digest, fixed retrieval settings and timestamps. Repeats are bounded to
1-10 and a dataset to 200 questions; there are no automatic retries.

Each timed semantic request verifies the installed model digest before
embedding. Index creation checks it before and after the document batch.
The embedding request sends `truncate: false`; an oversized input fails
explicitly rather than silently changing the indexed text. A local model
request uses the [Ollama embedding API](https://docs.ollama.com/api/embed).
The query instruction follows the query/document distinction in the
[Qwen model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

Implemented offline tests check metric denominators, provider failures,
fallbacks, expected concept/fact references and fixture shape. Live quality
and latency are established only by a separately recorded experiment.
The [13 September experiment](../../.local/experiments/2026-09-13-semantic-search.md)
records warm and unloaded-model timings and the remaining retrieval misses.

```shell
./.venv/Scripts/python.exe -m unittest discover -s packages/runtime/tests
```
