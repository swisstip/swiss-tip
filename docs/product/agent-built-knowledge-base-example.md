# Building a knowledge base with AI agents - a worked example

**Last update:** 22 September 2026

A complete, copy-pasteable walkthrough of the
[knowledge base pipeline](../architecture/knowledge-base-pipeline.md) run by
AI agents, with one human in the loop. It uses a topic the served packs do not
cover - **self-employment and founding a business in Switzerland, with the
Canton and City of Zurich** - and shows, for every step, the prompt that drives
the agent, what the agent produces and what the pipeline checks.

The example is written for someone who wants to reproduce the route, not for
someone who wants to read about it. Every command is a real command of this
repository and every file a real pipeline artefact. The pack in the example is
called `mvp-selfemployed` and nothing in it exists yet: this document is the
plan an agent would execute, not the record of a run.

Read the [pipeline page](../architecture/knowledge-base-pipeline.md) first for
the step order, and the [functional specification](functional-specification.md)
for what a release has to be.

## 1. What "agent-operated" means here

| Role | Who | What it does |
| --- | --- | --- |
| Coordinator | One Claude Code session (Opus or Fable) in the packs repository | Owns the plan, edits `sources.json` and `curation.yaml`, runs every CLI command, reads every report, hands work to subagents and merges their output |
| Scout | Subagents with web access, one per authority branch | Find candidate official pages and report URL, publisher, language, why the page belongs and what it is *not* |
| Reader | Subagents (Sonnet), one per saved page | Read one text record and propose concepts and facts with block-level citations |
| Classifier | Subagents (Haiku or Sonnet), one per page packet | Assign the publishing institution and the basis of every excerpt with the committed prompt |
| Test author | Subagents, one per topic | Turn the acceptance questions into `acceptance.yaml` cases and write the breadth of `regression.yaml` |
| Grader | Subagents, one per live session | Grade a live caller's answer against the case criteria |
| **Reviewer (human)** | The person | Confirms, corrects or rejects every fact in the admin console, and attests readiness |

The agents run inside the Claude Code window on the plan, with subagents for
bulk work. This route needs no per-token API key, no `claude -p` loop and no
provider profile: the model that reads the pages is the one holding the
conversation. The `swisstip-concepts` package
([concept-extraction.md](../architecture/concept-extraction.md)) is the same
work with an external provider; its prompts and schemas are reused below.

### What an agent must never do

These rules are what make the output reviewable. Put them in the coordinator's
first message and repeat them in every subagent prompt.

- **Never type a quotation.** An excerpt is selected by block ID out of a saved
  text record. A quote that is not a byte range of a record cannot be built.
- **Never translate a search term.** `source_terms` are copied verbatim from
  the cited excerpt; the build verifies this.
- **Never set `human-reviewed`.** The highest status an agent may write is
  `assistant-authored-unreviewed` (hand-authored) or `model-candidate`
  (packaged from the extraction pipeline). Only the console's confirm action,
  driven by the person, sets `human-reviewed` and `reviewed_by`.
- **Never attest.** `--attested-by` carries a person's name. An agent does not
  put a name there to turn a gate green.
- **Never invent a number, a fee, a deadline or a telephone number**, including
  in the expected answer of a test case. Every figure in this document that is
  not quoted from a page is a *hypothesis to verify*, and is marked as one.
- **Never widen the crawl.** The downloader follows the catalogue; a page that
  should be read is added to `sources.json` under a new catalogue version.

## 2. The example topic, and why it is new

The current `mvp-zurich` release covers entry and visas, residence permits and
registration, tax at source and the tax return, social insurance on leaving,
pillar 3a, unemployment, health and accident insurance, family allowances,
housing, marriage, departure, naturalisation, voting rights, driving licence
and vehicles, office contacts and City of Zurich daily life (see
[COVERAGE.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/COVERAGE.md)).
It covers the newcomer as an **employee and resident**. It says nothing about
the newcomer as a **founder**.

`mvp-selfemployed` covers becoming self-employed and being recognised as such
by a compensation office, the legal forms (sole proprietorship, GmbH, AG) with
what each one requires, the commercial register, VAT liability, the social
insurance of a self-employed person, whether a foreign national may be
self-employed at all, and the Zurich offices that decide these questions.

It is a good example because it layers all three levels of the state (federal
acts and ordinances, cantonal offices, municipal permits), because its rules
turn on sharply testable thresholds, and because a general assistant answers
it fluently and wrongly.

The traps the pack is built to catch - each one a **hypothesis the pipeline
must confirm or discard against a cited page**, never a statement of law in
this document:

| Trap | The plausible wrong answer | What the pack must make the caller check |
| --- | --- | --- |
| Self-employed status | "You decide you are self-employed and start sending invoices." | A compensation office (SVA Zurich) recognises the status; the criteria and the application are published |
| Commercial register | "Every business has to be registered." | For a sole proprietorship the duty depends on an annual turnover threshold; below it the entry is voluntary |
| VAT | "You register for VAT as soon as you start." | Liability starts at a turnover threshold, with exemptions and a voluntary route |
| Foreign nationals | "A residence permit lets you work for yourself." | Self-employment of a third-country national needs its own authorisation; EU/EFTA nationals have a different route |
| Unemployment insurance | "You keep your unemployment cover." | A self-employed person is not insured against unemployment |
| Occupational pension | "Your pension fund carries on." | The second pillar is not mandatory for the self-employed, and the third pillar limit differs |
| Accident insurance | "The business insures you." | Mandatory accident insurance covers employees; the owner insures themselves separately |
| Company capital | "A GmbH needs a small deposit." | Minimum capital, the paid-in share, the notarised deed and the capital payment account are fixed by law |
| Municipal permits | "A city business licence covers everything." | Only some activities need a municipal permit; most need none |

