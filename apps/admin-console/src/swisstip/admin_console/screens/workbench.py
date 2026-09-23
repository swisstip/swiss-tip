"""Screen 4.5: the curation workbench.

A left tree of topics and concepts, a right pane with one of four forms:
pack, topic, concept and fact. Every form validates through the curation
models on the server and then through a dry build, so a save that the
pipeline would refuse never reaches the file. The fact form is where a
citation becomes evidence; the reading view's cite action lands here.

The assistant draft of section 4.5 needs the provider layer of the
concept-extraction port in TODO.md. Until that port exists the button is
absent, which is what the design asks for: the console never writes a
statement on its own.
"""

from datetime import date
from urllib.parse import urlsplit

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from swisstip.build.curation import (Anchor, BasisSpec, CitationRef, CuratedConcept, CuratedFact, CuratedInstitution,
                                     CuratedTopic)
from swisstip.core.release import ContextFieldSpec, DecisionRule, Provenance, RankingPolicy, RequiredUserFact
from swisstip.extraction.anchors import make_anchor
from swisstip.runtime.service import tokens

from ..app import get_pack, render, require_editor
from ..basis import BASIS_KINDS, BASIS_LEVELS, citation_basis
from ..data import CANTONS, PackData
from ..writes import WriteRefused, write_curation

read_router = APIRouter()
write_router = APIRouter()

PROVENANCE_KINDS = ("curated-statement", "source-section", "model-candidate")
REVIEW_STATUSES = ("human-reviewed", "assistant-authored-unreviewed", "automatically-derived-unreviewed",
                   "model-candidate-automated-review")


def assistant_provider() -> str | None:
    """The concept-extraction provider port of TODO.md. It does not exist yet."""
    return None


def lines(value: str) -> list[str]:
    return [item.strip() for item in (value or "").splitlines() if item.strip()]


def tree(pack: PackData) -> list[dict]:
    """Topics with their concepts, fact counts, review progress and the warnings of section 4.5."""
    curation = pack.curation.current()
    if curation is None:
        return []
    nodes = []
    for topic in curation.topics:
        concepts = []
        for concept in curation.concepts:
            if concept.topic_id != topic.topic_id:
                continue
            reviewed = sum(1 for fact in concept.facts if fact.provenance.review_status == "human-reviewed")
            warnings = []
            if not concept.aliases:
                warnings.append("no aliases: search can only hit the label and the description")
            if concept.facts and concept.description.strip() == concept.facts[0].statement.strip():
                warnings.append("the description repeats the first statement")
            if not concept.questions:
                warnings.append("no questions")
            concepts.append(dict(concept=concept, facts=len(concept.facts), reviewed=reviewed, warnings=warnings))
        nodes.append(dict(topic=topic, concepts=concepts,
                          facts=sum(item["facts"] for item in concepts),
                          reviewed=sum(item["reviewed"] for item in concepts)))
    return nodes


def missing_publishers(pack: PackData) -> list[str]:
    """Hosts a fact cites for which the curation names neither a publisher nor an institution rule; the build
    falls back to the catalogue for them and stops when that fails too."""
    curation = pack.curation.current()
    if curation is None:
        return []
    hosts = set()
    for _, fact in pack.curated_facts():
        for citation in fact.evidence:
            entry = pack.dataset.entry(citation.document_id)
            if entry:
                hosts.add(urlsplit(entry["source_url"]).hostname or "")
    known = set(curation.publishers) | {rule.partition("/")[0] for institution in curation.institutions for rule in institution.urls}
    return sorted(host for host in hosts if host and host not in known and host.removeprefix("www.") not in known)


def search_terms(concept) -> dict[str, list[str]]:
    """The concept as `search` indexes it, so the expert sees which words a query can hit."""
    return {"label": sorted(tokens(concept.label)),
            "aliases": sorted(tokens(" ".join(concept.aliases))),
            "questions": sorted(tokens(" ".join(concept.questions))),
            "description": sorted(tokens(concept.description)),
            "statements": sorted(tokens(" ".join(fact.statement for fact in concept.facts)))}


