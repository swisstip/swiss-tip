# Country profiles

**Last update:** 27 September 2026

**Status:** partly implemented. Implemented and tested: step 0 (the baseline capture and `rebuild_swiss.py` in swiss-tip-mvp, with the whole lexical ranking of every search captured in place of recorded query vectors), steps 1 to 3 (the release models and `core/hierarchy.py`, `basis.py` and the declared validation checks, `text.fold` with letters, the `places.py` generalisation) and step 6 (the TERC importer and its CLI; the dry run is in `.local`). Planned: steps 4, 5, 7, 8 and 9, so the server still serves Swiss releases only. Two changes from the text below, made after review: the importer qualifies a commune name shared within a voivodeship, not within a county (by type when the types differ, otherwise by county), and the declared path looks a commune name up in the exact and loose keys together, so a plain name in one voivodeship does not hide a qualified pair of that name in another. Code references (`file:line`) are to the code before the change: swiss-tip `acf44b7`, whose code is unchanged since `9d590b2`, and swiss-tip-mvp `942daa2`.<br>
**Plan:** [poland-implementation-plan.md](../product/poland-implementation-plan.md). This document specifies workstreams 1 to 3 and the served-text part of workstream 5.<br>
**Related:** [release-format.md](release-format.md), [tool-contracts.md](tool-contracts.md), [knowledge-base-pipeline.md](knowledge-base-pipeline.md).<br>
**Schema versions introduced:** `swiss-tip-release/v3` and `swiss-tip-place-aliases/v2`. These stay unchanged: the tool contract `swiss-tip/v4` and its committed schema bundle, `swiss-tip-release/v2` for Swiss builds, `swiss-tip-places/v1`, `swiss-tip-curation/v1`, the acceptance-suite digest and the semantic `qwen3-retrieval/v1`.

A **country profile** is what a release declares about its country:

- its **place hierarchy**: levels, their names, the code format and the wording of places, carried in the place register;
- its **languages**: query languages and evidence languages, carried in the manifest.

A release without a profile is Swiss by definition. It is served exactly as today.

## 1. Purpose and constraints

### 1.1 Goal

Make the core country-neutral so that a Polish pack (`mvp-poland`) can be built, validated and served. The Polish pack has:

- four levels: national `PL`, voivodeship `PL-12`, county `PL-12-61`, commune `PL-12-61-011`. The last segment is the commune number followed by its TERYT type digit.
- a TERYT place register;
- Polish text handling;
- Polish legal-basis labels;
- served texts that name the pack's own country and levels.

### 1.2 Decisions this design implements

These come from the user's decisions of 27 September 2026:

- **Languages.**
  - Evidence is Polish only: every excerpt is cut from a Polish official page.
  - Statements, summaries, aliases and sample questions are English.
  - Polish source terms are copied verbatim from the excerpts. Polish sample questions are allowed but not required.
  - The release declares English and Polish as its query languages.
  - The server tells the calling model to call the tools in English or Polish.
- **Request parts** stay `country`, `region`, `city`. `region` is already an accepted alias of `canton` (`core/contracts.py:83`). The county is derived from the city.
- **Timing.** The software change is prepared before HackYeah; open-source reuse is allowed. The Polish data is built at the event as a proof of concept: the place files, the curation, the review and the attestation. Before the event the TERC importer only dry-runs on the downloaded file.
- **Reviewer.** The user reviews, as the same named reviewer as `mvp-zurich`.
- **Versions.** Commits are done. The package version bump is made late, by the user. No step here bumps a package version.

### 1.3 Hard constraints

1. Every attested Swiss release still validates and answers exactly as before, byte for byte in its tool results. The attested releases are `swiss-tip-mvp/releases/mvp-zurich/release.json` and `mvp-wallisellen/release.json`.
2. The country is data of the release, not code: levels, names, the code format and the place register travel in the release.
3. Package tests read no real pack (fixtures only) and use no network.
4. Schema and contract version changes are deliberate and justified here.

### 1.4 Design rules that follow

- **R1. Two switches, both read from the release.**
  - `place_register.hierarchy` switches places, levels, codes and the place wording.
  - `manifest.query_languages` switches text handling and the language note.

  Every attested Swiss file lacks both and cannot gain them, because `readiness.json` binds the bytes of `release.json`. Everything Swiss is therefore the "absent" side of both switches.
- **R2. Every new model field is dropped from the dump when absent** (`exclude_if=absent`). No new key reaches a Swiss dump, a Swiss `content_sha256`, a Swiss suite digest or a Swiss tool result.
- **R3. Nothing Swiss is rewritten:**
  - `releases/mvp-zurich/*` and `releases/mvp-wallisellen/*`;
  - `config/places/ch-*.json` (the knowledge builder hashes them, `apps/knowledge-builder/.../pipeline.py:181`);
  - `docs/architecture/tool-contracts.schema.json`;
  - `DEFAULT_RANKING_POLICY`, `RELEASE_SCHEMA_VERSION` and `QUERY_PREFIX`.
- **R4. Swiss served strings stay byte-identical.** Every legacy code path is either kept verbatim, or becomes a template whose legacy slot values are today's exact substrings. Tests pin each template's legacy rendering against a pasted copy of today's constant.
- **R5. Place knowledge is data; language processing is code keyed by declared language codes.**
  - The Polish hierarchy, names, nouns and notes travel in the release.
  - Letter folds, stopwords and facet words for `pl` live in code, like the English stopwords do today. They apply only to a release that declares `pl`.
  - A language is not a country: a later Ukrainian or Czech pack reuses the same mechanism.

### 1.5 Not in this design (deferred, section 12)

- a county slot in `ExecutedScope`;
- advertising `region` instead of `canton`;
- county codes in `city`;
- per-release output-schema descriptions;
- a country-neutral embedding prefix;
- a Polish stemmer, until measured;
- a Polish concepts-job prompt;
- postal `NN-NNN` and Polish datasets;
- the rest of the admin console.

## 2. The release additions (exact JSON)

### 2.1 `place_register.hierarchy` (inside the content digest)

New models in `core/release.py`, next to `Place` and `PlaceRegister` (`:76-95`):

```python
SEGMENT = r"^\[(?:0-9|A-Z|0-9A-Z)\]\{[1-9](?:,[1-9])?\}$"   # a character class and a width, nothing else

class PlaceLevel(Strict):
    id: str = Field(pattern=r"^[a-z][a-z_]*$", description=(
        "Level id, used as Institution.level and Basis.level: national, voivodeship, county, commune."))
    adjective: str = Field(description=(
        "The word before a basis kind ('Voivodeship ordinance'); lower-cased in 'Portal summary of ... rules' "
        "and in lists of levels."))
    noun: str = Field(description="One place of the level: 'voivodeship'.")
    plural: str = Field(description="Several places of the level: 'voivodeships'.")
    label: str = Field(description=(
        "Label template with at most {name}: 'commune of {name}'. Without a name the noun is used."))
    segment: str | None = Field(default=None, exclude_if=absent, pattern=SEGMENT, description=(
        "The code segment of the level, for example [0-9]{2}; absent at depth 0."))
    note: str | None = Field(default=None, exclude_if=absent, description=(
        "What a name at the lowest level is not (a district, a postcode); lowest level only."))

class PlaceHierarchy(Strict):
    country_adjective: str = Field(description="'Polish': 'official Polish sources'.")
    levels: list[PlaceLevel] = Field(min_length=3, description=(
        "The country first; the depth of a code (its number of dashes) is its index. Depth 1 is named by the "
        "request part canton (alias region), the last level by city; levels in between have no request part."))

class PlaceRegister(Strict):
    title: str; publisher: str; url: str; accessed_on: date; raw_sha256: str
    hierarchy: PlaceHierarchy | None = Field(default=None, exclude_if=absent, description=(
        "The country's levels and code format; absent in releases before swiss-tip-release/v3, which are Swiss."))
    generic_words: ...; places: ...
```

- The `segment` grammar is deliberately narrow: a character class and a width. A release cannot inject an arbitrary regular expression into the validator or the parser.
- Request parts map to depths **structurally, not through data**: `country` is depth 0, `canton` (alias `region`) is depth 1, and `city` is the lowest depth.
- The hierarchy decides which code is which level. It therefore belongs in `content_sha256`. `content_hash` (`core/release.py:282-294`) already dumps the whole register when present, so no hashing code changes.

The Polish register as embedded in a release:

```json
"place_register": {
  "title": "TERC - Krajowy rejestr urzędowy podziału terytorialnego kraju (official register of the territorial division of Poland), stan na 2026-01-01",
  "publisher": "Główny Urząd Statystyczny (Statistics Poland)",
  "url": "<the eTERYT full-file download page, as given to the importer with --url>",
  "accessed_on": "2026-09-27",
  "raw_sha256": "557edb28...",
  "hierarchy": {
    "country_adjective": "Polish",
    "levels": [
      {"id": "national", "adjective": "National", "noun": "country", "plural": "countries", "label": "national"},
      {"id": "voivodeship", "adjective": "Voivodeship", "noun": "voivodeship", "plural": "voivodeships",
       "label": "województwo {name}", "segment": "[0-9]{2}"},
      {"id": "county", "adjective": "County", "noun": "county", "plural": "counties",
       "label": "{name}", "segment": "[0-9]{2}"},
      {"id": "commune", "adjective": "Commune", "noun": "commune", "plural": "communes",
       "label": "commune of {name}", "segment": "[0-9]{3}",
       "note": "the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a village or a postcode"}
    ]
  },
  "generic_words": ["województwo", "woj", "voivodeship", "province", "powiat", "county", "gmina", "commune",
                    "municipality", "miasto", "m", "st", "m. st.", "miasto stołeczne", "city", "city of", "town", "the"],
  "places": [
    {"code": "PL", "name": "Poland", "aliases": ["Polska", "Rzeczpospolita Polska", "Republic of Poland", "POL"]},
    {"code": "PL-12", "name": "małopolskie", "aliases": ["Lesser Poland", "Małopolska", "Lesser Poland Voivodeship"]},
    {"code": "PL-12-06", "name": "powiat krakowski"},
    {"code": "PL-12-61", "name": "powiat m. Kraków"},
    {"code": "PL-12-61-011", "name": "Kraków", "aliases": ["Cracow", "Krakau"]}
  ]
}
```

- The level ids are the ones the catalogue already uses (`releases/mvp-poland/sources.json`, `scope.levels`). A source's `authority_level` therefore flows into `Institution.level` unchanged.
- The commune adjective is `Commune`, not `Municipal`. Labels, lists and the served `level` field then all say "commune".

### 2.2 `manifest.query_languages` and `manifest.evidence_languages` (outside the digest, bound by the readiness bytes)

Both go in `core/release.py`, `Manifest`, right after `languages` (`:233`):

