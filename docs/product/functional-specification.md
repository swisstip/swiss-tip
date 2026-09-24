# Swiss TIP - functional specification

**Product:** Swiss TIP, a Trusted Information Platform for Swiss public
information: an MCP server that gives an AI assistant grounded, cited facts
about Swiss rules, together with the knowledge pipeline that produces what
it serves.<br>
**Repositories:** `swiss-tip` (server, pipeline, tooling) and
`swiss-tip-mvp` (the published knowledge packs).<br>
**This document** states what the product does and what it must guarantee.
Field-level request and result shapes, the release schema and the internal
designs of the pipeline stages are specified in the architecture documents
that accompany the code.

---

## 1. Summary

An assistant asked a question about Swiss rules - which permit applies, by
when a new arrival must register, whether a foreign driving licence is still
valid - can search the web and will usually produce something plausible.
Plausible is the problem: public Swiss information is spread over federal,
cantonal and municipal publishers in four languages, the rule that applies
depends on the canton and the commune, and a generic answer silently mixes
jurisdictions, quotes an outdated page or reproduces a neighbouring
country's rule.

Swiss TIP moves that risk out of the model. It publishes a curated,
versioned knowledge base in which every fact is a short statement tied to an
exact excerpt of an official page, with the publisher, the URL, the access
date and the hashes of the excerpt and of the page it was cut from. Four MCP
tools let a calling assistant discover what is covered, find the concepts
that fit a question, resolve them for a stated place, date and situation,
and read the original excerpts. The server never composes an answer, and
when a question falls outside what it publishes it says so by name instead
of guessing.

The product is two things that ship together: the **server** that answers
from a release, and the **pipeline** that turns official pages into a
release that has passed acceptance and readiness checks. Neither imports the
other: serving needs no build tooling, no model, no credentials and no
network.

## 2. Users

| User | Uses the product to |
| --- | --- |
| Calling assistant (any MCP client: a chat assistant, an agent, a form or a workflow) | Discover coverage, find concepts, resolve facts for a place and date, quote excerpts |
| Knowledge curator | Turn official pages into concepts and facts with citations, aliases and sample questions |
| Reviewer | Confirm or correct every statement against its excerpt, and record who confirmed it |
| Operator | Run and monitor the server, refresh sources, publish and roll a release forward |

## 3. The product

### 3.1 Responsibility split

| Party | Owns |
| --- | --- |
| Calling assistant | Understanding the question, choosing the concepts and the scope, asking the user for missing details, composing and translating the answer |
| Swiss TIP | The published facts, the original-language excerpts they rest on, citations with URLs and access dates, applicability by jurisdiction, date and situation, freshness, and an explicit statement of what is not covered |

The server returns evidence, facts and a typed status, never prose. This
keeps the contract usable by a workflow as well as by a chat assistant, and
leaves the assistant free to phrase, translate and shorten.

### 3.2 Principles

1. **Evidence first.** Every served fact rests on an exact excerpt of an
   official page. The excerpt travels with the fact, so a citation can be
   checked against the release itself or against the live page.
2. **Explicit scope.** A request names the concepts and the place; missing
   context is reported as a typed, named gap rather than guessed.
3. **Honest limits.** Out-of-coverage and stale results carry a status the
   caller can act on. One correct "not covered" beats a plausible guess.
4. **Few, small calls.** A typical question is answered in two tool calls
   with responses of a few kilobytes; discovery pages stay small enough to
   be read whole.
5. **Reproducible and offline.** A release is a single hashed file. Default
   serving needs no model, no credentials and no network; semantic search is
   an optional local addition. Refreshing sources is a separate, explicit
   build step.
6. **Source etiquette.** Acquisition respects robots.txt and its delays,
   identifies itself, rate-limits per host and stays inside a declared
   budget; the server itself makes no live request at all.
7. **Excerpts, not pages.** The product keeps what a citation needs and no
   more. Fetched pages are working material of the build and the review;
   they are not part of a release, and serving never reads them.

### 3.3 Worked scenario

> "I'm a Czech citizen and starting my work in Zurich next week. By when
> latest should I register my stay on the municipal authority?"

The assistant searches with the question and the user's place, gets the
federal registration deadline, the cantonal procedure and the municipal
move-in procedure, and resolves all three in one call for the City of
Zurich, today's date and an EU/EFTA population. It receives the facts, the
citations, two user facts the release cannot know (arrival date, first
working day) and the published rule for combining them. It asks the user for
the two dates, applies the rule, and answers: register with the municipality
within 14 days of arrival and before the first working day, citing the
federal FAQ and the cantonal page.

