# Extraction package - technical design

**Last update:** 21 September 2026

**Status:** implemented in `packages/extraction` (`swisstip.extraction`,
with offline tests); the text datasets of both packs
are built in the run directories, `releases/mvp-zurich/` (committed) and
`.local/swiss-residence/` (section 9)<br>
**Consumers:** the knowledge expert (reads the text, selects excerpts) and the
release build script (resolves curated block ranges to exact excerpts, offsets
and hashes). The serving side never imports it.

---

## 1. Purpose and position in the pipeline

The ingestion package saves the official pages of a pack byte for byte. The
extraction package turns those saved responses into a text dataset that can
be read, cited and hashed: one record per saved response, the visible text as
ordered blocks with stable identifiers, character offsets into one normalized
text string, and a hash of every block, of the whole text and of the raw
response it came from. Nothing is interpreted: no facts, no rules, no
language detection by a model, no summaries. It is the step between "we have
the page" and "the expert picks the sentence that supports a fact".

```text
releases/<pack>/sources.json        catalogue (committed)
        |  swisstip-download                ingestion package
        v
<run>/                              run: pages/<url-sha256>/attempt-NNN/response.*
        |                                + fedlex-documents/, plan.json, gap-report
        |  swisstip-extract                extraction package (this document)
        v
<run>/text/                         text dataset: documents/<id>.json, reading/<id>.md,
        |                                index.json, summary.json, validation.json
        |  expert reads reading/*.md, writes releases/<pack>/curation.yaml
        |  release build (WP2, separate)   resolves block ranges -> excerpt, offsets, hashes
        v
releases/<pack>/release.json        served by the MCP server
```

`<run>` is `releases/<pack>/` for a run committed with its pack (the MVP)
and `.local/<pack>/` for a run kept outside Git (the full pack).

The stages run one after the other as explicit commands. Each reads the
previous stage's directory, verifies it by hash before doing anything, and
writes its own directory; none is triggered by another and none is invoked
by the server.

Principles, from section 3.2 of the
[functional specification](../product/functional-specification.md):

- **Lossless and labelled, not filtered.** Navigation, footers, hidden text
  and cookie banners stay in the record with their region, visibility and
  furniture labels. Removing text is a curation decision, not an extraction
  decision.
- **Offsets are code points in the normalized text**, never byte positions in
  the raw HTML. The raw response is never rewritten; it is referenced by hash.
- **Fail closed.** A saved response whose bytes do not match its manifest hash
  stops the run. A page that cannot be parsed is recorded as
  `extraction_failed`, not skipped.
- **Offline.** No request, no model. Tests need no network.

## 2. Inputs

The extractor reads one run directory written by `swisstip-download` (or by
the adoption path of `legacy.py`). It never writes under `pages/` or
`*-documents/`.

| Input | Used for |
| --- | --- |
| `plan.json` | `catalogue_sha256` (copied into every record and the summary); `targets[]` for `registry_entries`, `references` and the `attribution` of discovered targets |
| `pages/<url-sha256>/latest.json` | The attempt to extract: `snapshots[]` (`relative_path`, `requested_url`, `final_url`, `content_type`, `sha256`, `bytes_downloaded`, `retrieved_at`, `review_flags`), `status`, `language`, `source_page_url`, `version_uri`, `discoveries`, `imported_from` |
| `pages/<url-sha256>/attempt-NNN/response.*` | The raw bytes, verified against the snapshot `sha256` and `bytes_downloaded` before parsing |
| `<plugin>-documents/pages/<url-sha256>/latest.json` and responses | Plugin documents (today the dated Fedlex HTML and PDF), read with the same manifest format; `source_page_url` is the catalogue page, `version_uri` the consolidated version |

Only `latest.json` is extracted. Earlier attempts are acquisition history; if
a later attempt saved new bytes, its record has a new `document_id` and the
old record is marked superseded (section 4.8). A target without an intact
snapshot is listed in `unavailable.json`, not as a record.

## 3. Outputs

### 3.1 Layout

Default output: `<run>/text/`, overridable with `--output`. Keeping the text
next to its run binds it to the same `plan.json` and makes a rerun after a
new download attempt a local update. The ingestion tooling ignores the
directory, and the extractor refuses an output path inside `pages/` or a
`*-documents/` folder.