```python
query_languages: list[str] | None = Field(default=None, exclude_if=absent, description=(
    "The languages callers write tool calls in, the translation target first. Absent: measured from the "
    "release's source terms (releases before swiss-tip-release/v3)."))
evidence_languages: list[str] | None = Field(default=None, exclude_if=absent, description=(
    "The only languages the cited excerpts may be in; the validator refuses any other."))
```

The Polish manifest excerpt:

```json
"schema_version": "swiss-tip-release/v3",
"jurisdictions": ["PL", "PL-12-61-011", "PL-14-65-011", "PL-24-69-011"],
"languages": ["pl"],
"query_languages": ["en", "pl"],
"evidence_languages": ["pl"],
```

- **English comes first, so English is the translation target.**
  - Labels, statements, aliases and sample questions are English, so the English index is the densest.
  - Polish inflection is where the six-character prefix stem matches worst.
  - So a Ukrainian question is better translated into English than into inflected Polish.
  - A Polish question is still sent as asked, because Polish is a listed query language.
- `evidence_languages` turns "evidence Polish only" into a rule the validator checks, not only a curation habit.

### 2.3 Release schema `swiss-tip-release/v3`

- `core/__init__.py:6` keeps `RELEASE_SCHEMA_VERSION = "swiss-tip-release/v2"` and gains `RELEASE_SCHEMA_VERSION_DECLARED = "swiss-tip-release/v3"`.
- `Manifest.schema_version` (`core/release.py:234`) becomes `Literal[v1, v2, v3]`.
- `Manifest.version_matches_fields` (`:258`) gains one rule, a load error like the existing `acceptance_suite_sha256` rule: `query_languages` and `evidence_languages` require v3.
- `validate_release` appends two issues after all existing ones:
  - "a place register that declares a hierarchy requires swiss-tip-release/v3";
  - "swiss-tip-release/v3 requires a place register with a hierarchy".
- v3 therefore means "declares its country".

Why the bump is justified:

- v3 changes how existing fields are read. `Institution.level` and `Basis.level` are no longer federal, cantonal or municipal. A code's level comes from the register instead of from the dash count and fixed Swiss names.
- Readers that parse `release.json` as plain JSON would silently misread `PL-12-61` as a municipality. Examples are pack scripts, the published release-format doc and older consoles.
- pydantic readers at 0.3.x already fail closed (`extra="forbid"` rejects `hierarchy`); v3 only makes the refusal name its cause.
- The precedent is v1 to v2, bumped for a field that changes how a release is judged.
- Swiss builds keep writing v2.

### 2.4 Build inputs

**`swiss-tip-place-aliases/v2`.** This is the hand-written file, and it carries the hierarchy. The hierarchy is authored country knowledge (English nouns, templates, the note), like the country entry and the generic words already in this file. It is not register output.

- v2 is v1 plus a required `hierarchy`.
- A v1 file that carries `hierarchy` is refused.
- An old builder refuses a v2 file by its version. Today `load_place_register` (`build/places.py:49-74`) silently ignores unknown keys, so it would drop the hierarchy.
- `ch-aliases.json` stays v1, byte for byte.

```json
{"schema_version": "swiss-tip-place-aliases/v2",
 "country": {"code": "PL", "name": "Poland", "aliases": ["Polska", "Rzeczpospolita Polska", "Republic of Poland", "POL"]},
 "hierarchy": {"country_adjective": "Polish", "levels": ["...as in 2.1..."]},
 "generic_words": ["...as in 2.1..."],
 "aliases": {"PL-12": ["Lesser Poland", "Małopolska", "Lesser Poland Voivodeship"],
             "PL-14": ["Masovia", "Mazowsze", "Masovian Voivodeship"],
             "PL-24": ["Silesia", "Śląsk", "Silesian Voivodeship"],
             "...": "one English name for each of the other 13 voivodeships (section 5.3)",
             "PL-12-61-011": ["Cracow", "Krakau"],
             "PL-14-65-011": ["Warsaw", "m. st. Warszawa", "Warschau"],
             "PL-24-69-011": ["Kattowitz"]}}
```

**The place file `swiss-tip-places/v1` is unchanged in shape.** The TERC importer (section 5) writes:

```json
{"schema_version": "swiss-tip-places/v1", "country": "PL",
 "title": "TERC - Krajowy rejestr urzędowy podziału terytorialnego kraju (official register of the territorial division of Poland), stan na 2026-01-01",
 "publisher": "Główny Urząd Statystyczny (Statistics Poland)", "url": "<--url>", "accessed_on": "2026-09-27",
 "raw_sha256": "557edb28...",
 "places": [{"code": "PL-02", "name": "dolnośląskie"}, {"code": "PL-02-01", "name": "powiat bolesławiecki"},
            {"code": "PL-02-01-011", "name": "Bolesławiec (gmina miejska)"}, "..."]}
```

**Curation (`swiss-tip-curation/v1`, additive, no bump).** The `mvp-poland` header:

```yaml
place_register: ../../config/places/pl-register.json
place_aliases: ../../config/places/pl-aliases.json
query_languages: [en, pl]
evidence_languages: [pl]
question_languages: [en]
```

- `query_languages` and `evidence_languages` are `list[str] | None = None`. The default is `None`, not `[]`, because `dump_curation` uses `exclude_none` and the admin console re-dumps Swiss `curation.yaml`.
- `question_languages` accepts `pl`. Listing `pl` makes the build require a Polish sample question on every concept. The decision above asks only for English questions, so the default header lists `[en]`.
- `extra="forbid"` makes an older builder refuse the new keys.

## 3. The Swiss default rule (proving identical results)

### 3.1 The rule

```text
hierarchy_of(register) = declared hierarchy   if register is not None and register.hierarchy is not None
                         SWISS                 otherwise (built in, never serialised; also for codes-only releases)
text_profile(manifest) = profile for the declared languages   if manifest.query_languages is not None
                         LEGACY_TEXT                          otherwise
```

Every Swiss release takes both "otherwise" branches, because the attested files carry neither field and cannot gain one.

### 3.2 The built-in `SWISS` hierarchy (`core/hierarchy.py`)

| Depth | id | adjective | noun / plural | label | segment | note |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | federal | Federal | country / countries | `federal` | - | - |
| 1 | cantonal | Cantonal | canton / cantons | `canton of {name}` | `[A-Z]{2}` | - |
| 2 | municipal | Municipal | municipality / municipalities | `municipality of {name}` | `[0-9]{1,4}` | `the political commune, not a district, a quarter or a postcode` |

- `country_adjective` is `Swiss`, `country` is `CH`, `builtin` is `True`.
- Its code pattern is **the same regex object** as today's `validation.JURISDICTION`: `^CH(?:-[A-Z]{2}(?:-\d{1,4})?)?$`. It moves to `hierarchy.py` as `SWISS_PATTERN`, with `validation.JURISDICTION = SWISS_PATTERN` kept as a re-export.
- The segments of `SWISS` are informational only, because `\d` is not `[0-9]`.
- The adjectives equal `LEVEL_WORD` (`core/basis.py:16`), and `adjective.lower() == id` for all three levels.

`builtin` selects the only place-specific branches kept verbatim. Everything else is the shared template path.

- **Parsing** (`core/places.py:27-29, 189-193, 207-209, 226-234`):
  - `CANTON_CODE` and `MUNICIPALITY_CODE`;
  - `numbered()` with `int()` zero-stripping;
  - a bare number accepted only next to a canton.
- **Validation:** the literal pattern.
- **`GUIDANCE_NEEDS_CONTEXT`:** the Czech-citizen example.
- **`tools/list`:** no substitution in the tool descriptions and the input schema.
- **The build:** the `bfs_code` append and the `"CH"` catalogue default (`build/release_build.py:163-167`).

### 3.3 `LEGACY_TEXT`

`LEGACY_TEXT` is `TextProfile(letters=None, stopwords=frozenset(STOPWORDS), facet_words=FACET_WORDS, stem=None)`, where `stem=None` means today's `stem` (`runtime/service.py:336`).

- With no letters, `fold(text, letters=None)` performs today's operations in today's order.
- The measured `query_languages()` path (`:297-333`) and the `NATIONAL_LANGUAGES` examples (`:171`, `:435`) run unchanged.

### 3.4 Why every Swiss result is identical

| Artifact | Why it does not change | Checked by |
| --- | --- | --- |
| `release.json` bytes, `content_sha256` | New fields are `exclude_if` absent; Swiss files are loaded, never re-dumped | `test_release.py:111-117` absence list (extended); `dump_release(load_release(p)) == bytes` |
| Readiness | Release bytes, suite digests and `query_prefix_sha256` untouched; no acceptance step field added | pack tests; readiness status stays `ready` |
| Validation issues | Same regex object; `level_id` of `SWISS` is today's `level_of_jurisdiction`, including `>= 2` as municipal; basis words are `LEVEL_WORD`; the national-basis check compares with `hierarchy.country` (`"CH"`), as today; new checks are declared-only or cannot fire on a valid Swiss release (the old `Literal` rejected any other level at load) | both attested releases validate empty; `test_release.py:130-172` |
| Place index | Parsing branch verbatim; three tables; the same keys (the loose `"<name> <canton>"` key at the lowest depth 2, segment keys at depths 0 and 1); the region ancestor `"-".join(code.split("-")[:2])` equals `rsplit("-", 1)[0]` at depth 2; `level_of(m) == lowest` equals `== 2` | `test_places.py` untouched; harness register sweep and error probes |
| Labels and messages | Template plus legacy values reproduce each literal (3.5) | literal-copy tests (10.1) |
| Search | `LEGACY_TEXT`: same `fold`, `STOPWORDS`, `FACET_TOKENS` and `stem`; measured query languages | `test_service.py:332-338, 714-725`; harness search families (lexical and replayed hybrid) |
| `tools/list` and the schema bundle | Built-in path returns `TOOL_DESCRIPTIONS[name]` and the `Jurisdiction` texts untouched, except the existing language swap; the output schema stays static; the anchors are constants whose concatenation is today's text | Swiss guard test; `contracts --check` test |
| Semantic search | `QUERY_PREFIX` untouched | `test_semantic.py:180, 200` |
| Build | A Swiss curation declares nothing: v2, `bfs_code` branch and `"CH"` default kept on the built-in path, alias deduplication without letters, question classification unchanged (0 of 577 measured) | in-memory rebuild proof (10.3) |

**The `ł` fold is scoped, not global.** A global `ł` to `l` fold was measured to change 1 of 6,031 Zurich result lines: the raw question of Q-PL-1. Global Polish stopwords changed 82 lines and failed UAT-121. Scoped to releases that declare `pl`, both produce zero Swiss differences.

### 3.5 Served strings that become templates

