"""Screen 4.6: the review queue.

Facts one card at a time, ordered by priority: the facts a stored check
expects, then the facts whose citation carried a warning or whose anchor no
longer matches, then everything else that is not reviewed, then the model
candidates. Only the confirm action of this screen, taken by a named person
in edit mode, sets `human-reviewed` (rule 5): the pipeline cannot, the
assistant draft cannot, and no other form can.

Confirming refreshes the anchor from the current record, so the confirmation
is bound to the exact bytes the reviewer read.

The bulk action applies confirm, flag or reject to several facts in one
write: the facts ticked in the side list, or every card matching the current
filter. It is all or nothing, one dry build and one audit entry. A fact
confirmed together with others carries a `review:` note saying so, because a
confirmation given to a group is weaker evidence of reading than one given
card by card, and the lead should be able to tell them apart.
"""

import hashlib
import random
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from swisstip.build.curation import Anchor
from swisstip.builder.autopilot.models import (Actor, ActorKind, AuthenticationKind,
                                               WorkflowState)
from swisstip.builder.autopilot.service import AutopilotService
from swisstip.builder.autopilot.store import WorkflowConflict, WorkflowNotFound
from swisstip.extraction.anchors import make_anchor

from ..app import get_pack, render, require_editor
from ..basis import BASIS_KINDS, citation_basis, fact_basis
from ..data import PackData, paginate
from ..writes import WriteRefused, pack_file_lock, record_rejection, write_curation

read_router = APIRouter()
write_router = APIRouter()

WARNING_OUTCOMES = ("same-text", "moved", "ambiguous", "changed", "dropped")
FILTERS = ("review_status", "kind", "jurisdiction", "language", "concept", "document", "by_check", "basis")
BULK_ACTIONS = ("confirm", "flag", "reject")


def governed_review(request: Request, pack: PackData, actor: str):
    service = AutopilotService(pack.root, pack.pack)
    try:
        workflow = service.status()
    except WorkflowNotFound:
        return None
    if workflow.state != WorkflowState.AWAITING_FACT_REVIEW:
        raise WriteRefused(f"facts can be confirmed only while workflow is awaiting fact review, not {workflow.state.value}")
    authentication = AuthenticationKind.HOSTED if request.app.state.users else AuthenticationKind.LOCAL_ASSERTED
    return service, workflow.revision, Actor(kind=ActorKind.HUMAN, actor_id=actor,
                                             authentication=authentication)


def record_governed_review(governed, fact_ids: list[str]) -> None:
    if governed is None:
        return
    service, revision, actor = governed
    try:
        service.record_fact_review(fact_ids, actor, revision)
    except (OSError, ValueError, WorkflowConflict) as exc:
        raise WriteRefused(f"fact-review receipt refused: {exc}") from exc


def anchor_state(pack: PackData, fact, citation, outcome: dict) -> str:
    """Whether the anchored block hashes still match the record the index holds."""
    if citation.anchor is None:
        return "no anchor yet"
    entry = pack.dataset.entry(citation.document_id)
    if entry is None:
        return "the cited record is no longer in the dataset"
    if entry.get("content_sha256") != citation.anchor.content_sha256:
        return "the record changed since the anchor was written"
    if outcome.get("warnings"):
        return "relocated with " + ", ".join(outcome["warnings"])
    return "anchored and matching the current record"