```text
text/
  documents/<document_id>.json   one record per saved response (section 3.2)
  reading/<document_id>.md       reading view of every eligible record (section 3.4)
  index.json, index.md           one entry per record; the Markdown is grouped by attribution kind
  unavailable.json               targets without an intact snapshot
  errors.json                    records with status extraction_failed
  summary.json                   counts, extractor and dependency versions, catalogue hash
  validation.json                written by swisstip-extract-validate (section 5)
  legacy-text-import.json        present when records were adopted instead of extracted
```

### 3.2 Record

`schema_version` stays `swisstip.source-intermediate/v1`; the fields marked
"new" are additions, nothing is renamed or dropped.

| Field | Content |
| --- | --- |
| `document_id` | `doc-` + first 20 hex digits of SHA-256 over the source URL and the raw hash |
| `corpus_id`, `catalogue_sha256` | Run directory name; the catalogue hash of `plan.json` |
| `source_url`, `document_url`, `requested_url` | Catalogue or discovered URL (`source_page_url` when a plugin resolved it), the final URL after redirects, the URL requested |
| `version_uri` | Consolidated version for Fedlex documents, else null |
| `attribution` | `kind` (`catalogue`, `in-scope`, `language-variant`, `out-of-scope`, `plugin-document`, `unplanned`) and `source_ids`; plugin documents add `plugin_id` and `resolved_from` |
| `source_registry`, `catalogue_references`, `discovery_provenance` | Copied from the manifest |
| `acquisition` | `path`, `attempt`, `manifest_path`, `manifest_sha256`, `raw_sha256`, `bytes`, `retrieved_at`, `declared_content_type`, `review_flags`, `http_status`, `imported_from` (the acquisition import note when present) |
| `representation` | `html`, `pdf`, `text`, `docx`, `xlsx`, `doc`, `rtf`, `image`, `unknown` |
| `page_kind` | `ordinary_page`, `application_shell`, `maintenance_page`, `error_page`, `pdf_document`, `text_document`, `office_document`, `rtf_document`, `image_document` |
| `status` | `extracted`, `excluded_source_response`, `no_extractable_text`, `extraction_failed` |
| `eligible_for_processing`, `exclusion_reasons` | True only for `extracted` records that are not excluded; reasons name the page kind or review flag |
| `title`, `html_title`, `main_headings` | Rules in section 4.5 |
| `language_declared`, `language_hint` | `<html lang>` as declared; hint from the manifest, the registry entry or the URL. No statistical detection |
| `decoding`, `charset_declared`, `warnings` | Encoding used, the HTTP and meta charsets seen, warning codes |
| `blocks` | Ordered blocks (section 3.3) |
| `content_text`, `content_sha256` | Blocks joined by two newlines; SHA-256 of that string in UTF-8 |
| `text_integrity` | HTML only: whether the whitespace-stripped DOM text equals the whitespace-stripped block sequence, with both hashes |
| `pdf_page_count`, `pages_without_text`, `form_fields` | PDF only |
| `representation_group`, `html_counterparts`, `preferred_representation`, `identical_raw_snapshots`, `identical_text_records` | Grouping rules in section 4.7 |
| `extractor_version`, `extracted_at` | Package version; a change invalidates reuse |
| `imported_text` | Present on adopted records: old dataset, old document ID, old extractor version, record file, import time |

### 3.3 Block

| Field | Content |
| --- | --- |
| `block_id` | `<document_id>:bNNNNN`, 1-based position |
| `kind` | `heading`, `paragraph`, `list_item`, `definition_term`, `definition`, `table`, `preformatted`, `quotation`, `caption`, `disclosure_title`, `control`, `address`, `text`, `pdf_page`, `pdf_paragraph`, `plain_text`, `office_paragraph`, `spreadsheet_row`, `spreadsheet_comment`, `legacy_word_paragraph` |
| `text` | Whitespace-normalized text (inner whitespace collapsed to one space; `pre` keeps its layout) |
| `start`, `end` | Zero-based, end-exclusive code-point offsets into `content_text`; the round trip is asserted when the record is written |
| `text_sha256` | SHA-256 of `text` in UTF-8 |
| `heading_path` | Texts of the open `h1` to `h6` headings above the block |
| `source_locator` | HTML: `dom_path` (XPath), `anchor`, `article_id`, `region`, `aria_roles`, `in_main`, `explicit_hidden`, `is_footnote`, `list_context`, `furniture`. PDF: `page`, `paragraph`, `furniture`. Office: `part` and `paragraph` or `row`. Text: `file` |
| `links` | `text`, `href`, `resolved_url` for every anchor inside or enclosing the block |
| `level` | Headings only |
| `rows`, `caption`, `nested_table_count` | Tables only; cells carry `text`, `header`, `rowspan`, `colspan`, `dom_path`, and `source: data-entities` for rows recovered from the table's embedded data |
| `inline_data` | Tables with embedded data rows only: `source` (`data-entities`), `rows` (count) and `text` (the recovered rows, also appended to the block `text`) |

