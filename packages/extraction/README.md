# swisstip-extraction

**Last update:** 22 September 2026

Build-side source-text extraction for Swiss TIP: turn the saved responses of
an ingestion run into text records that can be read, cited and hashed. One
record per saved response, the visible text as ordered blocks with stable
identifiers, code-point offsets into one normalized text string, and hashes
of every block, of the whole text and of the raw bytes. Nothing is
interpreted: no facts, no language classifier, no model, no request. The
serving side never imports this package.

The design and its rationale are in
[docs/architecture/extraction.md](../../docs/architecture/extraction.md).

## Install and test

```shell
./.venv/Scripts/python.exe -m pip install -e "packages/extraction[office]"
./.venv/Scripts/python.exe -m unittest discover -s packages/extraction/tests
```

Dependencies: `lxml` (HTML), `pypdfium2` and `pypdf` (PDF text and form
fields). The optional `office` group adds `olefile` and `striprtf` for legacy
Word and RTF files; without it those records are `extraction_failed` with the
reason. Tests build their inputs in temporary directories and make no network
request.

## Extract a run

```shell
./.venv/Scripts/python.exe -m swisstip.extraction.extract_cli --run releases/<pack>
./.venv/Scripts/python.exe -m swisstip.extraction.validate --text .local/<pack>/text
./.venv/Scripts/python.exe -m swisstip.extraction.extract_cli --run .local/<pack> --workers 4
```

| Option | Effect |
| --- | --- |
| `--run DIR` | Run directory of the ingestion package (required) |
| `--output DIR` | Text dataset directory; default `<run>/text`. Never inside `pages/` or a `*-documents/` folder |
| `--scope attributed` (default) or `all` | `attributed`: catalogue targets, `in-scope` and `language-variant` discovered pages, plugin documents. `all`: also `out-of-scope` pages |
| `--kind`, `--source`, `--document-id` | Narrow the selection; repeatable |
| `--workers N` | Process pool size, 1 to the CPU count |
| `--force` | Re-extract records that could be reused |
| `--prune` | Delete records of superseded attempts |
| `--no-reading-view` | Skip the Markdown reading views |

Every selected response is verified against its manifest hash and size
before parsing; a mismatch stops the run. A record is reused when its raw
hash and extractor version match (or when it was adopted from the
predecessor's datasets), so a rerun after a new download attempt touches only
the changed pages. The index and summary are rebuilt from `documents/` on
every run. Exit code nonzero when a selected record is `extraction_failed`.

## Dataset layout

```text
text/
  documents/<document_id>.json   one record per saved response
  reading/<document_id>.md       reading view of every eligible record, block numbers in the margin
  index.json, index.md           one entry per record; the Markdown is grouped by attribution kind
  unavailable.json               targets without an intact snapshot
  errors.json                    records with status extraction_failed
  summary.json                   counts, extractor and dependency versions, catalogue hash
  validation.json                written by the validate command
```

`document_id` is `doc-` plus 20 hex digits of SHA-256 over the source URL and
the raw hash, so an unchanged page keeps its ID across download attempts.

Every index entry also says whether a curator is expected to read the record
and how much of it: `curation_candidate` with `candidate_exclusion`
(`not_extractable`, `superseded`, `secondary_representation`,
`language_variant`, `out_of_scope_page`; the first rule that applies), and
`sections`, `content_sections` and `content_characters` from
`swisstip.extraction.sections`, which splits a record into heading runs and
keeps those with citable text (no page furniture, no footnote, no bare list
of links), and `repeated_sections`: the content sections whose text also
stands on another candidate page of the same host, with block range and page
count, from two pages up. The reading view of such a record marks every block
of the section `repeated on N pages` and lists the ranges in its header, so a
reader cites the contact card once, from the page that owns it; the mark
never hides a block. The summary counts `curation_candidates`,
`candidate_exclusions`, `records_with_repeated_sections` and
`repeated_sections`. The build's coverage stage and the concept-extraction
jobs share these rules, so "was this page read?" has one answer, and the
coverage stage applies the pack's boilerplate threshold to the same
repetition.
Block IDs are `<document_id>:bNNNNN`. Offsets are zero-based, end-exclusive
code points into `content_text`, which is the blocks joined by two newlines.

A record carries: identity and provenance (`source_url`, `document_url`,
`version_uri`, `attribution` with kind and source IDs, `acquisition` with
path, attempt, hashes, retrieval time, review flags), the extraction
(`representation`, `page_kind`, `status`, `eligible_for_processing`,
`exclusion_reasons`, `warnings`, `title`, `language_declared`,
`language_hint`, `blocks`, `content_text`, `content_sha256`) and the grouping
(`representation_group`, `html_counterparts`, `preferred_representation`,
`identical_raw_snapshots`, `identical_text_records`). HTML records add
`text_integrity`; PDF records add `pdf_page_count`, `pages_without_text` and
`form_fields`.

Each block has `kind`, `text`, `start`, `end`, `text_sha256`, `heading_path`,
`links` and a `source_locator`: for HTML the XPath, anchor, article ID,
region, ARIA roles, `in_main`, `explicit_hidden`, `is_footnote`,
`list_context` and `furniture` labels (`navigation`, `breadcrumb`, `banner`,
`footer`, `share`, `cookie-notice`, `related-links`, `link-only`, ...); for
PDF the page, paragraph and `page-header` or `page-footer` labels. Labels
never remove text.

## What is excluded and what is not

Application shells, maintenance pages, error pages and responses flagged
`javascript_application_shell` or `possible_access_challenge` are recorded
with `status: excluded_source_response`. Browser-rendered captures stay
eligible with a warning. PDF pages without embedded text are counted, not
recognised. Images are recorded without text.

## Re-anchoring

`swisstip.extraction.anchors` lets a release build find a curated excerpt
again after its page was downloaded anew: `make_anchor(record, first_block,
last_block)` stores block hashes, heading path, offsets and text;
`relocate(anchor, new_record, old_record)` returns `same-text`, `moved`,
`ambiguous` or `changed`, with `context-changed` and
`neighbourhood-changed` warnings and, for changed text, the closest new
blocks. See section 6 of the design document.

## Adopted records

`swisstip.extraction.legacy.adopt` rewrites a record of the predecessor's
text datasets to a snapshot of a run here: new document and block IDs,
attribution, catalogue hash, paths and attempt; blocks, offsets and hashes
unchanged; `imported_text` names the old dataset and record. The text datasets
of both pack runs were seeded this way by a one-time script kept outside Git
(`.local/scripts/import_legacy_text.py`); `legacy-text-import.json` in each
dataset lists every adopted record and every snapshot that had no old record.
Adopted records keep the old extractor version and lack the labels this
extractor adds (furniture, PDF paragraphs); `--force` replaces them with fresh
extractions.