def queue(pack: PackData) -> list[dict]:
    """The cards in priority order (section 4.6)."""
    checks = pack.checks.current()
    expected_facts = checks.expected_fact_ids() if checks else set()
    expected_concepts = checks.expected_concept_ids() if checks else set()
    outcomes = pack.citation_outcomes()
    curation = pack.curation.current()
    cards = []
    for order, (concept, fact) in enumerate(pack.curated_facts()):
        citations = outcomes.get(fact.fact_id, [])
        warned = any(item.get("warnings") for item in citations) or \
            any(item.get("outcome") in ("ambiguous", "changed", "dropped") for item in citations)
        stale_anchor = any(anchor_state(pack, fact, citation, citations[number] if len(citations) > number else {})
                           .startswith("the record changed") for number, citation in enumerate(fact.evidence))
        reviewed = fact.provenance.review_status == "human-reviewed"
        by_check = fact.fact_id in expected_facts or concept.concept_id in expected_concepts
        if by_check and not reviewed:
            priority = 1
        elif warned or (reviewed and stale_anchor):
            priority = 2
        elif fact.provenance.kind == "model-candidate":
            priority = 4
        elif not reviewed:
            priority = 3
        else:
            priority = 5
        flagged = any(note.startswith("flag:") for note in fact.provenance.notes)
        basis = fact_basis(pack, curation, fact)
        cards.append(dict(priority=priority, order=order, concept=concept, fact=fact, warned=warned,
                          by_check=by_check, reviewed=reviewed, flagged=flagged, stale_anchor=stale_anchor,
                          basis=basis["label"], basis_kind=basis["kind"]))
    return sorted(cards, key=lambda card: (0 if card["flagged"] else 1, card["priority"], card["order"]))


def sample_queue(pack: PackData, per_document: int = 3) -> list[dict]:
    """A fixed random sample per document, seeded by `content_sha256`, so it is stable."""
    by_document: dict[str, list[dict]] = {}
    for concept, fact in pack.curated_facts():
        for citation in fact.evidence:
            by_document.setdefault(citation.document_id, []).append(dict(concept=concept, fact=fact))
            break
    sampled = []
    for document_id, items in sorted(by_document.items()):
        entry = pack.dataset.entry(document_id) or {}
        seed = int(hashlib.sha256((entry.get("content_sha256") or document_id).encode()).hexdigest()[:16], 16)
        chosen = random.Random(seed).sample(items, min(per_document, len(items)))
        for item in sorted(chosen, key=lambda row: row["fact"].fact_id):
            basis = fact_basis(pack, pack.curation.current(), item["fact"])
            sampled.append(dict(item, document_id=document_id, priority=1, order=0, warned=False, by_check=False,
                                reviewed=item["fact"].provenance.review_status == "human-reviewed",
                                flagged=False, stale_anchor=False, basis=basis["label"], basis_kind=basis["kind"]))
    return sampled


def card_context(pack: PackData, card: dict) -> dict:
    """The right-hand side of a card: the excerpt inside its page context."""
    outcomes = pack.citation_outcomes().get(card["fact"].fact_id, [])
    curation = pack.curation.current()
    rows = []
    for number, citation in enumerate(card["fact"].evidence):
        outcome = outcomes[number] if len(outcomes) > number else {}
        entry = pack.dataset.entry(citation.document_id) or {}
        record = pack.dataset.record(citation.document_id)
        basis = citation_basis(pack, curation, card["fact"], citation)
        context, current_excerpt = [], None
        if record:
            last = citation.last_block or citation.first_block
            blocks = record["blocks"][citation.first_block - 1:last]
            if blocks:
                current_excerpt = record["content_text"][blocks[0]["start"]:blocks[-1]["end"]]
                heading = blocks[0].get("heading_path", [])
                context = [dict(number=index + 1, block=block,
                                selected=citation.first_block <= index + 1 <= last)
                           for index, block in enumerate(record["blocks"])
                           if block.get("heading_path") == heading]
        rows.append(dict(number=number + 1, citation=citation, entry=entry, outcome=outcome, context=context[:40],
                         current_excerpt=current_excerpt, basis=basis,
                         state=anchor_state(pack, card["fact"], citation, outcome)))
    return dict(citations=rows)


