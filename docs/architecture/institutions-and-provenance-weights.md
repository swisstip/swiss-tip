# Institutions, basis and provenance weights

**Last update:** 21 September 2026

**Status:** implemented in `swisstip.core.basis`, the release models and
validator, the build (which derives the article of a law citation from the
heading path, section 3.3, and reports a hand-written norm whose article
differs), the runtime service, the acceptance check, the concept-extraction
pipeline (a third call per chunk classifies the basis of every retained
proposal, [concept-extraction.md](concept-extraction.md) section 4.7) and
the admin console (the pack form edits the registry, the page defaults and
the ranking policy; the review queue shows the basis of every fact and
citation and filters by basis kind; the fact form sets a citation's basis).
A basis may carry `refers_to`, a norm the excerpt names without reproducing
it, appended to the label. The served releases carry the registry, the page
defaults, the basis of every citation and the ranking policy in their
curation files. The reading of every served excerpt behind that data is
`releases/<pack>/basis-review.md` in swiss-tip-mvp: for `mvp-zurich`
written by an assistant and not reviewed by a person; for
`mvp-wallisellen` (a registry of six City of Wallisellen institutions and
the basis of each of its 99 citations) classified by assistant subagents
with the prompt `basis_classification_v1.md` applied verbatim per page and
confirmed by one person. The numbers of section 6 were measured on an
earlier release with a scratch script that is not committed and were not
rerun on the current one.<br>
**Relation to other documents:** extends the release format
([release-format.md](release-format.md)), the tool contracts
([tool-contracts.md](tool-contracts.md)) and the lexical ranking of the
runtime ([packages/runtime/README.md](../../packages/runtime/README.md)).

## 1. Purpose

Three things a caller should get, each stated on its own:

1. **Who published the page.** A citation names a URL and a publisher string.
   It should also name the institution in a form a caller can reason about:
   the level of the state (federal, cantonal, municipal) and the jurisdiction
   it speaks for.
2. **What the excerpt is.** The basis of a statement is a property of the
   excerpt, not of the page it was found on: an article of the AIG quoted on
   a Canton of Zurich page is federal law, the canton's own FAQ answer next to
   it is cantonal authority guidance, and a ch.ch paragraph is a portal
   summary. The basis is stated on every served fact as "Federal act: AIG,
   SR 142.20, Art. 12" or "Cantonal authority guidance", whichever host the
   page lives on.
3. **Weights that rank.** The basis kind (law, ordinance, directive, guidance,
   summary) and the review of the statement carry internal weights that
   influence ranking. The publisher and its level never do: a page on
   `admin.ch` is not more authoritative than one on `zh.ch`, and a municipal
   page is authoritative for a narrower jurisdiction, not less authoritative.

## 2. Institutions: who published the page

### 2.1 Registry in the curation file

A pack-level list replaces the `publishers` map. The expert owns it, the
console's pack form edits it, the build copies it into the release. The
example shows five of the fifteen entries the current pack needs: seven
federal (SEM, Fedlex, ch.ch, FOPH, BSV, ZAS, ESTV), six cantonal (Migration
Office, Cantonal Tax Office, Road Traffic Office, Health Directorate, Office
for Municipalities, SVA Zürich) and two municipal (Population Office and
Naturalisation Division of the City of Zurich).

```yaml
institutions:
  - institution_id: ch-sem
    name: State Secretariat for Migration SEM
    native_name: Staatssekretariat für Migration SEM
    level: federal
    body: administration
    jurisdiction: CH
    urls: [www.sem.admin.ch]
  - institution_id: ch-fedlex
    name: Fedlex, the Swiss federal law collection (Federal Chancellery)
    level: federal
    body: law_collection
    jurisdiction: CH
    urls: [www.fedlex.admin.ch, fedlex.data.admin.ch]
  - institution_id: zh-steueramt
    name: Canton of Zurich, Cantonal Tax Office
    native_name: Kantonales Steueramt Zürich
    level: cantonal
    body: administration
    jurisdiction: CH-ZH
    urls: [www.zh.ch/de/steuern-finanzen/]
  - institution_id: zh-sva
    name: SVA Zürich, the cantonal social insurance office
    native_name: Sozialversicherungsanstalt des Kantons Zürich
    level: cantonal
    body: public_law_body
    jurisdiction: CH-ZH
    urls: [svazurich.ch]
  - institution_id: zh-261-personenmeldeamt
    name: City of Zurich, Population Office
    native_name: Personenmeldeamt der Stadt Zürich
    level: municipal
    body: administration
    jurisdiction: CH-ZH-261
    urls: [www.stadt-zuerich.ch/de/lebenslagen/einwohner-services/umziehen-melden/]
```

| Field | Values | Meaning |
| --- | --- | --- |
| `institution_id` | identifier | Stable within the pack; documents and evidence reference it |
| `name` | string | English display name; becomes the served `publisher` |
| `native_name` | string, optional | The institution's own name in its language; for the coverage documents and a later served field |
| `level` | `federal`, `cantonal`, `municipal` | The tier of the state; `intercantonal` reserved |
| `body` | `administration`, `law_collection`, `portal`, `public_law_body` | An office of the executive; the official publication of legislation; an information portal; an institution under public law executing a mandate |
| `jurisdiction` | `CH`, a canton code, a municipality code | Whom the institution speaks for; must match `level` |
| `urls` | host, or host plus path prefix | Attribution rules for the build; the longest matching prefix wins; not copied into the release |

The registry carries no weight: the institution is named and served, never
ranked (section 4).

### 2.2 Attribution in the build

For every cited text record the build resolves the institution in this
order and stops with a `BuildError` when nothing resolves:

1. The registry rule with the longest prefix matching the record's
   `source_url` (host first, then host and path).
2. The record's catalogue entries in `source_registry`: `canonical_authority`,
   `authority_level`, `jurisdiction` and `municipality` define an institution
   that the build adds to the release under a generated ID and lists in the
   build report, so the expert can name it in the registry. When a record
   matches several catalogue entries, the longest match of
   `allowed_path_prefixes` wins.

On the current release every one of the 43 cited documents matches at least
one catalogue entry and three match two entries of the same authority
(section 6), so the fallback alone would name every document.

`publisher` on documents, evidence and citations is the institution's
`name`, so a caller that reads only `publisher` sees the specific office
rather than a host-wide string.

### 2.3 Release format and contract

| Where | Change |
| --- | --- |
| `institutions` (new release part) | The registry entries without `urls` |
| `documents`, `evidence` | `institution_id`; on evidence denormalised like `publisher`, so citations and `get_evidence` need no join |
| `manifest` | `institution_levels`, documents per level, counted like `review_statuses` |
| `Citation`, `Evidence` (contract) | `level` (`federal`, `cantonal`, `municipal`) and `jurisdiction`, the institution's; `publisher` becomes the specific office |
| `CoverageRoot` (contract) | `institution_levels`, about 60 bytes |

Flat fields rather than a nested `institution` object: `resolve` payloads of
the added topics already reach 28 KB, a citation is listed once per page and
concept, and two short fields cost about 50 bytes per citation while a nested
object repeating the name costs twice that. `body` and `native_name` are not
served in the first step; `native_name` is the candidate for a later additive
field when a caller answering in German needs the office's own name.

## 3. Basis: what the excerpt is

### 3.1 The basis record

Every citation of a fact resolves to a basis with three parts. It describes
the content of the excerpt, not the page: the level is the level of the body
that enacted the rule or wrote the text, and the kind is what the text is.

| Part | Values | Meaning |
| --- | --- | --- |
| `level` | `federal`, `cantonal`, `municipal` | Who enacted the norm or wrote the guidance; for a summary, the level of the rules it summarises |
| `kind` | `act`, `ordinance`, `treaty`, `directive`, `guidance`, `directory`, `summary` | See the table below |
| `norm` | string; required for `act`, `ordinance`, `treaty`, `directive` | The identity of the legal basis: abbreviation, SR or LS number and article, or the directive's number |

| `kind` | What the excerpt is | Examples in the current release |
| --- | --- | --- |
| `act` | A law passed by a parliament | AIG (SR 142.20), Vested Benefits Act FZG (SR 831.42) |
| `ordinance` | An ordinance of a government or department | VZV (SR 741.51), Tax at Source Ordinance QStV (SR 642.118.2), refund of AHV contributions (SR 831.131.12) |
| `treaty` | An international agreement | The Agreement on the Free Movement of Persons FZA (SR 0.142.112.681) and its Annex I |
| `directive` | An authority's binding instruction or circular | Zürcher Steuerbuch 87.3; SEM directives when cited |
| `guidance` | An authority's own explanation of a rule or procedure: FAQ, procedure page, information sheet | The SEM free-movement FAQ, the Canton of Zurich procedure pages, the City of Zurich move-in page, the SVA Zürich pages |
| `directory` | An authority's list of offices | The SEM list of cantonal migration offices |
| `summary` | A plain-language restatement by a body that is not the competent authority | The ch.ch pages |

### 3.2 The served label

The build composes one string per basis, served as `basis`, so a caller can
quote it without parsing:

| `kind` | Label | Example |
| --- | --- | --- |
| `act` | `{Level} act: {norm}` | Federal act: AIG, SR 142.20, Art. 12 |
| `ordinance` | `{Level} ordinance: {norm}` | Federal ordinance: VZV, SR 741.51, Art. 42 |
| `treaty` | `International agreement: {norm}` | International agreement: FZA, SR 0.142.112.681, Art. 2 |
| `directive` | `{Level} directive: {norm}` | Cantonal directive: Zürcher Steuerbuch 87.3 |
| `guidance` | `{Level} authority guidance` | Cantonal authority guidance |
| `directory` | `{Level} authority directory` | Federal authority directory |
| `summary` | `Portal summary of {level} rules` | Portal summary of federal rules |

The label and the citation are independent by design. A fact whose excerpt
quotes AIG Article 12 on the Canton of Zurich migration page is served with
`basis` "Federal act: AIG, SR 142.20, Art. 12" and a citation whose
`publisher` is "Canton of Zurich, Migration Office" at `level` `cantonal`;
the canton's own answer two paragraphs down is "Cantonal authority guidance"
with the same citation.

### 3.3 Where the basis is set

The curator sets it per citation in the curation file, and the build fills a
default for every citation that does not carry one:

```yaml
page_basis:                          # default basis of every excerpt of a page, by host or prefix
  www.fedlex.admin.ch/eli/cc/2007/758/: {level: federal, kind: act, norm: "AIG, SR 142.20"}
  www.fedlex.admin.ch/eli/cc/1976/2423_2423_2423/: {level: federal, kind: ordinance, norm: "VZV, SR 741.51"}
  www.zh.ch/de/steuern-finanzen/steuern/treuhaender/steuerbuch/: {level: cantonal, kind: directive, norm: "Zürcher Steuerbuch 87.3"}
  www.sem.admin.ch/sem/de/home/sem/kontakt/kantonale_behoerden/: {level: federal, kind: directory}
  www.ch.ch: {level: federal, kind: summary}
concepts:
  - concept_id: zh-eu-registration
    facts:
      - fact_id: zh-eu-registration-1
        evidence:
          - document_id: doc-66553fde673cc7f06804
            first_block: 63
            last_block: 66
            basis: {level: federal, kind: act, norm: "AIG, SR 142.20, Art. 12"}   # the law quoted on the cantonal page
```

Resolution order per citation:

1. The citation's own `basis`.
2. The `page_basis` rule with the longest matching prefix. For `act`,
   `ordinance` and `treaty` the build appends the article from the excerpt's
   heading path, which the anchors already store: every Fedlex citation
   carries "Art. N" there (section 6). Two things the reading of the current
   release found must be handled first: Fedlex glues footnote numbers to
   article and paragraph numbers, so the AIG heading `Art. 4384` is
   Article 43 with footnote 84, and the Agreement on the Free Movement of
   Persons numbers the articles of its Annex I from 1 again, so the chapter
   segment of the heading path decides between the agreement
   (`I. Grundbestimmungen`) and the annex (`I. Allgemeine Bestimmungen`,
   `II. Arbeitnehmer` and so on). Until the build does both, the pack writes
   the norm out per citation. A rule without a `level` takes the
   institution's level.
3. Otherwise `guidance` at the institution's level, with no `norm`.

The build report lists, per fact, the resolved basis and which of the three
rules produced it, so the curator sees every default and overrides the ones
that are wrong, which is the case of a law quoted on an authority's page.
With `--update-curation` nothing is written into the citations: an authored
basis and a derived one must stay distinguishable.

The catalogue is not changed for this: its `source_kind` describes the page,
stays what it is, and is not read by the build for the basis. A catalogue
change would need a new run directory, which the basis does not.

### 3.4 Release format and contract

| Where | Change |
| --- | --- |
| `facts[].provenance` | `basis`: the highest-weighted basis among the fact's citations (a fact that cites the law and a FAQ rests on the law), as `level`, `kind`, `norm` and the composed `label` |
| `evidence` | `basis` with the same four fields, per excerpt |
| `manifest` | `basis_kinds`, facts per kind, counted like `review_statuses` |
| `Fact` (contract) | `basis`, the label; stated once on `ConceptResolution` when every served fact of the concept shares it, absent on the facts then, like the review fields |
| `Evidence` (contract) | `basis`, the label of that excerpt |
| `CoverageRoot` (contract) | `basis_kinds` |
| `ResolveResult.guidance_for_caller` | When a concept's served facts mix a `summary` with an `act`, `ordinance`, `directive` or `guidance` basis, one sentence is added: "Where a portal summary and the cited law or authority page differ, the law's excerpt is the more exact source." Otherwise unchanged |
| `ConceptResolution.citations` | Ordered by the highest basis weight among the evidence resting on the page, then first-cited order, so the law is cited before the FAQ and the FAQ before the portal |

Example of a resolved concept, abridged:

```json
{"concept_id": "aig-registration", "status": "SUPPORTED", "answering_jurisdiction": "CH (federal)",
 "review_status": "human-reviewed", "reviewed_on": "2026-09-14", "reviewed_by": "...",
 "basis": "Federal act: AIG, SR 142.20, Art. 12",
 "facts": [{"fact_id": "aig-registration-1", "statement": "...", "jurisdiction": "CH", "evidence_ids": ["e-aig-registration-1-1"]}],
 "citations": [{"evidence_ids": ["e-aig-registration-1-1"], "source_title": "AIG / LEI / FNIA, SR 142.20",
                "publisher": "Fedlex, the Swiss federal law collection (Federal Chancellery)", "level": "federal",
                "jurisdiction": "CH", "url": "https://www.fedlex.admin.ch/eli/cc/2007/758/de", "language": "de",
                "accessed_on": "2026-09-10"}]}
```

A label costs about 40 bytes per fact when the facts of a concept differ and
once per concept when they agree, which on the current release is the common
case (every fact of a concept rests on excerpts of one kind in 56 of 64 concepts).

## 4. Provenance weights

### 4.1 Two factors per fact

Two things make a fact more or less authoritative: what its excerpt is, and
how its statement came about. Neither is the level of the state or the host
of the page. A municipal page is authoritative for a narrower jurisdiction,
not less authoritative, and the containment rule of `resolve` already handles
that; weighting by level would push the City of Zurich procedure below the
SEM FAQ for a Zurich question, the opposite of what a caller needs. The
institution is served (section 2) and never weighted.

Basis weight `a` of an excerpt, from the basis `kind`:

| `kind` | Weight | Why |
| --- | --- | --- |
| `act`, `ordinance`, `treaty` | 1.00 | The rule itself. An ordinance is not weighed below the act: both bind, and the ordinance is often the more specific rule (the twelve months of VZV Article 42) |
| `directive` | 0.95 | The competent authority's binding practice |
| `guidance` | 0.90 | The competent authority's own explanation, not binding |
| `directory` | 0.90 | An authority's list of offices |
| `summary` | 0.75 | A restatement by a body that is not the competent authority |

Statement weight `p` of a fact, from its provenance:

| `provenance.kind` | Weight | `provenance.review_status` | Weight |
| --- | --- | --- | --- |
| `curated-statement` | 1.0 | `human-reviewed` | 1.0 |
| `source-section` | 0.9 | `model-candidate-automated-review` | 0.8 |
| `model-candidate` | 0.8 | `assistant-authored-unreviewed` | 0.7 |
| | | `automatically-derived-unreviewed` | 0.6 |

`w(fact) = a(fact) x p(fact)`, where `a(fact)` is the highest basis weight
among the fact's citations and `p(fact)` is the product of its two provenance
weights. A concept's authority `A(concept)` is the highest `w` among its
facts: a concept that rests on at least one reviewed statement of the law is
as authoritative as that fact, and a clarifying portal fact added later does
not penalise it. The mean over facts is the alternative for a pack where most
facts are unreviewed; section 6 measures both.

The tables live in the curation file as `ranking_policy` and are copied into
the manifest, so a release is self-contained and the server reads nothing but
the release; a pack without the block gets these defaults. No tool serves the
numbers. The release file is public, so "internal" means outside the wire
contract, not secret.

### 4.2 Where the weights act

- **Lexical search.** `score' = score x (b + (1 - b) x A(concept))` with
  `b = 0.7`, then the relative-score floor. The prior spans 0.7 to
  1.0: a reviewed law-backed concept keeps its score, a reviewed summary-only
  concept loses 7.5 percent, an unreviewed automatically derived section
  loses about 15 percent. That reorders near-ties and never overrides a
  clearly better lexical match; section 6 measures it on the current release.
- **Hybrid search.** The same factor is applied to each retriever's own score
  before ranks are taken: to the lexical score as above and to the cosine
  similarity of the semantic candidates after the candidate threshold.
  Reciprocal-rank fusion then runs unchanged. The factor must not be applied
  to the fused score: adjacent fused ranks differ by about two percent, so a
  ten percent prior would move a concept by several ranks in one mode and by
  one in the other.
- **Resolve.** Selection does not weigh: every applicable fact is served,
  and facts stay in curation order, because that order is the curator's
  reading order (rule, exception, procedure) and therefore content. The
  weights act on the citation order and the guidance sentence of section 3.4
  only.
- **KB2.** This is where the weights matter. The nationwide pack will hold
  source sections that nobody read next to curated, reviewed facts
  ([TODO.md](../../TODO.md), "KB2"); the search index over statements and
  headings planned there takes `w(fact)` as its document prior, so a reviewed
  curated statement outranks an automatically derived section on the same
  subject.

The `search` description says that the score includes a prior for the basis
of a concept's facts and the review of its statements; the score stays "not
comparable across releases".

## 5. Validation

The validator checks, in its order:

- Registry: unique institution IDs; `level` and `jurisdiction` consistent
  (`federal` if and only if `CH`, `cantonal` if and only if a canton code,
  `municipal` if and only if a municipality code), mirroring the catalogue
  validator; `body` within its enumeration.
- Every document names an institution of the registry; every evidence record
  names its document's institution and carries a basis.
- Basis: `level` and `kind` within their enumerations; `norm` non-empty for
  `act`, `ordinance`, `treaty` and `directive`; the label equal to the
  composition of section 3.2; a fact's `provenance.basis` equal to the
  highest-weighted basis of its evidence.
- Every fact's jurisdiction is contained in the jurisdiction of every
  institution it cites: a cantonal fact may rest on a federal or a cantonal
  page, a federal fact never on a cantonal or municipal page alone. The
  current release passes with zero violations (section 6).
- A fact published for `CH` has a basis of level `federal`; a cantonal or
  municipal basis cannot ground a federal fact.
- The manifest's `institution_levels` and `basis_kinds` equal the documents
  and the facts; `ranking_policy` names every kind and status with a weight
  in (0, 1] and a floor in [0, 1].

## 6. Measured effect

Measured on `mvp-zurich-2026-09-15-v3` with a scratch script over the
released service in lexical mode, without the semantic index; the
measurement has not been rerun on a later release. The basis of
every excerpt was the page default of section 3.3 (Fedlex pages as law, the
Zürcher Steuerbuch as a directive, the SEM office list as a directory, every
other page as guidance, and in the second variant ch.ch as a summary); no
per-citation override was simulated, so a law quoted on an authority's page
counted as guidance. The query fixture is the committed holdout of 42
questions, 35 of them supported, authored for `mvp-zurich-2026-09-13-v5` and
run with its identifiers, so the baseline differs from the numbers in the
runtime README. The acceptance suite is
`releases/mvp-zurich/acceptance.yaml`.

Attribution, basis defaults and containment:

| Check | Result |
| --- | --- |
| Cited documents matched to a catalogue entry | 43 of 43; three match two entries of the same authority |
| Documents per level | 26 federal, 14 cantonal, 3 municipal |
| Fedlex citations whose anchor names the article | 38 of 38 |
| Facts by page default | 184 guidance (23 of them ch.ch), 37 act or ordinance, 26 directory, 6 directive |
| Facts whose page's jurisdiction does not contain the fact's | 0 of 253 |

Search prior with `b = 0.7` and the maximum as the concept aggregate unless
stated. Every fact of this release is a reviewed curated statement, so only
the basis weight varies:

| Setting | Recall at 3 | Recall at 5 | First hit, 34 single-concept cases | Top-3 order changed | Acceptance suite | Pinned search steps |
| --- | --- | --- | --- | --- | --- | --- |
| No prior | 0.443 | 0.500 | 9 | 0 of 35 | passes | unchanged |
| Prior, ch.ch as guidance | 0.443 | 0.500 | 9 | 3 of 35 | passes | unchanged |
| Prior, ch.ch as summary | 0.443 | 0.514 | 9 | 4 of 35 | passes | unchanged |
| Prior with `b = 0.5`, ch.ch as summary | 0.443 | 0.514 | 8 | 5 of 35 | passes | unchanged |
| Mean instead of maximum, ch.ch as guidance | 0.443 | 0.500 | 9 | 5 of 35 | passes | unchanged |

With ch.ch as a summary, ten concepts drop to authority 0.75:
`permit-types`, `permit-renewal`, `permit-lost`, `family-eu-efta`,
`family-requirements`, `family-member-rights`, `third-country-work-procedure`,
`eu-permit-mobility`, `eu-self-employment` and `eu-job-search`. In the four
queries whose top three changed, the expected concept never moved down; in
one (`fr-06`) the federal work-permit concept rose above an unrelated
health-insurance concept.

Reading: on KB1 the prior is almost flat, because 44 of the 64 concepts rest
on guidance or the SEM directory only and every statement is reviewed, and
it is safe: no acceptance case and no pinned search step changes. Its value
lies in the ch.ch reclassification and the per-citation overrides on KB1, and
in the unreviewed material of KB2.

## 7. Deliberately not done

- Weights by level of the state or by host (section 4.1).
- Reading the basis from the catalogue's `source_kind`: it describes the
  page, not the excerpt.
- Reordering facts within a concept.
- A request-side filter such as `basis_kinds` or a minimum authority;
  `reviewed_only` covers the review dimension, and a basis filter can be
  added later as an optional field.
- Serving the numeric weights.
