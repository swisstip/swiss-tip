# Concept extraction - technical design

**Last update:** 21 September 2026

**Status:** phases 1 to 3 implemented and tested in `packages/concepts`;
offline tests cover the pipeline, providers, recovery, exchange, packaging
and legacy migration. A third call per chunk classifies the basis of every
retained proposal (section 4.7); no dataset has been extracted with it. No
live pilot or expert assessment has run; phases 4 and 5 remain planned
(section 10).<br>
**Consumers:** the curation file of a pack (facts of kind
`model-candidate`), the review queue of the admin console (its "Accept
candidate" action), and step 2 of the KB2 migration in [TODO.md](../../TODO.md).
The serving side never imports it.

---

## 1. Purpose and position in the pipeline

The text dataset is what the expert reads and the build cites; writing the
concepts and facts is manual, and it is the slowest step of the chain. The
concept-extraction package proposes them. It reads the text records of a
run, asks a model for candidate concepts per chunk of page content, asks a
second, separate model call to judge each proposal against only its cited
section, checks every citation as an exact span of the record, and packages
the retained candidates as curation entries of kind `model-candidate` for a
person to confirm, correct or reject in the review queue. It writes
proposals, never served facts: a candidate reaches a release only through
the same build, the same validator and the same review mark as a curated
statement.

```text
<run>/text/                          text dataset (extraction package): documents/<id>.json
        |  swisstip-concepts                 this package: sections, chunks, spans, two model calls
        v                                    per chunk, validation, checkpoints, budgets
.local/<pack>/concepts/              candidate dataset: documents/<id>.json, index.json, jobs/<job>/
        |  swisstip-concepts-package         this package: candidates -> curation entries
        v
releases/<pack>/curation.yaml        facts of kind model-candidate, status model-candidate-automated-review
        |  review queue (admin console)      a person confirms: curated-statement, human-reviewed
        |  release build, validator          exact excerpts, hashes, manifest counts
        v
releases/<pack>/release.json
```

Principles, from section 3.2 of the
[functional specification](../product/functional-specification.md):

- **The model selects, it does not quote.** Evidence is chosen from
  numbered spans that the package cuts from the record. A quotation is never
  typed by the model, and a span the record does not contain cannot be
  cited. The quote a candidate carries is exactly the text the build will
  cite.
- **One section per concept.** A concept names its primary section and
  cites only that section; conditions of a sibling section are never
  borrowed. This is the V3 rule that makes the review meaningful.
- **Separate review.** A second call, with its own prompt and schema,
  returns one verdict per proposal. A proposal it does not support stays in
  the report as rejected and is never packaged. The verdict is a model
  assessment and is recorded as such.
- **A person confirms.** `model-candidate-automated-review` is the highest
  review status the package can set. Only the review queue sets
  `human-reviewed`.
- **Budget before the first call.** The plan is computed offline; the run
  refuses to start when it exceeds the request, character or token
  ceilings, and stops when a ceiling is reached.
- **Every completion on disk.** Each model response is checkpointed under
  a key derived from the request. A rerun after a crash, a stop or a
  provider outage pays nothing for what was already answered.
- **Offline by default.** No provider is created without a named profile;
  the dry run makes no request; tests use a fake provider. The stages of the
  knowledge builder are untouched.
- **Original language.** All prose a candidate carries is in the language
  of the page, as the V3 prompts require and the review checks. An English
  rendering is a separate, optional step (section 10).

## 2. Inputs

| Input | Used for |
| --- | --- |
| `<run>/text/index.json` | Selection (attribution kind, source IDs, eligibility, preferred representation, superseded, language) and the reuse check |
| `<run>/text/documents/<document_id>.json` | `blocks` with `kind`, `text`, `start`, `end`, `heading_path`, `source_locator` (region, furniture, `explicit_hidden`, `is_footnote`, PDF labels); `content_sha256`, `acquisition.raw_sha256`, `title`, `language_declared`, `language_hint`, `source_url`, `source_registry` |
| `config/semantic-models.toml` | Profiles (adapter, model, base URL, provider, timeout, response mode), generation settings, extraction limits, retry settings (section 5.1) |
| Environment | `DEEPSEEK_API_KEY`, `HF_TOKEN`, `GROQ_API_KEY`; one variable per hosted adapter, fixed by name. The repository's Git-ignored `.env.dev` already defines these three names |
| Package data `prompts/*.md` | The V3 prompts and the basis prompt; `--extraction-prompt`, `--review-prompt` and `--basis-prompt` replace a whole prompt with a file, recorded by path and hash |
| `releases/<pack>/curation.yaml` | Packaging only: the topics and context fields the entries must fit, the concepts already present |
| A `swisstip.concept-proposal-batch/v1` file | Packaging only, through the legacy adapter (section 6.3) |

Selection options mirror the extraction package: `--scope attributed`
(default: catalogue targets, `in-scope` and `language-variant` discovered
pages, plugin documents) or `all`; `--kind`, `--source`, `--document-id`,
repeatable; `--language` (declared or hinted language of the record) and
`--max-pages`. A record is selected only when it is eligible, the
preferred representation of its source URL, and not superseded. PDF records
take part: their blocks are `pdf_paragraph` or `pdf_page` blocks. Records
whose text exceeds `max_characters_per_document` (default 120,000; the three
Fedlex acts of the full pack) are listed and skipped unless `--allow-large`.

Every record of both runs carries `furniture` labels
([extraction.md](extraction.md), section 9). The content selection of
section 4.3 also works without them, less precisely.

## 3. Outputs

### 3.1 Layout

Default output `.local/<pack>/concepts/`, overridable with `--output`. The
candidate dataset is working data of the same kind as the console's state
under `.local/<pack>/console/`: it holds prompts and raw model output, it is
large, and what is meant to be committed is the packaged curation entry,
which names the job and the report it came from. The run directory of a
committed pack (`releases/<pack>/`) is never written, which keeps rule 1
of section 4.2 of the specification intact. The command refuses an output
path inside `pages/`, `*-documents/` or `text/`.

```text
concepts/
  documents/<document_id>.json     candidate report per record (4.2)
  index.json, index.md             one entry per report; the Markdown is grouped by source
  summary.json                     counts over the reports, prompt hashes, models, tokens
  history/<document_id>/<hash>.json reports replaced after their content changed; removed by --prune
  jobs/<job_id>/
    plan.json                      the dry-run plan the job started from: selection, chunks, requests, ceilings
    job.log                        one line per request, per chunk and per record
    checkpoints/<key>.json         one file per model request (5.9)
    checkpoints/<key>.invalid.json a completion that failed structural validation, kept for the audit
    exchange/requests/, responses/ exchange adapter only (6.3)
    summary.json                   execution statistics, consolidation, duplicate review pairs (4.4)
```

`job_id` is `<date>-<profile>-<six hex digits of the plan hash>`.

### 3.2 Report

`schema_version: swisstip.concept-candidates/v1`, one file per record.

| Field | Content |
| --- | --- |
| `document_id`, `source_url`, `document_url`, `title`, `language` | Copied from the record; `language` is `language_declared`, else `language_hint` |
| `content_sha256`, `raw_sha256`, `extractor_version` | The record as extracted; the reuse check and the packaging step compare them with the current dataset |
| `job_id`, `profile`, `provider`, `model`, `model_identities` | The job and the profile that produced the report; one identity entry per completion with requested and observed model and request ID |
| `prompts` | `extraction`, `review` and `basis`, each with `sha256` and `sources` (package file names or the override path) |
| `settings` | `chunk_content_characters`, `chunk_overlap_characters`, `max_concepts_per_chunk`, `classify_basis`, generation settings |
| `sections` | Every section of the record in order: `section_id`, `first_block`, `last_block`, `heading_path`, `characters`, `kept` or the exclusion `reason` |
| `chunks` | Per chunk: `chunk_index`, `section_ids`, `characters`, `status` (`extracted`, `skipped_link_only`, `failed`, `awaiting_response`), `request_keys`, `prompt_tokens`, `output_tokens` |
| `candidates` | Retained candidates (4.3) |
| `rejected` | Every proposal not retained, with `stage` (`structural` or `review`), `reason`, `chunk_index`, `proposal_index` and the proposal as returned |
| `reviews` | Every verdict with `chunk_index`, `proposal_index`, `review_id`, `decision`, `issue`, `reason`, `preferred_label`, `primary_section_id` |
| `failures` | Chunks whose completion could not be parsed or validated as a whole, with the checkpoint key of the raw completion; a failed basis call is listed as `basis_unparseable` and leaves the chunk's candidates without a basis |
| `request_count`, `prompt_tokens`, `output_tokens` | Over the report; token sums are null when any completion lacked usage |
| `quality_metrics` | The V3 counters: proposals accepted and rejected, retained candidates, sections kept, excluded and cited, `uncited_section_ids`, empty questions, review requests, semantic rejections, the two fixed strings `confidence_interpretation: uncalibrated_model_assessment` and `evidence_validation: source_location_only; semantic_support_requires_review`; added: `basis_request_count`, `candidates_with_basis` and `basis_classification` (`separate_model_assessment` or `not_performed`) |
| `output_sha256` | SHA-256 over the canonical JSON of `candidates` |
| `generated_at`, `warnings` | |

### 3.3 Candidate

| Field | Content |
| --- | --- |
| `candidate_id` | `candidate-` + 16 hex digits of SHA-256 over `content_sha256`, label, scope, type, granularity, description and primary section, as in V3 |
| `preferred_label`, `alternative_labels`, `concept_type`, `granularity`, `description`, `scope`, `user_questions`, `confidence`, `relations` | As returned by the model and validated; `concept_type` in ENTITY, PROCESS, RULE, SERVICE, DOCUMENT, OTHER; `granularity` in DOMAIN, TOPIC, ANSWERABLE, DETAIL |
| `primary_section_id`, `heading_path` | The section the concept cites and its heading path |
| `evidence` | One item per selected span: `evidence_id`, `block_id`, `block_number`, `start`, `end` (code points into `content_text`), `quote` (exactly `content_text[start:end]`), `text_sha256` of the block |
| `review` | The verdict for this proposal: `decision: supported`, `issue: none`, `reason` |
| `basis` | What the evidence is, from the basis call (section 4.7): `kind` in act, ordinance, treaty, directive, guidance, directory, summary; `level` in federal, cantonal, municipal; `norm` (the act, SR number and article as the sources state them; required for act, ordinance, treaty and directive, else null); `refers_to` (a norm the evidence names without reproducing it, else null); `reason`. Null when the call was skipped or failed |
| `validation_state` | `CANDIDATE`, always; the packaging step and the review queue own the later states |

### 3.4 Job summary

`jobs/<job_id>/summary.json` carries the plan, the ceilings and what was
used of them, the execution statistics (`network_attempts`,
`retry_attempts`, `checkpoint_hits`, `incomplete_completions`,
`review_fallbacks`, token totals with the caveat that failed attempts are
not counted), the elapsed time, and the two consolidation views of
`concept_batch.py`: `consolidated_concepts` (groups of candidates that match
exactly on language, label, scope, type, granularity and description) and
`duplicate_review_pairs` (label overlap or shared aliases within one
language, capped at 200, never merged). `summary.json` at the top level
aggregates the reports on disk and is rebuilt at the end of every job.

## 4. Processing rules

### 4.1 Selection and reuse

A report is reused, and the record skipped, when a report exists whose
`content_sha256`, both prompt hashes, profile name, model and `settings`
equal the current ones and whose `failures` list is empty. `--force`
re-extracts everything selected; `--retry-failed` re-extracts only records
with failures, and the checkpoints make the successful chunks free. A
record whose report exists but whose `content_sha256` changed (a new
download attempt) is extracted anew; the old report is kept until `--prune`.

### 4.2 Sections from a record

A section is a maximal run of consecutive blocks with the same
`heading_path`. The heading block that opens it belongs to it and is not a
span; its text is the last element of the heading path the model sees.
Sections are numbered `section-0001` onward in document order over all
sections, kept or excluded, so the report's `uncited_section_ids` and
`sections[].reason` are meaningful. A section with no span after content
selection (only headings, or only excluded blocks) is not sent.

Table blocks are one span each: their `text` as the extractor wrote it.
`pdf_page` and `pdf_paragraph` blocks are ordinary spans. There is no second
parse of the saved bytes and no change to any offset.

### 4.3 Content selection

A block is excluded from the spans, with the reason recorded on its
section, when any of the following holds:

| Rule | Condition | Source |
| --- | --- | --- |
| `hidden` | `source_locator.explicit_hidden` is true | Record label |
| `region` | `source_locator.region` is `nav`, `footer`, `header` or `form` | Record label, present on every HTML record including adopted ones |
| `furniture` | `source_locator.furniture` contains `navigation`, `banner`, `footer`, `breadcrumb`, `skiplinks`, `share`, `cookie-notice`, `language-menu`, `related-links`, `anchor-navigation`, `scroll-to-top` or `chat-link` | Record label, present on freshly extracted records only |
| `control` | `kind` is `control` | Record |
| `page-furniture` | PDF: `furniture` contains `page-header` or `page-footer` | Record label |
| `footnote` | `is_footnote` is true | Record label; footnotes are kept as context for the review but not offered as spans |
| `page_furniture_heading` | An element of the heading path, lowercased and NFC-normalized, is in the V3 word list (`Kontakt`, `Contact`, `Zuständigkeit`, `Suche`, `Navigation`, `Inhaltsverzeichnis`, `Seite teilen`, `Feedback`, `Cookie-Einstellungen`, `Auf dieser Seite`, `Das könnte Sie auch interessieren`, ...), except on a page whose only heading is a contact heading | V3 rule, exact heading components, never keyword matching in body text |
| `embedded_news` | A heading below the first level is `News`, `Aktuell`, `Aktuelle Meldungen`, `Neuigkeiten` or `Nachrichten` | V3 rule |
| `link_only` | A whole section consists of `list_item` blocks labelled `link-only`, or (adopted records) its last heading is a links heading and every block's text equals its link labels | V3 rule; such a chunk is skipped without a model call |

`in_main` is not a criterion: SEM pages have no `main` element and every
block of the notification page carries `in_main: false`. Labels never remove
text from the record; they decide only what is offered as a span.

### 4.4 Chunks and evidence spans

Kept sections are packed in order into chunks of at most
`chunk_content_characters` (default 6,400) counting each span's text plus a
32-character allowance per span for its identifier. A section longer than a
chunk is split at block boundaries; a single block longer than a chunk is
split at word boundaries with `chunk_overlap_characters` (default 400) of
overlap, as in V3, and the resulting spans record their offsets into the
block. A span longer than 500 characters is cut at the last space after its
250th character, repeatedly; every piece is its own span with exact offsets.

Evidence IDs are `b<NNNNN>:<start>:<end>`: the block number and the
code-point offsets of the span inside the block's `text`. The absolute
offsets into `content_text` follow from the block's `start`. The catalogue
of a chunk lists `evidence_id`, `section_id` and `text` per span; the
model's schema enumerates exactly these IDs, so an unknown ID is refused by
the provider's JSON mode where it exists and by the validator always.

### 4.5 The extraction call

One call per chunk. System prompt: the V3 extraction prompt (the bundled
prompt texts are package data; a test pins their SHA-256,
`ccd968b7...` for extraction and `893394d8...` for review). User prompt:
the V3 `untrusted_page` object with `document_id`, `title`, `language`,
`chunk_index`, `chunk_count`, the chunk's `sections` (`section_id`,
`heading_path`, `fragment_start` when the section was split) and
`evidence_spans`. Response schema: the V3 extraction schema with
`maxItems: max_concepts_per_chunk` (default 6), the `evidence_id` enum and
the `primary_section_id` enum of the chunk. The provider's JSON mode is
requested where the adapter supports it; the local validation stays
mandatory in every case.

