# Knowledge graph

**Last update:** 24 September 2026

The knowledge graph is the orientation a calling model reads **before** it
searches: which level of the state sets the rules of a domain, who carries them
out and decides, which office a person deals with at their place, which laws
and sources are authoritative, and what trips a generic answer up. The server
serves it through the tool `get_knowledge_graph`; the server instructions and
every other tool's description tell the caller to start each new subject with
it, then search and resolve in the next turn.

The graph is built like a pack: a catalogue of official pages, fetched and
extracted by the same pipeline, curated into `graph.yaml` with every claim
citing an exact excerpt, reviewed in the admin console, compiled and validated
into a hashed `graph.json`, and embedded in each pack's release. It is
orientation, not evidence: the caller answers from `resolve`'s facts only.

- Implemented and tested: the model and validator, the compiler, Derive, the
  merge of agent proposals, the graph pipeline, the tool and its selection,
  the admin console screens, the refresh, the Claude Code skill and agents.
- Planned (see [next steps](../product/next-steps.md)): the provider route
  (`swisstip-concepts` writing graph candidates with a configured model), an
  approval inbox before processing, the model step started from the console.

## 1. Where it lives

| File | Repository | Role |
| --- | --- | --- |
| `graphs/<graph>/sources.json`, `sources.md` | packs | The graph's own pages (the constitution, federalism overviews); everything else it states rests on excerpts the packs already carry |
| `graphs/<graph>/graph.yaml` | packs | The curation: institutions and page rules of its own sources, the packs it derives from, role rules, nodes and edges with provenance and citations |
| `graphs/<graph>/checks.yaml` | packs | Orientation checks, replayed on every compile |
| `graphs/<graph>/graph.json` | packs | The compiled graph (`swiss-tip-graph/v1`), validated and hashed |
| `graphs/<graph>/graph-report.json`, `checks-report.json`, `pipeline-report.json` | packs | Build outcome, check results, stage record |
| `.local/graph-<graph>/` | packs, not in Git | The run (saved pages, text dataset) and the console's working state (audit, jobs, rejections) |
| `releases/<pack>/curation.yaml` | packs | `knowledge_graph: ../../graphs/<graph>/graph.json` and `graph_nodes` on each topic |
| `packages/core/.../release.py`, `graph.py` | code | Models, relation vocabulary, validator |
| `packages/build/.../graph_*.py` | code | Curation model, compiler, Derive, merge, embed, CLI (`swisstip-graph`) |
| `packages/runtime/.../graph.py` | code | Selection and the tool result |
| `apps/knowledge-builder/.../graph_pipeline.py` | code | `swisstip-build <graph> --graph` |
| `apps/admin-console/.../graph_data.py`, `screens/graph.py` | code | The console's graph screens |

## 2. Model

A **node** has a `node_id` of the form `<kind>.<slug>`, a `kind` (level,
principle, domain, role, institution, law, source, place, pitfall), a label,
`names` by language (each verbatim in a cited excerpt), a summary of at most
400 characters, keywords, an optional level, place (jurisdiction code) and SR
number, provenance and evidence.

An **edge** is one claim: `from_id`, `relation`, `to_id`, a one-sentence
`statement` (at most 300 characters), an optional `place` (served by
containment, like a fact: for that place and the places inside it),
provenance and evidence.

The **relation vocabulary** is code (`swisstip.core.graph.RELATIONS`), each
relation with the node kinds it may start from and point to:

| Relation | From | To | Meaning |
| --- | --- | --- | --- |
| `rules_set_by` | domain | level, institution | Who makes the rules |
| `executed_by` | domain | role, institution, level | Who carries them out |
| `decided_by` | domain | role, institution, level | Who decides |
| `approved_by` | domain | role, institution | Who approves on top |
| `first_contact` | domain | role, institution | Where a person turns first |
| `legal_basis` | domain, role, level, principle | law | The law it rests on |
| `authoritative_source` | domain, law, role, level | source | Where the binding text is published |
| `published_by` | domain | institution | An institution the pack cites for the domain |
| `varies_by` | domain | level | The rule differs by canton or commune |
| `pitfall` | domain | pitfall | A mistake a generic answer makes |
| `see_also` | domain | domain | A related domain |
| `instance` | role | institution | The institution that plays the role for a place |
| `part_of` | place, institution, level | place, level | Containment |
| `governed_by` | level | principle | A principle the level works under |

