# Swiss TIP - next steps and improvements

**Last update:** 25 September 2026

What we would build next, and why. Each item names what the product does
today, what should change and what it would bring. None of them is in this
release; the current behaviour is described in the
[functional specification](functional-specification.md) and the
architecture documents it links to.

## 1. Orientation before search: a knowledge graph of Switzerland

**Today.** The calling model arrives with no map of Switzerland. It does not
know that the canton issues a residence permit while the Confederation
writes the law, that the commune registers you, or which office handles it
at the user's place. Search and resolve find the facts, but only once the
model searches for the right thing.

**Next.** A fifth MCP tool, `get_knowledge_graph`, that the model calls
first for every new subject. It is built and measured on the branch
`feat/knowledge-graph`, but did not make this release.
- **What it returns:** for the best-matched domain, which level of the
  state sets the rules, who carries them out and decides, the office at the
  user's place with its names in the local languages, the laws and the
  pitfalls, and whether the answer depends on the canton or municipality.
  It serves no source URLs, so the model searches instead of reading the
  web.
- **How it is built:** it is a reviewed, cited knowledge unit with the same
  lifecycle as a pack, embedded in the release and hashed with it.
- **How it is measured:** its own regression replays the acceptance and
  regression questions. With the local embedding model, 507 of 546 reach
  the right domain.
- **What remains:**
  - calibrate its match thresholds on 50 to 100 off-topic questions;
  - grade whole conversations with and without it (DeepEval,
    `--no-knowledge-graph`);
  - ground its principles in the constitution's own articles;
  - widen it canton by canton.

**Where it is.** Two pull requests, both from the branch
`feat/knowledge-graph`, both open:
- [swiss-tip#6](https://github.com/swisstip/swiss-tip/pull/6): the code,
  version 0.4.0. It covers the tool, graph build and derive, the admin
  console screens to explore, review, compile and refresh the graph, and a
  kill switch (`--no-knowledge-graph` / `SWISSTIP_KNOWLEDGE_GRAPH=0`).
- [swiss-tip-mvp#2](https://github.com/swisstip/swiss-tip-mvp/pull/2): the
  packs. It holds the graph `graphs/ch/` (249 nodes, 674 edges), embedded in
  `mvp-zurich` and `mvp-wallisellen`, and the skill and agents that extend
  it.

To ship it:
1. Review and merge the code PR, then tag `v0.4.0rc1` (TestPyPI), check
   it, and tag `v0.4.0` (PyPI).
2. Rebuild and attest both packs from their source data (`swisstip-build
   <pack> --from build --until ready`, with the semantic index and the
   regression pack), then run the graph regression with and without
   embeddings.
3. Merge the packs PR only once 0.4.0 is on PyPI, since an older server
   rejects a release that carries a graph.
4. Evaluate with DeepEval against two servers built from the branch: one
   with the graph and one started with `--no-knowledge-graph`.

**Why.** Orientation before retrieval: the first call of a conversation
lands on the right level of the state and the right office, and a question
the service does not cover is declined at once.

## 2. Deterministic source discovery, driven from the admin console

**Today.** A pack's sources are found by agent swarms in a Claude Code
session: scouts per group of cantons and curators per topic, 63 agents for
the Zurich pack. That works, but it is costly in model calls and hard to
repeat exactly, and it runs outside the admin console.

**Next.**
- **Candidate sources from code:** the official registers and portals the
  packs already use (cantonal law collections, LexFind, the ch.ch and
  cantonal sitemaps, the publishers' own link structure), filtered by the
  catalogue's host and path rules.
- **Run from the console:** the console runs this as a job, shows the
  candidates, and a curator accepts them into the catalogue.
- **Agents only where code cannot decide:** for example, which of two pages
  is authoritative. They are started from the console with a budget shown
  first.

**Why.** Lower and predictable cost per pack, the same result on every run,
and a curator who stays in the browser instead of a Claude Code session.

## 3. Run the model step from the admin console

**Today.**
- `swisstip-concepts` is the provider-based model step. It proposes
  concepts and facts from the fetched pages, has a second model call review
  them, and classifies the basis of each excerpt.
- It is started from the command line. The admin console only shows the
  candidates it produced, in the review queue.
- A source or a concept that a curator adds by hand gets no model
  processing until someone runs the command line.

**Next.**
- The model step becomes a job in the admin console, like fetching and
  building already are. It runs for a whole pack, for one source or for one
  concept, and shows its budget before it starts.
- When a curator adds a source or a concept, the console offers to run it.
- The results arrive in the review queue as model candidates, as today.

**Why.** A curator can extend a pack without leaving the admin console, and
hand-added content gets the same processing as everything else.

## 4. Approve agent-built content before it is processed

**Today.** Agents or a person write facts straight into the curation file
as `assistant-authored-unreviewed`, and the next build serves them with
that status. Review in the admin console follows the build; it is not a
gate before it.

**Next.**
- An Inbox in the admin console. Agents only *seed* proposals, and a person
  approves, edits or rejects each one before it is processed.
- A pack setting to serve reviewed content only.
- A readiness policy that can require a minimum reviewed share.

**Why.** Nothing an agent proposes reaches a release without a person having
seen it, while the curator keeps the speed of AI-assisted building.

## 5. Attest a release from the admin console

**Today.** The readiness gates run from the admin console, except the last
step. That step needs the name of the person who attests the release, and it
is only given on the command line.

**Next.** An attest action on the release screen, for the logged-in lead,
with the gate results shown before the confirmation.

**Why.** The whole route from a new source to a ready release can then be
done in the browser.

## 6. A Swiss model for the model step

**Today.** The model step runs on a configurable provider profile; DeepSeek
is the default.

**Next.** Make Apertus, the Swiss open model, available through Swisscom's
API as a provider profile, measure it against the current default on the
existing acceptance and regression packs, and keep a one-line switch back.

**Why.** Swiss content processed by a Swiss model, with the quality measured
rather than assumed.

## 7. Keep the tool contract and its published schema in step

**Today.** The published JSON Schema of the MCP tools is generated from the
code. The check that it still matches is run by hand.

**Next.** Run that check in the test suite and in CI.

**Why.** Every MCP client sees exactly the contract the server serves.

## 8. Knowledge CI/CD, and reuse beyond public information

**Today.** A release is rebuilt on demand, and the admin console's refresh
re-fetches its sources when a person starts it.

**Next.**
- **Knowledge CI/CD:** a source watcher, cheap change checks first, then an
  incremental rebuild of what changed, the regression and publication gates,
  and promotion to a new immutable release. Callers keep their pinned
  release until they adopt the new one.
- **Reuse beyond public information:** the same engine for other rule-bound
  domains, such as banking and insurance regulation and a company's own
  policy, with licensed data products.

The [full presentation](../pitch/full-presentation.md) (slides 17 to 23)
sets out both.

**Why.** Answers stay current without a person starting every refresh, and
the platform serves any domain where a wrong answer is too expensive.

## 9. Business model

**Next.**
- **Hosted subscription:** we operate the server and keep the packs fresh.
  Tiers by call volume and by the packs served.
- **Self-hosting licence:** an organisation runs the server and the admin
  console on its own infrastructure, with a licence per instance and access
  to the attested packs.
- **Publisher-funded packs:** a canton, a commune or a federal office pays
  for the upkeep and review of its own pack. Every assistant can then serve
  the pack at no charge, and the publisher gets its rules answered correctly
  wherever people ask.

**Why.** Revenue that follows the value: the operators who serve answers,
the organisations that need them in-house, and the authorities whose
information is at stake.