### 4.6 Structural validation

Every proposal is checked before the review, and a failing one goes to
`rejected` with `stage: structural`:

- exactly the schema's properties, no extra keys;
- `preferred_label` up to 200 characters, `description` up to 1,200,
  `scope` up to 500 and non-empty, `alternative_labels` up to 10 of 200,
  `user_questions` 1 to 5 of 300, `relations` up to 10, whitespace
  collapsed; `confidence` a number in 0 to 1, not a boolean;
- `scope` is not the word `page`, not a section ID and not a breadcrumb
  (`a > b`);
- 1 to 5 evidence items, each an `evidence_id` of the chunk, deduplicated;
- `primary_section_id` is a section of the chunk and every evidence item
  belongs to it.

A chunk whose completion is not a JSON object with exactly one `concepts`
array, or has more entries than the limit, fails as a whole (section 4.10).

### 4.7 The review call and the basis call

One call per chunk with at least one structurally valid proposal. System
prompt: the V3 review prompt. User prompt: the V3 `untrusted_review` object
with `title`, `language`, the `primary_sections` the proposals cite
(`section_id`, `heading_path`, `text`, `fragment_start`,
`context_may_be_partial` when the section was split over chunks) and the
numbered `proposals`. Response schema: `verdicts` with exactly one entry per
`review_id`, `decision` in supported, unsupported, uncertain; `issue` in
`none`, `unsupported_claim`, `missing_condition`, `wrong_scope`,
`wrong_language`, `wrong_type`, `unanswerable_question`, `irrelevant`,
`insufficient_context`; `reason` of 1 to 500 characters. The parser rejects
a missing, duplicate or unknown `review_id`, a `supported` verdict with an
issue or an unsupported one without, and any other key.