A new domain, canton or source is data; a new relation is a code change.

**Citations.** A node or edge cites either a block range of the graph's own
text dataset (`text:`), resolved, attributed and given its basis by the same
functions as a pack's facts, or an evidence record of a pack's release
(`pack:`), which the compiler copies after checking its excerpt hash. Every
node except a place, and every edge except containment between places, cites
at least one excerpt.

**Provenance and review** are the release's: `kind`, `review_status`,
`author`, `reviewed_by`, `reviewed_on`, notes. `serve: reviewed` in
`graph.yaml` compiles only confirmed items.

## 3. How a graph is built

The first build of a graph, and any later extension by AI, runs as the
`build-knowledge-graph` skill of the packs repository with three agents
(`graph-scout`, `graph-reader`, `graph-checker`); the coordinator orchestrates
and merges, and never writes content itself. The steps, each also a button in
the admin console:

| Step | Command | Does |
| --- | --- | --- |
| Sources | scout agents, Graph > Sources | Official pages no pack carries yet go into `sources.json` |
| Fetch and extract | `swisstip-build <graph> --graph --until validate-text --download` | The pack pipeline's stages on the graph's catalogue; robots.txt obeyed unless overridden (`--no-obey-robots`, `SWISSTIP_OBEY_ROBOTS=0`) |
| Derive | `swisstip-build <graph> --graph --from derive --until derive --apply-derive` | Places, institutions, laws, sources and their `legal_basis`, `published_by`, `authoritative_source`, `instance` and `part_of` links from the packs, deterministic (section 4) |
| Read | reader agents, one per domain group | Proposals of nodes and edges, each citing evidence IDs |
| Merge | `swisstip-graph merge graphs/<graph>/graph.yaml <proposals>` | Deterministic (section 5) |
| Check cases | checker agent | `checks.yaml` |
| Compile and check | `swisstip-build <graph> --graph --from compile` | Resolve, validate (fail closed), replay the checks |
| Embed | pack build, or `swisstip-graph embed releases/<pack>` | The graph and its hash go into the pack's release |
| Review | admin console, Graph > Review | Confirm, flag or reject with the reviewer's name |

Agents write `assistant-authored-unreviewed`; Derive writes
`automatically-derived-unreviewed`; only the console's review writes
`human-reviewed`. A person's edit in the console takes an item over from Derive
and from the agents (its author becomes the editor), so neither changes it
again.

## 4. Derive from packs

`swisstip.build.graph_derive`, pure functions. From the place register: the
country, the cantons and the municipalities an institution speaks for, with
`part_of`. From each pack's release: its institutions (law collections and
portals become `source` nodes), the laws its excerpts rest on (the basis norm
without its article: "AIG, SR 142.20, Art. 12" is `law.aig`; a cantonal law
carries its place in the identifier), `authoritative_source` from a law to its
collection, and for every domain a pack topic bridges to (`graph_nodes`)
`legal_basis` and `published_by`. `role_rules` in `graph.yaml` link roles to
institutions by identifier pattern (`instance` edges). Every derived claim
cites the pack excerpt it follows from. Derive is idempotent and never changes
an item a person or an agent owns; it returns a diff that the console shows
before it is applied.

## 5. Merge