| Site | Slots | Legacy value (today, verbatim) | Polish value |
| --- | --- | --- | --- |
| `INSTRUCTIONS` `service.py:175` | `official {adjective} sources`; `the user's {region_noun} or {city_noun}` | Swiss; canton or municipality | Polish; voivodeship or commune |
| `SEARCH_PLACE_NOT_RECOGNISED` `:140` | `the official name of the {city_noun}{city_note}` | `municipality (the political commune, not a district, a quarter or a postcode)` | `commune (the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a village or a postcode)` |
| `GUIDANCE_PLACE_NOT_RECOGNISED` `:227` | as above, plus `or with the {region_noun}` | canton | voivodeship |
| `"the {part} {value!r}"` `:703`, `:900` | the word for each `not_recognised` key | `canton`, `city` | `voivodeship`, `city` |
| `GUIDANCE_NARROWER_NOT_PUBLISHED` `:221` | `{below_adjectives} part`, `another {below_nouns}` | cantonal or municipal; canton or municipality | voivodeship, county or commune; voivodeship, county or commune |
| `GUIDANCE_NEEDS_CONTEXT` `:214` | `{context_example}` | ` (for example a Czech citizen is population eu_efta)` | empty |
| Gap messages `:762`, `:820` | `add the {region_noun} or the city` | canton | voivodeship |
| Gap message `:766` | `{national} concepts still apply.` | Federal | National |
| `PlaceIndex.label` `places.py:120-128` | `levels[depth].label` | `CH (federal)`, `CH-ZH (canton of Zürich)`, `CH-ZH-261 (municipality of Zürich)`, `(canton)`, `(municipality)` | `PL (national)`, `PL-12 (województwo małopolskie)`, `PL-12-61 (powiat m. Kraków)`, `PL-12-61-011 (commune of Kraków)` |
| Region ambiguity `places.py:198` | `fits several {region_plural}` | cantons | voivodeships |
| Wrong region `places.py:217-219` | `The {city_noun} does not lie in the given {region_noun}` ... `give only the {city_noun}.` | municipality, canton | commune, voivodeship |
| City ambiguity `places.py:221-223` | `fits several {city_plural}`; tail | municipalities; `; add the canton or give the full name.` | communes; `; add the voivodeship, give the full name as listed or give the commune code.` |
| National-basis issue `validation.py:194-195` | `fact {id} is {levels[0].id} but rests on a {level} basis` | federal | national |
| Portal summary label `basis.py:42` | `Portal summary of {adjective.lower()} rules` | federal (and cantonal, municipal) | national, commune |
| Query-language note `service.py:163-170, 435` | the others list; the tools sentence | `NATIONAL_LANGUAGES` examples; none | none; ` Call every tool in English or Polish: the search query and the place names in jurisdiction.` |

The slot values live in one frozen `Wording` dataclass (4.2). `LEGACY_WORDING` holds today's substrings, pasted verbatim.

## 4. Module-by-module changes

### 4.1 `packages/core`

**`core/hierarchy.py` (new, about 120 lines).** It imports only `release.py` and `re`, so it creates no import cycle.

```python
@dataclass(frozen=True)
class Hierarchy:
    levels: tuple[PlaceLevel, ...]
    country_adjective: str
    country: str | None            # "CH" built in; the register's single depth-0 place when declared
    pattern: re.Pattern            # SWISS_PATTERN built in; ^PL(?:-[0-9]{2}(?:-[0-9]{2}(?:-[0-9]{3})?)?)?$ declared
    builtin: bool
    # lowest = len(levels) - 1; ids; words = {id: adjective}
    # depth(code) = code.count("-")
    # level_id(code): built in levels[min(depth, lowest)].id (today's level_of_jurisdiction); declared: None past lowest
    # region_of(code) = "-".join(code.split("-")[:2]) for depth >= 1
    # label(code, name): levels[d].label.format(name=...) when it has {name} and a name exists; the noun when it has
    #   {name} and no name exists; otherwise the template itself; prefixed with the code: f"{code} ({...})"

SWISS: Hierarchy
def hierarchy_of(register: PlaceRegister | None) -> Hierarchy
def either_or(items) -> str                 # "a or b", "a, b or c" (the join rule of runtime language_list)
def containment_sentence(h) -> str          # generated; reproduces today's Swiss sentence (10.1)
def level_list(h) -> str                    # "federal, cantonal, municipal"
```

A malformed declared hierarchy yields a never-matching pattern, and the validator reports the cause, so nothing crashes. Examples: no depth-0 place, or two depth-0 places.

**`core/release.py`**

| Line | Change |
| --- | --- |
| `:40` | `InstitutionLevel` stays as a type-hint alias of the Swiss ids for importers (`build/curation.py:21`, `core/basis.py`) |
| `:71` | `Institution.level: str`. It serialises exactly like the `Literal`; membership moves to the validator. `release_schema()` output changes, but nothing commits it |
| `:73`, `:79` | Descriptions only: "a subdivision code (CH-ZH, PL-12-61-011)" |
| `:76-95` | `PlaceLevel`, `PlaceHierarchy`, `PlaceRegister.hierarchy` (2.1) |
| `:101` | `Basis.level: str` |
| `:233-262` | v3 literal, `query_languages`, `evidence_languages`, the v3 rule in `version_matches_fields` (2.2, 2.3) |
| `:282-294` | `content_hash` unchanged |

**`core/basis.py`**

| Line | Change |
| --- | --- |
| `:16` | `LEVEL_WORD` stays: it is `SWISS.words` |
| `:26-47` | `basis_label(level, kind, norm=None, refers_to=None, words=LEVEL_WORD)`: `word = words[level]`; `:42` becomes `f"Portal summary of {word.lower()} rules"` (equal to the raw id for all three Swiss levels, so all 14 Zurich level/kind pairs recompute identically) |
| `:50` | `make_basis(..., words=LEVEL_WORD)` passes the words through |
| `:79-81` | `level_of_jurisdiction(code, hierarchy=SWISS)` returns `hierarchy.level_id(code)` |

- `NORM_KINDS`, `BasisKind` and `DEFAULT_RANKING_POLICY` are unchanged: there is no new basis kind (section 7).

**`core/text.py`**

```python
LETTERS = {"pl": {"ł": "l"}}   # letters NFKD leaves whole, per language; casefold turns "Ł" into "ł" first

def fold(text: str, letters: Mapping[str, str] | None = None) -> str:
    text = text.casefold()
    if letters:
        text = text.translate(str.maketrans(letters))
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))

def letters_for(codes) -> dict[str, str] | None: ...   # union of LETTERS for the codes; None when empty
```

- With `letters=None`, `fold` is today's function.
- `collapse_umlauts` stays global: it applies to index and query alike.
- `normalise` and `contains_phrase` are unchanged, because verbatim checks keep `ł`, `ą` and `ż` exact.
- No other letter is folded: `œ` occurs in mvp-zurich and `ı` in mvp-wallisellen.

**`core/validation.py`**

| Line | Change |
| --- | --- |
| `:20` | `JURISDICTION = SWISS_PATTERN` (re-export, same object) |
| `:31` | `contains`: docstring only ("a country contains every place below it") |
| `validate_release` | Computes `hierarchy = hierarchy_of(release.place_register)` once; `:80` (fact jurisdiction) uses `hierarchy.pattern` |
| `:148` | Institution jurisdiction uses `hierarchy.pattern` |
| `:150` | `hierarchy.level_id(institution.jurisdiction) != institution.level`; the message is unchanged |
| before `:178` | `if basis.level not in hierarchy.ids`: issue `evidence {id} basis level {level!r} is not a level of this release`, then `continue`. This replaces today's `KeyError` crash |
| `:178` | `basis_label(..., words=hierarchy.words)` |
| `:194-195` | `if fact.jurisdiction == hierarchy.country and basis.level != hierarchy.levels[0].id:`; message `f"fact {id} is {hierarchy.levels[0].id} but rests on a {level} basis"`. For CH this is exactly today's condition and text |
| `check_place_register` `:208-227` | `:221` uses `hierarchy.pattern`; docstring made neutral; the parent check (`rsplit`) already works at any depth, because counties are listed |

Checks appended after all existing issues, only when a hierarchy is declared:

- at least three levels, with unique ids;
- `segment` absent at depth 0 and present at every other depth;
- `note` only on the lowest level;
- label templates carry at most `{name}` (checked with `string.Formatter().parse`);
- exactly one depth-0 place, equal to the manifest's single country prefix;
- the v3 rule of 2.3;
- `query_languages` codes unique and matching `LANGUAGE`, and every fact's language among them;
- every evidence record's base language in `evidence_languages`.

**`core/places.py`**