A proposal is retained only with `decision: supported`. Every verdict is
recorded under `reviews`; the rejected proposals under `rejected` with
`stage: review`. When the review completion is truncated (`finish_reason`
`length`) and there is more than one proposal, the proposals are re-batched
in halves down to `review_fallback_batch_size` (default 2) and reviewed
again, verdicts renumbered and stitched; the split is recorded in
`review_fallbacks`.

**The basis call** follows the review: one call per
chunk with at least one supported proposal, skipped with
`classify_basis = false` in the extraction settings or `--no-classify-basis`.
System prompt: `basis_classification_v1.md`, a separate prompt so the V3
extraction and review hashes stand. User prompt: an `untrusted_basis` object
with `title`, `source_url`, `language`, the same `primary_sections` the
review saw, and the numbered `proposals`, each with its `preferred_label`,
the `heading_path` of its primary section and the `quotes` of its selected
spans. Response schema: `bases` with exactly one entry per `basis_id`,
`kind` (act, ordinance, treaty, directive, guidance, directory, summary),
`level` (federal, cantonal, municipal), `norm` and `refers_to` as strings
(empty for none, because the exchange checker's schema subset has no union
types) and a `reason` of 1 to 300 characters. The parser rejects a missing,
duplicate or unknown `basis_id`, an act, ordinance, treaty or directive
without a norm, and any other key. The prompt asks for the level of the
body that enacted the norm or wrote the text, not the publisher of the page,
and forbids inventing an abbreviation, a number or an article the sources do
not state: a federal act quoted on a cantonal page is federal; a page that
cites an article while explaining in its own words is guidance.