## 3. The run sheet

| Step | Agent work | Human work | Output |
| ---: | --- | --- | --- |
| 1 | Draft the scope and the acceptance questions | Approve the scope | `docs/product/selfemployed-user-acceptance-tests.md` |
| 2 | Discover the sources, write the catalogue | - | `releases/mvp-selfemployed/sources.json`, `sources.md` |
| 3 | Run the crawler, read the gap report, correct the catalogue | - | `.local/mvp-selfemployed/pages/`, `gap-report.md` |
| 4 | Run text extraction, check the reading views | - | `.local/mvp-selfemployed/text/` |
| 5 | Semantic extraction: concepts, facts, keys, aliases, source terms, institutions, basis; a disposition for every content section no fact cites | - | `releases/mvp-selfemployed/curation.yaml`, `curation-coverage.yaml` |
| 6 | Build until nothing is dropped and no content section is unclassified | - | `release.json`, `build-report.json`, `curation-coverage.json` |
| 7 | Start the console, prepare the review brief | **Review every fact** | `human-reviewed` statuses, rebuilt release |
| 8 | Author the acceptance and regression packs | Spot-check the traps | `acceptance.yaml`, `regression.yaml` |
| 9 | Replay both packs before and after the review, build the index, run and grade live sessions | - | `acceptance-report.json`, `regression-report.json` |
| 10 | Run the ready stage | **Attest** | `readiness.json` |
| 11 | Write the documents, propose the commit | Commit and publish | `COVERAGE.md`, `LIMITATIONS.md` |

Steps 6 to 10 repeat after every change to the curation, and step 10 comes
last because a single changed byte of `release.json` invalidates it. Step 9
runs twice on purpose: confirming a fact raises its concept's prior in search
from 0.7 to 1.0, so the review itself moves rankings, and a suite replayed
only before it has not seen the release that ships.

Every command below runs from the packs repository (`swiss-tip-mvp`) and uses
the Windows virtual environment path; on macOS or Linux replace
`.venv/Scripts/` with `.venv/bin/`.

## 4. Step 0 - the coordinator brief

The first message of the session. It fixes the rules that every later prompt
inherits, and it names the files that hold the state, so the work survives a
restart.

```text
You are the coordinator of a knowledge-base build for the Swiss TIP MCP server.

Repositories: swiss-tip (code, read-only for you) and swiss-tip-mvp (packs,
where you write). New pack: mvp-selfemployed.

Goal: a release that answers questions about becoming self-employed and
founding a business in Switzerland, for the Canton of Zurich and the City of
Zurich, grounded only in official pages we have saved.

Read first, in the code repository:
  docs/architecture/knowledge-base-pipeline.md   the step order
  docs/architecture/release-format.md            what curation.yaml must carry
  docs/architecture/acceptance-gate.md           what the gates check
  docs/product/functional-specification.md       what the server promises

Hard rules, which apply to you and to every subagent you launch:
  1. Evidence is selected, never written. Every citation is a block range of a
     saved text record under .local/mvp-selfemployed/text/.
  2. German search terms are copied verbatim from the cited excerpt. Never
     translate a term into source_terms.
  3. You never write review_status: human-reviewed and never pass
     --attested-by. Those are the reviewer's actions.
  4. No fact without an excerpt. If a page does not say it, it is not a fact,
     even when you know it is true.
  5. The crawler follows the catalogue only. To read a page, add it to
     sources.json and bump the catalogue version.

State on disk, so that any step can be resumed:
  releases/mvp-selfemployed/sources.json     the catalogue
  .local/mvp-selfemployed/                   the run: pages, text, reports
  releases/mvp-selfemployed/curation.yaml    the knowledge
  .local/mvp-selfemployed/worklist.md        your plan, one line per unit of
                                             work with its state

Work step by step. After each step, write the worklist, report what the
reports say in numbers, and stop for my go-ahead before the next step.
```

## 5. Step 1 - scope and the acceptance questions

The questions come before the sources: they decide which pages are needed, and
they become the acceptance suite in step 8.

### Prompt

```text
Draft docs/product/selfemployed-user-acceptance-tests.md for the new pack,
following the structure of docs/product/wallisellen-user-acceptance-tests.md
in the packs repository.

Write 12 primary questions in the words a user would use, plus 5 decline
questions that the release must refuse.

For each primary question give:
  - the question verbatim, as a user would type it, with the noise a real user
    adds (a personal situation, a wrong assumption, a mixed language);
  - the trap: the specific wrong answer a general assistant gives;
  - the expected answer, written as "what a correct answer must establish",
    NOT as a statement of the rule - we have not read a source yet. Mark every
    threshold, fee and deadline as [to verify].
  - the user facts the answer depends on (nationality, permit, turnover,
    legal form), because the server has to ask for them.

For each decline question give the reason it is out of scope and what the
assistant must say instead.

Also draft the scope statement and the out-of-scope list in the words that
will go into the release manifest, because every caller reads them.

Cover at least: recognition of self-employed status, the three legal forms,
the commercial register duty, VAT liability, social insurance contributions,
the second and third pillar, accident insurance, unemployment, self-employment
of EU/EFTA and of third-country nationals, the Zurich cantonal offices, and a
municipal permit case. Include one question in German and one in a mixture of
German and English.
```

