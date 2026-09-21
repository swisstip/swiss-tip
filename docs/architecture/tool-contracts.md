# Tool contracts

**Last update:** 21 September 2026

**Schema version:** `swiss-tip/v4`<br>
**Source of truth:** [`packages/core/src/swisstip/core/contracts.py`](../../packages/core/src/swisstip/core/contracts.py)
(Pydantic models, served by the mock and by the real server;
`scripts/test/mock-mcp/contracts.py` re-exports them); the exported JSON
Schema bundle is [`tool-contracts.schema.json`](tool-contracts.schema.json)<br>
**Status:** the contract is `swiss-tip/v4`; packages 0.2.5, the latest on
PyPI, serve it in full. The committed bundle is the frozen contract of the
first hour of the event (24 September): the reproduced models must export an
identical bundle (specification, section 5.2), and during the event only
additive changes are allowed, decided by the lead. Until the event starts
the lead may still change it, additively or not.

This document defines what a caller sends to and receives from the four MCP
tools. The mock server serves exactly these shapes; the real server must too.
Field tables are normative; the examples are real responses captured from
the mock on 12 September 2026, except where an example names another
source. Those of section 5 send the jurisdiction under the field names of
v3, which are still accepted, and show `executed_scope` with the codes
alone, without the names v4 adds.

Regenerate or verify the schema bundle:

```shell
./.venv/Scripts/python.exe -m swisstip.core.contracts --output docs/architecture/tool-contracts.schema.json
./.venv/Scripts/python.exe -m swisstip.core.contracts --check docs/architecture/tool-contracts.schema.json
```

The real server (`apps/mcp-server`, semantics in `packages/runtime`) serves
these shapes on `releases/mvp-zurich/release.json`; the mock keeps serving
its hardcoded scenario for harness development.

---

## 1. Common rules

1. **Strict objects.** Every request and result object rejects unknown
   fields. A request with an unknown field is an error, not a warning.
2. **Errors are typed.** A malformed request or an unknown identifier returns
   a `ToolError` with the MCP `isError` flag set (section 7). A request that
   is well formed but cannot be answered from the release is not an error: it
   returns a result with a status and a named gap (section 5).
3. **Two copies of every result.** The server returns the result as
   `structuredContent` and as the same JSON in the text content, so a client
   that reads either sees the same data.
4. **The server composes no answer.** Results carry facts, excerpts,
   citations, statuses and guidance. The calling assistant writes the answer.
5. **Dates** are ISO 8601 calendar dates (`2026-09-12`). **Languages** are
   ISO 639-1 codes (`en`, `de`, `fr`, `it`, `rm`). **Jurisdictions** are
   `CH` for the federal level, ISO 3166-2 canton codes (`CH-ZH`), and the
   canton code plus the BFS municipality number for municipalities
   (`CH-ZH-261` is the City of Zurich).
6. **Every result names the release** it was answered from in `release_id`
   and ends with a `limitations` list the caller should pass on when
   relevant. `get_coverage` carries the release's full list. `search`,
   `resolve` and `get_evidence`, which a caller reads on every call, carry
   two lines: the review status of the served facts with its counts, and
   a pointer that names what the full list says and where it is. Lines
   that belong to one request (facts withheld by `reviewed_only`, a stale
   snapshot, a query language search does not advertise) are added to
   them.
7. **Read-only.** All four tools are annotated `readOnlyHint`, not
   destructive, not open-world. Nothing a caller sends changes the release.

## 2. Shared types

### Jurisdiction

Where the user lives, in parts. Every part takes a name, in English or in
the local language, or a code.

| Field | Type | Default | Meaning | Also accepted as |
| --- | --- | --- | --- | --- |
| `country` | string or null | null: the country the release serves | `Switzerland`, `Schweiz`, `CH`; another country resolves `OUT_OF_COVERAGE` | `country_code` |
| `canton` | string or null | null | `Zurich`, `Zürich`, `Kanton Zürich`, `ZH`, `CH-ZH` | `canton_code`, `state`, `region` |
| `city` | string or null | null | The municipality (political commune), not a district, a quarter or a postcode: `Wallisellen`, `Zurich`, `CH-ZH-261`, or the BFS number `261` next to the canton | `municipality`, `municipality_id`, `commune`, `town` |

A caller gives the most specific level it knows, in the words the user
used; it does not need to know a code. The server turns the parts into
codes before anything else, with the place register of the release: the
country, the 26 cantons and every municipality of the official register of
municipalities, with their official names and the other-language names the
pack adds (`Geneva`, `Genf`, `Ginevra`). The register is data of the
release, not of the server, so a pack for another country brings its own
places; its level names (`state`) are accepted on the same fields. A
release serving one country needs no `country`; one serving several
requires it. The rules:

- **Spelling.** Case, diacritics, the ASCII spelling of an umlaut and
  punctuation do not matter: `Zürich`, `Zuerich` and `zurich`, or `St.
  Gallen` and `St Gallen`, are one name. Generic words around a name are
  ignored (`Canton of`, `Kanton`, `Stadt`, `City of`, `Ville de`). Each
  part of a name in two languages is the place (`Biel`, `Bienne`), and so
  is the register's qualified spelling (`Buchs (SG)`, `Buchs SG`). Where
  folding makes two places meet (`Brugg` AG and `Brügg` BE), the one
  written as the caller wrote it wins.
- **A city supplies its canton.** `{"city": "Wallisellen"}` is enough.
- **Nothing is guessed.** A part the register does not hold (a quarter
  such as `Oerlikon`, a postcode, a misspelling, a code that does not
  exist) is listed in `executed_scope.not_recognised`, the request runs
  for the broader place that was recognised, which still reaches its
  federal and cantonal facts, and `guidance_for_caller` says so. A bare
  number is read as a municipality only next to its canton: alone it could
  as well be a postcode (4001 is Basel's postcode and Aarau's number).
- **Errors.** A name several municipalities share without a canton
  (`Buchs` in ZH, SG and AG), a name two cantons share (`Basel`,
  `Appenzell`) and a city that lies in another canton than the one given
  are `INVALID_ARGUMENT` errors that list the candidates with their codes.
- **Without a register.** A release that embeds no place register reads
  codes only; the `jurisdiction` description the server sends with
  `tools/list` says so, and names the default country.