A parsed classification is copied onto each retained candidate as `basis`
(4.3). A failed call (invalid JSON, a rule violation, a truncated
completion) is recorded under `failures` as `basis_unparseable` with the
checkpoint marked invalid; the candidates stay without a basis and the
build later gives their citations the page default. The report's reuse
check (5.1) compares the basis prompt hash only when the call is on, so
reports of the two-call pipeline stay reusable for a run without it. The
plan counts three requests per chunk with the call on, two without.

### 4.8 Merge, identity, consolidation

Within a record, proposals from different chunks that agree on label, scope,
type, granularity, description and primary section are merged: labels,
questions, evidence and relations unioned within the limits, confidence the
maximum. Proposals that agree on label and scope but differ otherwise are
both kept (V3 `preserve_conflicts`), so a conflict is visible to the person
rather than resolved by order. `candidate_id` is derived as in 4.3. Across
records, the job summary groups exact matches and lists near matches for
review; nothing is merged across records.

### 4.9 Budgets, retries, checkpoints

The plan (`plan.json`, also the output of `--dry-run`) lists every selected
record with its sections, chunks and the request count: two per chunk
(extraction and review), one for a chunk that produced no valid proposal,
none for a skipped chunk. Ceilings, all from the configuration and each
overridable on the command line:

| Ceiling | Default | Effect |
| --- | --- | --- |
| `max_model_requests_per_page` | 12 | A record needing more is skipped and listed before the run starts |
| `max_model_requests_per_run` | 400 | The run refuses to start when the plan exceeds it; the count includes retries and failed attempts |
| `max_total_input_characters` | 1,200,000 | Over the kept sections of the selection |
| `max_characters_per_document` | 120,000 | See section 2 |
| `max_prompt_tokens_per_run` | none | Soft: the job stops before the next request once the recorded usage exceeds it |
| `--max-minutes` | none | Soft: the job stops before the next request |
| `max_retries`, `backoff_seconds`, `max_backoff_seconds`, `max_retry_after_seconds` | 3, 2, 30, 300 | Transient HTTP statuses 408, 429, 500, 502, 503, 504 and network timeouts; `Retry-After` honoured as seconds or HTTP date and refused beyond the cap; a progress line every 15 seconds while waiting |

Checkpoints: before a request is sent, its key is computed as SHA-256 over
the canonical JSON of the request (`system_prompt`, `user_prompt`,
`response_schema`) and a context (`version`, `document_id`,
`content_sha256`, profile name, model, generation settings). An existing
`checkpoints/<key>.json` whose stored hash verifies and whose model identity
passes today's policy is served without a request and counted as a
checkpoint hit. A new completion is written atomically (temporary file,
`fsync`, replace) before it is parsed; a write failure stops the job, since
losing paid work is worse than stopping. A completion that later fails
structural validation is renamed to `<key>.invalid.json` so it is neither
reused nor lost. Changing a prompt, a span or a setting changes the key by
construction; changing a timeout does not.

Resumption is the same command again: reports skip finished records,
checkpoints skip answered requests, and the job continues at the first
unanswered chunk.

Compatible checkpoints are also found in earlier jobs when selection or
ceilings change. A truncated review that triggered successful smaller
batches keeps its checkpoint as the instruction to repeat that split;
both the parent and child responses replay without provider calls. Each
job summary's execution statistics describe the latest invocation, while
its report totals include reused candidates and completion usage.

### 4.10 Failure handling

| Event | Effect |
| --- | --- |
| Provider refuses the request after the retries, or the ceiling is reached | The job stops; the record's report is written with the finished chunks and the remaining ones under `failures`; exit code 1 |
| Completion is not valid JSON, not a single `concepts` array, over the concept limit, or `finish_reason` is not `stop` | The chunk is `failed` with the checkpoint key of the raw completion; the other chunks proceed; the report lists the failure |
| Observed model differs from the configured one | The completion is refused, not checkpointed, and counted; the job stops after the record, because every later completion would carry the wrong attribution |
| Review verdicts do not parse | The chunk's proposals are rejected with `stage: review` and reason `review_unparseable`; the raw completion is kept |
| Record has no kept section | Report with zero chunks, `warnings: [no_content_sections]`, no request |

