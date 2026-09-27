# Swiss TIP for Poland - implementation plan

**Last update:** 27 September 2026

How the server and the pipeline are taken to a second country: a pack of
official public information for everyone who lives in a Polish city,
`mvp-poland` in the packs repository. The pack's scope, draft questions and
candidate sources are in its README (`releases/mvp-poland/README.md` of
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp)). The code serves
Swiss releases only; what is already built is listed under "Status".

## Status

Implemented and tested:

- **Source catalogue.** `ingestion/catalog.py` accepts a catalogue of
  another country that declares its levels and the regions it covers; a
  source's authority level is the level of its code's depth. Swiss
  catalogues are checked exactly as before.
- **Acquisition of Office documents.** The downloader saves DOCX, DOC and
  RTF responses, which Polish city offices publish resolutions as.
- **Hosts behind a bot challenge.** `swisstip-download --browser-host HOST`
  makes every request, robots.txt included, with the crawler's own HTTP
  client and the cookies of a browser session, and opens a headless browser
  only when a response is a JavaScript challenge; the challenge page may
  reach nothing but its robots.txt and AWS WAF's token service. The saved
  bytes stay the server's response and every crawler rule still applies
  (`ingestion/browser.py`, optional Playwright dependency).
- **Empty responses.** An empty body served with success is flagged
  `empty_response`, is refetched by `--retry-failed` like an error page
  served with success, and is a retriable gap in the gap report.
- **Language hints.** `pl` is a catalogue language (with `uk`), and the
  extraction reads a `pl` path segment or file-name suffix as a language
  hint.

Done in the packs repository: the civic-participation topics (the
participatory budget, the local initiative and grants for NGOs,
consultations and the resident's voice) have a source catalogue, a
downloaded run and an acceptance-test document
(`docs/product/poland-civic-acceptance-tests.md` of swiss-tip-mvp).

Everything else below is planned.

## Goal

Serve the questions whose answer depends on where a resident lives - the
waste fee, the clean transport zone, the anti-smog rules, the winter school
holidays, the participatory budget - for the cities of Kraków, Warsaw and
Katowice, with the same guarantees as `mvp-zurich`: every fact rests on an
excerpt of an official page, is applied by containment to the resident's
place, is reviewed by a named person, and the release passes its acceptance
suite and readiness gates.

The target events are the HackYeah challenges in the Smart City category
and on connecting residents' needs with knowledge and people (3-4 October
2026, Kraków).

## Principles

1. **Switzerland keeps working unchanged.** Every attested Swiss release
   validates and answers exactly as before, byte for byte in its results.
   The Swiss packs are the regression test of every step below.
2. **The country is data of the release, not code.** Levels, their names,
   the code format and the place register travel in the release, as the
   place register already does. The code knows the shape of a hierarchy,
   not the Swiss or the Polish one.
3. **The pipeline stays the only way knowledge changes.** The Polish pack is
   built, reviewed and attested with the same stages as `mvp-zurich`; no
   step is skipped for speed.
4. **Tests use a synthetic Polish pack**, never the real one (see
   `AGENTS.md`).

## Decisions before the first line of code

| Decision | Proposal | Why it matters |
| --- | --- | --- |
| Code format of places | `PL`, `PL-12` (voivodeship), `PL-12-61` (county), `PL-12-61-011` (commune: the last three digits of its seven-digit TERYT code, the commune number and its type digit) | One segment per level, so the level is the depth of the code; the type digit separates the urban and rural parts of one commune; containment stays a prefix test. The source catalogue accepts this format |
| Request parts | `country`, `region`, `city` as today (`region` is already an accepted alias of `canton`); the county is derived from the city | Residents name their city, rarely their county; the tool contract stays small |
| Language of statements | Polish, with English aliases and sample questions | The audience is the whole population; `mvp-zurich` states facts in English |
| Scope of the first release | The seven draft questions of the pack README, for three cities | Every question has at least two different answers in the pack |
| HackYeah rules | Read the participant manual on code written before the event | Decides whether the product is presented as the team's prior open-source work or rebuilt on site |

## Workstreams

### 1. Country-neutral hierarchy in the core

The code assumes three levels: a code `CC[-XX[-N]]`, the level as the number
of dashes, the names federal, cantonal and municipal. Containment itself is
already a prefix test and works at any depth.

- **Jurisdiction codes.** Replace the fixed `^CH...` patterns in
  `core/validation.py` and `core/datasets.py` by a pattern taken from the
  release's place register (country prefix, segment formats, depth).
- **Levels.** Replace `InstitutionLevel` (`core/release.py`) and the level
  word maps (`core/basis.py`, `concepts/schemas.py`, the admin console) by
  levels the release declares, each with a depth and a name. A Swiss
  release declares federal, cantonal and municipal and keeps its output.
- **Place index.** Generalise `core/places.py` from three fixed levels to
  the declared depth: the per-level indexes, `level_of`, `Scope` (a county
  slot), `resolve` (derive every intermediate level from the city), the
  labels ("canton of", "municipality of") from the level names.
- **Level of a jurisdiction.** `core/basis.py` `level_of_jurisdiction` reads
  the depth from the declared levels, so a county is not reported as a
  municipality.
- **Defaults.** Remove the silent `"CH"` defaults of a fact's jurisdiction
  in `build/curation.py` and `build/release_build.py`; the TERYT codes keep
  their leading zeros where the build now applies `int()`.
- **Replay.** `runtime/acceptance.py`, `core/acceptance.py` and the
  server's round trip take the request parts from the release instead of
  country, canton and city.

**Done when** the Swiss packs replay unchanged and a synthetic four-level
pack validates, resolves by containment and reports its gaps.

### 2. TERYT place register

