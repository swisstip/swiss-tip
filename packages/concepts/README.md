# Concept extraction

**Last update:** 20 September 2026

`swisstip-concepts` proposes concepts from the extraction package's text
records. A separate model call reviews each structurally valid proposal
against its primary section, and a third call says what the evidence of
each retained proposal is (an act and its article, an ordinance, a treaty,
a directive, the authority's own guidance, a directory entry or a portal
summary) and at which level; `--no-classify-basis` skips it. Every retained
quotation is an exact record span. `swisstip-concepts-package` can append those proposals to a curation
file as `model-candidate` facts with `model-candidate-automated-review`
status, each citation carrying the classified basis. A person confirms them
in the admin console's review queue.

Implemented: the V3 prompts, section and span selection, extraction and
review, conservative merging, offline planning, request/character/token/time
ceilings, retries, checkpoints, five provider adapters, dataset summaries,
packaging and the predecessor batch adapter. The package's `unittest` suite
uses synthetic records and fake providers; it makes no network requests.
Live provider quality, cost measurements and expert assessment are separate
pilot work. Translation and console job controls remain planned.

## Install and test

Run from the repository root; use `.venv/bin/python` on macOS or Linux.

```shell
./.venv/Scripts/python.exe -m pip install -e packages/concepts
./.venv/Scripts/python.exe -m unittest discover -s packages/concepts/tests
```

The library depends on `swisstip-extraction` and `swisstip-build`. It uses
`urllib` and `tomllib`, with no provider SDK. The serving runtime and the
knowledge builder's deterministic stages do not import it.

## Plan and extract

```shell
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run releases/<pack> --kind catalogue --dry-run
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run releases/<pack> --kind catalogue --profile deepseek_flash --max-requests 100
```

The dry run creates `.local/<pack>/concepts/jobs/<job>/plan.json`, lists
the selection and the skipped documents, and creates no provider. A run
requires an explicit `--profile`; the configuration's active profile is
used only to identify a profile for a dry run without one. The configuration
is found at the repository root, independent of the working directory;
`--config FILE` overrides it.

Selection reads `<run>/text/index.json`: eligible, preferred, non-superseded
records only. The default `--scope attributed` includes catalogue,
in-scope, language-variant and plugin documents. Use repeatable `--kind`,
`--source`, `--document-id` and `--language` filters, `--max-pages`, or
`--scope all`. Documents over 120,000 characters are listed and skipped
unless `--allow-large` is set. Documents needing more than 12 planned
requests are also skipped. `--max-requests-per-page` overrides that ceiling.

The default run ceilings are 400 provider attempts and 1,200,000 kept input
characters. The plan refuses to start above them. Retries and failed
attempts consume the request budget. `--max-requests`,
`--max-total-input-characters`, `--max-prompt-tokens`, and `--max-minutes`
set ceilings; token and elapsed-time limits stop before the next request.
Missing token usage remains null, with known usage recorded separately.
`--workers N` processes records concurrently under one shared budget;
requests within each record are sequential. `--verbose` prints progress.

Profiles in [semantic-models.toml](../../config/semantic-models.toml):

| Profile | Adapter | Credential environment variable |
| --- | --- | --- |
| `deepseek_flash`, `deepseek_pro` | DeepSeek | `DEEPSEEK_API_KEY` |
| `apertus_70b` | Hugging Face / PublicAI | `HF_TOKEN` |
| `groq_gpt_oss_120b` | Groq | `GROQ_API_KEY` |
| `ollama_apertus_8b` | Ollama | None |
| `assistant_exchange` | Assistant file exchange | None |

Only the selected adapter reads its credential. Configuration cannot contain
credentials, and `.env` files are not loaded implicitly. HTTP redirects
are refused, requests and responses are capped at 1 MB, JSON is decoded
strictly, and observed model identity is checked both live and on replay.
PublicAI fallback and response caching are disabled.

## Resume and inspect