def filter_cards(cards: list[dict], filters: dict[str, str]) -> list[dict]:
    """The queue's filters; the list screen and the bulk action's "all matching" scope share them."""
    if filters.get("review_status"):
        cards = [card for card in cards if card["fact"].provenance.review_status == filters["review_status"]]
    if filters.get("kind"):
        cards = [card for card in cards if card["fact"].provenance.kind == filters["kind"]]
    if filters.get("jurisdiction"):
        cards = [card for card in cards if card["fact"].jurisdiction == filters["jurisdiction"]]
    if filters.get("language"):
        cards = [card for card in cards if card["fact"].language == filters["language"]]
    if filters.get("concept"):
        cards = [card for card in cards if card["concept"].concept_id == filters["concept"]]
    if filters.get("document"):
        cards = [card for card in cards
                 if any(citation.document_id == filters["document"] for citation in card["fact"].evidence)]
    if filters.get("by_check"):
        cards = [card for card in cards if card["by_check"] == (filters["by_check"] == "yes")]
    if filters.get("basis"):
        # The kind of the fact's strongest basis, as the build states it; "none" selects facts without one.
        wanted = None if filters["basis"] == "none" else filters["basis"]
        cards = [card for card in cards if card.get("basis_kind") == wanted]
    return cards


@read_router.get("/packs/{pack}/review")
def index(request: Request, pack: PackData = Depends(get_pack), fact_id: str = "", page: int = 1,
          mode: str = "queue", review_status: str = "", kind: str = "", jurisdiction: str = "",
          language: str = "", concept: str = "", document: str = "", by_check: str = "", basis: str = ""):
    filters = dict(review_status=review_status, kind=kind, jurisdiction=jurisdiction, language=language,
                   concept=concept, document=document, by_check=by_check, basis=basis)
    cards = filter_cards(sample_queue(pack) if mode == "sample" else queue(pack), filters)
    current = next((card for card in cards if card["fact"].fact_id == fact_id), None) if fact_id else None
    if current is None and cards:
        current = next((card for card in cards if not card["reviewed"]), cards[0])
    listing = paginate(cards, page, 50)
    position = next((index for index, card in enumerate(cards) if current and
                     card["fact"].fact_id == current["fact"].fact_id), 0)
    facts = [fact for _, fact in pack.curated_facts()]
    checks = pack.checks.current()
    expected = checks.expected_fact_ids() if checks else set()
    return render(request, "review/index.html", pack=pack, active="review", listing=listing, card=current,
                  context=card_context(pack, current) if current else None, mode=mode,
                  previous=cards[position - 1] if current and position > 0 else None,
                  following=cards[position + 1] if current and position + 1 < len(cards) else None,
                  sha256=pack.curation.sha256,
                  progress=dict(total=len(facts),
                                reviewed=sum(1 for fact in facts if fact.provenance.review_status == "human-reviewed"),
                                expected=len(expected),
                                expected_reviewed=sum(1 for fact in facts if fact.fact_id in expected
                                                      and fact.provenance.review_status == "human-reviewed")),
                  filters=filters, basis_kinds=BASIS_KINDS,
                  concepts=[item.concept_id for item in (pack.curation.current().concepts
                                                         if pack.curation.current() else [])])


def find_fact(curation, fact_id: str):
    for concept in curation.concepts:
        for fact in concept.facts:
            if fact.fact_id == fact_id:
                return concept, fact
    raise WriteRefused(f"unknown fact {fact_id}")


def current_anchors(pack: PackData, fact_id: str) -> dict[int, Anchor]:
    """An anchor per citation from the record as it is now, read before the write takes the lock."""
    anchors = {}
    _, fact = pack.fact(fact_id)
    for number, citation in enumerate(fact.evidence if fact else [], 1):
        record = pack.dataset.record(citation.document_id)
        if record is not None:
            anchors[number] = Anchor(**make_anchor(record, citation.first_block,
                                                   citation.last_block or citation.first_block))
    return anchors


def mark_confirmed(fact, actor: str, anchors: dict[int, Anchor], note: str | None = None) -> None:
    fact.provenance.review_status = "human-reviewed"
    fact.provenance.reviewed_on = date.today()
    fact.provenance.reviewed_by = actor
    for number, anchor in anchors.items():
        fact.evidence[number - 1].anchor = anchor
    if note:
        fact.provenance.notes = [*fact.provenance.notes, f"review: {note} ({actor}, {date.today().isoformat()})"]


def add_flag(fact, actor: str, note: str) -> None:
    fact.provenance.notes = [*fact.provenance.notes, f"flag: {note} ({actor}, {date.today().isoformat()})"]