### Example of what comes back

```markdown
## UAT-4 - the Indian software engineer with a B permit

**Question.** I have a B permit through my job at a Zurich company and I want
to start doing freelance data work on the side. My friend says I just need to
register with the AHV. Is that right?

**Trap.** Treating self-employment as a tax and social-insurance formality and
missing that a third-country national's permit governs *which work* is allowed;
also treating registration as automatic rather than as a decision by the
compensation office.

**Expected answer.** Establishes (a) that the compensation office decides
whether the activity counts as self-employed and what it needs to decide it
[to verify], (b) that a third-country national needs an authorisation for
self-employment that the cantonal authority grants [to verify], and (c) that
the answer depends on the permit type and nationality, and asks for them if
they were not given. Does not name a fee, a processing time or a threshold
that the tools did not return.

**User facts the answer depends on.** Nationality, permit type, whether the
employment continues, expected annual turnover.
```

The reviewer reads the 12 questions and the scope statement and says yes.
That is the only human step before the review queue.

## 6. Step 2 - source discovery

Two prompts: a fan-out to scouts, then a catalogue written by the coordinator.
Only this step touches the open web; from step 3 on, the agents read saved
bytes.

### Prompt A - the scouts

Launch one scout per branch of the state. The branches follow from the
questions, not from a site map.

```text
You are a source scout for a Swiss public-information knowledge base about
self-employment and founding a business (Canton and City of Zurich).

Your branch: social insurance of the self-employed.

Find the pages of the responsible OFFICIAL publishers that a caseworker would
point at. For this branch that means the federal information of the AHV/IV
information centre, the federal office responsible for social insurance, and
the Zurich cantonal compensation office. Follow the official navigation, not a
search-engine summary, and open each page you report.

For every candidate page report exactly:
  url, publisher (the office, in its own language and in English),
  level (federal | cantonal | municipal), language(s) available,
  what the page establishes in one sentence,
  which of our questions (UAT numbers) it answers,
  page kind: act or ordinance | directive | authority guidance | portal
    summary | directory | form,
  stability: does it look like durable guidance or a news or campaign page,
  whether it is HTML or PDF, and whether the content sits behind a form,
    an application shell or a login.

Report at most 12 pages. Prefer the page that states the rule over the page
that links to it. Exclude: news, events, blog posts, commercial advisers,
chambers of commerce, law-firm explainers, and anything published by a company
rather than an authority.

Do NOT save pages, do NOT quote more than the page title, and do NOT state the
rules themselves - we will read them from the saved snapshot.
```

Run the same prompt for the other branches:

| Scout | Branch | Publishers it should reach |
| --- | --- | --- |
| 1 | Social insurance of the self-employed | AHV/IV information centre, SVA Zurich, the federal social-insurance office |
| 2 | Legal form and the commercial register | Federal commercial register office, Handelsregisteramt of the Canton of Zurich, the central business name index |
| 3 | Taxes | Federal tax administration (VAT), Zurich cantonal tax office |
| 4 | Federal one-stop portals | ch.ch, the SME portal, the federal business start-up platform |
| 5 | Foreign nationals and self-employment | State Secretariat for Migration, the Zurich office responsible for labour-market authorisations |
| 6 | Municipal permits | City of Zurich business and trade permits |
| 7 | The legal texts themselves | Fedlex: the code of obligations on the register duty, the commercial register ordinance, the VAT act, the old-age insurance act |

### Prompt B - the catalogue

```text
Write releases/mvp-selfemployed/sources.json (schema source-catalog/v1,
version draft-1) and sources.md from the scout reports. Use
releases/mvp-wallisellen/sources.json as the shape reference.

Rules:
  - One source entry per publisher branch, with start_url, allowed_hosts and
    allowed_path_prefixes tight enough that the crawler cannot wander into
    news or into another topic. A prefix like "/" is only acceptable for a
    small site.
  - sources.md lists every explicit page and PDF we intend to save, grouped by
    planning topic, as a link list.
  - Give each source: canonical_authority, jurisdiction (CH, CH-ZH,
    CH-ZH-261), language, authority_level, source_kind, priority, topic_hints,
    and discovery (method, reference_url, located_on).
  - Crawl profiles and budgets: copy the profile set of mvp-wallisellen and
    assign each source the smallest profile that reaches its pages. Total
    budget for the pack: at most 80 pages.
  - scan_sets: "smoke" (2 sources), "core" (the sources that answer UAT 1 to
    8), "all".
  - Write the scope and exclusions of the catalogue from the approved scope
    statement, not from what you happened to find.

Then print a table: source_id, pages expected, which UAT questions it serves,
and any UAT question that no source covers yet. Do not start the crawler.
```

The last line matters: a question with no source is the finding of this step.
It is resolved by another scout run or by narrowing the scope, before a single
byte is downloaded.

### A catalogue entry