- An importer for the GUS TERYT register (TERC) next to the BFS importer in
  `ingestion/places.py`, writing the same place files under
  `config/places/` of the packs repository: official names, the Polish
  generic words (`województwo`, `powiat`, `gmina`, `miasto`, `m. st.`),
  and aliases (`Warszawa`/`Warsaw`, `Kraków`/`Krakow`/`Cracow`).
- Cities with county rights (Kraków, Warsaw, Katowice) are both a county
  and a commune; the register states both and the index resolves the city
  to the commune.

**Done when** `Kraków`, `Krakow`, `Warszawa`, `Warsaw` and `Katowice`
resolve to their communes with voivodeship and county derived, and a name
two communes share is rejected with both candidates.

### 3. Polish text

- **Folding.** `core/text.py` `fold` keeps `ł`, and the place index and the
  search tokeniser keep only `[a-z0-9]`, so `Wrocław` and `Łódź` are cut in
  two. Fold `ł` to `l`; it is the only Polish letter NFKD does not
  decompose.
- **Search.** Polish stopwords in `runtime/service.py`, and a measured
  answer to inflection (`opłata`, `opłaty`, `opłatę`): the current
  six-character prefix stem, a Polish stemmer, or aliases alone, compared
  on a Polish regression pack lexically and in hybrid mode.
- **Languages.** `pl` is in the language lists of the catalogue and the
  extraction (done). Still to do: sample questions (`build/questions.py`,
  `build/curation.py`) and the console, and replace `NATIONAL_LANGUAGES` in
  the server by the languages the release declares.
- **Page hygiene.** Polish error, maintenance and furniture headings in
  `ingestion/acquisition.py`, `extraction/html_blocks.py` and
  `extraction/sections.py`.

**Done when** the synthetic pack's Polish questions find their concepts in
both search modes and the Swiss regression pack is unchanged.

### 4. Legal basis for Polish sources

- Basis labels from the declared levels and a Polish kind vocabulary:
  `ustawa`, `rozporządzenie`, `akt prawa miejscowego` (a voivodeship
  assembly's or commune council's `uchwała`), authority guidance, portal
  summary. `core/basis.py` `basis_label` and the validator's label check
  follow.
- Article locators for `§` sections as well as `Art.` in
  `build/release_build.py`.
- A Polish version of the basis classification prompt
  (`concepts/prompts/basis_classification_v1.md`) with Polish examples.
- No plugin is needed for the acts: ISAP and dziennikustaw.gov.pl disallow
  every crawler in robots.txt, but the Sejm's ELI API
  (`api.sejm.gov.pl/eli/acts/DU/<year>/<position>/text.pdf`) serves the same
  Dziennik Ustaw PDFs and publishes no robots.txt; the catalogue cites the
  acts there. City council resolutions are cited as published in the
  voivodeship's official journal.

### 5. Contracts and served text

- The server instructions, tool descriptions and guidance in
  `runtime/service.py` and `core/contracts.py` name Switzerland, cantons,
  Zurich examples and a Czech citizen. Take the country, the level names and
  the examples from the release manifest, so a Polish server speaks of
  voivodeships and communes.
- Postal codes: the four-digit pattern in `core/datasets.py`,
  `core/contracts.py`, `build/datasets.py` and `core/acceptance.py` becomes
  per country (`NN-NNN` in Poland), for a later waste-calendar connector.
- The embedding query prefix in `runtime/semantic.py` names "Swiss"
  concepts; a country-neutral prefix needs a rebuilt index and is measured.

### 6. The pack

In the packs repository, with the existing pipeline:

1. The acceptance-test document: the questions, their expected answers per
   city and the traps, before any page is fetched.
2. `sources.json` for Kraków, Warsaw, Katowice, their voivodeships and the
   national sources; the bounded download into `.local/mvp-poland/`.
3. Curation, with Polish statements and source terms copied from the
   excerpts; institutions and basis per excerpt.
4. Review of every fact by a named Polish-speaking reviewer.
5. Build, acceptance, regression pack, semantic index, readiness
   attestation; `COVERAGE.md` and `LIMITATIONS.md` extended to the pack.

### 7. Demo

- The pack image and the slim image with the sidecar, as for `mvp-zurich`.
- A side-by-side of a generic assistant and an assistant with the server on
  the draft questions, the same way the Swiss cases were graded.
- The civic-participation topic as the answer to the second challenge: the
  participatory budget and the local initiative per city, with dates and
  offices.

## Order and the smallest useful slice

Workstreams 1 to 3 block the pack; 4 and 5 improve it. The smallest slice
that serves a real question is: the hierarchy (1), the three cities in the
place register (2, from a hand-checked extract of TERYT if the importer is
not ready), `ł` folding and Polish stopwords (3), and one topic of the pack
(the waste fee) curated, reviewed and attested (6). The rest follows topic
by topic.

## Risks

| Risk | Mitigation |
| --- | --- |
| A change to validation or the place index alters a Swiss answer | The Swiss acceptance and regression packs replay on every change; a Swiss difference blocks |
| Polish inflection makes lexical search miss | Aliases in users' words, sample questions in Polish, hybrid search; the regression pack measures it |
| City pages change often (fees, dates, zones) | Validity windows on facts and the freshness policy; a stale release says so |
| No Polish-speaking reviewer | The release states its review counts; readiness does not require review, but the demo claims only what was reviewed |
| The event's rules exclude code written before it | Decided before the event (see "Decisions") |
| Official hosts behind a bot challenge: `um.warszawa.pl` and its document and district servers answer every request, robots.txt included, with a JavaScript challenge, so the crawler's robots check fails closed | The pages stay catalogued as `manual_adapter_required`, and the download fetches them with `--browser-host`; a host that changes its challenge can break that session, so the Warsaw facts that the Public Information Bulletin or the voivodeship's journal also state are cited there |