Exit code 0 when every selected record has a report without failures, 1
otherwise; 2 on a usage error. A report written is never a claim that a
candidate is correct; the report says what was proposed, what was rejected
and by which stage.

## 5. Providers

### 5.1 Protocol and configuration

```python
class Provider(Protocol):
    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_schema: Mapping[str, object]) -> Completion: ...

@dataclass(frozen=True)
class Completion:
    content: str
    provider: str
    model: str                 # configured identity
    requested_model: str | None
    observed_model: str | None # from the response, never inferred
    prompt_tokens: int | None
    output_tokens: int | None
    request_id: str | None
    finish_reason: str | None
```

`config/semantic-models.toml`, committed, one file:

```toml
schema_version = "swisstip.semantic-models/v2"
active_profile = "deepseek_flash"

[generation]
temperature = 0.0
max_output_tokens = 8192

[extraction]
chunk_content_characters = 6400
chunk_overlap_characters = 400
max_concepts_per_chunk = 6
max_model_requests_per_page = 12
max_model_requests_per_run = 400
max_total_input_characters = 1200000
max_characters_per_document = 120000
max_review_input_characters = 64000
review_fallback_batch_size = 2

[retries]
max_retries = 3
backoff_seconds = 2.0
max_backoff_seconds = 30.0
max_retry_after_seconds = 300.0

[profiles.deepseek_flash]
adapter = "deepseek"
model = "deepseek-flash"
base_url = "https://api.deepseek.com"
timeout_seconds = 180

[profiles.deepseek_pro]
adapter = "deepseek"
model = "deepseek-v4-pro"
base_url = "https://api.deepseek.com"
timeout_seconds = 180

[profiles.apertus_70b]
adapter = "huggingface"
model = "swiss-ai/Apertus-70B-Instruct-2509"
base_url = "https://router.huggingface.co/v1"
provider = "publicai"
response_mode = "json_schema"      # or prompt_only
timeout_seconds = 180

[profiles.groq_gpt_oss_120b]
adapter = "groq"
model = "openai/gpt-oss-120b"
base_url = "https://api.groq.com/openai/v1"
timeout_seconds = 180

[profiles.ollama_apertus_8b]
adapter = "ollama"
model = "MichelRosselli/apertus:8b-instruct-2509-q4_k_m"
base_url = "http://127.0.0.1:11434"
num_ctx = 8192
keep_alive = "5m"
timeout_seconds = 180

[profiles.assistant_exchange]
adapter = "exchange"
model = "claude-fable-5-1"
provider = "anthropic-assistant"
```

The loader rejects unknown keys, any `api_key` or `token` value, a profile
whose adapter is unknown, and the validation bounds (chunk size at least
500, overlap at most half the chunk, at most 100 concepts per chunk,
per-page ceiling at most the per-run ceiling, retries at most 5, backoff at
most 60 seconds, `Retry-After` cap at most 3,600 seconds, review batch at
most 10). The token variable is fixed per adapter and read only for the
selected profile: `DEEPSEEK_API_KEY`, `HF_TOKEN`, `GROQ_API_KEY`; Ollama and
the exchange need none. There is no fallback from one profile to another.

### 5.2 Adapters

All four hosted and local adapters use `urllib.request` with a handler that
refuses redirects (so a bearer token cannot follow one), a `User-Agent` of
`SwissTIP/0.1`, `stream: false`, a 1 MB request and a 1 MB response cap,
exactly one choice, no implicit retry (retries belong to section 4.9), and a
JSON decoder that rejects duplicate keys, non-finite numbers and invalid
UTF-8.

| Adapter | Request | Response checks |
| --- | --- | --- |
| `deepseek` | `POST /chat/completions`, `response_format: {type: json_object}`, `thinking: {type: disabled}`, the response schema serialized into the system prompt, `max_tokens`, `temperature` | `model` equals the requested model; one choice; `finish_reason == stop`, else the completion is `incomplete` with the reason; no `refusal` or `tool_calls`; content parses to a JSON object |
| `huggingface` | OpenAI-compatible `POST /chat/completions` at the router with `model: "<model>:<provider>"`; `response_format: {type: json_schema, json_schema: {name, schema, strict: true}}`, or `prompt_only` (schema appended to the system prompt); for `publicai` additionally `disable_fallbacks: true` and `cache: {no-cache: true, no-store: true}`, because PublicAI substitutes `aisingapore/Qwen-SEA-LION-v4-32B-IT` and serves cached completions without them; optional `X-HF-Bill-To` | `finish_reason` in stop, eos_token, stop_sequence; the observed model must be the requested one or an approved alias listed in the adapter with the date it was verified; 429 bodies classified into token-limit and rate-limit errors from at most 4 KB of valid JSON |
| `groq` | OpenAI-compatible with strict `json_schema`, `reasoning_effort: low`, `max_completion_tokens` | As DeepSeek; the explicit `User-Agent` is required (HTTP 403 `error code: 1010` without it) |
| `ollama` | `POST /api/chat` with `format: <schema>`, `think: false`, `options: {num_ctx, num_predict, temperature}`, `keep_alive` | `done` true and `done_reason == stop` |

DeepSeek is the default because it is the only model measured to retain a
usable draft in every run: GPT-OSS approved unsupported claims in its
reviews, and no usable Apertus completion was obtained through PublicAI with
fallbacks disabled. The Apertus profiles stay for the
day that changes and are marked `untested-live` in the configuration until a
recorded run says otherwise.

### 5.3 Exchange adapter

The `exchange` adapter lets an assistant in the IDE answer the request
files by hand, which is how the batches under `.local/extraction/` were
produced:

- `generate_structured` computes the key of section 4.9. If
  `exchange/responses/<key>.json` exists, its `content` is returned as a
  completion with `provider: anthropic-assistant` and the configured model.
  Otherwise the request is written to `exchange/requests/<key>.json`
  (`schema_version: swisstip.assistant-extraction-request/v1`, `kind`
  extraction or review, `document_id`, `title`, `chunk_index`,
  `chunk_count`, `proposal_count`, `captured_at`, `system_prompt`,
  `user_prompt`, `response_schema`); `kind` is extraction, review or
  basis.