```json
{
  "definition": {
    "source_id": "zh-handelsregister",
    "start_url": "https://www.zh.ch/de/wirtschaft-arbeit/handelsregister.html",
    "allowed_hosts": ["www.zh.ch"],
    "allowed_path_prefixes": ["/de/wirtschaft-arbeit/handelsregister"],
    "canonical_authority": "Handelsregisteramt des Kantons Zuerich",
    "jurisdiction": "CH-ZH",
    "language": "de"
  },
  "title": "Commercial register of the Canton of Zurich",
  "authority_level": "cantonal",
  "source_kind": "official_guidance",
  "priority": "P0",
  "topic_hints": ["legal-form", "commercial-register"],
  "discovery": {
    "method": "official_page_link",
    "reference_url": "https://www.zh.ch/de/wirtschaft-arbeit.html",
    "located_on": "2026-09-21"
  },
  "scan_status": "ready",
  "notes": "Registration of a sole proprietorship, GmbH and AG; fees and forms."
}
```

## 7. Step 3 - the agent runs the crawler

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli mvp-selfemployed \
  --until gaps --download --workers 4
```

The bounded downloader plans the run from the catalogue, saves every target
byte for byte under `.local/mvp-selfemployed/pages/`, and writes `plan.json`
and `gap-report.md`. Nothing is saved under `releases/`.

### Prompt

```text
Run the acquire and gaps stages for mvp-selfemployed with --download
--workers 4. Then read .local/mvp-selfemployed/gap-report.md and classify
every gap into exactly one of:

  (a) catalogue error   - wrong URL, wrong prefix, page moved: fix sources.json
  (b) publisher reality - dead link, application shell, login, robots block,
                          a soft error page served with HTTP 200: record it in
                          sources.md as a known limit, do not retry
  (c) budget            - the profile stopped before the page: raise that
                          source's profile, not the pack budget
  (d) not needed        - the page does not serve any UAT question: drop it

For (a) and (c): bump the catalogue version to draft-2, keep the old run
directory under .local/mvp-selfemployed-run-<date>-draft-1/, and plan a fresh
run. Do not edit a saved page, ever.

Report: pages saved, bytes, failures by class, and the UAT questions that now
have no saved page behind them. Use a browser user agent if a publisher serves
a soft error page to the default one, and say so in the report.
```

The soft-error case is not hypothetical: ch.ch serves a 200 response with an
error body to a non-browser user agent, which is why the classifier has to
look at the body and not only at the status code.

## 8. Step 4 - text extraction

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli mvp-selfemployed \
  --until validate-text --workers 4
```

No model is involved. The stage writes labelled blocks with code-point offsets
and hashes, plus a Markdown reading view per record. The reading view is what
the readers in step 5 read, and the block IDs are what they cite.

### Prompt

```text
Run the extract and validate-text stages. Then check the text dataset before
anyone reads it:

  - For each of the 12 UAT questions, open the reading view of the page that
    should answer it and confirm the answer is actually in the text. Report
    the record ID and the heading path.
  - The other way round: for every record the index marks curation_candidate,
    name the UAT questions its content sections can answer, or say that it
    answers none. A question-first check finds missing pages; only a
    page-first check finds pages nobody will use. Keep the list: step 5 reads
    every candidate, and step 6 checks that every content section was either
    cited or dispositioned.
  - List records whose text is suspiciously short against their saved HTML
    size, and say why: a client-rendered table, a component the extractor does
    not read yet, a PDF that is an image (no OCR), or a genuinely short page.
  - List PDFs without a declared language; they need a page_languages rule.

If a publisher puts content in a component the extractor does not read, do not
work around it in the curation: report it. Extending the extractor with tests
and raising EXTRACTOR_VERSION in the code repository is the correct fix, and
it is a separate change.
```

Two components of the City of Zurich pages (a contact card and a data table)
needed exactly that fix for `mvp-zurich`, so the check is in the run sheet.

## 9. Step 5 - semantic extraction and the corpus

This is where the knowledge is made. One reader subagent per curation
candidate, a classifier pass over the result, and the coordinator merging
everything into `curation.yaml` and `curation-coverage.yaml`.

Every candidate gets a reader: the records the text index marks
`curation_candidate`, which are the catalogue targets, the link-discovered
`in-scope` pages and the plugin documents (the acts and ordinances Fedlex
resolved), in their preferred representation. Not only the pages the plan
named. The Zurich pack lacked the five-year settlement rule for a week
because one page that held it was a discovery nobody listed and the other a
catalogued page nobody walked to; twenty-nine catalogued German pages and
five federal acts had been fetched and never read. A reader that finds
nothing to propose does not say so in chat and move on: it writes the
disposition (below), because afterwards a page read and rejected must be
distinguishable from a page never opened.

### Prompt A - one reader per page