def evidence_rows(pack: PackData, fact) -> list[dict]:
    """Each citation with its excerpt, its anchor state, its basis and the outcome of the last build."""
    outcomes = pack.citation_outcomes().get(fact.fact_id, [])
    curation = pack.curation.current()
    rows = []
    for number, citation in enumerate(fact.evidence, 1):
        entry = pack.dataset.entry(citation.document_id) or {}
        outcome = outcomes[number - 1] if len(outcomes) >= number else {}
        anchor = citation.anchor
        if anchor is None:
            state = "no anchor yet"
        elif outcome.get("outcome") == "same-snapshot" and not outcome.get("warnings"):
            state = "anchored and matching the current record"
        elif outcome.get("outcome") in ("same-text", "moved"):
            state = "relocated" + (f" with {', '.join(outcome.get('warnings', []))}" if outcome.get("warnings") else "")
        elif outcome:
            state = f"broken: {outcome.get('reason') or outcome.get('outcome')}"
        else:
            state = "anchored, not seen by a build yet"
        rows.append(dict(number=number, citation=citation, entry=entry, outcome=outcome, state=state,
                         excerpt=anchor.excerpt if anchor else "", heading_path=anchor.heading_path if anchor else [],
                         basis=citation_basis(pack, curation, fact, citation)))
    return rows


def yaml_block(text: str, name: str):
    """A form field holding a YAML block of the curation file; empty means none."""
    if not (text or "").strip():
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WriteRefused(f"{name} is not valid YAML: {exc}") from exc


def yaml_text(value) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=110) if value else ""


def jurisdiction_choices(pack: PackData) -> list[str]:
    """CH, the cantons, and the municipalities the release already knows."""
    release = pack.release.current()
    known = set(release.manifest.jurisdictions) if release else set()
    known |= {fact.jurisdiction for _, fact in pack.curated_facts()}
    municipalities = sorted(code for code in known if code.count("-") == 2)
    return ["CH", *CANTONS, *municipalities]


def containment_sentence(code: str) -> str:
    parts = code.split("-")
    if len(parts) == 1:
        return "answers for CH and every canton and municipality"
    if len(parts) == 2:
        return f"answers for {code} and its municipalities, not for other cantons"
    return f"answers for {code} only"


def base_context(request: Request, pack: PackData, **extra) -> dict:
    curation = pack.curation.current()
    return dict(pack=pack, active="workbench", nodes=tree(pack), curation=curation,
                curation_error=pack.curation.error, sha256=pack.curation.sha256,
                missing_publishers=missing_publishers(pack), draft_provider=assistant_provider(), **extra)


@read_router.get("/packs/{pack}/workbench")
def index(request: Request, pack: PackData = Depends(get_pack)):
    curation = pack.curation.current()
    blocks = dict(institutions=yaml_text([item.model_dump(mode="json", exclude_none=True) for item in curation.institutions]),
                  page_basis=yaml_text({rule: spec.model_dump(mode="json", exclude_none=True)
                                        for rule, spec in curation.page_basis.items()}),
                  ranking_policy=yaml_text(curation.ranking_policy.model_dump(mode="json") if curation.ranking_policy else None)
                  ) if curation else {}
    return render(request, "workbench/index.html", **base_context(request, pack, form="pack", blocks=blocks))


@read_router.get("/packs/{pack}/workbench/topics/{topic_id}")
def topic_form(request: Request, topic_id: str, pack: PackData = Depends(get_pack)):
    curation = pack.curation.current()
    topic = next((item for item in (curation.topics if curation else []) if item.topic_id == topic_id), None)
    return render(request, "workbench/index.html", **base_context(request, pack, form="topic", topic=topic,
                                                                 creating=topic is None, topic_id=topic_id))