### 3.4 Reading view

`reading/<document_id>.md` is for the knowledge expert. Header: title, source
and document URL, version, attribution, language fields, retrieval date and
attempt, raw and text hashes, warnings. Body: one line per block, prefixed
with its block number and kind, indented by heading depth, with region,
furniture, hidden and footnote marks in brackets. Tables are rendered as
Markdown tables. The JSON record is the source of truth; the view is
regenerated whenever the record is written.

## 4. Processing rules

### 4.1 Selection

| Option | Effect |
| --- | --- |
| default (`--scope attributed`) | Catalogue targets, `in-scope` and `language-variant` discovered targets, plugin documents |
| `--scope all` | Also `out-of-scope` discovered targets (the full pack has 10,687) |
| `--kind KIND` | Only targets of that attribution kind |
| `--source ID` | Only targets attributed to that source (repeatable) |
| `--document-id ID` | Only that record |

### 4.2 Integrity before parsing

For every selected target: read `latest.json`, resolve each snapshot path,
refuse a path that escapes the run, read the bytes, compare SHA-256 and
length with the manifest. A mismatch raises and stops the run.

### 4.3 Format dispatch and decoding

Decided from the bytes, with the declared content type and file suffix as
fallbacks: `%PDF-`; a ZIP with `word/document.xml` or `xl/workbook.xml`; an
OLE container with a `WordDocument` stream; `{\rtf`; JPEG or PNG signatures;
an HTML signature in the first 8 KB or an `.html` suffix; a declared `text/*`,
JSON or XML type as one `plain_text` block; anything else is
`extraction_failed` with the reason.

Decoding order: BOM, strict UTF-8, the charset of the HTTP `Content-Type`,
the `<meta>` charset, then Windows-1252 (Latin-1 as the last resort) with the
`character_encoding_fallback` warning. Bytes that are valid UTF-8 are taken as
UTF-8 whatever the headers say, because Latin-1 text is almost never valid
UTF-8 while misdeclared headers are common. Invalid bytes are never replaced
silently.

### 4.4 HTML and PDF rules

HTML: `script`, `style`, `noscript`, `svg`, `canvas`, `template` and `head`
are ignored and counted; structural elements start a new block, inline
elements join the enclosing block; headings maintain the `heading_path`; a
table is one block with rows, cells, header flags and spans; region, ARIA
roles, `in_main`, explicit hidden state, footnote markers and list context
come from the ancestors; a `<base href>` changes link resolution; the
text-integrity check compares the whitespace-stripped body text with the
blocks.

A client-side data table (the Stadt Wallisellen pages have them) ships an
empty `<tbody>` and its rows as JSON in a `data-entities` attribute,
`{"data": [{column key: HTML}]}`, with each header cell naming its key in
`data-data`. The rows are read from that attribute in header order, their
HTML reduced to text, appended to the table block and recorded in
`inline_data`; the text-integrity check leaves them out, because they are
not DOM text. Without this the emergency numbers, collection dates,
department contacts and legal-collection entries of those pages were saved
but not extracted. A table without header keys or valid JSON is extracted as
before.

Added: every block carries `furniture` labels derived from its ancestors:
`navigation` (a `nav` element or role), `banner` and `footer` (page-level
`header` and `footer` elements or the roles), `breadcrumb`, `skiplinks`,
`share`, `cookie-notice`, `language-menu`, `related-links`,
`anchor-navigation`, `scroll-to-top`, `chat-link`, from known class tokens
of admin.ch and zh.ch pages and a few generic patterns; and `link-only` for
a list item whose whole text is its link labels. Labels never remove text.