```text
You are reading ONE saved official page and proposing knowledge entries for a
Swiss public-information knowledge base.

Reading view: .local/mvp-selfemployed/text/reading/<record-id>.md
Record:       .local/mvp-selfemployed/text/documents/<record-id>.json
Sections:     the record's content sections (swisstip.extraction.sections);
              a disposition names them by section_id
Publisher:    Handelsregisteramt des Kantons Zuerich (cantonal)
Our questions: docs/product/selfemployed-user-acceptance-tests.md

Propose at most 6 concepts from this page. A concept is one thing a user wants
to know, at the granularity of one question. For each concept give:

  concept_id    stable kebab-case key, unique across the pack, in English,
                naming the thing and not the page: sole-proprietorship-register
  topic_id      one of the topics in the plan
  label         short English title
  description   one sentence: what this concept establishes
  aliases       6 to 12 strings in the words USERS use, in English and German,
                including misspellings and colloquial forms we expect in a
                query. Never the office's own jargon alone.
  source_terms  terms copied CHARACTER BY CHARACTER from the excerpts you cite
                below, in the page's language. These are what a German query
                matches. If a term is not in your excerpt, it is not allowed.
  questions     at least one sample question in English and one in German,
                written from these facts, in a user's voice. Not copied from
                the UAT document.
  required_context   which of country/canton/city the answer depends on
  required_user_facts  what the caller must ask the user (nationality, permit,
                turnover, legal form), as short field names with a description
  not_served    what a reader could wrongly expect this concept to cover:
                individual decisions, current fees if the page has none,
                availability, legal advice
  facts         1 to 6 statements, each with:
                  statement   one self-contained English sentence stating the
                              rule. It must be fully supported by ONE excerpt
                              from THIS page. No general knowledge, no second
                              page, no conditions borrowed from another
                              section.
                  language    en
                  jurisdiction  CH | CH-ZH | CH-ZH-261
                  evidence    the block IDs (bNNNNN) of the reading view that
                              carry it, and the heading path. Copy the block
                              IDs; do not retype the text of the block. If the
                              statement restates a provision of an act or an
                              ordinance the run holds (a plugin document in
                              the index), add that article as a second
                              evidence item now: the fact then rests on the
                              law, not on the authority's summary of it, and
                              the reviewer sees both excerpts once. Adding it
                              after the review reopens the fact.
                  valid_through  if the statement is dated (a fee for a year, a
                              deadline for 2026), the date it stops being safe
                              notes       anything the reviewer should check

Rules:
  - One section per concept. Cite only the section your concept is about.
  - A number in a statement must appear in the cited block. If the page says
    "in der Regel", the statement says "as a rule", not a flat claim.
  - If the page only points at another authority, that IS the fact: say that
    this office publishes the pointer, and name the other authority.
  - Every content section of the page ends in one of two places: cited by a
    fact you propose, or dispositioned. For each content section you cite
    nothing from, return a disposition for
    releases/<pack>/curation-coverage.yaml:
      kind          out_of_scope (name the manifest's out_of_scope entry it
                    rests on) | duplicate (name the record that carries the
                    same content) | navigation (a hub whose children carry
                    the content) | deferred (we could curate this and have
                    not; give reaffirm_by, the date the disposition expires)
      reason        one or two sentences a reviewer can check
      document_id and section_ids, or a url_prefix rule for a whole tree
                    with the known_documents it covers today
    "Answers none of our questions" is a disposition with a kind, not a
    remark. A gap the release should admit goes into not_served or
    out_of_scope, never into a test expectation.

Output YAML in the curation shape of releases/mvp-zurich/curation.yaml, with
provenance.kind: curated-statement and
provenance.review_status: assistant-authored-unreviewed, followed by the
dispositions. Do not write the files; return the YAML.
```

### Prompt B - institutions and basis

Reuse the committed prompt instead of writing a new one, so that the
classification matches the one the served packs used:

```text
For every page in .local/mvp-selfemployed/text/, build a packet of the page
title, the heading path, the publisher and the excerpts our facts cite, and
classify it with the prompt
packages/concepts/src/swisstip/concepts/prompts/basis_classification_v1.md of
the code repository. One subagent per packet.

Write two things:
  - the institutions registry of curation.yaml: institution_id, name,
    native_name, level, body, jurisdiction and the URLs each institution
    publishes. The Handelsregisteramt is not "the Canton of Zurich"; name the
    office.
  - the basis of every citation: act | ordinance | treaty | directive |
    authority guidance | directory | portal summary, with its level. Put the
    page-wide answer in page_basis and only the exceptions on the citation.

Record the outcome, with the disagreements you had to resolve, in
releases/mvp-selfemployed/basis-review.md, so the reviewer can check the
classification without re-reading every page.

The publisher's level never weighs a search result by itself; the basis does.
A summary portal saying the same thing as an act does not outrank the act.
```

### Prompt C - the coordinator merges

```text
Merge the reader output into releases/mvp-selfemployed/curation.yaml.

Besides the concepts and facts, the file must carry:
  - the manifest: scope_statement, out_of_scope, out_of_scope_response and the
    served limitations, in the approved words. Every caller reads them, and
    they must match what LIMITATIONS.md will say.
  - freshness_max_age_days, publishers, page_languages for records without a
    language, institutions and page_basis,
  - place_register and place_aliases pointing at config/places/, so a caller
    can name "Zurich" instead of CH-ZH-261,
  - topics with a one-line description each.

Merge rules:
  - Two readers proposing the same thing from different pages: keep one
    concept, keep BOTH facts with their own evidence, and make the
    jurisdiction difference explicit (federal rule, cantonal procedure).
  - Contradiction between two pages: keep both facts, flag the pair in
    .local/mvp-selfemployed/worklist.md for the reviewer, and do not resolve
    it yourself.
  - Alias collisions across concepts: an alias that matches three concepts
    helps none of them. Make it specific or drop it.
  - Every concept needs a question in every language of question_languages, or
    the build refuses it.

Merge the readers' dispositions into releases/<pack>/curation-coverage.yaml
(schema swiss-tip-curation-coverage/v1, pack, dispositions) and run the
coverage stage:

  ./.venv/Scripts/python.exe -m swisstip.builder.cli <pack> --from build --until coverage

Then report: topics, concepts, facts, excerpts, cited documents, facts per
concept (min, median, max), every concept whose facts all come from one page
- those are the fragile ones - and, from curation-coverage.json: the
candidate records, the content sections cited, dispositioned and
unclassified, every disposition of kind deferred with its reaffirm_by, and
every catalogue source whose status is `nothing`. Unclassified must be zero
before step 7: a reviewer confirms facts against pages someone read, and the
report is how you show which pages those were.
```