@read_router.get("/packs/{pack}/workbench/concepts/{concept_id}")
def concept_form(request: Request, concept_id: str, pack: PackData = Depends(get_pack)):
    concept = pack.concept(concept_id)
    curation = pack.curation.current()
    return render(request, "workbench/index.html",
                  **base_context(request, pack, form="concept", concept=concept, creating=concept is None,
                                 concept_id=concept_id, terms=search_terms(concept) if concept else {},
                                 context_fields=sorted(curation.context_fields) if curation else []))


@read_router.get("/packs/{pack}/workbench/facts/{fact_id}")
def fact_form(request: Request, fact_id: str, pack: PackData = Depends(get_pack)):
    concept, fact = pack.fact(fact_id)
    curation = pack.curation.current()
    return render(request, "workbench/index.html",
                  **base_context(request, pack, form="fact", fact=fact, concept=concept, fact_id=fact_id,
                                 creating=fact is None, evidence=evidence_rows(pack, fact) if fact else [],
                                 jurisdictions=jurisdiction_choices(pack),
                                 containment=containment_sentence(fact.jurisdiction) if fact else "",
                                 context_fields=curation.context_fields if curation else {},
                                 kinds=PROVENANCE_KINDS, statuses=REVIEW_STATUSES,
                                 basis_kinds=BASIS_KINDS, basis_levels=BASIS_LEVELS))


# --- writes -----------------------------------------------------------------


@write_router.post("/packs/{pack}/workbench/pack")
def save_pack_form(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                   title: str = Form(...), scope_statement: str = Form(...), out_of_scope: str = Form(""),
                   out_of_scope_response: str = Form(...), limitations: str = Form(""),
                   freshness_max_age_days: int = Form(60), publishers: str = Form(""),
                   context_fields: str = Form(""), confirm_enum_change: str = Form(""), sha256: str = Form(""),
                   institutions: str = Form(""), page_basis: str = Form(""), ranking_policy: str = Form("")):
    parsed_publishers = {}
    for line in lines(publishers):
        host, _, name = line.partition("=")
        if not name.strip():
            raise WriteRefused(f"publisher line {line!r} is not host=name")
        parsed_publishers[host.strip()] = name.strip()
    try:
        parsed_institutions = [CuratedInstitution.model_validate(item) for item in (yaml_block(institutions, "institutions") or [])]
        parsed_page_basis = {rule: BasisSpec.model_validate(spec) for rule, spec in (yaml_block(page_basis, "page_basis") or {}).items()}
        policy = yaml_block(ranking_policy, "ranking_policy")
        parsed_policy = RankingPolicy.model_validate(policy) if policy else None
    except (ValidationError, TypeError, AttributeError) as exc:
        raise WriteRefused(f"the institutions, page_basis or ranking_policy block would not validate: {exc}") from exc
    fields: dict[str, ContextFieldSpec] = {}
    for line in lines(context_fields):
        name, _, rest = line.partition("=")
        enum_part, _, description = rest.partition("|")
        values = [item.strip() for item in enum_part.split(",") if item.strip()]
        fields[name.strip()] = ContextFieldSpec(enum=values or None, description=description.strip() or name.strip())
    used = {name: [fact.fact_id for _, fact in pack.curated_facts() if name in (fact.condition or {})]
            for name in (pack.curation.current().context_fields if pack.curation.current() else {})}
    for name, facts in used.items():
        if facts and name not in fields:
            raise WriteRefused(f"context field {name} is used by {len(facts)} fact(s) and cannot be removed",
                               facts[:10])
    for name, spec in fields.items():
        in_use = {(fact.condition or {}).get(name) for _, fact in pack.curated_facts() if (fact.condition or {}).get(name)}
        lost = sorted(value for value in in_use if spec.enum and value not in spec.enum)
        if lost and not confirm_enum_change:
            raise WriteRefused(f"enum values {', '.join(lost)} of {name} are in use; confirm to rename them",
                               [fact.fact_id for _, fact in pack.curated_facts()
                                if (fact.condition or {}).get(name) in lost])

    def mutate(curation) -> None:
        curation.title = title.strip()
        curation.scope_statement = scope_statement.strip()
        curation.out_of_scope = lines(out_of_scope)
        curation.out_of_scope_response = out_of_scope_response.strip()
        curation.limitations = lines(limitations)
        curation.freshness_max_age_days = freshness_max_age_days
        curation.publishers = parsed_publishers
        curation.institutions = parsed_institutions
        curation.page_basis = parsed_page_basis
        curation.ranking_policy = parsed_policy
        curation.context_fields = fields

    write_curation(pack, mutate, actor, f"edit the manifest fields of {pack.pack}", expected_sha256=sha256 or None,
                   ids=[pack.pack])
    return RedirectResponse(f"/packs/{pack.pack}/workbench?notice=pack+saved", status_code=303)