def remove_fact(curation, fact_id: str) -> tuple:
    """Take the fact out of its concept, and the concept out of the file when it was the last fact."""
    concept, fact = find_fact(curation, fact_id)
    concept.facts.remove(fact)
    curation.concepts = [item for item in curation.concepts if item.facts]
    return concept.concept_id, fact


@write_router.post("/packs/{pack}/review/{fact_id}/confirm")
def confirm(request: Request, fact_id: str, pack: PackData = Depends(get_pack),
            actor: str = Depends(require_editor), sha256: str = Form(...), next_fact: str = Form("")):
    """The only action that sets `human-reviewed`, with the bulk action; it also refreshes the anchor."""
    with pack_file_lock(pack):
        governed = governed_review(request, pack, actor)
        anchors = current_anchors(pack, fact_id)

        def mutate(curation) -> None:
            mark_confirmed(find_fact(curation, fact_id)[1], actor, anchors)

        write_curation(pack, mutate, actor, f"confirm fact {fact_id}", expected_sha256=sha256, ids=[fact_id])
        record_governed_review(governed, [fact_id])
    target = f"&fact_id={next_fact}" if next_fact else ""
    return RedirectResponse(f"/packs/{pack.pack}/review?notice=confirmed+{fact_id}{target}", status_code=303)


@write_router.post("/packs/{pack}/review/{fact_id}/flag")
def flag(request: Request, fact_id: str, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
         note: str = Form(...), sha256: str = Form("")):
    """A flag is a note and nothing else; the status does not change."""
    def mutate(curation) -> None:
        add_flag(find_fact(curation, fact_id)[1], actor, note.strip())

    write_curation(pack, mutate, actor, f"flag fact {fact_id}: {note.strip()}", expected_sha256=sha256 or None,
                   ids=[fact_id])
    return RedirectResponse(f"/packs/{pack.pack}/review?notice=flagged+{fact_id}&fact_id={fact_id}", status_code=303)


@write_router.post("/packs/{pack}/review/{fact_id}/reject")
def reject(request: Request, fact_id: str, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
           note: str = Form(...), sha256: str = Form("")):
    """The fact leaves the curation file; the reason and the fact as it was are kept outside Git."""
    if not note.strip():
        raise WriteRefused("rejecting a fact needs a note")
    removed: list[tuple] = []

    def mutate(curation) -> None:
        removed.append(remove_fact(curation, fact_id))

    write_curation(pack, mutate, actor, f"reject fact {fact_id}: {note.strip()}", expected_sha256=sha256 or None,
                   ids=[fact_id], allow_drop=True)
    record_rejection(pack, removed[0][0], removed[0][1], actor, note.strip())
    return RedirectResponse(f"/packs/{pack.pack}/review?notice=rejected+{fact_id}", status_code=303)