### What a merged entry looks like

```yaml
- concept_id: sole-proprietorship-register
  topic_id: legal-form
  label: Commercial register entry for a sole proprietorship
  description: When a sole proprietorship must be entered in the commercial
    register and when the entry is voluntary.
  aliases:
  - Einzelfirma anmelden
  - do I have to register my business
  - Handelsregister Pflicht
  - sole trader registration
  - Einzelunternehmen eintragen
  - register my freelance business
  source_terms:
  - Handelsregister
  - Einzelunternehmen
  - eintragungspflichtig
  questions:
  - Do I have to enter my one-person business in the commercial register?
  - Muss ich mein Einzelunternehmen im Handelsregister eintragen?
  required_context: [country, canton]
  required_user_facts:
  - field: annual_turnover
    description: expected annual turnover of the business in CHF
  not_served:
  - whether a specific business name is available or admissible
  - the individual decision of the register office
  facts:
  - fact_id: sole-proprietorship-register-1
    statement: "[written from the cited excerpt: the turnover threshold above
      which a sole proprietorship must be entered, and that the entry is
      voluntary below it]"
    language: en
    jurisdiction: CH
    provenance:
      kind: curated-statement
      review_status: assistant-authored-unreviewed
      author: Claude subagent reading the saved page
    evidence:
    - document_id: doc-<id>
      first_block: 84
      anchor:
        source_url: https://www.zh.ch/de/wirtschaft-arbeit/handelsregister...
        block_ids: [doc-<id>:b00084]
        heading_path: [Handelsregister, Eintragung, Einzelunternehmen]
      basis:
        level: federal
        kind: act
```

The statement is a placeholder on purpose: writing it here, without the saved
page in front of the model, is exactly the failure the pipeline exists to
prevent.

## 10. Step 6 - build and validate

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli mvp-selfemployed
```

Runs `build`, `validate-release`, `coverage`, `health` and `accept`. The
build resolves every citation against the text dataset, pins it with hashes,
verifies that every source term occurs in its excerpt, and refuses an invalid
release. The coverage stage then joins the text dataset with the release and
writes `curation-coverage.json` and `.md`: every content section of every
candidate record is cited, dispositioned, or listed as unclassified with its
heading path and block range. A section whose text recurs on five or more
candidate pages (a contact card, a closure notice) is set aside as boilerplate
and traced instead: the report's `repeated_sections` table names the page
where a fact cites it, so that an office address repeated on twenty pages is
served once, from the office's own page, and one that is served from no page
appears as `cited_nowhere`. Under the default `coverage_policy: report` it
only writes; a new pack sets `coverage_policy: enforce` in `curation.yaml`
from the start, so that an unclassified section is a build error and not a
number in a report someone has to remember to read.

### Prompt

```text
Build the pack and iterate until build-report.json lists zero dropped facts.

For each dropped fact, classify and fix at the source of the error:
  - citation does not resolve      -> the block IDs are wrong: re-read the
                                      record, do not "fix" the excerpt
  - source term not in the excerpt -> the term was translated: replace it with
                                      a term that is in the excerpt, or drop it
  - statement not supported        -> the reader over-reached: narrow the
                                      statement to what the block says
  - concept without a question in a listed language -> write one from the
                                      facts, not from the test cases
  - schema or enum error           -> fix the field

Never make a fact build by widening its excerpt to a block that does not carry
it. A fact that cannot be cited is deleted and reported.

Then run:
  python -m swisstip.build.source_terms releases/mvp-selfemployed/release.json
and add the candidate terms it lists that genuinely appear in our excerpts.
Each added term shifts the rarity weights, so say in your report which
concepts changed rank.

Then read curation-coverage.md. Every unclassified section is a page someone
saved and nobody read: either a reader missed it (send it back to step 5) or
it needs a disposition with a reason. Do not disposition a section as
out_of_scope to make the count zero: the disposition must name the manifest
entry that excludes it, and the stage checks that it exists.

Then read the repeated-sections table. A `cited_nowhere` entry that is an
address, a telephone number or opening hours is contact information the
release does not serve: cite it once, from the office's own page. A closure
notice or a browser warning that is cited nowhere is right as it is.

