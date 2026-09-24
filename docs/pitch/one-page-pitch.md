# Swiss TIP - One-minute jury pitch

**Last update:** 23 September 2026

*Spoken script: 177 words, spoken over a recorded exchange that replays
beside it. At 150 words a minute that is 71 seconds, and 64 with the two
sentences marked below taken out; most people run faster than that under
lights, so time yourself rather than trust the arithmetic. The sentence
marked "never cut" stays in at any length.
[Running the demo](#running-the-demo) is the setup it assumes.*

---

**[0:00 - send the question, then turn to the room]**

> You landed in Zurich on Wednesday. You start work on Monday. By when must
> you register?
>
> What you are watching is a recording of a real run, so the minute holds.

*Never cut the second sentence. Nothing on the screen says the exchange is a
recording, so this is the only place it is said.*

**[0:05 - while it runs]**

> We recorded a normal assistant on this question. It gave the fourteen-day
> rule as the legal deadline, and registering before you start work as a
> recommendation.
>
> Believe that, and you walk into your first day at a new Swiss job
> unregistered, thinking you still have two weeks.

**[0:22 - the answer is on the screen; point at it]**

> Same question, with Swiss TIP underneath. Two limits, each quoted from the
> page it came from, and the earlier one binds: before Monday, not in two
> weeks' time. The rules come from the server; the date is arithmetic on
> top.

*(First cut: the last sentence. The slide carries it in writing.)*

**[0:38]**

> Swiss TIP is an MCP server: over a thousand facts from official sources,
> each checked by a person against its page. It never writes the answer - it
> supplies the evidence, and names a gap when it cannot.

**[0:52]**

> Zurich, from the residence permit to the rubbish calendar. Same pipeline
> for any canton. The evidence layer your assistant can cite.

*(Second cut: "Same pipeline for any canton." Land on the last sentence.)*

The exchange finishes about eleven seconds after it is sent, so from 0:15 the
answer stands on the screen while you talk, and the jury reads the citations
in its own time instead of watching a spinner.

The spoken figures are deliberately round - "over a thousand facts", "twenty
topics" - so that the script survives the next release. The exact contents
are in [COVERAGE.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/COVERAGE.md); read them before the pitch and say
nothing sharper than the round number.

The failure is the recorded control run of 13 September 2026
(`.local/experiments/2026-09-13-standing-cases-control.md`), the same
question with no server: it called 27 September, fourteen days from arrival,
"the legal latest registration date", and offered the 15th, the day before
work began, as merely "recommended". The script tells it without those dates
on purpose - they belong to that run's scenario, not to the one on the
screen, and two sets of dates in one minute is one too many. Keep the
verbatim quotation for Q&A, where it is the proof.

## Running the demo

Nothing runs. [stage.html](stage.html) is one file opened in a browser: no
server, no model, no container, no network, and the same seconds every time.
The minute cannot be spent waiting for a provider, a cold embedding model or
the venue's wifi.

Start the pack's demo image anyway and keep it in a second tab. It is what
you open when a juror asks whether it is real, and it is where a rerun of the
recording comes from.

The dates on the screen are the model's arithmetic over the served rules, not
served facts, and that is the division of labour the pitch describes: the
server supplies the two limits and that the earlier governs, the assistant
works out the day. Say it that way if asked, and do not defend a date as
something the release publishes. One recording in three put that arithmetic a
day wrong - it offered Friday the 26th, which is a Saturday - so read the
dates of any rerun against a calendar before it replaces this one.

## The panel beside the demo

The jury has spare attention from 0:05 to 0:30, while the answer arrives, and
the screen in that window is the interface. So the technical detail goes
beside it, not on a slide to switch to: the slides on about two thirds of the
screen, the exchange on the right, for the whole minute. Nothing on the
slides is spoken.

[stage.html](stage.html) is that screen. The slides are sections of the one
document, swapped by script, so moving between them never interrupts the
exchange. It carries the card below as its fourth slide, a clock that colours
itself at 0:50 and 1:00, and arrow keys, buttons and dots to move. Enter
sends, `f` ends the exchange at once when the minute is running late, and a
double-click on the thread resets it between rehearsals. The prompt starts
with the question already in it.

The screen the jury sees carries none of that. The time mark and stage
direction on each slide, the key hints and the arrows are the presenter's,
and they are hidden until `p` is pressed, or `?rehearse` stands in the
address. The clock stays, small and dim, and becomes legible in that mode
too. The default is the clean screen, so forgetting the key costs nothing,
while showing "0:00 - SEND THE QUESTION, THEN TURN TO THE ROOM" to a jury
would cost a good deal. The answer begins at eight seconds and is
complete near twenty-three, so a question sent at 0:05 lands on the 0:30
slide.

The interface scales with the height of the screen and the thread scrolls
with the answer, as the real one does. On a screen of 1080 lines the whole
exchange stands without scrolling; below that the first searches scroll out
of view while the answer is still arriving, which costs nothing at 0:30 but
is worth seeing once. Open the page at the resolution the projector will
run, send the question, and watch it to the end before the day.

### The card

```
Swiss TIP - MCP server - contract swiss-tip/v4

  4 tools      get_coverage - search - resolve - get_evidence
               + lookup, where a dataset connector is registered
  4 statuses   SUPPORTED - NEEDS_CONTEXT - OUT_OF_COVERAGE - STALE
               the server never composes the answer

  every fact   jurisdiction - validity dates - reviewer
               excerpt + source URL + access date + hash
  release      content-hashed, attested, stale from 22 November 2026
  search       lexical + embeddings, source terms in DE, EN, FR and IT

  Zurich       20 topics - 211 concepts - 1,268 facts - 334 documents
  cantons      registering on arrival in all 26 - Zurich in full
  datasets     5 waste calendars - 4,547 collection dates
```

Read the bottom line out of [COVERAGE.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/COVERAGE.md) before the
pitch. Put nothing on the card that the script contradicts, and nothing that
cannot be defended in Q&A: every line is a fact of the served release or of
[the tool contract](https://github.com/swisstip/swiss-tip/blob/main/docs/architecture/tool-contracts.md).
The dataset line is not part of the release: the collection dates are five
bundles under `datasets/mvp-zurich`, served by the connector through
`lookup`, which is why they are counted apart from the 1,268 facts. A server
started without the connector has four tools and no dataset line, and a jury
that inspects the server must find what the card claims - so check which of
the two the machine beside you is running before you show this card or say
"rubbish calendar" out loud.

Keep the architecture diagram, the pipeline and the nationwide corpus off
this card. They belong on a denser leave-behind slide the jury can read
during Q&A, where reading time is not borrowed from the minute.

### Saying that it is a recording

Nothing on the screen says so. The panel carried a "recorded run" badge with
the date and the model until 23 September 2026, when it was dropped in favour
of saying it out loud, in the second sentence of the script.

That makes the spoken line load-bearing. A pitch whose subject is provenance
cannot afford a jury working out for itself that the screen was not live, so
the sentence is marked never to be cut, and it comes before any claim rather
than after. Said first it costs nothing, and the running image is there for
anyone who wants the same question answered in front of them.

### The dates live in one place only

No slide and no spoken line carries a date. They are all in the recording,
where they are right by construction, because the question asks in relative
terms - "arrived today", "start work next Monday" - and the assistant
resolves them against the day it runs.

That is also the catch: a recording is only current on the day it was made.
The one in the page is of 23 September 2026 and says "today, Wednesday,
23 September 2026" on its face. **Re-record on the morning of the pitch.** It
takes a minute, the dates follow by themselves, and nothing else in the page
or the script has to change.

The question says "on Wednesday this week" rather than "today" for that
reason: Wednesday of a given week is the same date on the Thursday, the
Friday and the weekend that follow it, so a recording made once holds for the
rest of its week. The one in the page was made on Wednesday 23 September
2026 and reads arrival Wednesday the 23rd, fourteen days to 7 October, a
first working day of Monday the 28th, and a binding limit of Friday the 25th
- every one of which is still right if it is shown on the Thursday or the
Friday of that week. Shown in the following week, none of them is.

Recorded on the Wednesday itself the phrasing is a coin toss: in two of three
recordings the assistant stopped to ask "today is Wednesday, September 23 -
is that the day you arrived?" instead of answering, because on a Wednesday
"Wednesday this week" can mean today. Recorded on the Thursday or the Friday
that ambiguity is gone. Check the weekday of every date it prints before you
use it.

### What is being replayed

A real exchange, recorded on 23 September 2026 through the OpenCode interface
of the pack image against the server, on the free model, in eleven seconds
(`.local/experiments/2026-09-23-stage-replay.md`, with the transcripts and
the screenshots the panel is drawn from). The two searches, the resolve, the
answer, its dates, its quotations and its sources are that run's, word for
word. The panel is drawn from the screenshots of the same run, down to the
tab strip, the grey question, the `Called` lines with the arguments beside
them, the indented binding deadline, the blue source links and the composer.

Two sections of the answer are left out for the minute, "What you'll need"
and "Important notes". The run was made on the release of 22 September, not
the attested one; the registration concepts are the same, but a rerun on the
current release is worth the five minutes.

The question gives the assistant everything it needs - arrival, first working
day, contract length - so it computes a date instead of asking for one. It
takes "today" from the `as_of` of its own resolve, which is why the recording
is dated on its face: inside it, today is 23 September 2026.

To record it again: start the demo image and open it at `?ask=<the question>&send=1`
([the OpenCode image](https://github.com/swisstip/swiss-tip/blob/main/docker/opencode/README.md#opening-the-interface-with-a-question-ready)),
let it answer, and put the calls and the answer into `CALLS` and `SAID` in
the page. Both are plain lists at the top of its script. Check every date it
prints against a calendar first.

## Additional info for Q&A

**The release.** Contents, languages and snapshot date are in
[COVERAGE.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/COVERAGE.md); the review status in absolute numbers, and
what is weak or missing, in [LIMITATIONS.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/LIMITATIONS.md). Every
fact of the served Zurich release is `human-reviewed` against its cited
excerpt by one named reviewer, most in bulk groups; it is not a legal
review. The English excerpts added to federal pages are not reviewed by a
person. Every fact the server returns carries that status, the reviewer and
the date, so the calling assistant can say who stands behind a statement,
and a caller facing a critical question can send `reviewed_only`, which
serves confirmed facts only and names a gap for anything unconfirmed.

**The recorded failure in the script.** The four edge cases were run with the
server and, as a control, without any server, on 13 September 2026
(`.local/experiments/2026-09-13-edge-case-questions.md`). Without it the
model produced plausible law with invented article numbers, thresholds and
procedures, and missed the exception in every case; in one run it cited a
German gazette reference for a Swiss law and named an office under a name it
no longer carries. The verbatim exchanges are in that record.

**Beyond Zurich.** The nationwide corpus, fetched and extracted outside Git,
holds 12,117 official pages of 12,461 download targets, from a catalogue of
59 federal and cantonal sources covering all 26 cantons
([releases/README.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/releases/README.md)). Its concept candidates
passed automated review only; no release is built or served from it. These
are source-preparation statistics: exhaustive coverage, semantic
interpretation, translation equivalence and OCR remain incomplete.

**How it was built in the time available.** One press past the close brings up
[the agent map](screenshot-agent-map.png), a screenshot of the build as it
ran: one coordinator holding the plan and 63 agents under it, scouts by group
of cantons and curators by topic, each with its own elapsed time and token
count. It is the honest answer to "how did you get eleven hundred reviewed
facts out of a hackathon", and it is better than a diagram because the
numbers in it are real.

Say the second half of it too, unprompted: the agents draft, and a person
confirms every fact against its cited excerpt before it is served. A jury
that reads the picture as "a model wrote your knowledge base" has read half
of it, and the review is what makes the other half true
([LIMITATIONS.md](https://github.com/swisstip/swiss-tip-mvp/blob/main/LIMITATIONS.md) puts it in absolute numbers, and
names the one reviewer).

The pipeline itself ships: it is a Claude Code plugin,
[swiss-tip-skills](https://github.com/swisstip/swiss-tip-skills), and the slide
carries the two lines that drive it. Say them as they read:

> Install it, and then you do not type a command at all - you say what you
> want covered. "Build a knowledge base about AHV and pensions for the Canton
> and City of Zurich." Eleven steps, four subagents, and two gates that stay
> human: a person confirms the facts, and a person attests the release.

The skill inside the plugin is `build-knowledge-base`, so a slash invocation
would be `/swisstip-kb-builder:build-knowledge-base` - but the plugin is not
driven that way and the README does not document it that way. Do not put a
shorter command on the slide than the one that works; a juror may type it.

There is no mock Claude window on the slide, and there should not be. The
agent map beside it is a real screenshot of the real build, and a fabricated
one next to it would put the two in the same frame and make the real one
worth less. The two lines of type say the same thing and are true.

The slide sits after the close and is left out of the dots, so the minute
still reads as five slides and the map is one keypress away when it is asked
for. It is not part of the spoken script.

**Data that changes every week.** The waste calendars are the worked example:
not facts read from a page but five dataset bundles under
`datasets/mvp-zurich`, 4,547 collection dates by zone, rebuilt from the city's
own data and served through a fifth tool, `lookup`, beside the four of the
contract. The release states the rules for waste and recycling as seven
concepts like any other topic; the dates sit next to them, because a fact
with a review status and a snapshot date is the wrong shape for a calendar.

**The opportunity.** The same governed release format carries any regulated
knowledge: potential myAI integration, reuse across assistants, and a source
of truth that banks, insurers and other regulated institutions could serve
to their own AI applications under the same evidence contract.

**How it compares.** Other Swiss MCP servers (the Fedlex servers, mcp-swiss,
the City of Zurich open data server, ZüriCityGPT's site search) pass queries
through to live data, and chatbots such as ZüriCityGPT or the migration
office chatbots answer people directly. Swiss TIP is the evidence layer any
assistant can call: reviewed facts with a jurisdiction and date scope, and a
named gap for what it does not cover. It complements those servers rather
than replacing them - a client can connect ZüriCityGPT's server next to
Swiss TIP for City of Zurich questions outside its topics
([related work](../product/related-work.md)).