- **A flattened place is folded.** A small model that sends a part next to
  the other arguments (`{"concept_ids": [...], "city": "Wallisellen"}`)
  instead of inside `jurisdiction` is served rather than sent round again:
  every name the field accepts is folded into `jurisdiction`. The schema
  keeps advertising the nested object, which is what a caller should send,
  and `executed_scope` echoes what was understood. The same part given
  twice, once next to `jurisdiction` and once inside it, is an
  `INVALID_ARGUMENT` error. `search` and `resolve` fold; no other tool takes
  a place.

Short codes are normalized: `ZH` becomes `CH-ZH`, `261` with a
canton becomes `CH-ZH-261`, a full municipality code supplies a missing
canton, and case is ignored.

### ExecutedScope

`executed_scope` in the `resolve` result: the jurisdiction as the server
understood it.

| Field | Type | Meaning |
| --- | --- | --- |
| `country_code` | string or null | `CH`; for a country that is not served, the caller's code, or null when it was named in words |
| `canton_code` | string or null | For example `CH-ZH` |
| `municipality_id` | string or null | For example `CH-ZH-261` |
| `country`, `canton`, `city` | string or null | The register's official names (`Switzerland`, `Zürich`, `Wallisellen`); `country` holds the caller's words for a country that is not served |
| `not_recognised` | object | The parts the register does not hold, by field, as sent; omitted when every part was recognised |

Containment rule: a concept
published for `CH` answers for any canton or municipality; a concept
published for a canton answers for that canton and its municipalities; a
municipal concept answers only for that municipality. Never upward or
sideways: a Zurich concept does not answer for Bern, and a municipal concept
does not answer a canton-level request.

### FreshnessPolicy

| Field | Type | Meaning |
| --- | --- | --- |
| `snapshot_date` | date | Day the source pages were saved |
| `max_age_days` | integer | Days after the snapshot during which results are current |
| `stale_from` | date | First day on which results report `STALE` |

### Status

| Value | Meaning | What the caller does |
| --- | --- | --- |
| `SUPPORTED` | Facts and citations returned for the requested scope | Build the answer from the facts; cite the URLs |
| `NEEDS_CONTEXT` | A required context field is missing | Derive it from what the user said; ask only if it cannot be derived; call again |
| `OUT_OF_COVERAGE` | The release has nothing for this jurisdiction, date, concept or context | Say so and do not answer from general knowledge as if grounded; use the `published_values` from the gap only if the user's situation matches one |
| `STALE` | Facts returned, but `as_of` is on or after `stale_from` | Present the facts as published on the snapshot date, not as holding on `as_of`; tell the user to check the cited page; never resolve again with an earlier `as_of` to avoid the status |

### ContextField

| Field | Type | Meaning |
| --- | --- | --- |
| `type` | `"string"` | Only string context values exist in v1 |
| `enum` | list of string or null | Allowed values, when closed |
| `description` | string | What the field means and how to derive it |

## 3. `get_coverage`

Discover the catalog. The root page is deliberately small (about 1.5 KB in
the mock, about 5 KB on the current Zurich release with its seven topics and
28 jurisdictions; the round-trip check allows 6 KB) so that one call is
enough to refuse an outside question.

### Request

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `release_id` | string | no | Release to read; omit for the active release |
| `parent_id` | string | no | A `topic_id` from the root page to list its concepts; omit for the root |

### Result without `parent_id`: CoverageRoot

| Field | Type | Meaning |
| --- | --- | --- |
| `release_id` | string | Active release |
| `schema_version` | string | This contract version |
| `scope_statement` | string | One paragraph: what the release covers |
| `out_of_scope` | list of string | Named exclusions |
| `out_of_scope_response` | string | What to tell the user when the question is outside scope |
| `jurisdictions` | list of string | Every jurisdiction with at least one concept |
| `languages` | list of string | Evidence languages in the release |
| `query_languages` | list of QueryLanguage, or absent | The languages to write `search` queries in, best first (section 4.2): `code`, `rank` (1 is preferred), `indexed` (what the release indexes in that language) |
| `freshness` | FreshnessPolicy | |
| `topics` | list of TopicSummary | `topic_id`, `title`, `concept_count`. The jurisdictions of a topic are not repeated here: `jurisdictions` above lists all of them and the topic page carries them per concept, which keeps the root small |
| `institution_levels` | map of string to integer, or absent | Cited documents per level of the institution that published them: `federal`, `cantonal`, `municipal` |
| `basis_kinds` | map of string to integer, or absent | Facts per kind of the excerpt they rest on: `act`, `ordinance`, `treaty`, `directive`, `guidance`, `directory`, `summary` |
| `limitations` | list of string | |

### Result with `parent_id`: CoverageTopic

| Field | Type | Meaning |
| --- | --- | --- |
| `release_id`, `topic_id`, `title` | string | |
| `concepts` | list of ConceptSummary | See below |
| `limitations` | list of string | |

ConceptSummary:

| Field | Type | Meaning |
| --- | --- | --- |
| `concept_id` | string | Stable identifier, dotted, lower case: `residence.zh.registration-eu-efta` |
| `topic_id` | string | |
| `label` | string | One line, English |
| `description` | string | One or two sentences, English |
| `jurisdictions` | list of string | The one jurisdiction the concept is published for |
| `required_context` | list of string | Context fields `resolve` needs for this concept |

The listing carries what a caller needs to pick a concept and no more: it
carries neither `aliases` (the terms `search` matches) nor `context_schema`,
which together were 60% of the largest topic page. The allowed values of a context field reach the
caller on a search hit (`context_schema`) and on a `NEEDS_CONTEXT` resolution
(`missing_context` with `options` and `hint`).

### Example: root page

```json
{
  "release_id": "mock-residence-registration-2026-09-11",
  "schema_version": "swiss-tip/v3",
  "scope_statement": "Residence permits and registration for foreign nationals in Switzerland: federal rules (apply in every canton), Canton of Zurich procedures and the City of Zurich move-in procedure. This mock release covers one scenario: EU/EFTA nationals taking up employment.",
  "out_of_scope": [
    "Third-country (non-EU/EFTA) nationals: not in this mock release",
    "Cantons other than Zurich beyond the federal rules",
    "Fees, processing times, office hours and appointment availability",
    "Visas for tourism, asylum, naturalisation",
    "Any topic outside residence permits and registration",
    "Legal advice"
  ],
  "out_of_scope_response": "Tell the user that this service does not cover their question, quote the scope statement, and do not answer from general knowledge as if it were grounded.",
  "jurisdictions": ["CH", "CH-ZH", "CH-ZH-261"],
  "languages": ["en", "de"],
  "freshness": {"snapshot_date": "2026-09-11", "max_age_days": 60, "stale_from": "2026-11-10"},
  "topics": [
    {"topic_id": "residence", "title": "Residence permits and registration for foreign nationals", "concept_count": 4}
  ],
  "limitations": ["Mock server: hardcoded content for one scenario, taken from official pages saved on 2026-09-11. Not the curated knowledge base and not legal advice."]
}
```