- `--on-missing capture` (default) returns an empty placeholder
  (`{"concepts": []}`, all-`uncertain` verdicts, or all-`guidance` bases
  marked as placeholders) so the whole plan is captured in one pass; the
  report marks such chunks `awaiting_response` and they are not reused.
  `--on-missing fail` stops at the first missing response. With the basis
  call on, a chunk takes three passes: the extraction request, then the
  review request, then the basis request.
- `swisstip-concepts check` re-validates every response file against its
  request's schema and moves an invalid one aside.

The assistant that answers is a provider like any other: its answers pass
the same validation, the same review call and the same packaging, and its
identity is recorded on every completion.

The response envelope requires `observed_model` or `model` alongside the
JSON string in `content`. A content-only response file needs explicit model
attribution before replay; a missing observed identity is never inferred
from configuration. Completed batches are imported through section 6.3.

### 5.4 Identity policy

`observed_model` comes from the provider's response and is never inferred.
A completion whose observed model is not the configured model (or, for
PublicAI, not an approved alias) is refused, whether it comes from the
network or from a checkpoint: integrity of a checkpoint does not establish
attribution. Every report lists one identity entry per completion.

## 6. Packaging into the curation file

`swisstip-concepts-package` turns retained candidates into curation entries.
It is a separate command, never run by the extraction, and it writes the
curation file only with `--write`; without it the entries are printed and a
`packaging-report.json` says what would change.

### 6.1 Mapping

| Curation field | From |
| --- | --- |
| `concept_id` | `mc-` + the first 8 hex digits of `document_id` after `doc-` + `-` + an ASCII slug of `preferred_label` of at most 40 characters; `-2`, `-3` on collision within the file |
| `topic_id` | `--topic`, required, must exist in the file |
| `label` | `preferred_label` |
| `description` | `description` |
| `aliases` | `alternative_labels` |
| `questions` | `user_questions` |
| `source_terms` | Empty; `python -m swisstip.build.source_terms` lists candidates for the curator after the first build |
| `required_context`, `required_user_facts`, `decision_rule` | Empty; a condition is a person's decision |
| `notes` | `scope: ...`, `confidence: ...` (uncalibrated), `review: <reason>`, `relations: ...`, `basis: <kind> (<reason>)` when the candidate carries one, and `proposed by <provider>:<model> on <date>, job <job_id>` |
| `facts[0].fact_id` | `<concept_id>-1` |
| `facts[0].statement` | `description`, in the page language, unchanged |
| `facts[0].language` | The report's `language`, reduced to its two-letter code |
| `facts[0].jurisdiction` | `--jurisdiction`, else `definition.jurisdiction` of the record's first `source_registry` entry (the KB2 catalogue carries `CH-AG`, `CH-ZH`, ...; the federal entries `CH`); a candidate with neither is listed and not packaged, because a wrong `CH` would answer for every canton |
| `facts[0].provenance` | `kind: model-candidate`, `review_status: model-candidate-automated-review`, `author: <provider>:<model>`, `source: concepts job <job_id>, document <document_id>, candidate <candidate_id>, prompts <8 hex>/<8 hex>`, `notes: [the review reason]` |
| `facts[0].evidence` | The candidate's spans grouped by block: consecutive blocks become one citation with `document_id`, `first_block`, `last_block` and an `anchor` from `swisstip.extraction.anchors.make_anchor` on the current record; when the candidate carries a `basis`, each citation gets it as its `basis` (`kind`, `level`, `norm`, `refers_to`, without the reason), which the build labels as it labels a curator's (see [institutions-and-provenance-weights.md](institutions-and-provenance-weights.md), section 2.3) and the reviewer confirms or corrects in the workbench |

The cited excerpt is the whole block range, since the build cites blocks; a
span that was a 500-character cut of a longer block cites the block. The
anchor pins the text at packaging time, so a later download attempt goes
through the build's relocation like any curated citation.

### 6.2 Rules