| Line | Change |
| --- | --- |
| `:61-74` | `name_words(text, letters=None)` and `place_key(text, letters=None)` pass `letters` to `fold` |
| `:83-105` | `PlaceIndex.__init__(register, jurisdictions, letters=None)`: `self.hierarchy = hierarchy_of(register)`; tables `[{} for _ in hierarchy.levels]` (`:93-94`); `:102` `if level == hierarchy.lowest:` adds `"<part> <segment 1>"` (for CH `Buchs ZH`; for PL `Kraków 12`, harmless); `:104` `if level <= 1:` adds the segment keys (`CH`, `ZH`; `PL`, `12`; never a county's `61`); `generic`, `add` and `lookup` use `name_words(..., self.letters)` |
| `:120-128` | `label()` via `hierarchy.label(code, self.name(code))` |
| `:130-133` | `described()` unchanged |
| `:172-183` | `resolve()`: `if municipality and canton_code is None and level_of(municipality) == hierarchy.lowest: canton_code = hierarchy.region_of(municipality)`; `Scope` unchanged (no county slot) |
| `:185-201` | `canton_of`: `if hierarchy.builtin:` today's `CANTON_CODE` block verbatim. Otherwise `(?i)^(?:([a-z]{2})-)?(<segment 1>)$` gives `f"{country}-{segment}"`, accepted when listed. Then `lookup(1, ...)` as today. The message is templated (3.5). The path `jurisdiction.canton` and the key `not_recognised["canton"]` stay, because they are wire field names |
| `:203-224` | `city_of`: `if hierarchy.builtin:` today's `MUNICIPALITY_CODE` and `numbered(int(...))`. Otherwise a full code at the lowest depth, `(?i)^(?:([a-z]{2})-)?(seg1)-(seg2)-(seg3)$`, with segments kept as strings, accepted when listed and never zero-stripped. A code that parses but is not listed is not recognised, with no name fallback, like `CH-ZH-999` today. Names are looked up with `lookup(hierarchy.lowest, ...)`, never at the county. Messages are templated (3.5): the wrong-region message names `region_of(code)`, the ambiguity message names the parent (`rsplit`, the county for PL, which separates candidates in different counties) |
| `:226-234` | `numbered()` unchanged; built-in path only |

A county code or a bare TERYT number sent as `city` is reported in `not_recognised`. The request then runs for the broader place.

**`core/contracts.py`.** No model, field or text change. `SCHEMA_VERSION` (`:21`) stays `swiss-tip/v4`, and `tool-contracts.schema.json` stays byte-identical. The only change is named anchor constants, used in the existing concatenations so that the text is unchanged:

```python
SEARCH_DESCRIPTION_PLACE = "When the user's canton or municipality is known"                       # :631
RESOLVE_DESCRIPTION_EXAMPLE = '{"canton": "Zurich", "city": "Wallisellen"}'
RESOLVE_DESCRIPTION_NAMES = "Names in English or the local language"
RESOLVE_DESCRIPTION_CONTAINMENT = ("A federal concept answers for any canton, a cantonal concept for its "
                                   "municipalities, never upward or sideways.")
RESOLVE_DESCRIPTION_LEVELS = "its level (federal, cantonal, municipal)"
RESOLVE_DESCRIPTION_BASIS = ("'Federal act: AIG, SR 142.20, Art. 12', 'Cantonal authority guidance' or 'Portal "
                             "summary of federal rules'")
JURISDICTION_COUNTRY_DESCRIPTION, JURISDICTION_CANTON_DESCRIPTION, JURISDICTION_CITY_DESCRIPTION   # :76-87 texts
```

A test asserts that each anchor occurs exactly once in its description.

**Unchanged in core:**

- `core/acceptance.py`: part names and `OPTIONAL_DIGEST_FIELDS` stay, so the Swiss suite digests stand;
- `core/datasets.py`, `core/connector.py`, `core/readiness.py`.

### 4.2 `packages/runtime` and `apps/mcp-server`

**`runtime/service.py`, text profile**

```python
POLISH_STOPWORDS = frozenset(...)       # section 6.2, folded
POLISH_FACET_WORDS = "adres adresy adresu godziny godzin otwarcia czynne czynny telefon telefonu kontakt kontaktowy dojazd siedziba"
LANGUAGE_TEXT = {"pl": (POLISH_STOPWORDS, POLISH_FACET_WORDS)}
QUERY_LANGUAGE_TOOLS = " Call every tool in {languages}: the search query and the place names in jurisdiction."

@dataclass(frozen=True)
class TextProfile:
    letters: Mapping[str, str] | None
    stopwords: frozenset[str]
    facet_words: str
    stem: Callable[[str], str] | None = None      # None: prefix-6 (stem() at :336); the hook for a measured Polish stemmer

LEGACY_TEXT = TextProfile(None, frozenset(STOPWORDS), FACET_WORDS)

def text_profile(manifest) -> TextProfile:
    if manifest.query_languages is None:
        return LEGACY_TEXT
    codes = sorted(set(manifest.query_languages))
    extra = [LANGUAGE_TEXT[c] for c in codes if c in LANGUAGE_TEXT]
    return TextProfile(letters_for(codes), frozenset(STOPWORDS).union(*(s for s, _ in extra)),
                       " ".join([FACET_WORDS, *(f for _, f in extra)]))

def tokens(text: str, profile: TextProfile = LEGACY_TEXT) -> set[str]:          # :349
    folded = ELISION.sub(" ", fold(text, profile.letters))
    stemmer = profile.stem or stem
    return {stemmer(collapse_umlauts(t)) for t in re.findall(r"[a-z0-9]+", folded) if t not in profile.stopwords}
```

- The module-level `tokens()` default and `FACET_TOKENS` (`:354`) stay for importers: `admin_console/screens/workbench.py:27` and `swiss-tip-mvp/scripts/test/packs/test_match_strength.py`, which also imports the threshold constants.
- `ReleaseService.__init__` sets `self.text = text_profile(manifest)` and `self.facet_tokens = frozenset(tokens(self.text.facet_words, self.text))`.
- `field_tokens` (`:493-496`), `lexical_hits` (`:589`) and `anchored_match` (`:616`) use `self.text` and `self.facet_tokens`.

**`runtime/service.py`, languages**

| Line | Change |
| --- | --- |
| `:213` | `LANGUAGE_NAMES` gains `"pl": "Polish"` and `"uk": "Ukrainian"` (additive keys; disclosures `:890-892` then read "English summaries of Polish pages") |
| `:297-333` | `query_languages(release, letters=None)`. Folding uses `letters`. When `manifest.query_languages` is declared, the order is the declared one and `codes[0]` is the translation target. `indexed` gives the measured source terms of a language where it has any, plus "the concept labels, sample questions and statements" for the facts' language, or "declared by the release" when neither applies. A measured source language that is not declared is still reported as partial, as today |
| `:435` | `others = [...NATIONAL_LANGUAGES...]` only when `query_languages` is undeclared; otherwise `[]` (no parenthesis) |
| `:436-441` | When declared, `QUERY_LANGUAGE_TOOLS.format(languages=language_list(codes))` is appended to `self.query_language_note`, so it reaches the instructions, the search description and the `query` field |

**`runtime/service.py`, places and wording**

| Line | Change |
| --- | --- |
| `:388` | `PlaceIndex(release.place_register, manifest.jurisdictions, letters=self.text.letters)`; then `self.hierarchy = self.place_index.hierarchy` and `self.wording = wording_for(self.hierarchy)` |
| `:140`, `:175`, `:214`, `:221`, `:227` | Constants become templates (3.5), rendered once in `__init__` from `self.wording` |
| `:703`, `:900` | `" and ".join(f"the {self.wording.part_word(part)} {value!r}" ...)` |
| `:762-766`, `:820` | Gap messages from `self.wording` (`:832` "add the city to resolve them" is already neutral) |
| `:905` | `GUIDANCE_NEEDS_CONTEXT` rendered with `context_example` |
| `:481-491` | `jurisdiction_note` unchanged: a declared hierarchy always has a register, so the no-register example is never reached |
| `:734-737` | `executed_scope` unchanged. For Poland `canton_code` and `canton` carry the voivodeship, and `municipality_id` and `city` the commune |

```python
@dataclass(frozen=True)
class Wording:
    adjective: str; region_noun: str; region_plural: str; city_noun: str; city_plural: str; city_note: str
    below_adjectives: str; below_nouns: str; national: str; context_example: str; ambiguity_tail: str
    def part_word(self, key: str) -> str      # "canton" -> region_noun, "city" -> "city"

LEGACY_WORDING = Wording(adjective="Swiss", region_noun="canton", region_plural="cantons", city_noun="municipality",
    city_plural="municipalities",
    city_note=" (the political commune, not a district, a quarter or a postcode)",
    below_adjectives="cantonal or municipal", below_nouns="canton or municipality", national="Federal",
    context_example=" (for example a Czech citizen is population eu_efta)",
    ambiguity_tail="; add the canton or give the full name.")

def wording_for(h: Hierarchy) -> Wording:    # LEGACY_WORDING when h.builtin, else generated from h (generate_wording)
```

`generate_wording(h)` is the declared generator. A test runs it on the `SWISS` levels and requires `LEGACY_WORDING`, except `context_example` and `ambiguity_tail`, which differ on the declared path by design.

The same `Wording` also feeds the `places.py` messages. `PlaceIndex` renders them through `wording_for(self.hierarchy)`, which moves to core.

**`runtime/service.py`, `tools/list`, declared path only.** When `self.hierarchy.builtin` is set, `tool_description` (`:459`) and `tool_input_schema` (`:466`) run exactly as today. Otherwise:

| Anchor | Polish rendering |
| --- | --- |
| `SEARCH_DESCRIPTION_PLACE` | `When the user's voivodeship or commune is known` |
| `RESOLVE_DESCRIPTION_EXAMPLE` | `{"city": "Kraków"}`: the name of the first lowest-depth code in `manifest.jurisdictions` |
| `RESOLVE_DESCRIPTION_NAMES` | `Names in English or Polish` (the declared query languages) |
| `RESOLVE_DESCRIPTION_CONTAINMENT` | `containment_sentence(h)`, then ` executed_scope names the voivodeship in canton_code and canton, and the commune in municipality_id and city.` |
| `RESOLVE_DESCRIPTION_LEVELS` | `its level (national, voivodeship, county, commune)` |
| `RESOLVE_DESCRIPTION_BASIS` | The release's own first labels of kind act or ordinance, guidance and summary, in evidence order, quoted and joined with `either_or`; labels composed from the level words where a kind is missing |

`containment_sentence` for Poland reads: "A national concept answers for any voivodeship, a voivodeship concept for its counties and communes, a county concept for its communes, never upward or sideways." The same generator yields today's Swiss sentence, and a test checks it.

The input schema keeps the property names `country`, `canton` and `city`. The three part descriptions are replaced with texts generated from the register:

- **country:** "Country name or ISO 3166-1 code, for example Poland, Polska or PL. Omit it for the country this server covers; another country resolves OUT_OF_COVERAGE."
- **canton:** "Voivodeship name or code, in English or Polish, for example małopolskie, Lesser Poland or PL-12; the field also accepts the name region. Not needed when the city is given."
- **city:** "The commune the user lives in (the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a village or a postcode): its name in English or Polish, for example Kraków or Cracow, or its code (PL-12-61-011)."

A startup assert checks that every anchor is present before a replacement.

**Unchanged in the runtime and the server:**

- `runtime/semantic.py`: `QUERY_PREFIX` stays. The Polish index is built with the same prefix, and a country-neutral prefix is a later, measured `query_version` change.
- `runtime/acceptance.py`: `ran_for` (`:136`) works with the commune in `municipality_id`.
- `runtime/connectors.py` and `search_cli.py`.
- `apps/mcp-server/server.py`: all served text flows through `ReleaseService`, and `:90` (`exclude_none`) keeps results byte-identical.
- `roundtrip.py:84-87`: for a commune concept it sends `canton: "PL-12"` and `city: "PL-12-61-011"`, which the declared parser accepts. National and voivodeship codes work too.

**Known limitation.** The round trip fails for a concept whose first jurisdiction is a county code, because no request part names a county (section 12, Q8). Rule for the first slice: no county-level facts.

### 4.3 `packages/build`

| File:line | Change |
| --- | --- |
| `curation.py:49` | `BasisSpec.level: str \| None`, checked against the hierarchy at build time |
| `curation.py:51` | Description names `Dz.U. poz.`, `Uchwała nr` and `§` |
| `curation.py:67` | `CuratedInstitution.level: str`, checked against the hierarchy |
| `curation.py:78` | `CuratedFact.jurisdiction` becomes required. All 1,345 Zurich and 99 Wallisellen facts state it, so parsing and dumping are unchanged |
| `curation.py:131-139` | New `query_languages` and `evidence_languages` (2.4); `question_languages: list[Literal["en", "de", "pl"]]` |
| `questions.py:15-37` | A `POLISH` function-word set (6.4); `question_language` takes the unique maximum over three counts, otherwise `None`; `LANGUAGES` and the docstring updated |
| `places.py:31-46` | `ALIASES_SCHEMA_VERSIONS = (v1, v2)`; v2 requires `hierarchy`, v1 must not carry it; `PLACES_SCHEMA_VERSION` stays v1 |
| `places.py:49-74` | `load_place_register(register_path, aliases_path=None, letters=None)`: passes `hierarchy=PlaceHierarchy(**aliases["hierarchy"])` when present; alias deduplication uses `place_key(..., letters)` |
| `places.py:91-97` | `place_register_for` passes `letters_for(curation.query_languages or [])` (None for Swiss), so the build and the server fold alike and the Polish register bytes are stable across builds |
| `release_build.py:163` | On a declared hierarchy, a catalogue definition without `jurisdiction` raises `BuildError`; the built-in path keeps the `"CH"` default |
| `release_build.py:165-167` | The `bfs_code` append runs on the built-in path only; TERYT codes arrive complete and keep their zeros |
| `release_build.py:171` | `level or level_of_jurisdiction(jurisdiction, hierarchy)`; an `authority_level` outside `hierarchy.ids` raises `BuildError` |
| `release_build.py:176-212` | On a declared hierarchy only: `SECTION_HEADING = ^§\s*(\d+)([a-z]?)`, derived as `§ N` with the literal number (no glued-footnote heuristic), and `ANNEX_HEADING_PL = ^Załącznik(?:\s+nr)?\s+(\d+\|[IVXLC]+)`; `ARTICLE_IN_NORM` also warns on a hand-written `§`. `Anhang` is never widened. No text-start locator: Polish PDFs and DOCX have empty heading paths, so the curator writes `§` or `Art.` in the citation's `basis.norm`, and a citation's own basis never gets a derived article appended |
| `release_build.py:237` | A `spec.level` outside `hierarchy.ids` raises `BuildError(f"fact {id}: basis level {level!r} is not a level of the place register ({ids})")` |
| `release_build.py:250` | `make_basis(..., words=hierarchy.words)` |
| `release_build.py:253` | With `evidence_languages` declared, a cited record in another language raises `BuildError` naming the URL. Example: `warszawa19115.pl/en/...` is declared `en-US` although its content is Polish; the Polish version is cited instead |
| `release_build.py:379-397` | `query_languages=curation.query_languages`, `evidence_languages=curation.evidence_languages`; `schema_version=RELEASE_SCHEMA_VERSION_DECLARED if place_register and place_register.hierarchy else RELEASE_SCHEMA_VERSION`. `query_languages` or `evidence_languages` in a curation without a hierarchy raises `BuildError` |
| `release_build.py:379` (checks) | Each declared query language is a fact language, or carries verbatim source terms on at least one concept, or is in `question_languages`. Otherwise the note would advertise a language the index cannot match |
| `release_build.py:414-418` | The report adds places per level only when a hierarchy is present (`build-report.json` is committed for Swiss) |
| `case_catalogue.py:45` | `LANGUAGE_ORDER` gains `PL` at the end |

Unchanged:

- `build_cli.py:36`, `apps/knowledge-builder/.../pipeline.py:181-209` and `admin_console/writes.py:123`. The hierarchy travels inside the register, so no signature changes.
- `DEFAULT_RANKING_POLICY`: Wallisellen embeds it on a rebuild.

### 4.4 `packages/ingestion`

- `ingestion/places.py`: a TERC reader beside `parse_snapshot` (`:45-78`); the BFS path (`:34-125`) is untouched (section 5).
- `ingestion/catalog.py:30`: the `_depth` docstring example becomes `PL-12-61-011`.
- `packages/ingestion/README.md:191-210`: the TERC importer.

### 4.5 Other packages

- `packages/concepts` is not used for Poland in the first slice: the pack is curated in the Claude Code window. `concepts/schemas.py:5` (`BASIS_LEVELS`), the prompts and `package.py:254` are deferred.
- Swiss checkpoint keys stand.

## 5. The TERYT place file and importer

### 5.1 Input

The input is `swiss-tip-mvp/.local/mvp-poland/places/TERC_Urzedowy_2026-09-27.csv`:

- UTF-8 with BOM, CRLF, `;` delimiter;
- columns `WOJ;POW;GMI;RODZ;NAZWA;NAZWA_DOD;STAN_NA`;
- 4,360 rows, all with `STAN_NA` 2026-01-01;
- sha256 `557edb28...`, byte-identical to the CSV inside the ZIP (the ZIP itself hashes to `01710da0...`).

### 5.2 Mapping

| TERC row | Kept as | Code | Name written |
| --- | --- | --- | --- |
| `POW` empty (16) | voivodeship | `PL-{WOJ}` | `NAZWA.lower()`: `MAŁOPOLSKIE` becomes `małopolskie`, `KUJAWSKO-POMORSKIE` becomes `kujawsko-pomorskie` (the official form in "województwo małopolskie") |
| `GMI` empty, `NAZWA_DOD` `powiat` (314) | county | `PL-{WOJ}-{POW}` | `powiat {NAZWA}`: `powiat krakowski` |
| `GMI` empty, `miasto na prawach powiatu` (65) | county | `PL-12-61` | `powiat m. {NAZWA}`: `powiat m. Kraków` |
| `GMI` empty, `miasto stołeczne, na prawach powiatu` (1) | county | `PL-14-65` | `powiat m. st. Warszawa` |
| RODZ 1, 2, 3 (302 + 1,453 + 724 = 2,479) | commune | `PL-{WOJ}-{POW}-{GMI}{RODZ}`, every segment kept as a string | `NAZWA`, or `f"{NAZWA} ({NAZWA_DOD})"` when two or more kept communes share (WOJ, POW, NAZWA): 142 pairs, for example `Bolesławiec (gmina miejska)` PL-02-01-011 and `Bolesławiec (gmina wiejska)` PL-02-01-022 |
| RODZ 4, 5 (724 + 724) | dropped | - | Town and rural parts of an urban-rural commune: same name as the commune, and siblings, not children |
| RODZ 8 (18 Warsaw dzielnice), 9 (19 delegatury) | dropped | - | A code such as PL-14-65-028 would be a sibling of Warszawa PL-14-65-011, not inside it |

- Kraków, Warszawa and Katowice stay unqualified: `PL-12-61-011`, `PL-14-65-011` and `PL-24-69-011`. These equal the catalogue's `region_codes`.
- Qualified names work through the existing `QUALIFIER` loose keys (`core/places.py:30, 99`):
  - `Bolesławiec` is ambiguous, and the error names both qualified candidates in `powiat bolesławiecki (PL-02-01)`;
  - `Bolesławiec (gmina wiejska)` matches exactly.
- Names shared across counties (226 names on 466 communes nationally) stay unqualified. The ambiguity message names each candidate's county, and the hint offers the voivodeship or the commune code.
- Villages are not in TERC (they are in SIMC), so a village name is not recognised. The commune note tells the caller so.

### 5.3 The importer

**Functions:**

- `parse_terc(raw: bytes) -> tuple[list[dict], date, Counter]` returns the places, the `STAN_NA` date and the dropped rows per RODZ.
- `terc_place_file(raw, url, day)` writes the v1 shape of 2.4. `raw_sha256` is the hash of the CSV bytes read, and `STAN_NA` goes into the title, because `PlaceRegister` has no validity-date field.

**Refusals.** The importer refuses the file on:

- a missing column;
- a voivodeship count other than 16;
- a county without its voivodeship;
- a commune without its county;
- a duplicate code;
- an unknown RODZ;
- an unexpected county `NAZWA_DOD`;
- more than one `STAN_NA`;
- a qualified name still not unique within its county.

**Output.** Places are sorted by code; fixed widths sort correctly.

**CLI:**

```text
swisstip-places --terc .local/mvp-poland/places/TERC_Urzedowy_2026-09-27.csv \
                --url <eTERYT full-file download page> --accessed-on 2026-09-27 \
                --output config/places/pl-register.json
```

- It never fetches, because the eTERYT download is a form.
- It prints `voivodeships 16, counties 380, communes 2479, dropped {4: 724, 5: 724, 8: 18, 9: 19}`.
- The default invocation, its arguments and its printed keys stay BFS; `test_places.py` pins them.

**Expected result.** 16 + 380 + 2,479 = 2,875 places. The alias file's country entry makes 2,876.

**`pl-aliases.json`** is hand-written, as in 2.4. It holds:

- the country entry;
- the hierarchy;
- the generic words;
- one English name per voivodeship:
  - PL-02 Lower Silesia, PL-04 Kuyavia-Pomerania, PL-06 Lublin Voivodeship, PL-08 Lubusz;
  - PL-10 Łódź Voivodeship, PL-12 Lesser Poland, PL-14 Masovia, PL-16 Opole Voivodeship;
  - PL-18 Subcarpathia, PL-20 Podlaskie Voivodeship, PL-22 Pomerania, PL-24 Silesia;
  - PL-26 Holy Cross, PL-28 Warmia-Masuria, PL-30 Greater Poland, PL-32 West Pomerania;
- the three cities' English and German names.

More on the aliases:

- "Krakow" and "Lodz" fold equal to the official names once `ł` is folded, so the build drops them as redundant (`build/places.py:62-67`).
- Warsaw dzielnice are not aliases: "Bielany" is also a rural commune, PL-14-29-022.
- Locative forms (`Krakowie`, `Warszawie`) are an optional later data addition.

### 5.4 When

- **Before the event:** the importer and its tests. A dry run on the real CSV checks the counts, with the output kept in `.local`.
- **At the event,** as the proof-of-concept data step: write and commit `config/places/pl-register.json` and `pl-aliases.json`. The name decisions (12, Q3) must be settled before that first write, because the register bytes enter the Polish digest.
- `ch-register.json` and `ch-aliases.json` are never touched.

## 6. Polish text

### 6.1 Summary

| Topic | Decision | Scope |
| --- | --- | --- |
| `ł` / `Ł` | `LETTERS["pl"] = {"ł": "l"}` in `fold`, used by `tokens`, `query_languages`, the place index and the build's alias deduplication | Releases whose `query_languages` include `pl` |
| Other letters | Not folded (`œ` in Zurich, `ı` in Wallisellen) | - |
| Umlaut digraphs | `collapse_umlauts` stays global; index and query collapse alike, so Polish words still match themselves | all |
| Stopwords | `STOPWORDS` union `POLISH_STOPWORDS` | Release-scoped |
| Facet words | `FACET_WORDS` plus `POLISH_FACET_WORDS` | Release-scoped |
| Stem | Six-character prefix, unchanged; `TextProfile.stem` is the hook | Measured before any change (6.3) |
| Verbatim checks | `normalise` unchanged; `excerpt_contains` and `source_terms` must contain `ł` exactly | - |
| Match-strength thresholds | Unchanged; re-measured on the Polish pack before any change | - |

Effects on Polish text:

- `Wrocław` becomes `wroclaw` and `Łódź` becomes `lodz`.
- `Jak złożyć wniosek w Krakowie?` gives the tokens `zlozyc`, `wniose` and `krakow` (the prefix-6 of `krakowie` meets `Kraków`).
- The legacy `tokens` on the same text keeps `jak` and `w`, and cuts `złożyć` into `z` (a stopword) and `ozyc`.

### 6.2 `POLISH_STOPWORDS` (folded form)

```text
w we z ze o u na do od po za przy przez dla bez nad pod przed miedzy ku
i a oraz lub albo ale lecz ani czy bo gdy jesli jezeli zeby aby niz wiec czyli
jak jaki jaka jakie jakiego jakich co czego czym kto kogo komu ktory ktora ktore ktorego ktorej ktorych
ile gdzie kiedy dlaczego sie nie tak juz tez takze tylko jeszcze tu tam teraz
jest sa byc byl byla bylo byly bedzie beda jestem moge mozna musze trzeba nalezy chce prosze mam ma maja
moj moja moje mojego moim mnie mi ja my wy on ona oni ich jego jej je im nam nas sobie swoj swoja swoje
ta te tego tej tym tych to
```

- `ten` and `go` are left out: the English statements of the Polish pack say "ten days" and "go to the office".
- A global union was measured to change 82 Zurich result lines and fail UAT-121. The list therefore never enters the Swiss path.

### 6.3 Stemming

- The first slice keeps the six-character prefix.
- Inflection is carried by:
  - Polish source terms copied verbatim, in the inflected forms the pages use;
  - English aliases;
  - hybrid search;
  - optional Polish sample questions.
- Candidate `pl-light`: strip the longest of `ami ach ego emu owi ow om em ie y e a u i` (folded) while at least 4 characters remain, then take the six-character prefix. It brings `gmina`, `gminy` and `gminie` to `gmin`, and `opłata`, `opłaty` and `opłatę` to `oplat`.
- It is measured on the synthetic pack before the event, and on the Polish regression pack at the event, both lexically and in hybrid mode. It is adopted, as `TextProfile.stem` for `pl` releases only, if found@3 rises and no off-topic question turns strong.
- No new dependency.

### 6.4 Sample questions (`build/questions.py`)

The `POLISH` set is compared as the existing sets are: lower-cased, with diacritics.

```text
czy jak gdzie kiedy ile co kto dlaczego jaki jaka jakie który która które mogę można muszę trzeba należy
jest są mam ma mają się nie dla przez przy od po za na jestem mnie mój moje lub oraz ale też już jeśli jeżeli żeby aby
```

- It is disjoint from `ENGLISH` and `GERMAN`: `a`, `i`, `do`, `to`, `on`, `we`, `ten` and single letters are left out. A test asserts the disjointness.
- Measured with such a set: 0 of 577 Swiss question classifications change.

## 7. Basis labels

- The label keeps its shape: `"{adjective} {kind}: {norm}"`.
- The norm is verbatim Polish, with `Art.`, `ust.` and `§` written by the curator.
- There is no new `BasisKind`, so these stay untouched:
  - `RankingPolicy` and `DEFAULT_RANKING_POLICY`;
  - `concepts/schemas.py`;
  - `NORM_OR_AUTHORITY`;
  - the console kind list;
  - Wallisellen's rebuild bytes.

| Polish source | kind | level | Served label (example) |
| --- | --- | --- | --- |
| ustawa (Dz.U., cited from the Sejm ELI API) | act | national | `National act: Ustawa z dnia 8 marca 1990 r. o samorządzie gminnym (Dz.U. 2024 poz. 1465), Art. 5a ust. 5` |
| rozporządzenie | ordinance | national | `National ordinance: Rozporządzenie ... (Dz.U. ... poz. ...), § 3` |
| uchwała of a commune council (akt prawa miejscowego) | ordinance | commune | `Commune ordinance: Uchwała nr LI/1410/21 Rady Miasta Krakowa (Dz. Urz. Woj. Małop. 2021 poz. ...), § 13` |
| uchwała of a voivodeship assembly (sejmik) | ordinance | voivodeship | `Voivodeship ordinance: Uchwała nr ... Sejmiku Województwa Małopolskiego, § 2` |
| zarządzenie of a mayor, or a council resolution that is not local law | directive | commune | `Commune directive: Zarządzenie nr ... Prezydenta Miasta Krakowa, § 4` |
| city page, BIP explanation | guidance | commune | `Commune authority guidance` |
| office list | directory | commune | `Commune authority directory` |
| gov.pl, obywatel.gov.pl | summary | national | `Portal summary of national rules` |
| 19115 city portal | summary | commune | `Portal summary of commune rules` |

**Institutions.** A resolution cited from the voivodeship's official journal is published by that journal: `law_collection`, level `voivodeship`, jurisdiction `PL-12`. Its basis level is still `commune`. The fact at `PL-12-61-011` passes the containment check, because `PL-12` contains it.

**Norms per citation.** All Kraków council resolutions share one URL path and differ only by `?id=`, so page rules cannot single one out. Each citation therefore states its own `basis.norm`.

## 8. Served texts and contracts

### 8.1 What the calling model sees on a Polish release

**`initialize.instructions`:**

> Swiss TIP serves published, cited facts from official Polish sources; it composes no answers. Translate first: when a question is in a language other than English or Polish, search with its key terms translated into English, and still answer in the user's language; an untranslated query can match the wrong concept. Search ONCE per question, in English or Polish, never in more than one of them: a second search in another language rarely finds other concepts. Write the search query in English or Polish: lexical search matches only these languages, and a question already in one of them is sent as asked. Call every tool in English or Polish: the search query and the place names in jurisdiction. A question normally takes two calls: search with the question and, when known, the user's voivodeship or commune as jurisdiction, then resolve the relevant concept_ids with the user's jurisdiction, today's date and the context the question implies. Call get_evidence only for a verbatim quote. Follow guidance_for_caller in every result. Scope: ...

- The `Scope: ` marker is kept; `roundtrip.py:73` parses it.
- The rules still come before the scope. A test keeps the text up to "Follow guidance_for_caller" under Claude Code's 2,048 characters.

**Resolve for Kraków:**

```json
{"executed_scope": {"country_code": "PL", "canton_code": "PL-12", "municipality_id": "PL-12-61-011",
                    "country": "Poland", "canton": "małopolskie", "city": "Kraków"},
 "results": [{"answering_jurisdiction": "PL-12-61-011 (commune of Kraków)",
              "basis": "Commune ordinance: Uchwała nr LI/1410/21 Rady Miasta Krakowa (...), § 13",
              "citations": [{"level": "voivodeship", "jurisdiction": "PL-12", "language": "pl", "...": "..."}]}],
 "guidance_for_caller": "... Tell the user that the statements are English summaries of Polish pages, not official translations. ..."}
```

**Other results:**

- **Gaps:**
  - "This concept is published for PL-12-61-011 (commune of Kraków), below the requested PL-12 (województwo małopolskie); add the voivodeship or the city if the user lives there, otherwise only broader concepts apply."
  - "... no procedure is published for that jurisdiction. National concepts still apply."
- **Place not recognised** (`city: "Bemowo"`): `not_recognised` is `{"city": "Bemowo"}`, and the guidance reads "The place register does not hold the city 'Bemowo', so this result is for PL (national). If the narrower place matters, resolve again with the official name of the commune (the gmina, not a city district (dzielnica or delegatura), a housing estate (osiedle), a village or a postcode) or with the voivodeship; ..."
- **Errors:**
  - "'Bolesławiec' fits several communes: Bolesławiec (gmina miejska) (PL-02-01-011) in powiat bolesławiecki (PL-02-01), Bolesławiec (gmina wiejska) (PL-02-01-022) in powiat bolesławiecki (PL-02-01); add the voivodeship, give the full name as listed or give the commune code."
  - "The commune does not lie in the given voivodeship: Kraków (PL-12-61-011) lies in małopolskie (PL-12), not in mazowieckie (PL-14); correct one of them, or give only the commune."
- `NEEDS_CONTEXT` carries no Czech example.
- The narrower caveat names "a voivodeship, county or commune part".

**Unchanged and already right for Poland:**

- `RESULT_LIMITATIONS_NOTE` ("English paraphrases of the cited excerpts");
- `jurisdiction_note` ("This server covers Poland (PL); omit country.");
- the coverage root, which has the same shape with `query_languages` `[en rank 1, pl rank 2]` and `languages` `["pl"]`.

**Known limitation.** The `outputSchema` descriptions in `tools/list` keep the Swiss examples (`LEVEL_DESCRIPTION`, `BASIS_DESCRIPTION`, `ExecutedScope`). They never appear in tool results, and the per-release resolve description names the levels and says which `executed_scope` field holds what.

### 8.2 Versions

| Artifact | Decision | Why |
| --- | --- | --- |
| Tool contract `swiss-tip/v4` and `tool-contracts.schema.json` | **Unchanged**; `--check` stays current | Per-release wording is served-time text, as the query-language note already is (tool-contracts 4.2); `region` is an existing alias; `not_recognised` is a free map; `level` and `basis` are free strings |
| `swiss-tip-release` | **v3 added**, written only with a hierarchy; Swiss builds keep v2; v1 and v2 readable | 2.3 |
| `swiss-tip-place-aliases` | **v2 added** (required `hierarchy`); v1 unchanged and must not carry it | An old builder would otherwise drop the hierarchy silently |
| `swiss-tip-places/v1` | Unchanged | TERC writes the same shape |
| `swiss-tip-curation/v1` | Unchanged version; additive optional fields | `extra="forbid"` makes old builders refuse them |
| Acceptance digest | Unchanged | No new step or case field |
| Semantic `qwen3-retrieval/v1` | Unchanged | Swiss readiness binds `query_prefix_sha256` |
| Dataset and connector v1 | Unchanged | No Polish dataset yet |
| Package versions, `SERVER_VERSION` | Not touched; bumped late by the user (a new release schema suggests 0.4.0) | The packs workflow installs PyPI 0.3.4 and cannot build `mvp-poland` until then; the Polish checks run locally against the checkout |

## 9. Admin console minimum

The reviewer confirms, flags and rejects through `writes.dry_build`, which works as soon as the build accepts PL. Editing is the problem: today it corrupts or crashes on Polish data. The minimum below must be in place before the review at the event.

1. **`screens/sources.py:233`: saving a Polish source fails with HTTP 500** (`KeyError` on `scope["canton_codes"]`). Branch on `scope["country_code"] == "CH"`, keeping the Swiss code verbatim. Otherwise, add a jurisdiction of depth 1 or more to `region_codes` unless a listed region already contains it, and never add the country.
2. **`screens/sources.py:28-31, 157-169`.** For a non-CH scope:
   - `AUTHORITY_LEVELS` comes from `scope.levels`;
   - `JURISDICTIONS` comes from the country, `region_codes` and the codes already used;
   - the prefill defaults come from `country_code`, `levels[0]` and `language_discovery.preferred_seed_language`;
   - the hint (`views/sources/form.html:46`) and the `bfs_code` fieldset (`:72-78`) are shown only for CH.
3. **`screens/workbench.py:141` `jurisdiction_choices`.** On a declared hierarchy, build the list from the curation's register (`place_register_for`): the country, the depth-1 regions, every code the facts use at any depth, and always the fact's current value. This stops the fact form from preselecting `CH` and silently rewriting `PL-12-61-011` on save. The Swiss list is unchanged.
4. **Default jurisdiction.** `workbench.py:383, 485, 525` and `views/documents/excerpt_body.html:32` default to the register's country code, which is `CH` for Swiss packs.
5. **`basis.py:10-11`.** `BASIS_KINDS` becomes `get_args(BasisKind)` (same order). `BASIS_LEVELS` becomes a function of the pack: `hierarchy_of(register).ids`. `citation_basis` passes `words=hierarchy.words` to the build. Without this, every Polish review card raises `KeyError`, because only `BuildError` is caught. `workbench.py:206` and `:438` use the per-pack ids.

Deferred:

- the coverage matrix (`data.py:28, 325`), which is empty for PL but harmless;
- the pack template (`packs.py:24`);
- `containment_sentence` (`workbench.py:150`);
- standing checks (`checks.py:25`);
- sandbox default arguments (`views/sandbox/index.html:53`);
- the console's use of the legacy `tokens` (`workbench.py:27`).

## 10. Tests and the Swiss before/after harness

### 10.1 Package tests (synthetic fixtures only, no network)

**The synthetic Polish register** is inline in each package's tests, or a JSON fixture where a file is needed. It carries the hierarchy of 2.1 and the generic words of 2.1.

| Code | Name (aliases) |
| --- | --- |
| PL | Poland (Polska) |
| PL-02 | dolnośląskie (Lower Silesia) |
| PL-02-01 | powiat bolesławiecki |
| PL-02-01-011 | Bolesławiec (gmina miejska) |
| PL-02-01-022 | Bolesławiec (gmina wiejska) |
| PL-10 | łódzkie |
| PL-10-61 | powiat m. Łódź |
| PL-10-61-011 | Łódź |
| PL-12 | małopolskie (Lesser Poland, Małopolska) |
| PL-12-06 | powiat krakowski |
| PL-12-06-xxx | a rural commune of powiat krakowski (synthetic code) |
| PL-12-61 | powiat m. Kraków |
| PL-12-61-011 | Kraków (Cracow) |
| PL-14 | mazowieckie (Masovia) |
| PL-14-65 | powiat m. st. Warszawa |
| PL-14-65-011 | Warszawa (Warsaw) |

**The synthetic Polish release** (v3) has:

- facts at `PL` (a national act), at `PL-12-61-011` (Kraków guidance) and at `PL-14-65-011`;
- a county fact at `PL-12-61`, which must reach Kraków;
- a county fact at `PL-12-06`, which must not reach Kraków, because that would be sideways;
- English statements, synthetic Polish excerpts, English aliases, Polish source terms, and `query_languages` and `evidence_languages` declared.

| Package | New tests | Swiss pins kept untouched |
| --- | --- | --- |
| core `test_hierarchy.py` (new) | **Literal-copy tests:** every legacy-rendered template (instructions, both place guidances, the narrower caveat, the four gap sentences, the three place errors, the labels, the containment sentence, the level list) equals a pasted copy of today's constant. `SWISS.pattern is validation.JURISDICTION`. `generate_wording` on the `SWISS` levels equals `LEGACY_WORDING`, except `context_example` and `ambiguity_tail`. The declared pattern accepts PL, PL-12, PL-12-61 and PL-12-61-011, and rejects PL-12-61-11, PL-1 and CH-ZH. `level_id`, `region_of`, `label` | - |
| core `test_places.py` | `Kraków`, `Krakow`, `Cracow`, `PL-12-61-011`, `pl-12-61-011` and `12-61-011` give PL-12-61-011 with PL-12 derived; region `PL-12`, `12`, `Małopolska`, `województwo małopolskie`, `Lesser Poland`; `m. st. Warszawa` and `Warsaw`; `Łódź` and `Lodz` meet only with `pl` letters, while `place_key("Wrocław")` without letters is still `wrocaw`; Bolesławiec ambiguity names both qualified candidates; `Bolesławiec (gmina wiejska)` resolves; Kraków with region PL-14 gives the wrong-region error; `powiat krakowski`, `PL-12-61`, `1261011` and `Bemowo` as city are not recognised; zeros kept; four labels | all existing tests, including `zh-0069`, Buchs and the four labels (`:26-104`) |
| core `test_release.py` | Absence list gains `hierarchy`, `query_languages` and `evidence_languages`; the PL release validates; each hierarchy issue; v3 gating both ways; an unknown basis level is an issue, not a crash; institution level versus depth; "is national but rests on a commune basis"; an English excerpt refused by `evidence_languages`; a fact language outside `query_languages` refused; Polish labels round-trip through the validator | `:111-117`, `:130-172`, `:186-241` |
| core `test_text.py` | `fold` without letters equals today's function over a Unicode sample; `fold("Wrocław", LETTERS["pl"]) == "wroclaw"` | - |
| core `test_contracts.py` | Each anchor occurs exactly once; **`schema_bundle()` equals `docs/architecture/tool-contracts.schema.json`** (the `--check` the docs promise, run as a test) | bundle |
| runtime `test_service_poland.py` (new) | Instructions: "official Polish sources", "voivodeship or commune", "English or Polish", "translated into English", "Call every tool in English or Polish", no "Romansh", `Scope: ` present, under 2,048 characters up to "Follow guidance_for_caller"; `tokens("Jak złożyć wniosek w Krakowie?")` with the profile drops `jak` and `w` and keeps `zlozyc`; the county fact at PL-12-61 reaches Kraków, the one at PL-12-06 does not; gap texts; NEEDS_CONTEXT without the Czech example; tool descriptions and input schema per 4.2; disclosures "Polish pages"; `get_coverage` query languages `[en, pl]` with nothing partial | - |
| runtime `test_service.py` | **Swiss guard:** for the CH fixture, `tool_description(n) == TOOL_DESCRIPTIONS[n]` for every n except the search language swap; the jurisdiction part descriptions equal `Jurisdiction`'s; `service.text is LEGACY_TEXT`; `tokens("co ten we")` unchanged | every literal pin (`:332-338, 390-446, 556-660, 864-1000, 1088-1092, 1122`) |
| runtime `test_acceptance.py` | PL cases: `expect_scope: PL-12-61-011`; `expect_not_recognised: [canton]` for `canton: "Mazowsze"` misspelt; `expect_error: jurisdiction.city` for Bolesławiec | `:52-99` including the digest |
| mcp-server | `tests/fixtures/release-pl.json` (synthetic); a stdio `roundtrip()` on it passes; instructions over stdio; resolve by name | `fixtures/release.json` byte-identical (quickstart compares it) |
| build `test_release_build.py`, `test_questions.py` | Aliases v2 embeds the hierarchy; v1 with a hierarchy is refused; a v1 pair gives no `hierarchy` key; `§` derivation only when declared; unknown level and missing catalogue jurisdiction raise `BuildError` on the declared path; `evidence_languages` refuses an English record; v3 only when declared; Polish questions detected, ties give None; the POLISH, ENGLISH and GERMAN sets are disjoint | existing place-file, label and question tests (`:74-370`) |
| ingestion `test_places.py` | Inline synthetic TERC text: BOM, CRLF, `;`, an upper-case voivodeship, a land county, city county 61 with its 011 commune, an urban/rural pair, a RODZ 3/4/5 triple, one RODZ 8 and one RODZ 9 row; every refusal; qualified names; counts | the four BFS tests unchanged |
| admin console | A PL fixture pack: source save (no 500); fact form keeps `PL-12-61-011`; basis levels are the PL ids; new facts default to `PL`; `citation_basis` labels a Polish citation | existing Swiss console tests |
| knowledge-builder | A PL pipeline fixture with the two PL place files; the Swiss `build_inputs` digest unchanged | `test_pipeline.py:208-236` |

### 10.2 The before/after capture (packs repository)

The harness reads real packs, so it lives in swiss-tip-mvp. It **extends the existing `scripts/test/baseline/snapshot_results.py`** (149 lines, untracked, written on 27 September) rather than starting a new one. That script already records, in-process with `ReleaseService`, one JSON line per request with the exact server payload (`model_dump(mode="json", exclude_none=True)`, then `json.dumps(ensure_ascii=False, separators=(",", ":"))`):

- instructions with coverage listed and hidden;
- each tool's description, input schema and output schema;
- `tools`, `health`, the coverage root and every topic;
- every acceptance and regression search and resolve step at the suite's policy date;
- `get_evidence` for every served excerpt;
- 17 probes (Polish places, `ł` names, malformed requests).

It also compares with `--compare` and exits 1 on any difference.

To add:

1. **A header:** swiss-tip git sha; package, pydantic, mcp and Python versions; `release_sha256`, `content_sha256`, suite digests and index sha. `--compare` refuses unless both headers name the same release, suites and index.
2. **Register sweep:** every place's official name, each alias, the bare name, `<name> <canton abbreviation>`, `CH-ZH-261`, `ZH-261`, `zh-0261`, and the bare number next to its canton.
3. **Error probes:**
   - ambiguous city (Buchs);
   - city outside the given canton (Winterthur with BE);
   - unknown country;
   - flattened top-level parts;
   - every alias field name (`region`, `state`, `commune`, `town`, `municipality`, `canton_code`, `municipality_id`).
4. **Concept by place matrix:** every concept at its own jurisdictions and at CH, CH-BE, CH-BE-351, CH-ZH-230, CH-GE-6621, CH-TI-5192 and DE. Each is run with and without context, at `stale_from`, before `valid_from`, and with `reviewed_only`.
5. **Raw questions:** every case question as asked, including Q-PL-1, plus "Jak długo trwa pozwolenie w Zurychu?", "Łódź opłata", city "Łyss" and "Łausanne".
6. **Hybrid mode:** with Ollama running (`qwen3-embedding:0.6b`, digest `ac6da0df...`), a recording embedder stores `{sha256(prefix + query): vector}` once in `.local/baseline/query-vectors.json`. A replay embedder serves them offline and **raises on a miss**, never falling back to lexical. Search families run lexical and replayed-hybrid.
7. **Stdio meta:** one server session per pack, with `--with-coverage` and without, capturing `initialize.instructions` and the `tools/list` JSON without `serverInfo.version`.
8. **Controls:** an explicit `as_of` on every call; `checked_at` and `serverInfo.version` dropped; the after-capture run once with `PYTHONHASHSEED=0` and once with a random seed.

Measured on the prototype: 6,031 Zurich calls in 24-29 s, and 2,524 Wallisellen calls in 1 s, byte-identical across processes. With the additions the estimate is about 15,000 calls in about 60 s.

**Target: no difference at all.** There is no allowlist. Any Swiss difference blocks the next step until it is removed.

**Informative run, not a gate.** Embed a declared copy of the `SWISS` hierarchy into each Swiss release in memory (v3, digest recomputed) and capture again. The labels, gaps, narrower caveat, instruction place words and name-based resolves must be identical. The expected differences are exactly the declared-path design choices:

- no zero-stripping and no bare numbers;
- no Czech example;
- the ambiguity tail;
- generated tool descriptions.

This shows that the declared path is a complete description of a three-level country.

### 10.3 Other proofs, run after every step

- `python -m swisstip.core.contracts --check docs/architecture/tool-contracts.schema.json`.
- **Rebuild proof** (new `scripts/test/baseline/rebuild_swiss.py`, local only, because the text datasets are in `.local`):
  - Build `mvp-zurich` and `mvp-wallisellen` in memory from `curation.yaml` and `.local/<pack>/text`.
  - Pin `created_at`, `acceptance_suite_sha256` and `schema_version` from the committed manifest (Wallisellen is v1).
  - Require the bytes to equal `release.json`, and the sha to equal `readiness.release_sha256`. Zurich is byte-identical today in 16 s.
  - Also check `dump_release(load_release(p)) == bytes`.
- **Pack tests:** `python -m unittest discover -s scripts/test/packs` (17 tests, about 60 s), `check_server.py` and `check_wallisellen.py`.
- **Regression replay:** `run_regression.py --lexical-only --no-write`, compared with the committed `runs.lexical`, ignoring `checked_at` (725 of 725 records today).
- **Package tests** of swiss-tip: core, runtime, mcp-server, build, ingestion, admin console and knowledge builder.

Without Ollama the whole proof takes about 3 minutes, plus about 5 minutes once to record the vectors.

**CI caveat.** `knowledge-bases.yml` installs PyPI 0.3.4, so it proves nothing about this code. The proof runs locally against the editable checkout.

## 11. Ordered work breakdown

Every step ends with its package tests, the capture compare, the rebuild proof and the pack tests. A Swiss difference blocks the next step.

| # | Step | Sites | Estimate | Risk and guard |
| --- | --- | --- | --- | --- |
| 0 | Extend the baseline harness (10.2) and write `rebuild_swiss.py`; capture the baseline **before any code change**; record the query vectors with Ollama | swiss-tip-mvp `scripts/test/baseline/*` | 0.25 d | Low. Without it nothing proves identity |
| 1 | Release models: `PlaceLevel`, `PlaceHierarchy`, `hierarchy`, `query_languages`, `evidence_languages`, the v3 literal and rule, `str` levels; `core/hierarchy.py` with `SWISS` | `core/release.py:40-101, 233-262`; `core/__init__.py:6`; new `core/hierarchy.py` | 0.5 d | **High impact:** a leaked default changes every Swiss digest and the server refuses to start. Guard: `exclude_if`, the absence list, the round trip |
| 2 | `basis.py` words and `level_of_jurisdiction(hierarchy)`; `validation.py` threading and declared checks | `core/basis.py:16-81`; `core/validation.py:20-227` | 0.5 d | Medium: the label recomputation must stay byte-identical. Guard: `test_release` label pins; both attested releases validate empty |
| 3 | `text.fold(letters)`; the `places.py` generalisation (tables, keys, labels, resolve, parsing with the built-in branch verbatim, templated messages) | `core/text.py:8`; `core/places.py:61-234` | 0.75 d | High: runs on every Swiss place request. Guard: `test_places` untouched, literal-copy tests, register sweep and error probes |
| 4 | Service: `TextProfile`, Polish stopwords and facets, declared query languages, `LANGUAGE_NAMES`, `QUERY_LANGUAGE_TOOLS`, `Wording` templates, declared-path descriptions and input schema; `contracts.py` anchors | `runtime/service.py:48-237, 297-354, 388-496, 589, 616, 703, 762-832, 896-921`; `core/contracts.py:71-87, 607-692` | 1 d | High: served text. Guard: literal pins, the Swiss guard test, the full capture, `--check` |
| 5 | Build: curation fields, `questions.py`, aliases v2 loader with letters, levels and words, catalogue defaults on the declared path, evidence-language refusal, manifest fields and version, declared checks; `§` and `Załącznik` patterns (cuttable) | `build/curation.py`, `questions.py`, `places.py:31-97`, `release_build.py:154-418`, `case_catalogue.py:45` | 0.5 d | Medium. Guard: Zurich rebuild byte-identical, Wallisellen content-identical with v1 pinned |
| 6 | TERC importer, CLI and tests; dry run on the real CSV (16 / 380 / 2,479, dropped per RODZ); draft `pl-aliases.json` kept in `.local` | `ingestion/places.py`, `tests/test_places.py`, `catalog.py:30` | 0.5 d | Low: a separate path. Name decisions (Q3) are settled before the first committed import |
| 7 | Admin console minimum (section 9) | `screens/sources.py:28-31, 157-169, 233`; `screens/workbench.py:141, 206, 383, 438, 485, 525`; `basis.py:10-11`; `views/documents/excerpt_body.html:32` | 0.5 d | Low to medium: a missed default silently rewrites `PL` to `CH`. Guard: PL console fixture; Swiss console tests untouched |
| 8 | Synthetic Polish end to end: fixture release, build, validate, stdio serve, acceptance, round trip, replayed hybrid; measure `pl-light` against prefix-6 and record the decision | fixtures in core, runtime, build and mcp-server | 0.5 d | Medium: Polish recall. Guard: Polish questions found in both modes |
| 9 | Full Swiss proof (10.2-10.3), then docs: `release-format.md` (hierarchy, v3, languages, aliases v2), `tool-contracts.md` (section 2 on the Polish mapping of `ExecutedScope`, 4.2 on declared languages), `knowledge-base-pipeline.md:296-302`, the ingestion and build READMEs, the plan's Status | docs | 0.5 d | None |

**Total:** about 5.5 person-days, for Monday 28 September to Friday 2 October plus today.

- **Critical path:** 0, 1, 2, 3, 4, 5, 8, 9, about 4.5 days. Steps 6 and 7 run in parallel after step 2.
- **Serving slice**, if time runs short (steps 0 to 5 plus the importer dry run, about 3.5 days): a Polish release that builds, validates and answers correctly.
- **Cut order:**
  1. the `§` and `Załącznik` patterns;
  2. the informative declared-SWISS run;
  3. the `pl-light` measurement (prefix-6 stays);
  4. console items 2 and 4 (items 1, 3 and 5 are needed for the review);
  5. the stdio meta capture.
- **At the event** (data, proof of concept; the Swiss packs are not touched):
  1. Run the importer and commit `pl-register.json` and `pl-aliases.json`.
  2. Write the curation header (2.4).
  3. Curate the participatory budget: English statements, Polish source terms and per-citation norms.
  4. Turn PL-CIV-1 to 13 into `acceptance.yaml`.
  5. The user reviews every fact.
  6. Build, index, run the acceptance suite, attest readiness.
  7. Then the local initiative and the consultations.

## 12. Open questions

Each question has a default, and none blocks steps 0 to 5.

| # | Question | Default | Alternative and its cost |
| --- | --- | --- | --- |
| Q1 | Release schema for declaring releases | `swiss-tip-release/v3` (2.3) | Additive fields under v2: old servers still fail closed through `extra="forbid"`, but plain-JSON readers misread levels |
| Q2 | Advertised request part for the voivodeship | Keep `canton` in the input schema, with a Polish description that says `region` is accepted; `not_recognised` keys, error paths and acceptance parts stay `canton` | Advertise `region` on the declared path: rename the served property, the `not_recognised` key and the `PlaceError` path, and extend `core/acceptance.py:133`. About 0.25 d, and a second declared code path in `places.py`, `service.py` and `acceptance.py` |
| Q3 | Echoed place names (settle before the first import) | Voivodeship `małopolskie` (official lower-case form) with label `województwo małopolskie`; county `powiat krakowski`, `powiat m. Kraków`, `powiat m. st. Warszawa`; commune `NAZWA`, qualified by `NAZWA_DOD` only for same-county pairs | English-style `Małopolskie Voivodeship`, title-cased per hyphen part; or an optional `Place.kind` field in place of qualified names |
| Q4 | Translation target | English first: `query_languages: [en, pl]` | Polish first; this is curation data and can flip after the Polish regression pack is measured |
| Q5 | Polish sample questions | Optional (`question_languages: [en]`), following the user's "English for questions" | `[en, pl]`: the build then requires a Polish question on every concept, at a curation cost at the event |
| Q6 | Stemming for `pl` | Prefix-6; `pl-light` only if measured better | A dictionary stemmer: a new dependency, not planned |
| Q7 | Warsaw dzielnice and Kraków delegatury | Neither places nor aliases; the commune note steers the caller to the city | Aliases of their commune, which makes "Bielany" ambiguous with PL-14-29-022 |
| Q8 | County echo and county-level concepts | Deferred: no county-level facts in the first slice, so the round trip holds | Optional `county_code` and `county` on `Scope` and `ExecutedScope`: additive under v4 and dropped by `exclude_none` for Swiss, but it changes the Swiss `outputSchema` and regenerates the bundle. Also accept county codes in `city` and make the round trip depth-aware |
| Q9 | Other-language example in the language note | None on the declared path | Add `uk` (and `ru`) through an optional declared list |
| Q10 | Product name in the Polish instructions | "Swiss TIP serves ... official Polish sources" | A declared product name; the plan's section 7 wants another name for the Polish deployment. About 1 hour |
| Q11 | Embedding query prefix | Unchanged ("Swiss public-information concepts") for the Polish index | A country-neutral prefix as a new `query_version`, keeping the Swiss one; needs a rebuilt index and a measurement |
| Q12 | Output-schema descriptions | Static Swiss wording, documented | Per-release overrides of the `outputSchema` texts; `tools/list` only, about 0.5 d |
| Q13 | Postal codes and Polish datasets | Deferred | Per-country postal pattern `NN-NNN` in `core/datasets.py:31-32`, `core/contracts.py:413`, `build/datasets.py:36`, `core/acceptance.py:153`; changes the lookup input schema |
| Q14 | Tokenisation in the admin console | Legacy `tokens` (`workbench.py:27`) | Pass the pack's text profile; console-only effect |
