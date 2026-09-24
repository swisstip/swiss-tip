# Building the knowledge graph with AI agents

**Last update:** 24 September 2026

How the knowledge graph behind `get_knowledge_graph` is built and extended by
Claude Code agents, with a person reviewing in the admin console. It is the
counterpart of the [pack example](agent-built-knowledge-base-example.md) and
follows the same rules; the design is
[knowledge-graph.md](../architecture/knowledge-graph.md).

## 1. Who does what

| Role | Who | Does |
| --- | --- | --- |
| Coordinator | One Claude Code session in the packs repository, running the skill `build-knowledge-graph` | Scopes the build, runs every command, prepares the evidence packets, sends agents their work and their errors, merges; writes no content |
| Scout | `graph-scout` agents, one per missing area | Finds official pages no pack carries, confirmed to hold the text |
| Reader | `graph-reader` agents, one per domain group | Reads a packet of verified excerpts and proposes nodes and edges, each citing evidence IDs, names copied verbatim |
| Checker | `graph-checker` agent | Writes the orientation checks and runs them |
| Code | `swisstip-graph derive`, `merge`, `compile`, `embed` | Everything deterministic: Derive, citations, folding duplicates, validation, the hash |
| Reviewer (a person) | The admin console, Graph > Review or Explore | Confirms, corrects or rejects every item |

The skill and the agent definitions are committed in the packs repository
(`.claude/skills/build-knowledge-graph/`, `.claude/agents/graph-*.md`), so the
next build or extension runs the same way: open Claude Code in `swiss-tip-mvp`
and run `/build-knowledge-graph extend <domain, canton or source>`.

### What an agent must never do

- Type a quotation: every claim cites an evidence ID of its packet.
- Translate a name: `names` are copied from a cited excerpt, and the compiler
  refuses any other.
- Set `human-reviewed` or attest.
- Invent an office, a law or a competence: an unsupported claim is listed as
  a gap instead.

When an agent stops early (a rate limit, an error), the coordinator resumes it
with its context or relaunches it; it does not write the agent's output
itself. A problem the compiler finds in a proposal goes back to the agent that
wrote it; a systematic problem is fixed in the tested code.

## 2. The steps

1. **Scope.** A skeleton of node IDs (levels, principles, domains, roles,
   pitfalls) in `graphs/<graph>/graph.yaml`; each pack topic bridged to its
   domains with `graph_nodes` in the pack's curation.
2. **Sources.** Scouts verify pages for what no pack carries; they go into
   `graphs/<graph>/sources.json`.
3. **Fetch and extract.**
   `swisstip-build <graph> --graph --packs-dir . --until validate-text --download`.
4. **Derive.** `swisstip-build <graph> --graph --packs-dir . --from derive --until derive --apply-derive`.
5. **Readers.** One packet per domain group (the verified excerpts of the
   bridged topics, one per line, and the graph's own text blocks), one reader
   per packet in parallel, plus the list of node IDs Derive created so the
   readers reuse them.
6. **Merge.** `swisstip-graph merge graphs/<graph>/graph.yaml <proposals> --text .local/graph-<graph>/text`,
   from the post-Derive state, so a merge can be repeated after an agent
   revises its proposal.
7. **Compile and check.** `swisstip-build <graph> --graph --packs-dir . --from compile`;
   the checker writes `checks.yaml` and adjusts only its expectations.
8. **Embed.** `swisstip-graph embed releases/<pack>` (or the pack build).
9. **Review** in the admin console, then compile, embed, accept and attest.

## 3. The first build (24 September 2026)

- Sources of its own: the ch.ch federalism pages (German, English) and the
  FDFA "About Switzerland" page. The Fedlex file server refused the
  constitution; the principles it alone states are recorded as gaps
  ([next steps](next-steps.md), item 6).
- Derive from `mvp-zurich` and `mvp-wallisellen`: places, institutions, laws,
  sources, and their links, 573 items.
- Six reader groups (federalism; residence, registration and entry;
  naturalisation and political rights; social insurance and taxes; health,
  mobility and housing; waste and schools), 52 recorded gaps.
- Compiled: 238 nodes and 668 edges resting on 439 excerpts, none dropped;
  333 items assistant-authored and 573 derived, all unreviewed until a person
  confirms them.
- Orientation checks: 14 cases, 13 passing; the failing, non-blocking one
  records that no excerpt names a residents' office for Lugano.
