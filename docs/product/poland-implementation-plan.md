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

The target event is HackYeah (3-4 October 2026, Kraków). Two of its tasks
match the pack: the open task **Smart City** ("technology that helps cities
work better", with communication with citizens, public services and access
to information named in the brief) and the partner task **HubMI.pl**
("connect residents' needs more effectively with knowledge, proven
solutions, and people ready to take action"). The event allows one project
per task and discourages entering one project in two tasks, so the pack
enters one of them (see "Decisions" and "Demo").

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

## Decisions

| Decision | Decided | Why it matters |
| --- | --- | --- |
| Code format of places | `PL`, `PL-12` (voivodeship), `PL-12-61` (county), `PL-12-61-011` (commune: the last three digits of its seven-digit TERYT code, the commune number and its type digit) | One segment per level, so the level is the depth of the code; the type digit separates the urban and rural parts of one commune; containment stays a prefix test. The source catalogue accepts this format |
| Request parts | `country`, `region`, `city` as today (`region` is already an accepted alias of `canton`); the county is derived from the city | Residents name their city, rarely their county; the tool contract stays small |
| Languages | Evidence in Polish only: every excerpt is cut from a Polish official page. Statements, summaries, aliases and sample questions in English, as editorial translations of the Polish excerpts; source terms and sample questions in Polish too. The release names English and Polish as its query languages, and the server tells the calling model to call the tools in English or Polish | The excerpt stays the authoritative text; English statements keep one wording across the Swiss and Polish packs, and a caller can search in either language |
| Scope of the first release | The civic-participation topics for the three cities, the participatory budget first | They match the HubMI.pl partner brief and carry the strongest trap questions |
| HackYeah | Open-source code written before the event is allowed when it is fairly cited in the presentation and the code, and AI tools are credited (FAQ 13 and 14 of hackyeah.pl, verified on 27 September 2026); the core idea and the final solution must be the team's own. So the software changes of this plan are prepared in advance, and the Polish data is built during the event as a proof of concept | The code must be ready and tested before 3 October 2026; the pack's curation, review and attestation happen at the event; the slides and the README credit Swiss TIP as the base and the tools used |
| Entry | Open, decided by the morning of 3 October: one project per task, and the organisers strongly discourage one project in two tasks. Proposed: the open task Smart City, whose brief, criteria (Idea & Innovation 30 %, Relation to Category 20 %, Practical Applicability 20 %, Design 20 %, Completeness 10 %) and language rules are known now. HubMI.pl (15 000 PLN, "create a concept") publishes its own rules, jury and any rights transfer no later than 3 October; a second entry there would have to be a distinct product, for example an assistant that routes a resident's idea to the right instrument of their city | The jury scores the relation to the chosen task at 20 %, and the description, the slides and the screenshots are written for one brief |
| Reviewer | The same named person who reviews `mvp-zurich` | Readiness records who confirmed each statement |

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
3. Curation, with English statements summarising the Polish excerpts and
   Polish source terms copied from them; institutions and basis per
   excerpt.
4. Review of every fact by a named Polish-speaking reviewer.
5. Build, acceptance, regression pack, semantic index, readiness
   attestation; `COVERAGE.md` and `LIMITATIONS.md` extended to the pack.

### 7. Demo

- The pack image and the slim image with the sidecar, as for `mvp-zurich`,
  under a name chosen for the Polish deployment: "Swiss TIP" and "Grounded.
  In Switzerland." are the Zurich pitch, not Kraków's.
- A side-by-side of a generic assistant and an assistant with the server on
  the draft questions, the same way the Swiss cases were graded, recorded
  as screenshots and a 60-second video: the open-task jury scores Design
  and Practical Applicability at 20 % each, and the server has no interface
  of its own, so the chat client of the demo image is what the jury sees.
- The civic-participation topic as the HubMI.pl material, if that task is
  entered: the participatory budget and the local initiative per city, with
  dates and offices.
- What HackYeah requires, verified on 27 September 2026 on hackyeah.pl (the
  rules of 7 July 2026, the FAQ and the participant guide):
  - The submission goes to the HackTribe platform: a title of at most five
    words and a description of at most 500 words, both in English; at least
    one image; a presentation of at most ten slides as PDF, in English;
    optionally a 60-second video, a demo link and one repository the jury
    can open. The description names every team member.
  - Deadlines: the participant guide says a draft by Saturday 3 October at
    20:00 and the final submission by Sunday 4 October at 12:00; the FAQ
    and the rules say Sunday at 23:00. Plan for noon and confirm on site.
    Nothing may change after the deadline.
  - The full task text is revealed when coding starts; the rules of the
    partner tasks are published no later than 3 October. Finalists pitch to
    the jury, one presenter per team. A jury awards nothing below 50 % of
    the points.
  - Previously written code, external resources and AI tools are allowed
    and must be cited in the presentation and the code; a partner task may
    require the winners to transfer the rights to their solution.

## Order and the smallest useful slice

Workstreams 1 to 3 block the pack; 4 and 5 improve it. The smallest slice
that serves a real question is: the hierarchy (1), the three cities in the
place register (2, from a hand-checked extract of TERYT if the importer is
not ready), `ł` folding and Polish stopwords (3), and one topic of the pack
(the participatory budget) curated, reviewed and attested (6). The rest
follows topic by topic. Workstreams 1 to 5 are code and are done before
the event; step 6 is the event's proof of concept.

## Risks

| Risk | Mitigation |
| --- | --- |
| A change to validation or the place index alters a Swiss answer | The Swiss acceptance and regression packs replay on every change; a Swiss difference blocks |
| Polish inflection makes lexical search miss | Aliases in users' words, sample questions in Polish, hybrid search; the regression pack measures it |
| City pages change often (fees, dates, zones) | Validity windows on facts and the freshness policy; a stale release says so |
| The reviewer reads Polish excerpts against English statements | Each statement is checked against its Polish excerpt, which stays the authoritative text; the release states its review counts, and the demo claims only what was reviewed |
| Official hosts behind a bot challenge: `um.warszawa.pl` and its document and district servers answer every request, robots.txt included, with a JavaScript challenge, so the crawler's robots check fails closed | The pages stay catalogued as `manual_adapter_required`, and the download fetches them with `--browser-host`; a host that changes its challenge can break that session, so the Warsaw facts that the Public Information Bulletin or the voivodeship's journal also state are cited there |
| Design and usability are 40 % of the open-task score, and the server has no interface of its own | The chat client of the demo image, the screenshots and the 60-second video are prepared with the pack, and the description and the slides are written for the chosen task's brief |
| Expected answers written on 27 September 2026 go stale before the event: Kraków's voting closed on 28 September, Katowice's consultation on 29 September, and its 2027 local-initiative call opens on 1 October | The acceptance-test document restates the date-bound cases as of 3 October 2026 before they become claims, and every replay states its date in the request |