Report: release ID, content digest, topics, concepts, facts, excerpts, cited
documents, dropped facts (must be 0), unclassified sections (must be 0),
dispositions by kind, catalogue sources with status `nothing`, and the
validator's warnings.
```

## 11. Step 7 - the human review

The agent prepares the review; it does not perform it.

```shell
./.venv/Scripts/python.exe -m swisstip.admin_console.app --actor "<reviewer name>"
```

### Prompt

```text
Start the admin console with --actor set to the reviewer's name and write
.local/mvp-selfemployed/review-brief.md for them.

The brief must contain, in this order:
  1. How to review: open the review queue, filter review_status =
     assistant-authored-unreviewed, and for each card compare the statement
     with the excerpt shown next to it. Confirm, correct or reject.
  2. The review order I recommend, hardest first: the facts carrying a number
     or a threshold, then the facts about foreign nationals, then the ones
     whose concept has a single source page, then the rest.
  3. The 6 pairs of facts I flagged as possibly contradictory, with both
     excerpts side by side and the question the reviewer has to settle.
  4. The facts whose statement generalises a "in der Regel" phrasing, listed
     explicitly, because those are where I am most likely to have over-reached.
  5. The institutions and basis classification: the table from
     basis-review.md, with the 4 assignments I was least certain about.
  6. What the reviewer is NOT confirming: that the rule is currently in force,
     that the page is still live, or that this is legal advice.

Do not set any review status yourself. When the reviewer tells you they are
done, read the statuses back out of curation.yaml and report the counts: how
many human-reviewed, by whom, on which date, how many corrected, how many
rejected, and how many confirmed in bulk groups. Those numbers go verbatim
into the served limitations and into LIMITATIONS.md.

Then rebuild (step 6). The rebuilt release gets a new version and digest.
```

The console's confirm action is the only writer of `human-reviewed` and
`reviewed_by`. If the reviewer works through a bulk group, the brief says so
and the limitation line says so: "confirmed in groups of N" is a different
claim from "read card by card", and the release states which one it is.

## 12. Step 8 - the agent writes the test packs

Two packs with different jobs. `acceptance.yaml` is the gate: the questions of
step 1, replayed with no model, plus what a live caller's answer must contain.
`regression.yaml` is breadth: many more queries, no model, retrieval only.

### Prompt A - the acceptance pack

```text
Write releases/mvp-selfemployed/acceptance.yaml (schema
swiss-tip-acceptance/v1) from docs/product/selfemployed-user-acceptance-tests.md.
Use releases/mvp-wallisellen/acceptance.yaml as the shape reference.

One case per question, plus the tool-request variants and the decline cases.
For each case:

  case_id, label, spec (the anchor in the UAT document), question, trap
  expected_answer   now rewritten from the BUILT release: replace every
                    "[to verify]" of step 1 with what the reviewed facts
                    actually say, or delete the claim if no fact supports it
  steps             the tool calls a correct caller makes:
                    search: query + expect_concept (+ expect_strength)
                    resolve: concept_ids + jurisdiction + expect_status
  claims            what the served facts must carry, each with
                    statement_contains (words of our English statement) and
                    excerpt_contains (a phrase of the cited page, VERBATIM -
                    copy it out of release.json, do not retype it)
  answer            for the live harness: criteria, must_mention patterns,
                    must_not patterns (the trap in regex form), cites, resolved

Decline cases (DECLINE-1..5) assert the server's behaviour only: status
OUT_OF_COVERAGE and the expected gap. They never assert what the LLM said,
because that is not ours to fix.

Also add place cases: the same question for a municipality in the canton that
is not the City of Zurich, asserting that the municipal concepts are not
served and the caveat gap names the level that applies.

A case that encodes a rule no reviewed fact carries is a broken case, not a
found bug. Report any such case instead of writing it.
```

### Prompt B - the regression pack

```text
Write releases/mvp-selfemployed/regression.yaml: retrieval breadth, no answer
checking. Follow the case-per-fact ratio of releases/mvp-zurich/regression.yaml.

Case families, with a prefix each:
  Q-EN-   plain English questions, one per concept, in a user's voice
  Q-DE-   the same in German, phrased from the page's own vocabulary
  N-      noisy: typos, no punctuation, keyword-only, a sentence with the
          situation before the question
  CTX-    the same question with the place given in three ways: canton code,
          canton name, city name
  DATE-   questions whose answer depends on a date, to catch stale facts
  OOS-    out-of-scope questions that must NOT match a concept strongly
  DECLINE- server-only: expect OUT_OF_COVERAGE and the gap

Rules:
  - A case may not repeat a question that is already in acceptance.yaml.
  - A German case may not contain a word that only appears in our English
    statement: it must be answerable from the German page.
  - Never put the expected answer's words into the query. A query that quotes
    the fact tests nothing.
  - Each case names expect_concept and, where it matters, expect_strength.
  - Never write an expectation that asserts the release does NOT know
    something, unless that absence stands in not_served or out_of_scope. A
    must_not pattern that forbids a true, citable rule locks the gap in: the
    Zurich pack's UAT-5 forbade the five-year settlement rule for a week,
    and every green run confirmed the pack was "right". A gap is a
    disposition of kind deferred and a line in LIMITATIONS.md, not a test.

Then say how many cases per concept you wrote, and which concepts have fewer
than the pack ratio.
```

## 13. Step 9 - validating the release against the tests

Three runs, in this order, and the whole set again after the review of
step 7: confirming a fact raises its concept's prior in search from 0.7 to
1.0, so the review reorders hybrid results and can displace an expected
concept that ranked while the new facts were still unreviewed.

```shell
# 1. the gate: acceptance replayed with no model
./.venv/Scripts/python.exe -m swisstip.builder.cli mvp-selfemployed --from build --until accept