A question the release does not publish - an annual permit quota, a school
admission - produces a weak or empty search verdict with the scope
statement, so the assistant declines within the same call.

## 4. Functional requirements

### 4.1 The knowledge release

A release is one versioned JSON bundle, self-contained and hashed:

| Part | Content |
| --- | --- |
| `manifest` | Release ID, pack, title, scope statement, out-of-scope list and the response to give outside it, the jurisdictions and languages actually present, freshness policy, provenance and review counts, institution and basis counts, the ranking policy, limitations, and a content hash over everything else |
| `institutions` | Who published each cited page: name, level of the state (federal, cantonal, municipal), kind of body, and the jurisdiction it speaks for |
| `documents` | One entry per cited page: URLs, title, publisher, language, access date, and the hashes of the page as fetched and of its normalised text |
| `topics`, `concepts` | The catalogue: topics, and concepts with label, description, aliases, sample questions, the jurisdictions they are published for, their context schema, optional required user facts and decision rule, and what they do not serve |
| `facts` | Short statements with a jurisdiction, optional situation condition and validity window, the evidence they rest on, and provenance |
| `evidence` | The excerpts: text, offsets in the source record, block identifiers, hashes, publisher, language, access date, and basis |
| `place_register` | The country, its cantons and its municipalities with official names and accepted aliases, so a caller can name a place instead of a code |

A fact carries one jurisdiction; a concept may span several. Facts are
served by containment: a federal fact answers for every canton and
municipality, a cantonal fact for its municipalities, a municipal fact only
for its own municipality - never upward or sideways.

Validation is mandatory and fails closed. The validator checks the content
hash, identifier form and uniqueness, every reference in both directions,
concept jurisdictions against their facts, conditions against the context
schema, excerpt offsets and hashes, declared jurisdictions and languages,
access dates against the snapshot date, the institution registry, the basis
of every excerpt and fact, and the place register. The build runs it before
writing a release; the server runs it at startup and refuses to serve a
release that does not pass.

### 4.2 Provenance, review and basis

Every fact says where its statement came from and who looked at it.

| Provenance kind | Meaning |
| --- | --- |
| `curated-statement` | Written by a person or an assistant reading the cited page |
| `source-section` | The excerpt itself is the statement |
| `model-candidate` | Proposed by a model from the cited page |

| Review status | Meaning |
| --- | --- |
| `human-reviewed` | A named person confirmed the statement against its excerpt; the reviewer and the date are recorded |
| `assistant-authored-unreviewed` | Written by an assistant, not confirmed |
| `automatically-derived-unreviewed` | Produced deterministically, never read |
| `model-candidate-automated-review` | A model proposal that passed an automated review only |

Every result states the review status of what it served, and the release
counts are in every result's limitations. A caller that needs confirmed
statements only asks for reviewed facts and gets a named gap instead of
unreviewed ones.

