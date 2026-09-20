# Knowledge release and curation file

**Last update:** 19 September 2026

**Status:** implemented in `packages/core` (release
models, basis labels and validator) and `packages/build` (curation file and
release build); the MVP release `releases/mvp-zurich/release.json` is built
from the migrated predecessor curation (section 6) and, since 16 September
2026, names the institution of every page and the basis of every excerpt
(section 2, "Institutions and basis")<br>
**Schema versions:** `swiss-tip-release/v1`, `swiss-tip-curation/v1`<br>
**Relation to the tool contracts:** the server maps release records onto the
tool results of [tool-contracts.md](tool-contracts.md); the release carries
more (hashes, provenance, block IDs) than any tool returns.

## 1. Position in the pipeline

```text
releases/<pack>/curation.yaml     what the expert (or a migration) writes
<run>/text/                        text dataset of the pack's run (extraction package);
                                  <run> is releases/<pack>/ when committed, else .local/<pack>/
        |  swisstip-build-release
        v
releases/<pack>/release.json      the validated, hashed bundle the server serves
releases/<pack>/build-report.json every citation outcome, every dropped fact
```

The build is the only step that turns curated text into served content. It
resolves every citation against the text dataset, pins it with hashes, runs
the validator and refuses to write a release that does not validate. The
server runs the same validator once at startup and fails closed.

## 2. Release file

One JSON object with eight parts. Identifiers are `[A-Za-z0-9][A-Za-z0-9._-]*`.
The release is self-contained: every excerpt is stored in the bundle with
the hashes of the excerpt and of the page text it was cut from, so the
server and a citation check need no fetched page. The text records of
section 1 are working material of the build and the review, not part of
the release.