Run the same command again. Reports with matching content, prompts, profile,
model and settings skip completed records. Request checkpoints reuse each
answered call, including successful chunks from a partial report and the
smaller review calls used after a truncated response. The key includes the
request, record hash, model, profile and generation settings; changing a
timeout does not invalidate paid work. Compatible checkpoints can be reused
across jobs.

`--force` rebuilds selected reports using checkpoints; `--fresh-inference`
also bypasses checkpoints. `--retry-failed` processes only existing failed
reports. Invalid completions are kept as `*.invalid.json` and retried on a
later run. Reports from replaced content are archived under `history/`;
`--prune` removes that history and reports of superseded documents.

The dataset contains `documents/<id>.json`, `index.json`, an index grouped
by source in `index.md`, and `summary.json`. Each job contains its plan,
append-only `job.log`, checkpoints and a summary with execution statistics,
exact-match consolidation groups and at most 200 suggested duplicate pairs.
Candidates are never merged across records. Reports include structural and
review rejections, failed chunks, exact spans, prompt hashes and model
identities. Exit codes are 0 for completed work, 1 for failed or awaiting
work, and 2 for invalid configuration or command usage.

Output defaults to `.local/<run-name>/concepts`. `--output` may select a
different working directory, but never one inside `pages/`, `text/`, a
`*-documents/` directory or a pack folder under `releases/`. Prompts and raw responses
stay in working data. `--extraction-prompt FILE`, `--review-prompt FILE`
and `--basis-prompt FILE` replace entire prompts and record their paths and
effective hashes; the V3 extraction and review prompts are untouched by the
basis prompt, which is a separate file.

## Assistant exchange

```shell
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run .local/<pack> --source ag-residence --profile assistant_exchange
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli check --output .local/<pack>/concepts
```

The first pass captures extraction requests in
`jobs/<job>/exchange/requests/<key>.json`. A response goes in
`responses/<key>.json`, for example:

```json
{
  "schema_version": "swisstip.assistant-extraction-response/v1",
  "observed_model": "claude-fable-5-1",
  "content": "{\"concepts\": []}"
}
```

Use the model that actually answered and the exact key from the request.
`content` is a JSON string satisfying the request's `response_schema`.
Responses require explicit `observed_model` or `model`; attribution is
never inferred from the filename or configured profile. Optional usage
fields are `prompt_tokens` and `output_tokens`.

Rerunning consumes these responses and captures review requests for valid
proposals; a third rerun captures the basis requests for the supported ones.
Answer those and rerun to finish. Missing responses produce
`awaiting_response` chunks and are never checkpointed as completions.
`--on-missing fail` stops at the first missing file. `check` validates the
response schemas, evidence ownership, verdict rules and identities and
moves invalid responses aside.

## Package candidates

```shell
./.venv/Scripts/python.exe -m swisstip.concepts.package_cli --concepts .local/<pack>/concepts --curation releases/<pack>/curation.yaml --text .local/<pack>/text --topic residence
```

Without `--write`, the command prints proposed entries and writes a
`packaging-report.json` beside the curation file. Add `--write` to append
them after validation. The topic must already exist. Jurisdiction comes
from the current record's first registry entry or an explicit
`--jurisdiction`; missing jurisdiction is reported and skipped. Stale
reports and already-packaged candidate IDs are skipped. A fact a person
accepted is never changed.

Filters include repeatable `--document-id`, `--source`, `--concept-type`,
`--granularity`, plus `--min-confidence` and `--job`. The default excludes
`DOMAIN` candidates. Evidence spans become contiguous block citations,
anchored by the extraction package. The complete proposed curation is
validated and dry-built; every citation must resolve `same-snapshot`
before anything is written.

Use repeatable `--legacy-batch FILE` instead of `--concepts` to migrate
`swisstip.concept-proposal-batch/v1` files. The adapter strips known V3
heading prefixes and finds normalized quotations in the current records.
Missing or ambiguous quotations are dropped and listed. No candidate is
automatically accepted, regardless of confidence or review decision.

See the [technical design](../../docs/architecture/concept-extraction.md)
for report fields, the content-selection rules and the remaining pilot.