- **Idempotent.** A candidate already present (same `candidate_id` in a
  fact's `provenance.source`) is skipped. A packaged fact whose kind or
  status a person changed is never touched. Candidates removed from a
  report are not removed from the file; rejecting is the queue's action.
- **Current record only.** The candidate's `content_sha256` must equal the
  current record's; otherwise the candidate is listed as `stale` and skipped
  until the extraction is rerun on the new record.
- **Filters.** `--document-id`, `--source`, `--min-confidence`,
  `--concept-type`, `--granularity` (default: all except `DOMAIN`), and
  `--job` to package one job's candidates.
- **Validation before writing.** The entries are validated through
  `swisstip.build.curation`, and a dry build against the text dataset must
  resolve every citation `same-snapshot`; any other outcome stops the
  packaging with the citation named.
- **Never a served fact by itself.** The build carries the kind and status
  into the release and its manifest counts; the coverage root and every
  tool result's `limitations` name them; the review queue lists them at
  priority 4 and its accept action (`screens/review.py`, implemented) is
  what turns one into `curated-statement` with `human-reviewed`.

### 6.3 Legacy batches

`--legacy-batch FILE` reads a `swisstip.concept-proposal-batch/v1` document
that was produced outside this pipeline (the assistant batches under
`.local/extraction/assistant-v3-2026-09-11/runs/batch-*/result.json` of the
predecessor checkout). This is step 2 of the KB2 migration. For each
retained candidate:

1. The record is found by `provenance.source_url` (else `final_url`) and
   `provenance.sha256` through the text index; failing the hash, the newest
   eligible preferred record of the source URL.
2. Each quote is normalized as the extractor normalizes (whitespace
   collapsed) and the heading-path prefix V3 glued to a section's first span
   (`<heading_path>\n`) is stripped when present.
3. The quote must occur exactly once in the record's `content_text`; the
   covering blocks become the citation. Not found or ambiguous: the
   candidate is dropped and listed with the reason, as `TODO.md` requires.
4. The remaining fields map as in 7.1, with `source` naming the batch file,
   the report and the candidate, and `author` the batch's provider and
   model (`anthropic-assistant:claude-fable-5-1`).

The count of anchored, dropped and ambiguous candidates is the acceptance
record of this step and goes into `.local/experiments/`.

## 7. Sizing and budget

The figures in this table are sizing estimates. Use `--dry-run` for the
actual selection and ceilings of a run before starting a job.

Chunks and requests follow from the characters of the kept sections; the
figures below use all characters of the eligible preferred records as an
upper bound, one chunk per 6,400 characters and two requests per chunk; the
basis call adds a third, smaller request to every chunk with a supported
proposal, so a plan with it counts about half as
many requests again. The
token figure assumes about 10,000 tokens per chunk (roughly 8,000 in and
2,000 out: the chunk text once in the spans, the prompts, the schema with
its enums, and the review's primary sections and proposals), to be replaced
by the measured value of the pilot.

| Scope | Records | Characters | Chunks | Requests | Tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| MVP run, catalogue pages only | 29 | 318,400 | 50 | 100 | 0.5 M |
| MVP run, attributed scope | 376 | 3,678,419 | 575 | 1,150 | 5.8 M |
| Full run, attributed scope (KB2) | 1,685 | 10,946,860 | 1,710 | 3,420 | 17 M |
| of which the three Fedlex acts over 120,000 characters | 3 | 762,373 | 120 | 240 | 1.2 M |
| Full run, everything eligible | 10,992 | 165,621,271 | 25,900 | 51,800 | 260 M |

The last row is not a target; the out-of-scope pages are outside the
residence topic by attribution. At about 20 seconds per DeepSeek call, the
catalogue pages take about half an hour sequentially, the MVP attributed
scope about six hours and the KB2 scope about nineteen hours; `--workers N`
runs records in parallel subject to the provider's rate limit. Cost is
tokens times the provider's list price on the day; the job records both
token counts per request, and the pilot of section 10 fixes the number
before anything larger runs.

The Fedlex acts are better served by KB2 step 3 (source sections per
article) than by concept extraction over 120 chunks of statute text; the
default skips them.

## 8. Command line

```shell
./.venv/Scripts/python.exe -m pip install -e packages/concepts
./.venv/Scripts/python.exe -m unittest discover -s packages/concepts/tests

# plan only: sections, chunks, requests and ceilings, no provider created
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run releases/<pack> --kind catalogue --dry-run

# run with the default profile; resumable; stops at the ceilings
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run releases/<pack> --kind catalogue --profile deepseek_flash --max-requests 100

# full pack, German catalogue and in-scope pages, four records in parallel
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run .local/<pack> --language de --profile deepseek_flash --workers 4 --max-minutes 120

# assistant exchange: capture requests, answer them, run again to consume the responses
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli --run .local/<pack> --source ag-residence --profile assistant_exchange
./.venv/Scripts/python.exe -m swisstip.concepts.concepts_cli check --output .local/<pack>/concepts

# package retained candidates; prints without --write
./.venv/Scripts/python.exe -m swisstip.concepts.package_cli --concepts .local/<pack>/concepts --curation releases/<pack>/curation.yaml --text .local/<pack>/text --topic residence --write
./.venv/Scripts/python.exe -m swisstip.concepts.package_cli --legacy-batch ../Hackathon2026-SwissTIP/.local/extraction/assistant-v3-2026-09-11/runs/batch-001/result.json --curation releases/<pack>/curation.yaml --text .local/<pack>/text --topic residence
```

| Option | Effect |
| --- | --- |
| `--run DIR`, `--output DIR` | The run whose `text/` is read; the candidate dataset, default `.local/<pack>/concepts/` |
| `--scope`, `--kind`, `--source`, `--document-id`, `--language`, `--max-pages`, `--allow-large` | Selection (section 2) |
| `--profile NAME`, `--config FILE` | The provider profile; without `--profile` only `--dry-run` is allowed |
| `--dry-run` | Write `plan.json`, print the request count and the ceilings, create no provider |
| `--max-requests`, `--max-minutes`, `--max-prompt-tokens` | Override the ceilings of section 4.9 |
| `--workers N` | Records in parallel; requests within a record stay sequential |
| `--force`, `--retry-failed`, `--prune`, `--fresh-inference` | Reuse control (section 4.1); `--fresh-inference` ignores checkpoints but writes new ones |
| `--extraction-prompt FILE`, `--review-prompt FILE`, `--basis-prompt FILE` | Replace a whole prompt; the report records the path and hash |
| `--no-classify-basis` | Skip the basis call (section 4.7); two requests per chunk, candidates without a basis |
| `--on-missing capture` or `fail` | Exchange adapter only |
| `--verbose` | Progress lines on standard error |

## 9. Package layout, dependencies, tests

```text
packages/concepts/
  pyproject.toml                swisstip-concepts; scripts swisstip-concepts, swisstip-concepts-package
  README.md
  src/swisstip/concepts/
    __init__.py                 PACKAGE_VERSION, REPORT_SCHEMA, JOB_SCHEMA, CHECKPOINT_SCHEMA
    sections.py                 sections from a record, content selection, exclusion reasons
    chunks.py                   chunk packing, oversize block split, evidence spans and IDs
    prompts.py                  bundled prompts, overrides, hashes; prompts/*.md as package data
    schemas.py                  extraction and review response schemas
    validation.py               structural validation of proposals, verdict parsing
    extract.py                  the per-record flow: chunks, calls, validation, review, merge, report
    providers/
      base.py                   Provider protocol, Completion, errors, the HTTP helper without redirects
      deepseek.py, huggingface.py, groq.py, ollama.py, exchange.py
      config.py                 semantic-models.toml loader and validation
    budget.py                   ceilings, retries, Retry-After, review split, statistics
    checkpoints.py              keys, atomic writes, verification, invalid checkpoints
    results.py                  report reuse, index, summary, consolidation (from concept_batch.py)
    package.py                  candidates to curation entries, anchors, packaging report
    legacy.py                   the concept-proposal-batch/v1 adapter
    concepts_cli.py, package_cli.py
  tests/                        offline; a FakeProvider that answers from canned JSON; synthetic records
```

Dependencies: `swisstip-extraction` (records, anchors), `swisstip-build`
(curation models) and, through it, `swisstip-core` and PyYAML; `tomllib`
from the standard library; no HTTP client library and no provider SDK. The
knowledge builder does not gain a stage: a model call is neither offline
nor deterministic, and the pipeline's stages are both. The admin console
may later start the command as a job behind the same confirmation as the
download flag (section 10, phase 5).

Tests, each on synthetic records built in a temporary directory, no network:

| Area | Cases |
| --- | --- |
| Sections and selection | Runs by heading path; heading blocks not spans; every exclusion rule of 5.3 once, on a record with and without furniture labels; contact-only page kept |
| Chunks and spans | Packing at block boundaries; an oversize block split with overlap and correct offsets; the 500-character cut; every span's `quote` equals `content_text[start:end]`; ID enum equals the catalogue |
| Prompts | The bundled prompts hash to their pinned values; an override is recorded by path and hash |
| Validation | Each rule of 5.6 rejects exactly its case; a valid proposal passes; verdict parser rules of 5.7 |
| Flow | A record through the fake provider yields the expected report: merge of two chunks, conflict preserved, review rejection recorded, quality metrics, `output_sha256` stable |
| Budgets | The plan refuses over the ceilings before any call; a retry on 429 with `Retry-After`; refusal beyond the cap; the review split on a truncated review |
| Checkpoints | Hit on the second run with zero calls; a changed prompt misses; an invalid completion moved aside and not reused; identity mismatch refused from a checkpoint |
| Adapters | Each adapter's request body and headers against a fake `urlopen`; each response check of 6.2; redirect refused; the 1 MB caps; the PublicAI flags |
| Exchange | Capture writes the request file; a response file is served; `check` moves an invalid response aside |
| Results | Reuse rule of 5.1; `--force`; `--retry-failed`; index and summary rebuilt from disk; consolidation groups and duplicate pairs |
| Packaging | Every field of 7.1; contiguous spans become one citation; anchor equals `make_anchor`; idempotent second run; an accepted fact untouched; missing jurisdiction listed; stale content skipped; dry build resolves `same-snapshot` |
| Legacy | Quote found once, with and without the heading prefix; ambiguous and missing quotes dropped and listed; record found by hash and by URL |

## 10. Remaining phases

Phases 1 to 3 (the pipeline with its fake provider and `--dry-run`, the
provider adapters with budgets and checkpoints, and packaging with the
legacy adapter) are implemented and tested. Two phases are open:

| Phase | Delivers | Acceptance | Size |
| --- | --- | --- | --- |
| 4 | Pilot: the 29 MVP catalogue pages through `deepseek_flash` with `--max-requests 100`, recorded in `.local/experiments/` with requests, tokens, seconds, candidates retained and rejected, and the expert's verdict on a sample of 20 | Measured tokens per chunk replace the estimate of section 7; a go or no-go for the KB2 scope with its cost | One afternoon |
| 5 | Optional: an English rendering call per retained candidate (label and statement in English, kept beside the original, `language: en` on a second fact); the console job with confirmation; the "Draft with assistant" button of the console design (4.5) on this provider layer | Each its own record under `.local/experiments/` | Later |

Phase 4 needs the network and a key, and its outputs stay under `.local/`
until packaged. The offline MVP plan schedules 28 of 29 selected records for
at most 90 requests under the default per-page ceiling; the SEM entry FAQ
needs 20 requests and is skipped, so all 29 require
`--max-requests-per-page 20 --max-requests 110`.

## 11. Out of scope

- Any change to the release format, the tool contracts, the server or the
  build beyond reading facts of kind `model-candidate`, which they already
  do.
- Deciding conditions, context fields or decision rules for a candidate; a
  person does that in the workbench.
- Translation as content; an English rendering is phase 5 and is marked as
  a model rendering when it exists.
- Extraction over the out-of-scope pages of the full run, OCR'd PDFs or
  images.
- Automatic acceptance of any candidate, whatever its confidence or verdict.
- A vector index, retrieval or ranking; `search` stays lexical.

## 12. Open questions

- **Language of the statement.** The V3 prompts write in the page language
  and the review rejects English prose on a German page, while section 3.4
  of the specification describes KB2 as English statements with aliases in
  every source language. This design keeps the candidate in the page
  language (that is what the review checked) and makes English a separate
  rendering step (phase 5). The alternative, an English description in the
  extraction call, would need a new prompt profile and lose the equivalence
  with the existing batches.
- **Which pack gets candidates first.** Recommended: KB2
  (`releases/swiss-residence/curation.yaml`), where the legacy batches
  already belong and where unreviewed content is declared. For KB1,
  packaging into the served curation file changes what the jury's caller
  sees in `search`; it should happen only for pages the expert names, after
  the event, or on a branch.
- **Jurisdiction of discovered pages.** The registry entry's
  `definition.jurisdiction` is taken for every record of a source; a
  cantonal portal that links a municipal page would mark it cantonal. KB2
  step 4 (host-to-canton mapping) may refine this; until then the packaging
  report lists the jurisdiction it assigned per candidate.
- **The exchange route.** Whether assistant-answered batches remain a
  supported route once DeepSeek runs. Recommended: keep it; it produced the
  only candidates that exist, costs nothing, and its identity is recorded.
- **Parallelism and rate limits.** DeepSeek's limits for this account are
  not recorded; the pilot runs with `--workers 1` and the record says what a
  higher setting did.