| Part | Content |
| --- | --- |
| `manifest` | `release_id`, `pack`, `title`, `created_at`, `scope_statement`, `out_of_scope`, `out_of_scope_response`, `jurisdictions` and `languages` actually present, `freshness` (`snapshot_date` = latest access date of the cited pages, `max_age_days`, `stale_from`), `provenance_kinds` and `review_statuses` (counts over the facts), `institution_levels` (documents per level of their institution), `basis_kinds` (facts per kind of their basis), `ranking_policy` (the weights the server ranks with), `limitations`, `content_sha256` over the canonical JSON of the other parts |
| `institutions` | Who published the cited pages: `institution_id`, `name` (the served publisher), optional `native_name`, `level` (`federal`, `cantonal`, `municipal`), `body` (`administration`, `law_collection`, `portal`, `public_law_body`), `jurisdiction` |
| `documents` | One entry per cited text record: `document_id`, `source_url`, `document_url`, `version_uri`, `title`, `publisher`, `institution_id`, `language`, `accessed_on`, `raw_sha256` (the page as fetched), `content_sha256` (normalized text) |
| `topics` | `topic_id`, `title`, `description` |
| `concepts` | `concept_id`, `topic_id`, `label`, `description`, `aliases`, `questions`, `jurisdictions` (sorted jurisdictions of its facts), `required_context`, `context_schema` (field name to `type`, `enum`, `description`), `fact_ids`, optional `required_user_facts` and `decision_rule`, `notes`, and `not_served` (left out when empty, so older releases keep their content hash) |
| `facts` | `fact_id`, `concept_id`, `statement`, `language`, `jurisdiction`, optional `condition` (field to value), `valid_from`, `valid_through`, `evidence_ids`, `provenance` (with the fact's `basis`, see below) |
| `evidence` | `evidence_id`, `document_id`, `source_title`, `publisher`, `institution_id`, `url`, `language`, `accessed_on`, `start_offset`, `end_offset` (code points in the record's `content_text`), `original_excerpt`, `excerpt_sha256`, `block_ids`, `content_sha256`, `raw_sha256`, `basis` |
| `place_register` | The places a caller can name instead of a jurisdiction code: `title`, `publisher`, `url`, `accessed_on` and `raw_sha256` of the official register it was read from, `generic_words`, and `places`, each with `code`, `name` (the official name) and optional `aliases`; see below |

A fact has one jurisdiction; a concept may therefore span several (the
cantonal contact concept has one fact per canton). The server filters facts
by containment: `CH` answers for every canton and municipality, `CH-ZH` for
its municipalities, `CH-ZH-261` only for the City of Zurich.

### Institutions and basis

Two things are stated separately for every excerpt, and only one of them
weighs in ranking. The institution says who published the page: the
registry entry the document names, with its level of the state and the
jurisdiction it speaks for. The basis says what the excerpt is, independent
of the page that carries it, so an article of the AIG quoted on a cantonal
page is federal law and the canton's own answer next to it is cantonal
guidance. A basis has a `level` (of the body that enacted the norm or wrote
the text), a `kind`, a `norm` (the identity of the norm; required for `act`,
`ordinance`, `treaty` and `directive`), an optional `refers_to` (a norm the
excerpt names without reproducing it) and the `label` the server serves,
composed by `swisstip.core.basis.basis_label`:

| `kind` | Label | Example |
| --- | --- | --- |
| `act`, `ordinance` | `{Level} act: {norm}`, `{Level} ordinance: {norm}` | Federal act: AIG, SR 142.20, Art. 12 |
| `treaty` | `International agreement: {norm}` | International agreement: FZA, SR 0.142.112.681, Annex I, Art. 6 |
| `directive` | `{Level} directive: {norm}` | Cantonal directive: Zürcher Steuerbuch 87.3, Rz 13 Abs. 1 |
| `guidance` | `{Level} authority guidance` | Federal authority guidance, referring to BüG, SR 141.0, Art. 9 |
| `directory` | `{Level} authority directory` | Federal authority directory |
| `summary` | `Portal summary of {level} rules` | Portal summary of federal rules |

A fact's `provenance.basis` is the strongest basis among its excerpts by the
manifest's `ranking_policy` (an act, ordinance or treaty at 1.0, a directive
at 0.95, guidance and a directory at 0.9, a summary at 0.75; then the
provenance kind and review status of the statement), the first of equals.
The policy is never served; `search` multiplies a concept's score by
`floor + (1 - floor) * authority`, with the authority the highest fact
weight of the concept, so a concept resting on the law and a reviewed
statement outranks one resting on a summary or an unreviewed candidate when
a query does not separate them. The publisher's level never weighs. The
design and its measurement are in
[institutions-and-provenance-weights.md](institutions-and-provenance-weights.md).

All of these fields are optional and left out of the dump when absent, so a
release built before 16 September 2026 loads, validates and keeps its
content digest; a release with a registry must name an institution and a
basis for every document and excerpt.

### Place register

A caller says where the user lives in names (`{"canton": "Zurich", "city":
"Wallisellen"}`), and the server turns them into the codes the facts carry
(`swisstip.core.places`, rules in
[tool-contracts.md](tool-contracts.md), section 2). The names are data of
the release, not of the server: the register decides which code a named
place becomes and so which facts a request reaches, so it is part of
`content_sha256`, the acceptance suite runs against it, and an upgraded
server cannot change what an attested release answers. A pack for another
country brings its own places.

| Field | Content |
| --- | --- |
| `title`, `publisher`, `url`, `accessed_on`, `raw_sha256` | Where and when the register was read, and the hash of the response, like every other source |
| `generic_words` | Words a caller may put around a name without changing the place: `canton of`, `Kanton`, `Stadt`, `ville de` |
| `places` | The country, its cantons and its municipalities: `code` (`CH`, `CH-ZH`, `CH-ZH-261`; the level follows from the code), `name` (the official name as the register spells it, which the server echoes: `Zürich`, `Bern / Berne`, `Buchs (ZH)`), `aliases` (other spellings accepted on input and never served: `Geneva`, `Genf`, `Ginevra`) |

The build embeds two files the curation file names (section 3).
`config/places/ch-register.json` is written by `swisstip-places` from the
snapshot endpoint of the Federal Statistical Office's register of
municipalities (Amtliches Gemeindeverzeichnis): the 26 cantons and every
municipality valid on the day, with the canton's abbreviation and the BFS
number as the code; districts are dropped, since no fact is published for
one. `config/places/ch-aliases.json` is written by hand: the country's own
entry, which the register does not list, the other-language names of
cantons and larger cities, and the generic words. The register holds
official local names only, so the English names are authored; they
normalise input and are never served, which keeps them apart from the
search terms, which are copied from the cited excerpts.

Every pack of a country embeds the whole register, not its own places
only: a user in Lausanne still has to be placed in Vaud to get the Vaud
facts, and a Wallisellen pack has to place a user from Winterthur to tell
them that its facts are not theirs. Municipalities merge, mostly on
1 January, and their numbers retire: the register is dated data, fetched
again and rebuilt like any other source, and an alias for a code the new
register no longer lists fails the build. The field is optional and left
out of the dump when absent, so a release built before 18 September 2026
loads, validates, keeps its content digest and accepts codes only.

### Provenance and review status

Every fact says where its statement came from and who looked at it:

| `provenance.kind` | Meaning |
| --- | --- |
| `curated-statement` | A statement written by a person or an assistant reading the cited page |
| `source-section` | The excerpt itself serves as the statement; nothing was written |
| `model-candidate` | A statement proposed by a model from the cited page |

| `provenance.review_status` | Meaning |
| --- | --- |
| `human-reviewed` | A named person confirmed the statement against its excerpt (`reviewed_on`, `reviewed_by`) |
| `assistant-authored-unreviewed` | Written by an assistant, not confirmed by a person |
| `automatically-derived-unreviewed` | Produced by a deterministic generator, never read |
| `model-candidate-automated-review` | A model proposal that passed an automated review only |

`provenance.reviewed_by` names the person who confirmed the fact; it is
optional and validated as non-empty when `review_status` is
`human-reviewed`. The admin console's review queue is what sets it, see
[admin-console.md](admin-console.md).

`provenance.author`, `source` and `notes` carry the detail. The manifest
counts both fields so the root `get_coverage` page can state them, and the
specification's "review mark" is this status, not a boolean.

### Validator

`swisstip-validate-release <release.json> [--text DIR] [--run DIR]` checks,
in this order: the manifest content hash; `stale_from`; unique and
well-formed identifiers; every reference in both directions (concept to
facts, fact to concept and evidence, evidence to document); concept
jurisdictions equal to the facts' jurisdictions; conditions within the
concept's context schema and enum; offsets against excerpt length; excerpt
hashes; declared jurisdictions and languages; uncited documents or evidence;
access dates against the snapshot date; provenance counts; the institution
registry (unique, level and jurisdiction consistent, every entry cited),
every document's and excerpt's institution, every basis (norm present for a
law or directive, label equal to its fields), every fact's basis being the
strongest of its excerpts, every fact's jurisdiction lying within the
jurisdiction of every institution it cites, no federal fact resting on a
cantonal or municipal basis, the manifest's institution and basis
counts, and the place register (unique well-formed codes, a name on every
place, every municipality under a listed canton and every canton under a
listed country, and a place for every jurisdiction the manifest declares,
so that the server can name whatever it answers for). With `--text`, it
reads every cited record and checks content hash, raw hash, the excerpt at
its offsets and the covering block IDs; with `--run`, it re-hashes the saved
responses. The build runs the `--text` level before writing.

## 3. Curation file

`releases/<pack>/curation.yaml`, validated by the Pydantic models in
`swisstip.build.curation`:

```yaml
schema_version: swiss-tip-curation/v1
pack: mvp-zurich
title: ...
scope_statement: ...
out_of_scope: [...]
out_of_scope_response: ...
limitations: [...]
freshness_max_age_days: 60
publishers:                       # publisher name by host; the fallback when no institution rule matches
  www.sem.admin.ch: State Secretariat for Migration SEM
institutions:                     # who publishes the cited pages; a page matching no rule falls back to its catalogue entry
  - institution_id: zh-steueramt
    name: Canton of Zurich, Cantonal Tax Office
    native_name: Kantonales Steueramt Zürich
    level: cantonal
    body: administration
    jurisdiction: CH-ZH
    urls: [www.zh.ch/de/steuern-finanzen/]   # host, or host plus path prefix; the longest rule wins
page_basis:                       # default basis of every excerpt of a page; a citation's own basis wins
  www.fedlex.admin.ch/eli/cc/2007/758/: {level: federal, kind: act, norm: "AIG, SR 142.20"}
  www.ch.ch: {level: federal, kind: summary}
page_languages:                   # language of a page whose record declares none; the record's own language wins
  www.wallisellen.ch: de
ranking_policy:                   # optional; the defaults of swisstip.core.basis when absent
  floor: 0.7
  aggregate: max
  basis_kind: {act: 1.0, ordinance: 1.0, treaty: 1.0, directive: 0.95, guidance: 0.9, directory: 0.9, summary: 0.75}
  provenance_kind: {curated-statement: 1.0, source-section: 0.9, model-candidate: 0.8}
  review_status: {human-reviewed: 1.0, model-candidate-automated-review: 0.8, assistant-authored-unreviewed: 0.7, automatically-derived-unreviewed: 0.6}
place_register: ../../config/places/ch-register.json   # optional, relative to this file; embedded as the release's place register
place_aliases: ../../config/places/ch-aliases.json     # optional; the country's entry, other-language names, generic words
question_languages: [en, de]      # optional; every concept needs a sample question in each (English and German only)
context_fields:                   # shared definitions referenced by conditions
  population: {enum: [eu_efta, third_country, uk_new, uk_acquired], description: ...}
topics:
  - {topic_id: residence, title: ..., description: ...}
concepts:
  - concept_id: zh-eu-registration
    topic_id: residence
    label: ...
    description: ...
    aliases: [...]                 # authored search terms, any language
    questions: [...]
    source_terms: [...]            # the publisher's own words, verbatim from the cited excerpts
    required_context: [population]
    notes: [...]
    not_served: [...]              # what users ask that the cited pages do not answer; served with resolve
    facts:
      - fact_id: zh-eu-registration-1
        statement: ...
        jurisdiction: CH-ZH
        condition: {population: eu_efta}
        valid_from: null
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: ..., source: ..., notes: [...]}
        evidence:
          - document_id: doc-66553fde673cc7f06804
            first_block: 63
            last_block: 66
            basis: {kind: act, norm: "AIG, SR 142.20, Art. 12"}   # what this excerpt is, when the page default does not say
            anchor: {...}          # written by the build; see below
```

`source_terms` are words and phrases copied from the concept's cited
excerpts in the language of the source ("Familiennachzug", "Anmeldung bei
der Wohngemeinde", "Nicht-EU/EFTA-Angehörige"). The build checks that every
term occurs in the concept's resolved evidence, ignoring case and line
breaks, and fails when one does not; the verified terms are merged into the
released `aliases`, after the authored ones, so `search` matches them and a
German question reaches an English-labelled concept without translation.
`python -m swisstip.build.source_terms releases/<pack>/release.json` lists
candidate terms per concept from the excerpts for the curator to choose
from. Every concept of the current release carries source terms.

`questions` are sample questions phrased the way users ask, and `search`
weighs them as an anchor field next to the label and the aliases. A pack
that lists its query languages in `question_languages` gets a build that
refuses a concept without a sample question in each of them, so a question
in that language does not have to reach the concept through its label or
the publisher's terms alone. The build tells a question's language from its
function words (`swisstip.build.questions`, English and German only; words
both languages use count for neither). `mvp-zurich` lists `[en, de]`;
`mvp-wallisellen`, whose sample questions are English only, lists none.

The expert writes `document_id`, `first_block` and `last_block` from the
reading view of the text dataset. The build adds an `anchor` to every
citation: source URL, document ID, content and raw hash, block IDs and
hashes, heading path, offsets and the excerpt as last resolved. It is what
makes a refresh survivable (section 4).

## 4. Build and relocation

`swisstip-build-release --curation FILE --text DIR --release-id ID --output release.json [--update-curation]`

For every citation:

1. If the cited record is listed in the dataset index and not superseded,
   cut the block range; if the citation has an anchor, the block hashes must
   match. Outcome `same-snapshot`.
2. Otherwise take the anchor and the newest eligible, preferred record of
   the same source URL and call `swisstip.extraction.anchors.relocate`.
   Outcomes `same-text` and `moved` are accepted, with their warnings
   (`context-changed`, `neighbourhood-changed`, `blocks-restructured`)
   recorded in the build report; `ambiguous` and `changed` drop the fact.
3. A citation without an anchor whose record is gone drops the fact.

A concept whose facts were all dropped is dropped. Dropped facts are listed
in `build-report.json` with the reason and, for changed text, the closest
new blocks; the command exits nonzero when anything was dropped, but still
writes the release, because a release with fewer facts is better than none.
With `--update-curation` the relocated document IDs, block numbers and fresh
anchors are written back into the curation file. Nothing is guessed and no
statement is ever edited by the build.

The institution of a page is the registry entry whose rule (a host, or a
host plus path prefix) is the longest one the record's source URL falls
under; a page matching no rule takes its catalogue entry from the record's
`source_registry` (the entry with the longest `allowed_path_prefixes` match:
`canonical_authority`, `authority_level`, `jurisdiction` and `municipality`
become an institution named `catalogue-<source_id>`, with the `publishers`
name for its host when there is one); a page matching neither stops the
build when the pack declares a registry, and otherwise keeps a publisher
name only, from the `publishers` map or the catalogue, as before. The
served `publisher` is the institution's name. The basis of an excerpt is the
citation's own `basis`, else the `page_basis` rule with the longest prefix,
else guidance at the institution's level; a page without an institution
gets no basis. The build report names, per document, the institution and
the rule that attributed it, and per citation the basis and its rule, so a
default the curator has not confirmed stays distinguishable from an
authored one. Language is the record's declared language, else its hint,
else the curation file's `page_languages` rule with the longest prefix (for a
PDF that declares no `/Lang` and has no catalogue entry), else `und`.

The place files the curation file names are merged into the release's
place register: the country's entry from the alias file first, then the
register's cantons and municipalities, each with its aliases; an alias
that only repeats the official name in another case or without diacritics
is dropped, and an alias for a code the register does not list stops the
build. The build report states the register's URL, access date and hash
and the numbers of places and aliases. `build_release` takes the loaded
register as an argument (`swisstip.build.places.place_register_for`), so
the command, the knowledge builder and the admin console's dry build, which
know where the curation file lies, embed the same one. The knowledge
builder counts the place files among the inputs of its build stage: a
register fetched anew or an edited alias rebuilds the release under the
next ID, like an edited fact.

## 5. Packages

```text
packages/core/src/swisstip/core/
  release.py        models, content_hash, load_release, dump_release
  basis.py          basis labels, the default ranking policy, fact and concept weights
  places.py         PlaceIndex: the parts of a jurisdiction, in names or codes, to the codes of the release
  validation.py     validate_release, assert_valid, contains (jurisdiction containment), CLI
packages/build/src/swisstip/build/
  curation.py       curation models, load_curation, save_curation
  places.py         load_place_register, place_register_for (the place files a curation names)
  release_build.py  TextDataset, resolve_citation, build_release
  build_cli.py      swisstip-build-release
packages/ingestion/src/swisstip/ingestion/
  places.py         swisstip-places: the official register of municipalities to a place file
```

`swisstip-core` depends on Pydantic only. `swisstip-build` depends on core,
the extraction package and PyYAML. The tests of core cover the round trip,
tampering, containment, the basis labels and weights, the institution,
basis and place-register checks of the validator, and the place index
(names, codes, a city supplying its canton, the spelling as written winning
over the folded one, places not recognised, shared names, another country,
a release without a register); those of build cover resolution and
validation, relocation after a refresh, a dropped fact after rewording, a
missing publisher, source terms, attribution through the registry and
through the catalogue, the basis of an excerpt from a citation, a page
rule or the default, and the place files (merged, hashed, refused when they
do not fit). All run on synthetic runs without network
(`./.venv/Scripts/python.exe -m unittest discover -s packages/core/tests`,
likewise `packages/build/tests`).

## 6. The migrated MVP release

KB1 was migrated from the predecessor without human review steps. The
one-time script `.local/scripts/migrate_kb1_curation.py` (outside Git) read
the predecessor's curated selections
(`scripts/corpora/residence_mvp_curated.py`: 35 concepts, 84 facts including
26 cantonal contacts, all authored by an assistant), built an anchor for every
block range from the predecessor's 10 September text records, relocated it
onto the MVP text dataset of this repository and wrote
`releases/mvp-zurich/curation.yaml`; `kb1-migration-report.json` next to it
lists every citation.

| Result | Count |
| --- | ---: |
| Concepts migrated | 35 of 35 |
| Facts migrated | 84 of 84 |
| Citations relocated as `same-text` | 76 |
| Citations relocated as `moved` | 8, all citing the SEM notification-procedure page (`ch-sem-notification-procedure`), relocated by `.local/scripts/add_notification_concepts.py` onto the same block numbers of its 11 September record (`kb1-migration-report.json`, `amendments`) |

Every fact is a `curated-statement`. The predecessor had no concept
descriptions and no aliases beyond the contact concept; the descriptions,
the authored aliases and questions (including everyday German words a user
writes, such as `Trennung`, `Scheidung`, `Sozialhilfe beziehen`, and Zurich
German spellings that the cited pages do not use) and the source terms
(section 3) were added in this repository. The contents of the current
release are listed in [COVERAGE.md](../../COVERAGE.md), its review status in
[LIMITATIONS.md](../../LIMITATIONS.md).

Earlier states of this document: [history](../history/release-format-history.md).