@write_router.post("/packs/{pack}/review/bulk")
def bulk(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
         action: str = Form(...), note: str = Form(""), sha256: str = Form(...), scope: str = Form("selected"),
         fact_ids: list[str] = Form(default=[]), review_status: str = Form(""), kind: str = Form(""),
         jurisdiction: str = Form(""), language: str = Form(""), concept: str = Form(""),
         document: str = Form(""), by_check: str = Form(""), basis: str = Form("")):
    """Confirm, flag or reject several facts in one write: the ticked ones, or every card matching the filter."""
    filters = dict(review_status=review_status, kind=kind, jurisdiction=jurisdiction, language=language,
                   concept=concept, document=document, by_check=by_check, basis=basis)
    note = note.strip()
    if action not in BULK_ACTIONS:
        raise WriteRefused(f"unknown bulk action {action!r}; use one of {', '.join(BULK_ACTIONS)}")
    if scope == "matching":
        ids = [card["fact"].fact_id for card in filter_cards(queue(pack), filters)]
    else:
        ids = list(dict.fromkeys(fact_ids))
    if not ids:
        raise WriteRefused("no fact selected; tick facts in the list or choose all matching the filter")
    known = {fact.fact_id for _, fact in pack.curated_facts()}
    unknown = [fact_id for fact_id in ids if fact_id not in known]
    if unknown:
        raise WriteRefused(f"unknown fact(s): {', '.join(unknown)}; reload the queue")
    if action in ("flag", "reject") and not note:
        raise WriteRefused(f"to {action} facts in bulk, give a comment")

    count = f"{len(ids)} fact{'s' if len(ids) != 1 else ''}"
    reason = f"bulk {action} of {count}" + (f": {note}" if note else "")
    removed: list[tuple] = []
    if action == "confirm":
        with pack_file_lock(pack):
            governed = governed_review(request, pack, actor)
            anchors = {fact_id: current_anchors(pack, fact_id) for fact_id in ids}
            # A single fact confirmed through the bulk form is ordinary; a group says it was one.
            group = f"confirmed in a bulk review of {count}" if len(ids) > 1 else ""
            confirm_note = "; ".join(part for part in (group, note) if part) or None

            def mutate(curation) -> None:
                for fact_id in ids:
                    mark_confirmed(find_fact(curation, fact_id)[1], actor, anchors[fact_id], confirm_note)

            write_curation(pack, mutate, actor, reason, expected_sha256=sha256, ids=ids)
            record_governed_review(governed, ids)
    elif action == "flag":
        def mutate(curation) -> None:
            for fact_id in ids:
                add_flag(find_fact(curation, fact_id)[1], actor, note)
        write_curation(pack, mutate, actor, reason, expected_sha256=sha256, ids=ids)
    else:
        def mutate(curation) -> None:
            removed.clear()
            removed.extend(remove_fact(curation, fact_id) for fact_id in ids)
        write_curation(pack, mutate, actor, reason, expected_sha256=sha256, ids=ids,
                       allow_drop=True)
    for concept_id, fact in removed:
        record_rejection(pack, concept_id, fact, actor, note)
    done = dict(confirm="confirmed", flag="flagged", reject="rejected")[action]
    query = {key: value for key, value in filters.items() if value}
    return RedirectResponse(f"/packs/{pack.pack}/review?" + urlencode(dict(query, notice=f"{done} {count}")),
                            status_code=303)


@write_router.post("/packs/{pack}/review/{fact_id}/accept")
def accept_candidate(request: Request, fact_id: str, pack: PackData = Depends(get_pack),
                     actor: str = Depends(require_editor), statement: str = Form(...), sha256: str = Form(...)):
    """A model candidate becomes a curated statement; the model stays in `author` and `source`."""
    with pack_file_lock(pack):
        governed = governed_review(request, pack, actor)

        def mutate(curation) -> None:
            _, fact = find_fact(curation, fact_id)
            if fact.provenance.kind != "model-candidate":
                raise WriteRefused(f"fact {fact_id} is not a model candidate")
            fact.statement = statement.strip()
            fact.provenance.kind = "curated-statement"
            fact.provenance.review_status = "human-reviewed"
            fact.provenance.reviewed_on = date.today()
            fact.provenance.reviewed_by = actor

        write_curation(pack, mutate, actor, f"accept model candidate {fact_id}", expected_sha256=sha256,
                       ids=[fact_id])
        record_governed_review(governed, [fact_id])
    return RedirectResponse(f"/packs/{pack.pack}/review?notice=accepted+{fact_id}", status_code=303)


@write_router.post("/packs/{pack}/review/sample")
def record_sample(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                  document_id: str = Form(...), blocks_checked: int = Form(0), verdict: str = Form(...)):
    """Sample mode records a verdict per document in `checks.yaml`, not a review status per fact."""
    from ..checks import ChecksFile, Sample
    from ..writes import write_checks

    checks = pack.checks.current() or ChecksFile(pack=pack.pack)
    updated = checks.model_copy(deep=True)
    updated.samples[document_id] = Sample(reviewed_by=actor, on=date.today(), blocks_checked=blocks_checked,
                                          verdict=verdict.strip())
    write_checks(pack, updated, actor, f"sample verdict on {document_id}: {verdict.strip()}", [document_id])
    return RedirectResponse(f"/packs/{pack.pack}/review?mode=sample&notice=sample+recorded", status_code=303)