## 4. `search`

Find concepts by free text. Search never widens scope: a hit only says that
a concept exists; `resolve` decides whether it applies. With the user's
place, search leaves out the concepts that cannot apply there and names
them (section 4.4).

### Request

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `query` | string, non-empty | yes | The user's question or key terms in one of the query languages (section 4.2); a question already in one of them is sent as asked, a question in another language is sent with its key terms translated into the preferred one |
| `limit` | integer 1 to 10 | no, default 3 | Maximum hits. Three is enough for a question about one subject; a caller raises it only to explore |
| `jurisdiction` | Jurisdiction (section 2) | no | Where the user lives, when the question or the conversation says so, in the same parts and spellings as for `resolve`. Empty, or the country alone, is a search without a place (section 4.4) |

### Result: SearchResult

| Field | Type | Meaning |
| --- | --- | --- |
| `release_id`, `query` | string | |
| `results` | list of SearchHit | Ranked, best first; with a `jurisdiction`, only concepts that can apply at that place |
| `executed_scope` | ExecutedScope or absent | The place the search ran for, as for `resolve` (section 2); absent without a `jurisdiction` |
| `published_elsewhere` | list of PublishedElsewhere, omitted when empty | With a `jurisdiction`: the concepts that would have been among the first `limit` hits but are published for other cantons or municipalities only, each with `concept_id`, `label` and `jurisdictions`. They are not to be resolved (section 4.4) |
| `matched_count` | integer or absent | Number of selected candidates before `limit`, among the concepts that can apply at the place when one was given; not a count of every relevant concept. Older mock responses may omit it. |
| `truncated` | boolean | Some selected candidates were omitted by `limit` |
| `retrieval_mode` | `lexical`, `hybrid` or `lexical-fallback` | Method actually used for this query |
| `ranking_model`, `ranking_model_digest` | string or absent | Configured embedding model and the digest bound to the index |
| `fallback_reason` | string or absent | Why optional semantic retrieval could not run |
| `match_strength` | `strong`, `weak`, `none` or absent | Whether the question's distinctive words reached a published concept (section 4.1). `strong`: resolve the relevant hits. `weak`: the hits rest on incidental words or a nearby subject; the question probably lies outside the scope, decline it unless a hit is clearly its subject. `none`: no candidate. Older mock responses omit it |
| `match_signals` | object or absent | `lexical_share`, `anchored_weight` and `best_semantic_score`, the signals behind the verdict, for the record |
| `scope_statement` | string or absent | The release's scope statement, present on `weak` and `none` results so the caller can decline in the same call without `get_coverage` |
| `guidance_for_caller` | string or absent | One text per `match_strength`: ranked candidates still need resolution against the caller's explicit scope and rephrasing rarely finds other concepts; weak candidates rest on incidental words, so compare the question with `scope_statement` and decline unless a hit is clearly its subject; an empty result does not establish domain noncoverage, so compare with `scope_statement` and, only if a topic fits, call `get_coverage` once for that topic. On `weak` and `none` it adds one exception: a question in a language other than the query languages is searched once more with its key terms in the preferred one |
| `limitations` | list of string | |

SearchHit: `concept_id`, `topic_id`, `label`, `description`, `jurisdictions`
and `required_context` as in ConceptSummary, `context_schema` (map of field
name to ContextField: the fields the concept understands), plus `score`
(number, higher is better, not comparable across releases) and `matched_on`
(which of `label`, `aliases`, `questions`, `description`, `statements`, `semantic`
matched). A hit needs a match on the label, an alias or a question; overlap
with the description or the fact statements only raises the lexical score.
No lexical match means no indexed terms matched, not that the release lacks
the subject. Semantic candidates do not require lexical overlap.
Each query token counts once per concept, at the best field it appears in
(label and alias 3, question 2, description 1, statement 0.5), multiplied by its rarity across the concepts, so a question
is ranked by its distinctive words ("Familiennachzug", "Grenzgänger") and not
by how often "Aufenthalt" or "Schweiz" recurs; aliases weigh as much as the
label because they carry the publisher's source-language terms. The score is
then multiplied by the concept's prior, `0.7 + 0.3 x authority`, where the
authority follows from the basis of the concept's facts (the law above
guidance above a portal summary) and the review of their statements, never
from the level of the publisher; the prior reorders near-ties only, and the
weights are in the release's manifest, not on the wire.
`context_schema` lets the first `resolve` carry the context; the mock
returns it empty.

### 4.1 Match strength

A question outside the release still produces hits: "Wie hoch ist die
Mehrwertsteuer in der Schweiz?" matches concepts on "Schweiz", "How do I
get a Halbtax?" on "get", and hybrid search adds the nearest concept
whatever the distance. The hits stay, because a strong model may still
want them, but the result carries a verdict computed after fusion, in
both modes, from raw signals rather than from the scores:

- `lexical_share`: the share of the query's rarity-weighted tokens that
  the label, aliases or questions of one concept match, for the concept
  where that mass is highest. A token the index has never seen counts as
  unmatched at the weight of a token found in one concept, so
  "Mehrwertsteuer" is not free.
- `anchored_weight`: the same matched mass in units of the rarest possible
  token. It carries natural questions, whose names and places dilute the
  share; the share carries one- or two-word queries, whose mass is small.
- `best_semantic_score`: the best raw cosine over every concept before
  the candidate cut, hybrid mode only.