`swisstip.build.graph_merge`: turns the proposals' evidence strings into
citations (`<pack>:<evidence_id>` with the excerpt's hash, or
`text:<document_id>:<first>-<last>`), fills the skeleton's nodes, adds new
nodes and edges, recognises an existing edge by its link (from, relation, to,
place) whatever its identifier, folds a proposed institution, law or source
onto the node Derive made when its label or a name is the same, and removes
skeleton nodes no proposal supports. A reference to an excerpt that does not
exist, an unknown relation or endpoint kind, and an edge to an unknown node are
refused and reported, never repaired. Problems in a proposal go back to the
agent that wrote it.

## 6. Compile and validate

`swisstip.build.graph_build.build_graph` resolves every citation, relocates a
text citation through its anchor after a refresh, drops a node or edge whose
citation cannot be resolved (and the edges of a dropped node) with the reason,
refuses a name that occurs in none of a node's excerpts and any node that still
carries the skeleton's placeholder summary, and runs
`swisstip.core.graph.validate_graph`: identifiers, relations and endpoint
kinds, places against the place register, instance places against their
institution, levels against places, citations and hashes, lengths, review
counts, the content hash. A graph that does not validate is not written. The
release validator checks an embedded graph again against the pack's place
register and the topics' `graph_nodes`.

## 7. The tool

`get_knowledge_graph` (contract `swiss-tip/v5`) is listed first, and only when
the release carries a graph. Request: `question`, `node_ids` (at most five),
`jurisdiction` (the same place parts as search and resolve), `reviewed_only`.
Without a question and node IDs it returns the root page: the levels, the
principles and every domain with whether the release publishes facts for it.

Selection (`swisstip.runtime.graph`) is deterministic. The question's words
are matched against each domain's label, names and keywords, the labels and
names of the roles and pitfalls linked to it, and its summary; each word is
weighted by its rarity across domains and counted once at its strongest field.
The names of the country and the cantons (from the place register) and of the
graph's place nodes are not subject words: "in Zurich" says where the user is,
and would otherwise match every domain whose offices carry the name (SVA
Zürich, Steueramt Zürich).