Separately from who published a page, every excerpt states **what it is**:
an act, an ordinance, a treaty, a directive, an authority's guidance, a
directory or a portal summary, with the norm where one applies ("Federal
act: AIG, SR 142.20, Art. 12"). The basis is a property of the text, not of
the page that carries it, so an article of federal law quoted on a cantonal
page is federal law, and the canton's own answer next to it is cantonal
guidance. The basis is served on every fact and weighs in search ranking;
the publisher's level never does.

### 4.3 The tools

Four read-only tools, annotated as such, strict about unknown fields, and
returning every result both as structured content and as the same JSON in
text. Every result names the release it was answered from and ends with the
limitations the caller should pass on.

| Tool | Purpose |
| --- | --- |
| `get_coverage` | Walk the catalogue. The root page returns the scope statement, the out-of-scope list and what to say outside it, the jurisdictions, the evidence languages, the languages to write queries in, the freshness policy and the topics - small enough that one call can settle whether a question is in scope. A topic page lists its concepts |
| `search` | Free text to concepts. Returns a short ranked list with each hit's context schema, a verdict on how well the question reached the release, and guidance on what to do next |
| `resolve` | Up to five concepts in one call, for one place, one date and one context. Returns the applicable facts, one citation per cited page, a status per concept and named gaps, plus any user facts and decision rule the concept publishes |
| `get_evidence` | Read the full original excerpts behind up to five evidence identifiers |

#### Places

A request names where the user lives in parts - country, canton, city -
each as a name in any of the country's languages or as a code. The server
turns them into codes with the release's own place register before anything
else: case, diacritics, ASCII spellings of umlauts and generic words around
a name are ignored, a city supplies its canton, and nothing is guessed. A
part the register does not hold (a quarter, a postcode, a misspelling) is
reported back, the request runs for the broader place that was recognised,
and the guidance says so. A name several municipalities or two cantons
share, and a city that does not lie in the canton given, are rejected with
the candidates listed. Because the register is data of the release, an
upgraded server cannot change which facts an attested release answers with.

#### Statuses

| Status | Meaning | What the caller does |
| --- | --- | --- |
| `SUPPORTED` | Facts and citations returned for the requested scope | Answer from the statements only, cite the returned URLs |
| `NEEDS_CONTEXT` | A required context field is missing | Derive it from what the user said, ask only if it cannot be derived, call again |
| `OUT_OF_COVERAGE` | Nothing is published for this jurisdiction, date, concept or context | Say so; do not answer from general knowledge as if grounded |
| `STALE` | Facts returned, but the snapshot is past its freshness window | Present them as published on the snapshot date and tell the user to check the cited page |

#### Named gaps

A well-formed request that cannot be answered is not an error. It returns a
status with a named reason and the values that are published:

| Dimension | When |
| --- | --- |
| `jurisdiction_not_covered` | The concept is published for another place |
| `more_specific_jurisdiction_available` | Answered, and a narrower published concept exists below the request |
| `more_specific_jurisdiction_not_published` | Answered from a broader level, while the topic publishes a narrower level for other places only |
| `date_outside_coverage` | The date lies outside the published validity window |
| `concept_not_published` | Unknown concept identifier |
| `context_not_covered` | An unknown context field, or a value with no published facts |
| `review_status_not_met` | Only unreviewed facts exist and the caller asked for reviewed ones |

#### Search behaviour

Search never widens scope: a hit says that a concept exists, and `resolve`
decides whether it applies.

- **Ranking.** Each query token counts once per concept, at the strongest
  field it appears in (label and alias, sample question, description,
  statement), weighted by how rare it is across the catalogue, so a question
  is ranked by its distinctive words. The score is then adjusted by the
  concept's authority, which follows from the basis of its facts and the
  review of its statements.
- **Verdict.** Every result reports whether the question's distinctive words
  actually reached a published concept: `strong` (resolve the hits), `weak`
  (the hits rest on incidental words; decline unless one is clearly the
  subject) or `none`. Weak and empty results carry the scope statement, so
  an outside question can be declined in the same call.
- **Query languages.** The release states which languages it can be searched
  in, best first, measured from the publisher's own terms it carries. The
  server names them in its connect-time instructions, in the tool
  description and on the coverage root. A question in a listed language is
  sent as asked; any other question is sent once with its key terms
  translated before the caller declines.
- **Place-aware search.** With the user's place, concepts that cannot apply
  there are left out of the ranking and named separately, with the
  instruction not to resolve them and to carry over no rule, office, fee or
  deadline from them.
- **Retrieval mode.** Lexical search is the default and needs no model.
  Optional hybrid search adds semantic candidates from a release-bound index
  and fuses the two rankings; if the index or the embedding service is
  unavailable, the server reports that it fell back to lexical search rather
  than failing. Every result says which mode answered it.

#### Errors

Malformed requests, unknown tools, unknown evidence, topic or release
identifiers, and operational failures return a typed error with the dotted
path of each issue. An unknown concept identifier inside `resolve` is a gap,
not an error, so one bad identifier does not fail a call of five.

### 4.4 Call pattern and efficiency

The intended sequence for a question in scope is two calls: `search` with
the question and the user's place, then `resolve` with the selected
concepts, the place, the date and the derived context. `get_coverage` is for
the case where the assistant is unsure the question is in scope at all, and
`get_evidence` only when the user wants a verbatim quote; the tool
descriptions say so. Requirements that keep this true:

- the coverage root stays within a few kilobytes, and no discovery page
  exceeds the tool-output limit of common clients;
- search returns three hits by default and at most ten;
- one `resolve` carries up to five concepts, so one question needs one
  resolution call rather than one per level of government;
- every hit carries what the following `resolve` needs;
- every result tells the caller when to stop.

## 5. The knowledge pipeline

A knowledge pack goes from an idea to a published release in stages, each
resumable and each skipped when its inputs are unchanged.

| Stage | What happens | Output |
| --- | --- | --- |
| Scope and questions | Decide what the pack covers, at which levels and in which languages; write the user questions, their expected answers and the trap each one sets, before any page is fetched | Acceptance-test document |
| Catalogue | One entry per source: start URL, host and path allowlist, authority, jurisdiction, language and crawl budget; explicit pages and PDFs listed | Source catalogue |
| Acquire | Bounded crawl that follows nothing outside the catalogue, saves every response byte for byte, and reports dead links, access denials and soft error pages as catalogue decisions | Saved pages, gap report |
| Extract | Labelled text blocks with code-point offsets and hashes, plus a reading view per page; no model involved, no OCR | Text dataset |
| Curate | Topics, concepts and facts with citations as block ranges, aliases in users' words, source terms copied verbatim from the excerpts, sample questions in each query language, the institution registry and the basis rules | Curation file |
| Build | Resolve every citation onto the text dataset, pin it with hashes, verify the source terms, embed the place register, run the validator | Release, build report |
| Review | A person confirms, corrects or rejects each statement against its excerpt; the reviewer's name is recorded | Review statuses, rebuilt release |
| Index | Optional semantic index, bound to the release identifier and content digest | Index file |
| Test | Replay the acceptance suite and the wider regression pack, lexically and hybrid | Reports |
| Attest | Run the readiness gates and bind the record to the bytes of the release | Readiness record |
| Document | Coverage and limitations documents, with every number read from the release and its reports | Documents |
| Publish | Container images and a downloadable release bundle | Published artefacts |

Curation is the only way knowledge changes. The build never edits a
statement and never guesses: when a source page changes, a citation is
relocated through its stored anchor if the text is still there, and the fact
is dropped with a stated reason if it is not. A release with fewer facts is
written; an invalid one is not.

Three routes write curation, all producing the same file: a person or an
assistant reading the reading views, a model proposing candidates that are
packaged as reviewable candidate facts, and the review console. Whichever
route wrote a statement is recorded as its provenance.

## 6. Quality gates

### 6.1 Acceptance suite

Each pack carries a suite of cases, one per user question, holding the
question as typed, the expected answer, the trap a generic answer falls
into, the tool steps a replay executes, and the claims the served facts must
carry - each claim naming the phrases its statement and its excerpt must
contain, verbatim in the source language. Decline cases assert the status
and the named gap, never an answer. Cases are replayed against the same code
the server answers with, without a model and without network, on every
build; a failing blocking case stops the pipeline. Rewording a fact breaks
the claims that quote it on purpose, so a changed expectation is visible in
review.

A wider regression pack adds breadth beyond the acceptance document: plain
questions in the users' languages, noisy questions, translated questions and
declines. It is a measurement rather than a gate; a case that fails is
quarantined with a stated reason and keeps running.

### 6.2 Readiness

A release is *ready* only when a readiness record sits next to it naming its
exact bytes, with every gate passed:

| Gate | Requirement |
| --- | --- |
| G1 | The release validates against the text dataset and the saved responses, which are re-hashed |
| G2 | The build dropped no fact |
| G3 | Every blocking acceptance case passes the model-free replay |
| G4 | The release has runway: its stale date lies a policy margin beyond the attestation |
| G5 | Graded live-caller sessions meet the answer policy, which a pack sets to required, advisory or off |
| G6 | The committed acceptance report is current for this release and this suite |

Any change to the release file, down to a reworded limitation, invalidates
readiness; a changed suite requires the gates to be run again. The server
can be started in a mode that refuses to serve, or to report healthy,
without a matching record, and image builds refuse a candidate. Readiness
does not require human-reviewed facts: it requires the review counts to be
recorded and stated.

### 6.3 Answer quality

The model-free replay cannot judge what an assistant composes. A harness
runs the same cases through a real MCP client and model, records the
transcript, the call count and the bytes, and has each session graded
against the case's criteria: did the answer state the rule, cite the right
pages, name what is not covered, ask instead of guessing, and stay inside
the call and byte budget. The grades are committed bound to the release they
were produced on, and feed gate G5.

## 7. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Offline serving | No model, no credentials and no outbound request at serving time; semantic search is optional and, where used, runs against a local embedding service |
| Determinism | The same release and request produce the same result; retrieval settings are configuration, never hidden thresholds of applicability |
| Integrity | A release that fails validation is never written and never served; hashes bind every excerpt to the page it was cut from |
| Performance | Two calls and a few kilobytes for a typical question; discovery pages small enough to be read whole |
| Transports | stdio and Streamable HTTP, with a health route that reports the release, its digest and its readiness |
| Observability | One log line per call with tool, status, bytes and latency; a health payload naming the release and its review counts |
| Security | All tools read-only; nothing a caller sends changes a release; only public information is served; no secret ever enters the repositories |
| Etiquette | Acquisition respects robots.txt and its delays, identifies itself, and stays within a per-host rate limit and a declared budget |
| Reproducibility | A clean clone installs in one command and runs the full test suite without network; images are built by the same script that CI runs |
| Rights in quoted text | Official texts remain the property of their publishers and are reproduced only as cited evidence |

## 8. Delivery and operation

- **Container images.** An all-in-one image per pack, and a slim server
  image that takes any pack, with the embedding model in a separate sidecar
  container. One Compose file starts either shape; an optional profile adds
  a browser-based demo client.
- **Packages.** The core, runtime, server and build packages are published
  so that the server can be run from a package index against a release
  directory, without a container.
- **Release bundle.** Each published release is downloadable as an asset
  set (the release, its readiness record and its index), so that it can be
  served without an image or a clone.
- **Hosting.** A single infrastructure template stands the server up behind
  HTTPS for evaluation or internal use.
- **Client setup.** The server prints ready-made client configurations and
  sends its caller rules - scope statement, query languages, the two-call
  pattern - as MCP instructions at connect time, for clients that pass them
  to the model.
- **Refresh.** Sources are re-fetched deliberately, not continuously: a new
  snapshot is a new run, a new build and a new release identifier, through
  the same acceptance and readiness gates. The freshness policy makes an
  un-refreshed release report `STALE` rather than answer silently.

## 9. Components

| Component | Responsibility |
| --- | --- |
| `core` | Release, curation, acceptance and readiness models; the validator; basis labels and the ranking policy; the place index |
| `runtime` | Serving semantics: catalogue walk, lexical and hybrid search, resolution, evidence, acceptance replay |
| `mcp-server` | The MCP application: tool registration, transports, health, logging, readiness enforcement |
| `ingestion` | Catalogue planning, the bounded and polite crawler, the place-register importer |
| `extraction` | Pages to labelled text blocks with offsets, hashes and reading views |
| `build` | Curation file to validated release: citation resolution and relocation, source-term verification, suite loading |
| `concepts` | Model-proposed concept candidates with structural validation, review and basis classification |
| `knowledge-builder` | One command that runs the pipeline stages, skips unchanged ones and writes the stage reports |
| `admin-console` | The curator's and reviewer's local application: packs, sources, runs, reading views, curation workbench, review queue, release and readiness, tool sandbox |

The serving side imports nothing from the build side. Code and knowledge
live in separate repositories: the code carries no assumption about any
particular pack, and a pack is selected by argument.

## 10. Coverage of the first knowledge bases

Coverage is a data effort, not a code effort: language is a field on every
excerpt, jurisdiction a field on every fact, and a release declares what it
covers.

| Pack | Content |
| --- | --- |
| Arrival and life in Zurich (default) | What a foreign national meets when moving to and living in the Canton and City of Zurich: entry and visas, residence permits and registration, family reunification and marriage, housing, social insurance, tax at source, health and accident insurance, driving licences, unemployment, occupational and private pensions, family benefits, naturalisation, political rights, departure, and everyday municipal services. Federal, cantonal and municipal levels, with the other cantons reachable through their migration-office contacts |
| A municipality | The resident-facing services of one commune - moving in, civic life, schools, social services, waste and infrastructure - which proves that the same format and tools carry a municipal pack of many topics |
| Swiss residence, full coverage | The residence topic at the federal level and for all 26 cantons, in every language each source publishes: the credible extension of the first pack |

Each pack declares its own scope statement and out-of-scope list, and the
coverage root serves both, so a caller learns the boundary in one call.

## 11. Out of scope

The product does not give legal advice. Its statements summarise pages that
are mostly published in another language and are not official translations;
the excerpt is always the authoritative text. It performs no live web search
and passes no query through to a third-party service at answer time. It does
not publish fees, processing times, availability, tariff tables or
calculators unless a covered page states them, and it does not answer
questions outside the topics a pack declares. Human review is a check of
each statement against its excerpt by a named reviewer; it is not a legal
review, and every release states in absolute numbers how much of it has been
reviewed.

## 12. Extensibility

The format and the tools carry what a new pack needs without code changes:
new topics, further cantons and municipalities, additional evidence
languages, and packs on subjects other than residence. Two extensions do
touch the code and are planned as such: another country needs its own place
register and a widening of the jurisdiction codes, and a pack whose sources
publish in a language the search index does not yet weigh needs its terms
measured the way the existing ones were. Everything else - more sources,
more concepts, more reviewers - is pipeline work against the gates
described above.