The rule, with the thresholds of `packages/runtime` (`LEXICAL_STRONG_WEIGHT`
1.5, `LEXICAL_STRONG_SHARE` 0.5, `LEXICAL_PARTIAL_WEIGHT` 1.0,
`SEMANTIC_STRONG_SCORE` 0.7, `SEMANTIC_PARTIAL_SCORE` 0.6,
`SEMANTIC_VETO_SCORE` 0.45): no hit at all is `none`; a cosine below the
veto is `weak` whatever matched lexically; an anchored weight or a share
at or above its strong threshold is `strong`; otherwise a strong cosine,
or a partial weight together with a partial cosine, is `strong`; the rest
is `weak`. Facet words (`FACET_WORDS`: address, opening hours, open,
telephone, e-mail, contact and their German forms such as Adresse,
Öffnungszeiten, Telefon, E-Mail) are left out of the share and the weight:
they say what the user wants to know about a subject, not which subject, so
"opening hours of the Zurich zoo" reads `weak` although the office-contact
concepts of release `mvp-zurich-2026-09-17-v1` carry those words; they
still rank the hits. The thresholds were measured on release
`mvp-zurich-2026-09-16-v2`: every acceptance and round-trip question, the
Zurich German one included, reaches a weight of 1.6 or more, fourteen
off-topic questions stay at 1.35 or less with cosines up to 0.60, and the
one lexical coincidence above the share threshold (a Bern kindergarten
question matching "Anmeldung") has a cosine of 0.40, so hybrid search
vetoes it and lexical search does not. The runtime test
`scripts/test/packs/test_match_strength.py` replays those questions on the committed release;
a release that adds concepts changes the token weights, so a failure there
after a rebuild calls for a new measurement before a threshold moves. The
measurement, with every signal per question, is the record
`.local/experiments/2026-09-16-search-match-strength.md`.
Known misses: a French or Italian question about a covered subject reads
`weak` in lexical mode, because the release carries no French or Italian
terms, and hybrid search lifts it only when the cosine reaches 0.6 with a
partial lexical weight or 0.7 alone. Section 4.2 asks the caller to search in
German instead, and the `weak` and `none` guidance allows one translated
search before declining.

### 4.2 Query languages

Lexical search matches only the languages the release indexes, so the server
names them before the first search and asks the caller to write queries in
them. The server measures them on the release at startup, not in the build,
so the release and its readiness record stay as they are:

- A **source language** is the language of an excerpt that contains one of
  its concept's aliases verbatim (the source terms of the build). It is
  listed only when its terms reach at least half of the concepts
  (`QUERY_LANGUAGE_MIN_CONCEPT_SHARE` 0.5 in `packages/runtime`): a caller
  sends a question in a listed language untranslated, and a language whose
  terms sit on a few concepts (French office names on a contact page) would
  make most such questions read `weak`. A source language below the share is
  left out and named in `limitations` with its counts, so the caller
  translates instead. Listed source languages rank first, by their number of
  terms, because a query in them can meet the publisher's own words.
- The **statements' language** (English) is always listed, after them:
  labels, sample questions, descriptions and statements are written in it,
  however few of its terms occur in an excerpt.

On `mvp-zurich-2026-09-16-v2` German ranks first with 611 source terms on 76
of 77 concepts and English second (2 source terms on 1 concept, and the
authored fields); on `mvp-wallisellen` German has 119 on 27 of 30 concepts
and English only the authored fields. Neither release cites a French or
Italian excerpt, so nothing is left out yet. The server names the result in
four places:

1. the MCP `instructions` of the `initialize` result, with the scope
   statement and the two-call pattern ("Search ONCE per question, in German
   or English, never in more than one of them: a second search in another
   language rarely finds other concepts. Write the search query in German or
   English: lexical search matches only these languages. Send a question in
   one of them as asked, without translating it; translate the key terms of
   a question in any other language into German before searching, and still
   answer in the user's language."); clients that do not pass instructions
   to the model miss this one;
2. the same sentence appended to the `search` description;
3. the same sentence as the description of the `query` field;
4. `query_languages` on the coverage root.

The committed schema bundle and the mock keep the generic text, which names
English and German unless the server names others. When a search reads
`weak` or `none`, `guidance_for_caller` adds one exception to "do not search
again": if the question is in another language and the query was not
already translated, search once more with its key terms in German. On the
Zurich release a French registration question reads `weak` with unrelated
hits, and its key terms in German ("Tschechischer Staatsangehöriger
Stellenantritt Zürich Anmeldefrist Gemeinde Ankunft") read `strong` with the
registration concepts; `scripts/test/packs/test_match_strength.py` replays both. Whether live
callers follow the instruction was not yet measured.

Until 2026-09-21 the sentence read "German (preferred) or English" and did
not ask for one search. An OpenCode caller then sent an English question
about a dog in the City of Zurich twice, in English and in German. On
`mvp-zurich-2026-09-19-v15` both queries, and the question as asked, put the
same two concepts first, lexical and hybrid, so the second search only spent
a call. The first language is now named only as the target of a translation,
the rule of one search opens the sentence and the generic `search`
description, and a release with a single query language gets no such rule.

What the wording achieves was measured on 2026-09-21 with that question,
OpenCode 1.18.31 and the working-tree server in hybrid mode; the samples are
small:

| Caller and wording | Sessions with one search |
| --- | --- |
| `deepseek/deepseek-v4-flash`, rule at the end of the sentence | 1 of 1 |
| `opencode/ling-3.0-flash-fin-free`, rule at the end of the sentence | 0 of 3 |
| the same model, rule also first in the `search` description | 2 of 4 |
| the same model, rule also first in the sentence | 2 of 6 |
| the same model, and one sentence in the agent's own prompt | 5 of 6 |

The free model opens with two tool calls side by side, decided before it
reads a result: a German search next to the English one, a second English
wording, or, when it follows the rule, `get_coverage` in the second place.
No wording of the server stopped that. The sentence that did is the
caller's: "Make one tool call at a time, never several in parallel: one
search for the question, then one resolve for the concepts it found.", now
in the agent prompt of the OpenCode image (`docker/opencode/opencode.json`).
Every session, with one search or two, resolved the same two concepts and
answered from them; the second search costs a call, not the answer.

### 4.3 Hybrid search

Optional hybrid search independently selects up to ten semantic candidates
with cosine similarity at least 0.5, applies the same prior to their
similarity, then combines their ranks with all
lexical matches using reciprocal-rank fusion: each list contributes
`1 / (60 + rank)`, with ranks starting at one. Ties use concept ID. The
semantic candidate limit and threshold are configurable; these are
experimental retrieval settings, not confidence or applicability thresholds.
`matched_count` counts the union before the caller's `limit`. Hybrid scores
are fusion scores; lexical scores keep their existing meaning. Neither is
a probability. The server reports `lexical-fallback` if the index or local
embedding service is unavailable. `resolve` retains its deterministic
selection of all applicable published facts.

The index is a separate, hashed artifact bound to the release ID, release
content hash, embedding inputs and model digest. It does not change the
knowledge release. See [runtime README](../../packages/runtime/README.md)
for index preparation, the bounded benchmark and the explicit local-model
dependency.

### 4.4 Search for a place

Without a place, a question from another canton ranks the Canton and City of
Zurich concepts next to the federal ones; a caller resolves them and is
refused, or never sees the federal concept that applies. A caller that knows
where the user lives sends it as `jurisdiction`, read with the place register
exactly as for `resolve`: a name or a code per part, the same errors for a
name several municipalities share (`jurisdiction.city`), and a part the
register does not hold moves the search to the broader place and is named in
`executed_scope.not_recognised` and in `guidance_for_caller`.

- **What is ranked.** A concept stays in the ranking when one of its
  jurisdictions contains the place (a federal concept for every canton, a
  cantonal one for its municipalities) or lies below it: a caller that gave
  the canton may not know the city yet, and `resolve` then asks for it. A
  concept published beside the place only, for another canton or
  municipality, is left out. The score floor follows the best concept that is
  left, and in hybrid mode the semantic candidates are cut among the concepts
  that can apply, from the same single embedding request.
- **What is named.** The ranking over every concept is still computed. The
  concepts among its first `limit` hits that were left out are listed in
  `published_elsewhere`, and the guidance tells the caller not to resolve
  them, to carry nothing over from them and, when no result answers the
  question, to say that the service does not publish it for the user's place.
  It says so explicitly when the best hit of all is one of them.
- **What does not change.** `match_strength` and `match_signals` stay those
  of the whole release: they say whether the subject is published at all, and
  a sibling concept of another place often carries the words that say so.
  Judged on the applicable concepts alone, the lexical verdict turned six
  covered questions of the regression pack `weak`, five of them with their
  concept found, and corrected none. With no applicable hit the strength is `none`, with
  the scope statement. For a country the release does not serve, `results`
  is empty and the guidance says which country is served.

Measured on release `mvp-zurich-2026-09-18-v19` by replaying the 433 search
steps of the regression cases that name a canton or a city, once as the suite
sends them and once with that place: lexical search finds the expected
concept in 377 of 416 steps instead of 371 (among the seven gained are a
German citizen registering in Basel, a German licence in Basel and the
address of the migration office in Basel), hybrid search in 392 instead of
393; no verdict changes in either mode. The one step lost in both modes
expects the City of Zurich departure concept for a resident of Wädenswil,
which is now named in `published_elsewhere` instead of ranked. 51 lexical and
53 hybrid steps name at least one concept there, 76 and 71 hits in all that
a caller would otherwise have been offered and refused. On a result with
two such concepts the fields add about 0.4 KB. No live caller has been run
with the field yet, so whether callers send it is not measured.

Request `{"query": "I moved to Basel eight months ago with a German driving
licence. Do I have to exchange it?", "jurisdiction": {"city": "Basel"}}`, on
the release named above; without the jurisdiction the first three hits are
`zh-foreign-licence-exchange`, `foreign-licence-exchange` and
`zh-control-drive`:

```json
{
  "release_id": "mvp-zurich-2026-09-18-v19",
  "query": "I moved to Basel eight months ago with a German driving licence. Do I have to exchange it?",
  "results": [{"concept_id": "foreign-licence-exchange", "...": "..."},
              {"concept_id": "posted-service-notification", "...": "..."},
              {"concept_id": "health-insurance-deadline", "...": "..."}],
  "executed_scope": {"country_code": "CH", "canton_code": "CH-BS", "municipality_id": "CH-BS-2701",
                     "country": "Switzerland", "canton": "Basel-Stadt", "city": "Basel"},
  "published_elsewhere": [
    {"concept_id": "zh-foreign-licence-exchange", "label": "Exchanging a foreign driving licence in the Canton of Zurich", "jurisdictions": ["CH-ZH"]},
    {"concept_id": "zh-control-drive", "label": "Control drive for a foreign driving licence in the Canton of Zurich", "jurisdictions": ["CH-ZH"]}],
  "match_strength": "strong",
  "guidance_for_caller": "Ranked candidates from this release. ... The concepts in published_elsewhere match the question, the first of them better than any result, but are published for other places only and do not apply in CH-BS-2701 (municipality of Basel): do not resolve them and carry over no rule, office, fee or deadline from them. If no result answers the question, tell the user that this service does not publish it for their place.",
  "limitations": ["..."]
}
```

### Example

Request `{"query": "register stay municipal authority Zurich", "limit": 2}`:

```json
{
  "release_id": "mock-residence-registration-2026-09-11",
  "query": "register stay municipal authority Zurich",
  "results": [
    {
      "concept_id": "residence.registration-after-arrival-eu-efta",
      "topic_id": "residence",
      "label": "Registration with the municipality after arrival (EU/EFTA nationals taking up employment)",
      "description": "Federal deadline and order of steps: register with the municipality of residence and apply for the permit within 14 days of arrival and before taking up work.",
      "jurisdictions": ["CH"],
      "required_context": ["population", "purpose"],
      "score": 17.0,
      "matched_on": ["aliases", "questions", "description"]
    },
    {
      "concept_id": "residence.zh.city-zurich-registration",
      "topic_id": "residence",
      "label": "City of Zurich: registering a move-in from abroad",
      "description": "Municipal procedure of the City of Zurich: 14-day notification duty, registration only from the actual move-in date, appointment required when arriving from abroad.",
      "jurisdictions": ["CH-ZH-261"],
      "required_context": [],
      "score": 11.0,
      "matched_on": ["label", "aliases", "questions", "description"]
    }
  ],
  "limitations": ["..."]
}
```

## 5. `resolve`

Return the published facts, citations and guidance for up to five concepts
in one call, for one jurisdiction, one date and one context.

### Request

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `concept_ids` | list of string, 1 to 5, unique | yes | From `get_coverage` or `search` |
| `jurisdiction` | Jurisdiction | no, default the country the release serves | Where the user lives, in names or codes, at the most specific level known |
| `as_of` | date | no, default today | Applicability date |
| `context` | map of string to string | no, default empty | Values for the concepts' context fields |
| `reviewed_only` | boolean | no, default `false` | Serve only facts a person has confirmed; a concept whose facts are all unreviewed then resolves `OUT_OF_COVERAGE` with a `review_status_not_met` gap |

### Result: ResolveResult

| Field | Type | Meaning |
| --- | --- | --- |
| `release_id` | string | |
| `status` | Status | Overall status, derived from the per-concept statuses: `NEEDS_CONTEXT` if any concept needs context, else `STALE` if any is stale, else `SUPPORTED` if any is supported, else `OUT_OF_COVERAGE` |
| `as_of` | date | The date used |
| `executed_scope` | ExecutedScope | The jurisdiction as understood by the server: the codes, the register's names and the parts it could not place (section 2) |
| `freshness` | FreshnessPolicy | |
| `results` | list of ConceptResolution | One per requested concept, in request order |
| `required_user_facts` | list of RequiredUserFact | Facts the release cannot know (for example the arrival date); present only when a concept publishes them |
| `decision_rule` | DecisionRule or absent | Published procedure for combining facts and user facts; the caller executes it |
| `guidance_for_caller` | string or absent | How to proceed, by status: on `NEEDS_CONTEXT` derive the missing fields and call again; on `SUPPORTED` answer now from the statements only, cite only the returned URLs and add nothing they do not contain; on `STALE` the same, presenting the facts as published on the snapshot date; on `OUT_OF_COVERAGE` say the request is not covered, and after `review_status_not_met` do not resolve again without `reviewed_only` unless the user asks for unreviewed statements. When facts are returned it ends with what to tell the user: how many statements no person has reviewed, and that English statements summarise pages in another language and are not official translations. When a concept carries a `more_specific_jurisdiction_not_published` gap it adds one sentence: where the question has a cantonal or municipal part, tell the user that it is not published for their place, and carry over no rule, office, fee or deadline published for another canton or municipality. With every status, when `executed_scope.not_recognised` names a part, it adds which part the place register does not hold, which place the result is for, and that the municipality's official name (not a district, a quarter or a postcode) or the canton resolves the narrower place |
| `limitations` | list of string | |

ConceptResolution:

| Field | Type | Meaning |
| --- | --- | --- |
| `concept_id` | string | |
| `status` | Status | Per-concept status |
| `answering_jurisdiction` | string or absent | Which published level answered, with the place's name from the register: `CH (federal)` when a canton was asked and the federal concept applied, `CH-ZH (canton of Zürich)`, `CH-ZH-261 (municipality of Zürich)`; the level alone (`CH-ZH (canton)`) on a release without a register |
| `review_status`, `reviewed_on`, `reviewed_by` | as on Fact, or absent | Stated once here when every served fact of the concept has the same review fields; absent when they differ, and then each fact carries its own |
| `basis` | string or absent | Stated once here when every served fact of the concept rests on the same basis; absent when they differ, and then each fact carries its own |
| `facts` | list of Fact | Empty unless `SUPPORTED` or `STALE` |
| `citations` | list of Citation | One per cited page: the page whose excerpts carry the strongest basis first (the law before a FAQ, a FAQ before a portal summary), then in the order the facts first cite it |
| `missing_context` | list of MissingContext | `field`, `options`, `hint`; non-empty only on `NEEDS_CONTEXT` |
| `gaps` | list of CoverageGap | Named reasons and published values; see below |
| `not_served` | list of string, absent when empty | What users commonly ask about the concept that the release does not publish (for example a fee, a document list or live availability); the caller says so instead of filling it in |

Fact:

| Field | Type | Meaning |
| --- | --- | --- |
| `fact_id` | string | Stable within the release |
| `statement` | string | Short English paraphrase; the excerpt is authoritative |
| `jurisdiction` | string | The jurisdiction the fact is published for |
| `condition` | map or absent | Context values under which the fact applies |
| `valid_from`, `valid_through` | date or absent | Absent means unbounded, unless the source states a date |
| `evidence_ids` | list of string | The excerpts the statement rests on |
| `review_status` | ReviewStatus or absent | Whether a person confirmed this statement against its excerpt. Absent when the concept states it for all its facts |
| `reviewed_on` | date or absent | Date of the human review; absent when not human-reviewed |
| `reviewed_by` | string or absent | Person who confirmed the statement; absent when not human-reviewed |
| `basis` | string or absent | What the cited excerpt is, stated independently of the page that carries it: `Federal act: AIG, SR 142.20, Art. 12`, `Federal ordinance: VZV, SR 741.51, Art. 42 Abs. 1`, `International agreement: FZA, SR 0.142.112.681, Annex I, Art. 6 Abs. 1`, `Cantonal directive: Zürcher Steuerbuch 87.3, Rz 13 Abs. 1`, `Federal authority guidance` (a FAQ or procedure page; `, referring to BüG, SR 141.0, Art. 9` when the page names the norm), `Cantonal authority guidance`, `Municipal authority guidance`, `Federal authority directory`, `Portal summary of federal rules`. Absent when the concept states one basis for all its facts |

ReviewStatus is one of `human-reviewed`, `assistant-authored-unreviewed`,
`automatically-derived-unreviewed` and `model-candidate-automated-review`.
Only `human-reviewed` means a named person checked the statement against the
cited excerpt; the release format requires `reviewed_by` for it. The caller
decides what to do with the distinction: name it to the user when it matters,
or send `reviewed_only` for a question where an unreviewed statement is not
good enough. The counts across the whole release are in every result's
`limitations`, so a caller sees both the per-fact value and the aggregate.
Every served fact has a review status, on the fact or on its concept; the
shared form exists because the fields repeated on every fact of a reviewed
release, about 90 bytes each.

Citation: `evidence_ids` (the evidence IDs of the concept's facts that rest
on this page), `source_title`, `publisher`, `level` (the publisher's level of
the state: `federal`, `cantonal`, `municipal`), `jurisdiction` (whom the
publisher speaks for: `CH`, `CH-ZH`, `CH-ZH-261`), `url`, `language`,
`accessed_on`. A page is listed once per concept however many facts cite it;
two citations differ in at least one of the page fields, so two fetches
of one URL on different access dates stay apart. The publisher is the
institution that published the page, not the origin of the rule: an article
of the AIG quoted on a Canton of Zurich page is cited with the canton as its
cantonal publisher and served with the basis `Federal act: AIG, ...`.

When a concept's served facts mix a portal summary with the law or an
authority's own page, `guidance_for_caller` adds one sentence: where the
summary and the cited law or authority page differ, the law's excerpt is the
more exact source.

CoverageGap:

| `dimension` | When | `published_values` holds |
| --- | --- | --- |
| `jurisdiction_not_covered` | The concept's jurisdiction does not contain the request | The jurisdiction the concept is published for |
| `more_specific_jurisdiction_available` | The request was answered, and a narrower published concept exists below it | The narrower jurisdiction |
| `more_specific_jurisdiction_not_published` | The request was answered from a broader level, nothing is published below the requested place, and the concept's topic publishes a narrower level for another place only: the federal facts for Bern in a topic that also holds Canton of Zurich and City of Zurich facts, or the federal and cantonal facts for Winterthur in a topic that holds City of Zurich facts. The message names the deepest level that applies to the request | The other places the topic serves more deeply (`CH-ZH`, `CH-ZH-261`); their facts do not apply to the request |
| `date_outside_coverage` | `as_of` is before `valid_from` or after `valid_through` | The published validity window |
| `concept_not_published` | Unknown `concept_id` | The published concept IDs |
| `context_not_covered` | A context value has no published facts, or a context field is unknown | The context values the concept is published for |
| `review_status_not_met` | `reviewed_only` was set and no fact of the concept is human-reviewed | The review statuses the concept's facts actually carry |

`published_values` are always codes. The `message` of a jurisdiction gap
names up to six places with their level and the register's name (`published
for CH-ZH-261 (municipality of Zürich), not for CH-ZH-230 (municipality of
Winterthur)`), so a caller can tell the user whose rule it is without
knowing a BFS number; a longer list (the 26 cantons) stays bare codes.

RequiredUserFact: `name`, `status` (`NOT_PROVIDED_BY_SERVICE`, or the value
the caller already supplied), `instruction`. DecisionRule: `description`,
`steps` (ordered strings).

### Example: missing context

Request:

```json
{"concept_ids": ["residence.registration-after-arrival-eu-efta"], "jurisdiction": {"canton_code": "CH-ZH"}}
```

Result (abridged):

```json
{
  "status": "NEEDS_CONTEXT",
  "as_of": "2026-09-12",
  "executed_scope": {"country_code": "CH", "canton_code": "CH-ZH"},
  "results": [
    {
      "concept_id": "residence.registration-after-arrival-eu-efta",
      "status": "NEEDS_CONTEXT",
      "missing_context": [
        {"field": "population", "options": ["EU_EFTA", "THIRD_COUNTRY"], "hint": "EU_EFTA for citizens of an EU or EFTA state (for example Czech, German, Norwegian), otherwise THIRD_COUNTRY."},
        {"field": "purpose", "options": ["EMPLOYMENT", "SELF_EMPLOYMENT", "NO_EMPLOYMENT", "FAMILY_REUNIFICATION"], "hint": "Main purpose of the stay in Switzerland."}
      ]
    }
  ],
  "guidance_for_caller": "Derive the missing fields from what the user already said (for example a Czech citizen is population EU_EFTA and 'starting my work' is purpose EMPLOYMENT) and call resolve again. Ask the user only for fields that cannot be derived."
}
```

### Example: supported, with a narrower jurisdiction available

Request:

```json
{"concept_ids": ["residence.zh.registration-eu-efta"], "jurisdiction": {"canton_code": "CH-ZH"}, "as_of": "2026-09-12", "context": {"population": "EU_EFTA"}}
```

Result (abridged):

```json
{
  "status": "SUPPORTED",
  "results": [
    {
      "concept_id": "residence.zh.registration-eu-efta",
      "status": "SUPPORTED",
      "answering_jurisdiction": "CH-ZH (Canton of Zurich)",
      "review_status": "human-reviewed",
      "reviewed_on": "2026-09-24",
      "reviewed_by": "Anna Meier",
      "facts": [
        {
          "fact_id": "f-zh-canton-14-days",
          "statement": "The canton of Zurich instructs EU/EFTA nationals who will work longer than 90 days to register in person with their municipality within 14 days.",
          "jurisdiction": "CH-ZH",
          "condition": {"population": "EU_EFTA"},
          "evidence_ids": ["e-zh-eu-efta"]
        }
      ],
      "citations": [
        {"evidence_ids": ["e-zh-eu-efta"], "source_title": "Kanton Zuerich - Aufenthalt fuer EU/EFTA-Staatsangehoerige", "publisher": "Kanton Zuerich, Migrationsamt", "url": "https://www.zh.ch/de/migration-integration/aufenthalt/aufenthalt-fuer-euefta-staatsangehoerige.html", "language": "de", "accessed_on": "2026-09-11"}
      ],
      "gaps": [
        {"dimension": "more_specific_jurisdiction_available", "message": "A municipal procedure is published for the City of Zurich (concept residence.zh.city-zurich-registration); add municipality_id CH-ZH-261 to resolve it.", "published_values": ["CH-ZH-261"]}
      ]
    }
  ]
}
```

### Example: jurisdiction not covered

Request with `"jurisdiction": {"canton_code": "CH-BE"}` for the same
concept. Result (abridged):

```json
{
  "status": "OUT_OF_COVERAGE",
  "executed_scope": {"country_code": "CH", "canton_code": "CH-BE"},
  "results": [
    {
      "concept_id": "residence.zh.registration-eu-efta",
      "status": "OUT_OF_COVERAGE",
      "gaps": [
        {"dimension": "jurisdiction_not_covered", "message": "This concept is published for CH-ZH, not CH-BE; no cantonal procedure is published for that canton. Federal concepts still apply.", "published_values": ["CH-ZH"]}
      ]
    }
  ]
}
```

A federal concept in the same call would still return `SUPPORTED` with
`answering_jurisdiction` `CH (federal)`, and with the caveat that the topic's
narrower levels are another place's:

```json
{"dimension": "more_specific_jurisdiction_not_published", "message": "For CH-BE this topic publishes nothing narrower than CH (federal); its narrower facts are published for CH-ZH, CH-ZH-261 only and do not apply to CH-BE.", "published_values": ["CH-ZH", "CH-ZH-261"]}
```

The same gap is served inside the canton: a request for `CH-ZH-230`
(Winterthur) is answered from the federal and the cantonal facts, each with
a gap that names `CH-ZH (canton of Zürich)` as the deepest level and `CH-ZH-261` as the
place served more deeply. A request for `CH-ZH` alone gets
`more_specific_jurisdiction_available` instead, since the caller may still
add the municipality, and a topic that serves every place equally deeply
(the cantonal migration offices) gets neither.

### Example: a jurisdiction in names

Captured from the real server on release `mvp-zurich-2026-09-18-v10`, which
carries the place register; facts and citations are left out. The city alone
is enough:

```json
{"concept_ids": ["zh-eu-registration", "city-zurich-arrival"], "jurisdiction": {"city": "Winterthur"}, "as_of": "2026-09-18", "context": {"population": "eu_efta", "arrival_origin": "abroad"}}
```

```json
{
  "status": "SUPPORTED",
  "executed_scope": {"country_code": "CH", "canton_code": "CH-ZH", "municipality_id": "CH-ZH-230", "country": "Switzerland", "canton": "Zürich", "city": "Winterthur"},
  "results": [
    {"concept_id": "zh-eu-registration", "status": "SUPPORTED", "answering_jurisdiction": "CH-ZH (canton of Zürich)",
     "gaps": [{"dimension": "more_specific_jurisdiction_not_published", "message": "For CH-ZH-230 (municipality of Winterthur) this topic publishes nothing narrower than CH-ZH (canton of Zürich); its narrower facts are published for CH-ZH-261 (municipality of Zürich) only and do not apply to CH-ZH-230.", "published_values": ["CH-ZH-261"]}]},
    {"concept_id": "city-zurich-arrival", "status": "OUT_OF_COVERAGE",
     "gaps": [{"dimension": "jurisdiction_not_covered", "message": "This concept is published for CH-ZH-261 (municipality of Zürich), not for CH-ZH-230 (municipality of Winterthur); no procedure is published for that jurisdiction. Federal concepts still apply.", "published_values": ["CH-ZH-261"]}]}
  ]
}
```

A quarter is not a municipality. `{"canton": "Zurich", "city": "Oerlikon"}`
runs for the canton and says so:

```json
{
  "status": "OUT_OF_COVERAGE",
  "executed_scope": {"country_code": "CH", "canton_code": "CH-ZH", "country": "Switzerland", "canton": "Zürich", "not_recognised": {"city": "Oerlikon"}},
  "guidance_for_caller": "... The place register does not hold the city 'Oerlikon', so this result is for CH-ZH (canton of Zürich). If the narrower place matters, resolve again with the official name of the municipality (the political commune, not a district, a quarter or a postcode) or with the canton; otherwise tell the user which place the answer is for."
}
```

A name several municipalities share is an error, `{"city": "Buchs"}`:

```json
{"error": {"code": "INVALID_ARGUMENT", "issues": [{"path": "jurisdiction.city", "message": "'Buchs' fits several municipalities: Buchs (AG) (CH-AG-4003) in Aargau (CH-AG), Buchs (SG) (CH-SG-3271) in St. Gallen (CH-SG), Buchs (ZH) (CH-ZH-83) in Zürich (CH-ZH); add the canton or give the full name."}]}}
```

## 6. `get_evidence`

Read the full original excerpts behind citations.

### Request

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `evidence_ids` | list of string, 1 to 5 | yes | Exactly as returned in `citations` or `evidence_ids` |
| `release_id` | string | no | Omit for the active release |

### Result: GetEvidenceResult

`release_id`, `evidence` (list, in request order), `limitations`. Evidence:
`evidence_id`, `source_title`, `publisher`, `level`, `jurisdiction`, `basis`
(as on Citation and Fact; the basis of this one excerpt), `url`, `language`,
`accessed_on`, `original_excerpt`. The excerpt is the exact saved text; the real release
also records its offsets and content hash in the release file, so a
mismatch fails validation at startup.

### Example

```json
{
  "release_id": "mock-residence-registration-2026-09-11",
  "evidence": [
    {
      "evidence_id": "e-sem-faq-en",
      "source_title": "SEM - FAQ: EU/EFTA citizens in Switzerland (English)",
      "publisher": "State Secretariat for Migration SEM",
      "url": "https://www.sem.admin.ch/sem/en/home/themen/fza_schweiz-eu-efta/eu-efta_buerger_schweiz/faq.html",
      "language": "en",
      "accessed_on": "2026-09-11",
      "original_excerpt": "Within 14 days of their arrival and before actually taking up work, nationals of EU/EFTA states have to register with the local authorities of the commune in which they are residing and apply for a residence permit. A valid ID or passport and a written confirmation of employment (e.g. the contract of employment containing details of the duration of employment and the number of working hours) have to be presented."
    }
  ],
  "limitations": ["..."]
}
```

## 7. Errors

A `ToolError` is returned with `isError: true` for a malformed request, an
unknown tool, an unknown identifier that is not a coverage question
(evidence ID, parent ID, release ID) or an operational failure.

| Field | Type | Meaning |
| --- | --- | --- |
| `error.code` | `INVALID_ARGUMENT`, `RELEASE_UNAVAILABLE`, `OPERATIONAL_ERROR` | |
| `error.issues` | list of `{path, message}` | `path` is the dotted field path, for example `jurisdiction.city` |

Example for `{"evidence_ids": []}`:

```json
{"error": {"code": "INVALID_ARGUMENT", "issues": [{"path": "evidence_ids", "message": "List should have at least 1 item after validation, not 0"}]}}
```

An unknown `concept_id` in `resolve` is not an error: it is a
`concept_not_published` gap, so that one bad ID in a list of five does not
fail the whole call.

## 8. The expected call pattern

For the standing Czech-citizen question the intended sequence is two calls:

1. `search` with the whole question and, since the question names it, the
   user's place as `jurisdiction` (section 4.4): the hits are the federal registration
   deadline, the Canton of Zurich procedure and the City of Zurich move-in
   procedure when retrieved, each with its `context_schema`. Candidate counts
   and truncation describe retrieval; the caller selects the concepts needed
   for the question.
2. `resolve` with those concept IDs, the place the user named (`city`
   `Zurich`, which the server turns into `CH-ZH` and `CH-ZH-261`), today's
   date, and `population` `eu_efta` derived from "Czech citizen" with the
   schema.

The result carries the facts, the citations, the two required user facts
(arrival date, first working day) and the decision rule. The caller asks the
user for the two dates and applies the rule. `get_coverage` is for the case
where the caller is unsure the question is in scope at all, and
`get_evidence` only when the user wants a verbatim quote; both are otherwise
unnecessary, and the tool descriptions say so. A question in a language the
release does not index is searched with its key terms in the preferred query
language (section 4.2). The recorded caller runs are
in `.local/experiments/`.

## 9. Changes after the freeze

Additive changes (a new optional request field, a new result field, a new
gap dimension) keep the schema version. A change that removes or renames a
field, or makes an optional field required, bumps the version and needs the
lead's decision. The `--check` command above runs in the test suite so that
the committed bundle and the models cannot drift apart.