**Embedding matching.** When the server has a local embedder (started with
`--semantic-index`, as for hybrid search), the question is also embedded with
the same model (`qwen3-embedding:0.6b`, with its own instruction: "retrieve
the Swiss public-administration domain it concerns") and compared with one
text per domain: its label, names, keywords, the offices and pitfalls linked to
it, and its summary. The domain vectors are made on the first call from the
graph the server holds, in one request, so there is no index to build or
attest. The two rankings are fused (1/(5 + rank) in each list, the embedding
list holding the domains at a cosine of 0.45 or more). The cosine also sets
the strength: strong at 0.6 or more, and below 0.45 a word match is at most
weak, so one rare word no longer makes an off-topic question strong. If the
embedder fails, the words alone decide and the result says so in its
limitations. Both thresholds are first settings, to be calibrated with the
graph regression (section 10).

At most three domains are taken (those within half the best score) and
expanded by one hop. An edge with a place is served when that place contains
the user's place; a role is resolved to the institution that plays it at the
most specific level the place reaches (commune, then canton, then CH). The
result stays within 8 KB (about 2,000 tokens, so the orientation does not
crowd the caller's context before it searches). Over that, it is shortened
before it is cut: first the summaries of nodes other than the matched and
requested ones go (a caller asks for a node by its ID), then the edges' source
URLs, and only then links, the least important first. Every matched domain's
first contact, carrying-out, deciding and approving bodies, with the office at
the user's place, come before any domain's laws, sources and pitfalls, since
the second-best domain is often the subject.

**Place dependence.** Without a place the scope is CH, as for search and
resolve; no cantonal or municipal office is picked. `place_dependence` names
the most specific level among the matched domains' `executed_by`,
`decided_by`, `first_contact` and `varies_by` targets (`municipality`,
`canton` or `none`), whether the request's place is that specific, and the
question to ask. The guidance tells the caller to derive the place from what
the user said (the place of work is not the place of residence), to resolve no
cantonal or municipal concept before the place is known, and never to assume
one.

`next_search` carries the query (the question as asked), the official names of
the best-matched domain and its offices, and the place when one was given. The
guidance tells the caller to search with the query unchanged and use the terms
only to recognise the right office and concepts in the results: in a
simulation over the regression pack, appending the terms of every matched
domain to the query pulled search off the subject (588 of 707 cases passing,
against 665 for the question alone). `covered_topics` names the release's
topics that publish facts for the matched domains; when it is empty the
guidance says the release does not cover the subject. Every served item states
its review status (once for the whole result when all agree), every edge its
source URL, and `get_evidence` reads a graph evidence ID like a fact's.

**Kill switch.** An operator can serve a release that carries a graph without
it: `swisstip-mcp --no-knowledge-graph`, or `SWISSTIP_KNOWLEDGE_GRAPH=0` in
the environment (`--knowledge-graph` overrides the variable). The server then
lists the four tools, the instructions and descriptions without the
graph-first wording, and `get_evidence` refuses graph evidence IDs, exactly as
for a release without a graph. `/health` reports
`knowledge_graph: {status: disabled, graph_id}`; the release, its hash, its
readiness record and a semantic index stay valid, since nothing in the file
changes. It is meant for A/B runs of a caller with and without the graph, and
for switching the graph off if it misleads callers in production.

## 8. Admin console

Screen 4.10 of [admin-console.md](admin-console.md): the overview (curation
and compiled counts, checks, jobs, audit), Explore (Cytoscape.js, with
filters, focus and depth, a detail panel and links to edit and review), Items,
the item page (edit, excerpts, confirm, flag, reject, add an edge), the review
queue with bulk actions, Derive preview and apply, Compile and Refresh jobs,
and the sandbox panel of the tool. Every write goes through `write_graph`:
validate, compile into memory, save, audit.

## 9. Refresh

Graph > Refresh (or `swisstip-build <graph> --graph --download
--update-curation`) fetches the graph's pages again, extracts them, previews
Derive against the current pack releases, compiles with the citations relocated
through their anchors, and runs the checks. A citation whose text moved keeps
its item; one whose text changed or vanished drops the item with the reason in
`graph-report.json`, where the overview shows it for re-citing. A pack
excerpt that changed since the graph cited it drops the claim the same way.
`graph.json` carries a snapshot date and a freshness window; past it, the tool
tells the caller that offices and procedures may have changed.

## 10. Measuring

Two replays, both model-free:

- `checks.yaml`: the orientation checks the checker agent wrote, replayed at
  every compile (a node that must or must not be served, the place dependence,
  the match strength).
- The graph regression (`swisstip.runtime.graph_regression`): every question
  of a pack's acceptance suite and regression pack asked of the graph as a
  caller would, judged against what the suites already expect. A case whose
  search expects a concept expects one of the domains its topic bridges to
  among the first three, and the topic in `covered_topics`; a case whose
  search expects a decline expects no strong match on a covered topic. The
  packs repository runs it with
  `scripts/test/regression/run_graph_regression.py` and writes
  `graph-regression-report.json`.

On `mvp-zurich` with lexical matching (24 September 2026): 448 of 546 subject
questions reach a domain of their topic, 354 as the first domain; 8 of 28
off-topic questions are not pointed at a covered topic. The declines are the
weak point: stems collide across languages ("Generalabonnement" and
"general") and one rare word makes a strong match.
`--hybrid` replays the same questions with embedding matching and writes
`graph-regression-report-hybrid.json`; every result records the best cosine,
the material for calibrating the thresholds.

## 11. Extending

- A new domain: bridge a pack topic to it (`graph_nodes`), run the skill with
  "extend", review.
- A new canton's offices: add its pages or a pack that cites them, add a
  `role_rules` pattern, Derive, review.
- A new source: add it to `sources.json`, fetch, cite it.
- Another country: a new `graphs/<country>/` with its own place register; the
  relation vocabulary is shared.