@write_router.post("/packs/{pack}/workbench/topics")
def save_topic(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
               topic_id: str = Form(...), title: str = Form(...), description: str = Form(...),
               original_id: str = Form(""), sha256: str = Form("")):
    def mutate(curation) -> None:
        existing = next((item for item in curation.topics if item.topic_id == (original_id or topic_id)), None)
        if existing is None:
            curation.topics.append(CuratedTopic(topic_id=topic_id.strip(), title=title.strip(),
                                                description=description.strip()))
            return
        if original_id and original_id != topic_id:
            for concept in curation.concepts:
                if concept.topic_id == original_id:
                    concept.topic_id = topic_id.strip()
        existing.topic_id, existing.title, existing.description = topic_id.strip(), title.strip(), description.strip()

    write_curation(pack, mutate, actor, f"save topic {topic_id}", expected_sha256=sha256 or None, ids=[topic_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/topics/{topic_id}?notice=topic+saved", status_code=303)


@write_router.post("/packs/{pack}/workbench/concepts")
def save_concept(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                 concept_id: str = Form(...), topic_id: str = Form(...), label: str = Form(...),
                 description: str = Form(...), aliases: str = Form(""), questions: str = Form(""),
                 required_context: str = Form(""), required_user_facts: str = Form(""),
                 decision_rule_description: str = Form(""), decision_rule_steps: str = Form(""),
                 notes: str = Form(""), sha256: str = Form("")):
    user_facts = []
    for line in lines(required_user_facts):
        name, _, rest = line.partition("=")
        status, _, instruction = rest.partition("|")
        user_facts.append(RequiredUserFact(name=name.strip(), status=status.strip() or "unknown",
                                           instruction=instruction.strip() or name.strip()))
    rule = DecisionRule(description=decision_rule_description.strip(), steps=lines(decision_rule_steps)) \
        if decision_rule_description.strip() else None

    def mutate(curation) -> None:
        existing = next((item for item in curation.concepts if item.concept_id == concept_id), None)
        values = dict(topic_id=topic_id.strip(), label=label.strip(), description=description.strip(),
                      aliases=lines(aliases), questions=lines(questions),
                      required_context=lines(required_context), required_user_facts=user_facts,
                      decision_rule=rule, notes=lines(notes))
        if existing is None:
            raise WriteRefused(f"concept {concept_id} does not exist; create it with its first fact from a "
                               "reading view selection")
        for key, value in values.items():
            setattr(existing, key, value)

    write_curation(pack, mutate, actor, f"save concept {concept_id}", expected_sha256=sha256 or None, ids=[concept_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/concepts/{concept_id}?notice=concept+saved",
                            status_code=303)


@write_router.post("/packs/{pack}/workbench/concepts/{concept_id}/rename")
def rename_concept(request: Request, concept_id: str, pack: PackData = Depends(get_pack),
                   actor: str = Depends(require_editor), new_id: str = Form(...), sha256: str = Form("")):
    """Renaming is its own action: it rewrites the fact IDs that carry the concept ID and the check file."""
    new_id = new_id.strip()
    if not new_id or pack.concept(new_id):
        raise WriteRefused(f"{new_id!r} is empty or already a concept")
    renamed: dict[str, str] = {}

    def mutate(curation) -> None:
        concept = next((item for item in curation.concepts if item.concept_id == concept_id), None)
        if concept is None:
            raise WriteRefused(f"unknown concept {concept_id}")
        concept.concept_id = new_id
        for fact in concept.facts:
            if fact.fact_id.startswith(concept_id):
                renamed[fact.fact_id] = new_id + fact.fact_id[len(concept_id):]
                fact.fact_id = renamed[fact.fact_id]

    write_curation(pack, mutate, actor, f"rename concept {concept_id} to {new_id}", expected_sha256=sha256 or None,
                   ids=[concept_id, new_id])
    checks = pack.checks.current()
    if checks is not None:
        from ..writes import write_checks

        updated = checks.model_copy(deep=True)
        for check in updated.checks:
            ids = check.request.get("concept_ids")
            if ids:
                check.request = {**check.request, "concept_ids": [new_id if item == concept_id else item for item in ids]}
            check.expect.status = {(new_id if key == concept_id else key): value
                                   for key, value in check.expect.status.items()}
            check.expect.concept_ids_in_top = [new_id if item == concept_id else item
                                               for item in check.expect.concept_ids_in_top]
            check.expect.fact_ids = [renamed.get(item, item) for item in check.expect.fact_ids]
        write_checks(pack, updated, actor, f"rename concept {concept_id} to {new_id} in the checks", [new_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/concepts/{new_id}?notice=concept+renamed", status_code=303)


def provenance_from_form(fact, kind: str, author: str, source: str, reviewed_on: str, notes: str) -> Provenance:
    """`review_status` is read-only here; only the review queue changes it (rule 5)."""
    current = fact.provenance if fact else None
    return Provenance(kind=kind, review_status=current.review_status if current else "assistant-authored-unreviewed",
                      author=author.strip() or (current.author if current else "unknown"),
                      source=source.strip() or None,
                      reviewed_on=date.fromisoformat(reviewed_on) if reviewed_on.strip() else
                      (current.reviewed_on if current else None),
                      reviewed_by=current.reviewed_by if current else None, notes=lines(notes))


def clear_human_review(fact) -> None:
    if fact.provenance.review_status == "human-reviewed":
        fact.provenance.review_status = "assistant-authored-unreviewed"
        fact.provenance.reviewed_by = None
        fact.provenance.reviewed_on = None


@write_router.post("/packs/{pack}/workbench/facts")
def save_fact(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
              fact_id: str = Form(...), statement: str = Form(...), language: str = Form("en"),
              jurisdiction: str = Form("CH"), valid_from: str = Form(""), valid_through: str = Form(""),
              kind: str = Form("curated-statement"), author: str = Form(""), source: str = Form(""),
              reviewed_on: str = Form(""), notes: str = Form(""), condition: str = Form(""),
              sha256: str = Form("")):
    parsed_condition = {}
    for line in lines(condition):
        name, _, value = line.partition("=")
        if value.strip():
            parsed_condition[name.strip()] = value.strip()
    _, existing = pack.fact(fact_id)
    if existing is None:
        raise WriteRefused(f"fact {fact_id} does not exist; a fact is created from a reading view selection so "
                           "that it has a citation from the start")
    new_values = dict(
        statement=statement.strip(), language=language.strip() or "en", jurisdiction=jurisdiction.strip(),
        condition=parsed_condition or None,
        valid_from=date.fromisoformat(valid_from) if valid_from.strip() else None,
        valid_through=date.fromisoformat(valid_through) if valid_through.strip() else None,
        kind=kind, author=author.strip() or existing.provenance.author,
        source=source.strip() or None, notes=lines(notes),
    )
    changed = (
        existing.statement != new_values["statement"] or existing.language != new_values["language"]
        or existing.jurisdiction != new_values["jurisdiction"] or existing.condition != new_values["condition"]
        or existing.valid_from != new_values["valid_from"] or existing.valid_through != new_values["valid_through"]
        or existing.provenance.kind != new_values["kind"] or existing.provenance.author != new_values["author"]
        or existing.provenance.source != new_values["source"] or existing.provenance.notes != new_values["notes"])

    def mutate(curation) -> None:
        for concept in curation.concepts:
            for fact in concept.facts:
                if fact.fact_id != fact_id:
                    continue
                fact.statement = new_values["statement"]
                fact.language = new_values["language"]
                fact.jurisdiction = new_values["jurisdiction"]
                fact.condition = new_values["condition"]
                fact.valid_from = new_values["valid_from"]
                fact.valid_through = new_values["valid_through"]
                fact.provenance = provenance_from_form(fact, kind, author, source, reviewed_on, notes)
                if changed:
                    clear_human_review(fact)

    write_curation(pack, mutate, actor, f"save fact {fact_id}", expected_sha256=sha256 or None, ids=[fact_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/facts/{fact_id}?notice=fact+saved", status_code=303)


@write_router.post("/packs/{pack}/workbench/facts/{fact_id}/evidence")
def edit_evidence(request: Request, fact_id: str, pack: PackData = Depends(get_pack),
                  actor: str = Depends(require_editor), action: str = Form(...), number: int = Form(0),
                  document_id: str = Form(""), first_block: int = Form(0), last_block: int = Form(0),
                  basis_kind: str = Form(""), basis_level: str = Form(""), basis_norm: str = Form(""),
                  basis_refers_to: str = Form(""), sha256: str = Form("")):
    if action == "basis" and basis_kind and basis_kind not in BASIS_KINDS:
        raise WriteRefused(f"unknown basis kind {basis_kind!r}")
    if action == "basis" and basis_level and basis_level not in BASIS_LEVELS:
        raise WriteRefused(f"unknown basis level {basis_level!r}")

    def mutate(curation) -> None:
        for concept in curation.concepts:
            for fact in concept.facts:
                if fact.fact_id != fact_id:
                    continue
                if action == "remove":
                    if len(fact.evidence) == 1:
                        raise WriteRefused("a fact needs at least one citation; reject the fact instead")
                    fact.evidence.pop(number - 1)
                elif action == "basis":
                    # What this excerpt is, when the page default does not say: the law quoted on an authority's page.
                    # An empty kind returns the citation to the page default.
                    fact.evidence[number - 1].basis = BasisSpec(
                        kind=basis_kind, level=basis_level or None, norm=basis_norm.strip() or None,
                        refers_to=basis_refers_to.strip() or None) if basis_kind else None
                else:
                    citation = citation_for(pack, document_id, first_block, last_block)
                    if action == "add":
                        fact.evidence.append(citation)
                    else:
                        fact.evidence[number - 1] = citation
                clear_human_review(fact)

    write_curation(pack, mutate, actor, f"{action} citation of fact {fact_id}", expected_sha256=sha256 or None,
                   ids=[fact_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/facts/{fact_id}?notice=evidence+{action}ed",
                            status_code=303)


def citation_for(pack: PackData, document_id: str, first_block: int, last_block: int) -> CitationRef:
    record = pack.dataset.record(document_id)
    if record is None:
        raise WriteRefused(f"no record {document_id} in the text dataset")
    last = last_block or first_block
    if not (1 <= first_block <= last <= len(record["blocks"])):
        raise WriteRefused(f"block range {first_block}-{last} is outside {document_id}")
    return CitationRef(document_id=document_id, first_block=first_block, last_block=last,
                       anchor=Anchor(**make_anchor(record, first_block, last)))


@write_router.post("/packs/{pack}/workbench/cite")
def cite(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
         mode: str = Form(...), document_id: str = Form(...), first_block: int = Form(...),
         last_block: int = Form(0), fact_id: str = Form(""), concept_id: str = Form(""),
         new_fact_id: str = Form(""), statement: str = Form(""), jurisdiction: str = Form("CH"),
         language: str = Form("en"), sha256: str = Form("")):
    """The reading view's cite action: add the range to a fact, or start a fact under a concept."""
    citation = citation_for(pack, document_id, first_block, last_block)
    target = fact_id if mode == "existing" else (new_fact_id.strip() or f"{concept_id}-new")
    if mode == "existing" and not fact_id:
        raise WriteRefused("choose the fact the range belongs to")
    if mode == "new":
        if not concept_id:
            raise WriteRefused("choose the concept the new fact belongs to")
        if pack.fact(target)[1] is not None:
            raise WriteRefused(f"fact {target} already exists")

    def mutate(curation) -> None:
        if mode == "existing":
            for concept in curation.concepts:
                for fact in concept.facts:
                    if fact.fact_id == target:
                        fact.evidence.append(citation)
            return
        concept = next((item for item in curation.concepts if item.concept_id == concept_id), None)
        if concept is None:
            raise WriteRefused(f"unknown concept {concept_id}")
        concept.facts.append(CuratedFact(
            fact_id=target, statement=statement.strip() or citation.anchor.excerpt.strip()[:400],
            language=language.strip() or "en", jurisdiction=jurisdiction.strip(),
            provenance=Provenance(kind="curated-statement", review_status="assistant-authored-unreviewed",
                                  author=actor, notes=[f"Cited from the reading view on {date.today().isoformat()}."]),
            evidence=[citation]))

    write_curation(pack, mutate, actor,
                   f"cite {document_id} blocks {first_block}-{last_block or first_block} on fact {target}",
                   expected_sha256=sha256 or None, ids=[target])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/facts/{target}?notice=citation+added", status_code=303)


@write_router.post("/packs/{pack}/workbench/concepts/new")
def new_concept(request: Request, pack: PackData = Depends(get_pack), actor: str = Depends(require_editor),
                concept_id: str = Form(...), topic_id: str = Form(...), label: str = Form(...),
                description: str = Form(...), document_id: str = Form(...), first_block: int = Form(...),
                last_block: int = Form(0), statement: str = Form(""), jurisdiction: str = Form("CH"),
                sha256: str = Form("")):
    """A concept is created together with its first fact, because both models need one."""
    citation = citation_for(pack, document_id, first_block, last_block)
    concept_id = concept_id.strip()
    if pack.concept(concept_id):
        raise WriteRefused(f"concept {concept_id} already exists")

    def mutate(curation) -> None:
        if all(topic.topic_id != topic_id for topic in curation.topics):
            raise WriteRefused(f"unknown topic {topic_id}")
        curation.concepts.append(CuratedConcept(
            concept_id=concept_id, topic_id=topic_id.strip(), label=label.strip(), description=description.strip(),
            facts=[CuratedFact(fact_id=f"{concept_id}-1",
                               statement=statement.strip() or citation.anchor.excerpt.strip()[:400],
                               jurisdiction=jurisdiction.strip(),
                               provenance=Provenance(kind="curated-statement",
                                                     review_status="assistant-authored-unreviewed", author=actor),
                               evidence=[citation])]))

    write_curation(pack, mutate, actor, f"create concept {concept_id} with its first fact",
                   expected_sha256=sha256 or None, ids=[concept_id])
    return RedirectResponse(f"/packs/{pack.pack}/workbench/concepts/{concept_id}?notice=concept+created",
                            status_code=303)