# 2. the semantic index (needs local Ollama with qwen3-embedding:0.6b)
./.venv/Scripts/python.exe -m swisstip.runtime.search_cli index \
  --release releases/mvp-selfemployed/release.json \
  --output .local/semantic-search/index.json \
  --model qwen3-embedding:0.6b --base-url http://127.0.0.1:11434 --timeout 30

# 3. breadth: both suites, lexical and hybrid
./.venv/Scripts/python.exe scripts/test/regression/run_regression.py --pack mvp-selfemployed
```

### Prompt

```text
Run the three validation commands and read the reports, not the exit codes.

For every failing acceptance case, decide which of these it is, and say so:
  (a) the release is wrong        -> fix the curation, rebuild, re-run
  (b) the case is wrong           -> fix the case, and say why the earlier
                                     expectation was wrong
  (c) retrieval is wrong          -> the fact is right and the case is right,
                                     but search does not find the concept:
                                     fix aliases, source terms or the sample
                                     question, then replay the WHOLE
                                     regression pack, because every added word
                                     moves the rarity weights

Never make a case pass by weakening its excerpt_contains phrase. That phrase
is the tie between our statement and the official page.

For regression failures: a case that fails in the hybrid run and is a genuine
retrieval limit is committed with blocking: false and a quarantine_reason that
states what was measured, on which release ID. A case that fails because the
knowledge is missing is not quarantined; it is a gap and goes on the worklist.

Expect verdict drift when the release grows. match_strength reads strong when
the query's anchored weight reaches 1.5 or its lexical share 0.5; the weight
is an absolute sum of rarity weights, and every concept added to a topic
lowers the weight of that topic's words. Adding twenty concepts to the
Zurich pack moved eight questions from strong to weak without changing
their ranking. Report such cases separately, as "verdict only, ranking
unchanged", with the weight before and after; do not rewrite a user's
question to move the number.

Report a table: suite, cases, passed, failed, quarantined, lexical vs hybrid,
once before the review and once after it.
```

### The live caller run

The model-free replay proves the server. It does not prove that an assistant
holding these tools answers well, which is the actual product claim.

```text
Run the acceptance questions through the OpenCode harness against the real
server on releases/mvp-selfemployed/release.json, each question in a fresh
session, several repetitions, and once more as a control with the server
unavailable.

Then have one grader subagent per session grade the answer against the case
criteria, with the tool results in front of it:
  - did the answer use only facts the tools returned?
  - did it fall into the named trap?
  - did it ask for the user facts the case lists?
  - did it name a URL, telephone number or amount the tools did not return?
  - did it treat OUT_OF_COVERAGE, a gap and the unreviewed status as real
    boundaries?

Write the aggregate: per case, the rate over repetitions, with and without the
server. A single session is an observation; the rate is the result. Put the
transcripts under .local/experiments/<date>-selfemployed-acceptance.md and
quote only the numbers in the documents.
```

## 14. Step 10 - readiness, and the second human step

The record carries the coverage counts of the release it attests (candidate
records, content sections cited, dispositioned and unclassified, and whether
the report is clean) as information next to the six gates. Read them before
attesting: a release can pass every gate with a hundred sections nobody
opened, and the counts are the only place that says so.

```shell
./.venv/Scripts/python.exe -m swisstip.builder.cli mvp-selfemployed \
  --from accept --until ready --attested-by "<reviewer name>"
```

The ready stage runs the gates on the current files and binds `readiness.json`
to the bytes of `release.json` and the digest of the acceptance suite. The
server with `--require-ready`, the container build and the committed-pack test
all refuse a release without a matching record.

The agent prepares and explains; the person passes their own name:

```text
Before readiness, print the attestation packet: release ID, content digest,
counts, review numbers, the gates and what each one checks, the quarantined
regression cases with their reasons, and the open gaps from the worklist.

Say plainly what the attestor is taking responsibility for and what they are
not. Then stop. I pass --attested-by myself.
```

## 15. Step 11 - documents, commit, publish

```text
Write COVERAGE.md and LIMITATIONS.md for the new pack, the pack's row in
releases/README.md and the acceptance-test document's final numbers. Every
number is read out of release.json and the reports - no number is carried over
from an earlier draft of this document.

LIMITATIONS.md and the served limitations must state the same thing in the
same words, because a caller reads the served ones and a jury reads the file.

Then propose a one-line commit message in the style of the last 10 commits.
I commit and publish.
```

## 16. What this example proves, and what it does not

It proves that the route can be driven end to end by agents: the scope, the
catalogue, the crawl, the extraction, the corpus with its keys, aliases,
source terms, institutions and basis, the test packs and the validation are
all agent work, and each of them lands in a file that the next step checks
mechanically.

It does not remove the person. Two steps are theirs by design - confirming
every fact against its excerpt, and attesting the release - and a third,
approving the scope, is theirs because an agent that picks its own scope also
grades its own homework. Everything else the agents do is checkable precisely
because those two steps exist: the build refuses an uncitable fact, the gate
refuses an unsupported claim, and the release says, in the words every caller
reads, how many of its facts a person has actually read.
