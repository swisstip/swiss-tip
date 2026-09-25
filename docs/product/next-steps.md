# Swiss TIP - next steps and improvements

**Last update:** 25 September 2026

What we would build next, and why. Each item names what the product does
today, what should change and what it would bring. The items are planned
and none of them is implemented; the current behaviour is described in the
[functional specification](functional-specification.md) and the
architecture documents it links to. The knowledge graph is built the same
way as the knowledge packs, so every item below applies to both.

## 1. Run the model step from the admin console

**Today.**
- `swisstip-concepts` is the provider-based model step. It proposes
  concepts and facts from the fetched pages, has a second model call review
  them, and classifies the basis of each excerpt.
- It is started from the command line. The admin console only shows the
  candidates it produced, in the review queue.
- A source or a concept that a curator adds by hand in the admin console
  gets no model processing at all. Nothing proposes its facts, classifies
  its excerpts or drafts its statements until someone runs the command
  line.

**Next.**
- The model step becomes a job in the admin console, like fetching and
  building already are. It runs for a whole pack, for one source or for
  one concept, and shows its budget before it starts.
- When a curator adds a source and it has been fetched, the console offers
  to run the model step on that page. When a curator adds a concept, the
  console offers to draft its facts from the cited pages.
- The results arrive in the review queue as model candidates, exactly as
  today.
- The workbench's "Draft with assistant" button, already designed in the
  admin console specification, is part of the same change.

**Why.** A curator can extend a pack or the knowledge graph without leaving
the admin console. Hand-added content gets the same processing as
everything else.

## 2. Approve agent-built content before it is processed

**Today.**
- Agents in a Claude Code session, or a person, write facts (and graph
  nodes and edges) straight into the curation file as
  `assistant-authored-unreviewed`. The next build serves them with that
  status.
- Review in the admin console follows the build; it is not a gate before
  it.
- Readiness does not require reviewed content; the release states how much
  has been reviewed.

**Next.**
- An Inbox in the admin console. Agents only *seed* proposals: sources to
  fetch, and the skeleton of concepts or graph nodes. A person approves,
  edits or rejects each one.
- Only approved proposals run through the normal processing: fetch,
  extract, the model step of item 1, then review.
- A pack or graph setting to serve reviewed content only.
- A readiness policy that can require a minimum reviewed share.

**Why.** Nothing an agent proposes reaches a release without a person
having seen it, while the curator keeps the speed of AI-assisted building.

## 3. Attest a release from the admin console

**Today.** The readiness gates run from the admin console, except the
last step. That step needs the name of the person who attests the release,
and it is only given on the command line.

**Next.** An attest action on the release screen, for the logged-in lead,
with the gate results shown before the confirmation.

**Why.** The whole route from a new source to a ready release can then be
done in the browser.

## 4. A Swiss model for the model step

**Today.** The model step runs on a configurable provider profile;
DeepSeek is the default.

**Next.**
- Make Apertus, the Swiss open model, available through Swisscom's API as a
  provider profile, and measure it against the current default on the
  existing acceptance and regression packs.
- Keep a one-line switch back to the current profile, since Apertus is
  used for plain structured output rather than tool calling.

**Why.** Swiss content processed by a Swiss model, with the quality
measured rather than assumed.

## 5. Keep the tool contract and its published schema in step

**Today.** The published JSON Schema of the MCP tools is generated from
the code. The check that it still matches is run by hand.

**Next.** Run that check in the test suite, so a contract change cannot be
committed without its schema.

**Why.** Every MCP client sees exactly the contract the server serves.

## 6. Ground the knowledge graph's principles in the constitution

**Today.** The knowledge graph states the three levels of the state,
subsidiarity and the cantons' residual competence from the ch.ch and FDFA
overview pages. The Fedlex file server refuses the downloader, so the
constitution's own articles are not in the graph's text: "the cantons
implement federal law" (Art. 46) and "federal law takes precedence" (Art. 49)
are left out rather than stated without an excerpt.

**Next.** Fetch the constitution through the Fedlex data service, or with
the robots.txt override under an agreement with the Federal Chancellery, and
add the two principles with their articles.

**Why.** The caller learns why a canton, not the Confederation, handles most
procedures, from the text of the law itself.

## 7. Sharper orientation for everyday wording

**Today.** The graph matches the question's words against the domains' names
and keywords, without the place names, and with the local embedding model as
well when the server runs hybrid search. Its own regression replays every
question of the acceptance suite and regression pack against the graph: with
words only, 444 of 546 questions reach a domain of their topic (368 as the
first, the one the answer walks), and with the embedding model 507 (447 as the
first, measured before the label weight of 4.5), but only 8 and 10 of 28 off-topic questions are kept away from a
covered topic. The embedding thresholds (0.45 and 0.6) are first settings: 28
off-topic questions from one pack are too few to set them.

**Next.**
- Write 50 to 100 off-topic and near-miss questions in several languages,
  replay them on both packs, set the thresholds on one part and check them on
  the rest.
- Grade whole conversations: the team's DeepEval evaluator (OpenCode and the
  pi agent, several models) runs each question with and without the graph
  tool (`--no-knowledge-graph`), so the effect of the graph on the final
  answer is measured, not only the retrieval.

**Why.** The first call of every conversation lands on the right domain more
often, and a question the service does not cover is declined at once.

## 8. Widen the knowledge graph beyond the Zurich packs

**Today.** The graph knows the offices the packs cite: the migration office
of every canton, and the residents', tax, school and waste offices of the
City of Zurich, Wallisellen and Lugano only where a pack names them. No
excerpt names a residents' office for Lugano or a regional employment centre
for any place.

**Next.**
- Extend the graph canton by canton with the `build-knowledge-graph` skill.
- Add a provider route that writes graph candidates with a configured model
  (Apertus through Swisscom, with the pack pipeline's model as fallback), run
  from the admin console like item 1.

**Why.** The orientation becomes as useful in Lugano or Bern as in Zurich.