PDF: PDFium supplies the text per page; a page with blank lines is split at
them into `pdf_paragraph` blocks, a page without stays one `pdf_page` block.
A first or last line that recurs on at least half of the pages (three pages
minimum, digits ignored) marks its paragraph `page-header` or
`page-footer`. AcroForm fields come from pypdf; pages without text are
listed; there is no OCR.

### 4.5 Page kind, eligibility, title, headings

| Signal | Page kind |
| --- | --- |
| Title `Fedlex`, or a `fedlex-app` or `app-root` element | `application_shell` |
| `window.CONTENT_ID` and `window.IS_FRONTEND` in the source with under 300 characters of text | `application_shell` |
| Title matches maintenance words in German, French, Italian or Romansh | `maintenance_page` |
| Title or an `h1` is a 404, not-found, access-denied or forbidden phrase | `error_page` |

Excluded (`status: excluded_source_response`): shells, maintenance and
error pages, and responses whose snapshot carries the review flag
`javascript_application_shell` or `possible_access_challenge`. The imported
flag `browser-rendered-dom-not-raw-http-response` does not exclude; it is
carried as a warning. A record with no blocks is `no_extractable_text`.

`title` is the HTML title unless empty or a Fedlex `input-xx` placeholder;
then the first catalogue reference label, then the first main heading, then
the source URL. `main_headings` are the `h1` elements inside `main` without
furniture labels; failing that, the `h1` elements outside header, footer and
nav; failing that, all of them.

### 4.6 Identity and language

`document_id` hashes the source URL and the raw bytes. A page downloaded
again with identical bytes keeps its ID and its record; a page whose bytes
changed gets a new ID, and the old record is marked superseded. The block
IDs follow the document ID. `language_declared` is the `lang` attribute of
`<html>`. `language_hint` is the manifest `language` (set for discovered
language variants), else the registry entry's language, else a language code
that is a whole path segment of the URL (`/de/`, `/eli/cc/.../de`) or a
`_fr` / `-en` file-name suffix (`/it-services/` is not such a segment).

### 4.7 Representation groups and duplicates

Records with the same `source_url` form a `representation_group`. A PDF whose
group has an eligible HTML record lists it in `html_counterparts` and is not
the `preferred_representation`. Records with identical raw bytes list each
other in `identical_raw_snapshots`; records with identical normalized text
but different bytes (print views, untranslated language variants, pages
with a build stamp) list each other in `identical_text_records`. All stay
separate records because their URLs and attributions differ.

### 4.8 Reuse, resumption, workers

An existing record is reused when its raw hash equals the snapshot hash, its
status is not `extraction_failed`, and either its extractor version is the
current one or the record was adopted rather than extracted. `--force` re-extracts
everything. Records whose ID no longer matches a `latest.json` snapshot are
marked `superseded` in the index and deleted with `--prune`; records outside
the current selection are kept. The index and summary are rebuilt from
`documents/` at the end of every run, so an interrupted run leaves a
consistent directory after the next one. `--workers N` extracts in a process
pool; the parent writes the index and the group fields.

## 5. Validation

The extractor asserts the offset round trip for every block as it writes a
record. `swisstip-extract-validate --text <dir>` re-reads a dataset and
writes `validation.json` with five checks: records readable; content hash,
block offsets and block hashes; the raw response of every record present in
the run with the recorded hash; the index equal to the records on disk;
every eligible record with a reading view. Nonzero exit on any failure.

## 6. Contract with curation and the release build

A curated fact points at a `document_id` and a block range. The release
build resolves it to the exact excerpt and records, per evidence item, the
excerpt, the code-point offsets, the record's `content_sha256`, the raw hash,
retrieval time and URL, the block IDs and the language. A rebuild with the
same extractor version over the same run reproduces the same IDs, offsets
and hashes.

When a page is downloaded again, `swisstip.extraction.anchors` re-anchors
the curated range instead of failing the whole build:

| Step | Test | Outcome |
| --- | --- | --- |
| 0 | The new run holds a record with the same document ID | same snapshot, nothing to do (the build's own check) |
| 1 | The record for the same source URL has the same `content_sha256` | `same-text`: bytes changed, text did not |
| 2 | The sequence of block hashes occurs exactly once | `moved`: new block IDs and offsets |
| 3 | The exact excerpt text occurs exactly once in `content_text` | `moved` with `blocks-restructured`: a paragraph was split or merged |
| 4 | Several candidates | `ambiguous`, unless exactly one has the anchored heading path |
| 5 | Nothing found | `changed`: the closest new blocks are listed with a similarity ratio |

A successful relocation still warns `context-changed` when the heading path
differs and `neighbourhood-changed` when the other blocks under the same
heading were added, removed or rewritten. The build decides what the review
mark does with each outcome; the proposal is that `same-text` and a clean
`moved` keep it and everything else clears it.

## 7. Command line

```shell
./.venv/Scripts/python.exe -m pip install -e "packages/extraction[office]"
./.venv/Scripts/python.exe -m unittest discover -s packages/extraction/tests
./.venv/Scripts/python.exe -m swisstip.extraction.extract_cli --run releases/<pack>
./.venv/Scripts/python.exe -m swisstip.extraction.validate --text .local/<pack>/text
./.venv/Scripts/python.exe -m swisstip.extraction.extract_cli --run .local/<pack> --workers 4
./.venv/Scripts/python.exe -m swisstip.extraction.extract_cli --run .local/<pack> --scope all --workers 4
```

The options are listed in the [package README](../../packages/extraction/README.md).

## 8. Package layout, dependencies, tests

```text
packages/extraction/
  pyproject.toml                swisstip-extraction; scripts swisstip-extract, swisstip-extract-validate
  README.md
  src/swisstip/extraction/
    run_reader.py               plan, manifests, attribution, plugin folders, hash verification, selection, document IDs
    html_blocks.py              block walker, decoding, furniture labels, page kind, main headings, text integrity
    pdf_text.py                 PDFium page text, paragraphs, repeated lines, pypdf form fields
    office_text.py              DOCX, XLSX, DOC, RTF (optional dependencies)
    records.py                  dispatch, record assembly, title and language rules, offsets, hashes
    reading_view.py             Markdown view of a record
    dataset.py                  reuse, prune, index, summary, group fields, output guards
    anchors.py                  make_anchor and relocate
    legacy.py                   adopt an already extracted record into a run
    extract_cli.py              argument parsing, worker pool, exit code
    validate.py                 dataset checks and validation.json
  tests/                        tests on synthetic runs and documents; no network
```

Dependencies, pinned: `lxml` 6.1.3, `pypdfium2` 5.13.0, `pypdf` 6.18.0;
optional group `office`: `olefile` 0.47, `striprtf` 0.0.33. No Pillow, no
Lingua.

## 9. The datasets of both packs

Every record of both datasets is extracted from the saved responses by this
extractor, with its version and its labels (furniture, PDF paragraphs,
declared charsets); no adopted record remains.

| Dataset | Records | Eligible | Blocks | Text | Disk |
| --- | ---: | ---: | ---: | ---: | ---: |
| `mvp-zurich/text/` | 391 | 382 | 67,604 | 4.51 M chars | 73 MB |
| `swiss-residence/text/` | 12,117 | 11,398 | 1,261,602 | 176.6 M chars | 2.0 GB |

The nine excluded records of the MVP run are the six application shells
(three Fedlex ELI pages, whose dated documents are separate records, and
three Federal Publications shop pages) and the first responses of three
ch.ch pages, which are soft error pages; their later attempts are extracted
records that supersede them.

In the full run, 565 records are excluded (537 application shells, 28 error
pages), 154 have no extractable text, 237 PDF records have pages without
embedded text, and 2,605 records share their normalized text with another
record. The 14 Fedlex documents of the full run were also reached by the
crawl as ordinary pages with the same bytes; since identity is the source
URL plus the bytes, each is one record, and it carries the plugin
attribution (the source IDs of its ELI page) because plugin document folders
are read before `pages/`.

## 10. Out of scope

- Language identification by a classifier, topic tagging, assertion or
  sentence splitting: a later semantic stage, if one is wanted at all.
- OCR of textless PDF pages and images; they are counted and named.
- Rendering of JavaScript application shells; the gap report names them and
  the Fedlex plugin resolves the ones that matter.
- Any change to the run directory or the catalogue.
- The curation file format and the release build script (WP2 and WP4 of the
  specification); section 6 states what they can rely on.
