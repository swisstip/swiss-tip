"""Build a knowledge release from a curation file and a text dataset.

Every citation of the curation file is resolved against the text dataset:
the cited record is loaded, the block range is cut to an exact excerpt, and
the excerpt is pinned with the record's content hash and the raw hash of the
saved response. When the cited record no longer exists (the page was
downloaded anew), the citation's anchor is relocated onto the newest eligible
record of the same source URL with `swisstip.extraction.anchors`; outcomes
`same-text` and `moved` are accepted and reported, everything else drops the
fact from the release and lists it in the build report. Nothing is guessed.
"""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from swisstip.core.basis import DEFAULT_RANKING_POLICY, NORM_KINDS, level_of_jurisdiction, make_basis, strongest_basis
from swisstip.core.release import (Basis, Concept, EvidenceRecord, FactRecord, Freshness, Institution, KnowledgeGraph, Manifest,
                                   PlaceRegister, Release, SourceDocument, Topic, content_hash, sha256_text)
from swisstip.core.validation import validate_release
from swisstip.extraction.anchors import make_anchor, relocate

from .questions import missing_question_languages
from .source_terms import missing_source_terms
from .curation import Anchor, BasisSpec, CitationRef, Curation

ACCEPTED_OUTCOMES = {"same-text", "moved"}


class BuildError(ValueError):
    pass


class TextDataset:
    def __init__(self, text_dir: Path):
        self.text_dir = Path(text_dir)
        index_path = self.text_dir / "index.json"
        if not index_path.is_file():
            raise BuildError(f"Not a text dataset (no index.json): {text_dir}")
        self.index = {entry["document_id"]: entry for entry in json.loads(index_path.read_text(encoding="utf-8"))}
        self._records: dict[str, dict] = {}

    def is_current(self, document_id: str) -> bool:
        """Listed in the index and not superseded by a later download attempt."""
        entry = self.index.get(document_id)
        return bool(entry) and not entry.get("superseded")

    def record(self, document_id: str) -> dict | None:
        if document_id not in self._records:
            path = self.text_dir / "documents" / f"{document_id}.json"
            self._records[document_id] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return self._records[document_id]

    def newest_for_source(self, source_url: str) -> dict | None:
        candidates = [e for e in self.index.values() if e["source_url"] == source_url and e["eligible_for_processing"]
                      and not e.get("superseded") and e.get("preferred_representation")]
        if not candidates:
            return None
        newest = max(candidates, key=lambda e: e.get("retrieved_at") or "")
        return self.record(newest["document_id"])


def resolve_citation(dataset: TextDataset, citation: CitationRef) -> tuple[dict | None, dict]:
    """Return (record, resolution) where resolution has the excerpt coordinates or a reason for dropping."""
    record = dataset.record(citation.document_id) if dataset.is_current(citation.document_id) else None
    anchor = citation.anchor
    if record is not None and record.get("eligible_for_processing"):
        last = citation.last_block or citation.first_block
        blocks = record["blocks"][citation.first_block - 1:last]
        intact = len(blocks) == last - citation.first_block + 1
        if anchor and (not intact or [b["text_sha256"] for b in blocks] != anchor.block_hashes):
            # The same saved response extracted anew (a newer extractor) can number its blocks differently: find the
            # anchored excerpt in the record instead of dropping the fact.
            result = relocate(anchor.model_dump(), record, None)
            if result["outcome"] not in ACCEPTED_OUTCOMES:
                return record, dict(outcome="dropped", reason=f"block text changed inside an unchanged document id "
                                                              f"and relocation found it {result['outcome']}")
            return record, dict(outcome=result["outcome"], start=result["start"], end=result["end"],
                                excerpt=result["excerpt"], block_ids=result["block_ids"], warnings=result["warnings"])
        if not intact:
            return record, dict(outcome="dropped", reason=f"block range {citation.first_block}-{last} outside the record")
        start, end = blocks[0]["start"], blocks[-1]["end"]
        return record, dict(outcome="same-snapshot", start=start, end=end, excerpt=record["content_text"][start:end],
                            block_ids=[b["block_id"] for b in blocks], warnings=[])
    if anchor is None:
        return None, dict(outcome="dropped", reason=f"record {citation.document_id} is missing and the citation has no anchor")
    replacement = dataset.newest_for_source(anchor.source_url)
    if replacement is None:
        return None, dict(outcome="dropped", reason=f"no eligible record for {anchor.source_url} in the dataset")
    result = relocate(anchor.model_dump(), replacement, None)
    if result["outcome"] not in ACCEPTED_OUTCOMES:
        return replacement, dict(outcome=result["outcome"], reason=f"relocation {result['outcome']} on {replacement['document_id']}",
                                 candidates=result.get("candidates", [])[:3])
    return replacement, dict(outcome=result["outcome"], start=result["start"], end=result["end"], excerpt=result["excerpt"],
                             block_ids=result["block_ids"], warnings=result["warnings"])


def publisher_for(curation: Curation, record: dict) -> str:
    host = urlsplit(record["source_url"]).hostname or ""
    for candidate in (host, host.removeprefix("www.")):
        if candidate in curation.publishers:
            return curation.publishers[candidate]
    for entry in record.get("source_registry", []):
        authority = entry.get("definition", {}).get("canonical_authority")
        if authority:
            return authority
    raise BuildError(f"No publisher known for host {host!r}; add it to the curation file's publishers")


def match_rule(url: str, rules) -> str | None:
    """The longest rule (a host, or a host plus path prefix) that the URL falls under, else None."""
    parts = urlsplit(url)
    host, path = parts.hostname or "", parts.path or "/"
    best = None
    for rule in rules:
        rule_host, slash, rule_path = rule.partition("/")
        if rule_host != host or (slash and not path.startswith("/" + rule_path)):
            continue
        if best is None or len(rule) > len(best):
            best = rule
    return best


def catalogue_entry(record: dict) -> dict | None:
    """The record's catalogue entry with the longest path prefix containing its source URL, else None."""
    path = urlsplit(record["source_url"]).path or "/"
    best, best_length = None, -1
    for entry in record.get("source_registry", []):
        definition = entry.get("definition", {})
        if not definition.get("source_id"):
            continue
        for prefix in definition.get("allowed_path_prefixes") or ["/"]:
            stem = prefix.rstrip("/")
            if prefix == "/" or path == stem or path.startswith(stem + "/") or path.startswith(prefix):
                if len(prefix) > best_length:
                    best, best_length = entry, len(prefix)
    return best


def registry_institution(curation: Curation, source_url: str) -> tuple[Institution | None, str | None]:
    """The registry entry whose rule (a host, or a host plus path prefix) is the longest one the URL falls under."""
    rules = {url: institution for institution in curation.institutions for url in institution.urls}
    rule = match_rule(source_url, rules)
    if rule is None:
        return None, None
    curated = rules[rule]
    return Institution(institution_id=curated.institution_id, name=curated.name, native_name=curated.native_name,
                       level=curated.level, body=curated.body, jurisdiction=curated.jurisdiction), f"registry rule {rule}"


def institution_for(curation: Curation, record: dict) -> tuple[Institution | None, str]:
    """Who published the record's page: the registry rule with the longest prefix, else its catalogue entry, else none."""
    institution, rule = registry_institution(curation, record["source_url"])
    if institution is not None:
        return institution, rule
    entry = catalogue_entry(record)
    if entry is None:
        return None, "none"
    definition = entry["definition"]
    jurisdiction = definition.get("jurisdiction") or "CH"
    level = entry.get("authority_level")
    bfs_code = (entry.get("municipality") or {}).get("bfs_code")
    if level == "municipal" and bfs_code:
        jurisdiction = f"{jurisdiction}-{int(bfs_code)}"
    host = urlsplit(record["source_url"]).hostname or ""
    name = next((curation.publishers[candidate] for candidate in (host, host.removeprefix("www."))
                 if candidate in curation.publishers), None) or definition.get("canonical_authority") or host
    return Institution(institution_id=f"catalogue-{definition['source_id']}", name=name,
                       level=level or level_of_jurisdiction(jurisdiction), body="administration",
                       jurisdiction=jurisdiction), f"catalogue source {definition['source_id']}"


ARTICLE_HEADING = re.compile(r"^Art\.\s*(\d+)([a-z]{0,3})\b")
ANNEX_HEADING = re.compile(r"^Anhang\s+([IVXLC]+)")
ARTICLE_IN_NORM = re.compile(r"\bArt\.\s*(\d+[a-z]{0,3})\b")


def article_number(digits: str, previous: int, suffix: str) -> int:
    """Fedlex glues footnote numbers to article numbers, so the heading "Art. 4384" after Article 42 is Article 43
    with footnote 84: the shortest prefix that continues the sequence wins; a suffixed article (58a) may repeat the
    previous number."""
    candidates = [int(digits[:length]) for length in range(1, len(digits) + 1)]
    for candidate in candidates:
        if candidate > previous or (suffix and candidate >= previous):
            return candidate
    return candidates[-1]


def derive_article(record: dict, first_block_id: str) -> tuple[str | None, str | None]:
    """(annex, "Art. N") of the cited block from the heading paths of the record, walking the articles in order so
    that glued footnote digits and the restarted numbering of a treaty's annex resolve; (None, None) when the cited
    block is under no article heading."""
    previous, scope, last_heading, current = 0, None, None, None
    for block in record.get("blocks", []):
        path = block.get("heading_path") or []
        annex = next((match.group(1) for heading in path if (match := ANNEX_HEADING.match(heading))), None)
        if annex != scope:
            scope, previous, last_heading, current = annex, 0, None, None
        heading = next((item for item in reversed(path) if ARTICLE_HEADING.match(item)), None)
        if heading is None:
            current = None
        elif heading != last_heading:
            digits, suffix = ARTICLE_HEADING.match(heading).groups()
            number = article_number(digits, previous, suffix)
            previous, current = number, f"Art. {number}{suffix}"
        last_heading = heading
        if block.get("block_id") == first_block_id:
            return scope, current
    return None, None


def basis_spec_for(curation: Curation, citation: CitationRef, source_url: str, institution: Institution | None
                   ) -> tuple[BasisSpec | None, str]:
    """The curator's statement of what the excerpt is: the citation's own basis, else the page rule with the
    longest prefix, else guidance for a page with an institution; None for a page without one."""
    if citation.basis is not None:
        return citation.basis, "citation"
    match = match_rule(source_url, curation.page_basis)
    if match is not None:
        return curation.page_basis[match], f"page rule {match}"
    if institution is not None:
        return BasisSpec(kind="guidance"), "default"
    return None, "none"


def basis_for(curation: Curation, citation: CitationRef, record: dict, institution: Institution | None,
              fact_id: str, block_ids: list[str] | None = None) -> tuple[Basis | None, str, list[str]]:
    """What the excerpt is, with the article of a law citation derived from the record's heading paths when the
    page rule names the norm without one; returns the basis, the rule that produced it and warnings for a
    hand-written norm whose article differs from the derived one."""
    spec, rule = basis_spec_for(curation, citation, record["source_url"], institution)
    if spec is None:
        return None, rule, []
    level = spec.level or (institution.level if institution else None)
    if level is None:
        raise BuildError(f"fact {fact_id}: the basis of a citation of {record['source_url']} names no level and the page "
                         "has no institution; add the institution or the level")
    norm, warnings = spec.norm, []
    if spec.kind in ("act", "ordinance", "treaty") and block_ids:
        annex, article = derive_article(record, block_ids[0])
        if article and rule != "citation":
            norm = f"{norm}, Annex {annex}, {article}" if annex else f"{norm}, {article}"
        elif article and norm and (found := ARTICLE_IN_NORM.search(norm)) and found.group(1) != article.removeprefix("Art. "):
            warnings.append(f"the norm names {found.group(0)} but the cited block lies under {article}")
    if spec.kind in NORM_KINDS and not (norm or "").strip():
        raise BuildError(f"fact {fact_id}: a basis of kind {spec.kind} needs a norm ({record['source_url']})")
    return make_basis(level, spec.kind, norm, spec.refers_to), rule, warnings


def record_language(record: dict, page_languages: dict[str, str] | None = None) -> str:
    """The record's declared language, else its hint, else the curation's page rule, else `und`."""
    language = record.get("language_declared") or record.get("language_hint")
    if not language and page_languages:
        rule = match_rule(record["source_url"], page_languages)
        language = page_languages[rule] if rule else None
    return (language or "und").replace("_", "-").split("-")[0].lower()


def accessed_on(record: dict):
    return datetime.fromisoformat(record["acquisition"]["retrieved_at"]).date()


def build_release(curation: Curation, text_dir: Path, release_id: str, *, created_at: datetime | None = None,
                  update_citations: bool = False, place_register: PlaceRegister | None = None,
                  acceptance_suite_sha256: str | None = None,
                  knowledge_graph: KnowledgeGraph | None = None) -> tuple[Release, dict]:
    """`place_register` is the register the curation names, loaded by the caller, who knows where the curation file
    lies (`swisstip.build.places.place_register_for`); without one the release accepts jurisdiction codes only.
    `knowledge_graph` is the compiled graph the curation names (`swisstip.build.graph_embed.graph_for`), embedded
    as it is; the release validator checks it against the place register and the topics' `graph_nodes`."""
    dataset = TextDataset(text_dir)
    documents: dict[str, SourceDocument] = {}
    institutions: dict[str, Institution] = {}
    document_report: dict[str, dict] = {}
    policy = curation.ranking_policy or DEFAULT_RANKING_POLICY
    # A topic's graph_nodes bridge it to the embedded graph; without one there is nothing to bridge to yet (a graph is
    # derived from the pack's release before it is compiled and embedded), so they are left out.
    topics = [Topic(**t.model_dump(exclude=set() if knowledge_graph else {"graph_nodes"})) for t in curation.topics]
    concepts, facts, evidence = [], [], []
    report_facts, dropped, outcomes = [], [], Counter()
    term_issues, question_issues = [], []
    for curated_concept in curation.concepts:
        lacking = missing_question_languages(curated_concept.questions, curation.question_languages)
        if lacking:
            question_issues.append(f"{curated_concept.concept_id}: {lacking}")
        concept_facts, concept_excerpts = [], []
        for curated_fact in curated_concept.facts:
            fact_evidence = []
            fact_report = dict(fact_id=curated_fact.fact_id, citations=[])
            for number, citation in enumerate(curated_fact.evidence, 1):
                record, resolution = resolve_citation(dataset, citation)
                outcomes[resolution["outcome"]] += 1
                citation_report = dict(document_id=citation.document_id, **{k: v for k, v in resolution.items() if k != "excerpt"})
                fact_report["citations"].append(citation_report)
                if resolution["outcome"] in ("dropped", "changed", "ambiguous"):
                    fact_evidence = None
                    break
                document_id = record["document_id"]
                if document_id not in documents:
                    institution, institution_rule = institution_for(curation, record)
                    if institution is None and curation.institutions:
                        raise BuildError(f"No institution matches {record['source_url']}; add a rule to the curation file's institutions")
                    if institution is not None:
                        institutions.setdefault(institution.institution_id, institution)
                    documents[document_id] = SourceDocument(
                        document_id=document_id, source_url=record["source_url"], document_url=record["document_url"],
                        version_uri=record.get("version_uri"), title=record.get("title") or record["source_url"],
                        publisher=institution.name if institution else publisher_for(curation, record),
                        institution_id=institution.institution_id if institution else None,
                        language=record_language(record, curation.page_languages), accessed_on=accessed_on(record),
                        raw_sha256=record["acquisition"]["raw_sha256"], content_sha256=record["content_sha256"])
                    document_report[document_id] = dict(document_id=document_id, source_url=record["source_url"],
                                                        institution_id=institution.institution_id if institution else None,
                                                        institution_rule=institution_rule)
                document = documents[document_id]
                institution = institutions.get(document.institution_id) if document.institution_id else None
                basis, basis_rule, basis_warnings = basis_for(curation, citation, record, institution, curated_fact.fact_id,
                                                              resolution["block_ids"])
                citation_report.update(institution_id=document.institution_id, basis=basis.label if basis else None,
                                       basis_rule=basis_rule, basis_warnings=basis_warnings)
                evidence_id = f"e-{curated_fact.fact_id}-{number}"
                fact_evidence.append(EvidenceRecord(
                    evidence_id=evidence_id, document_id=document_id, source_title=document.title,
                    publisher=document.publisher, institution_id=document.institution_id, url=record["document_url"],
                    language=document.language, accessed_on=document.accessed_on,
                    start_offset=resolution["start"], end_offset=resolution["end"], original_excerpt=resolution["excerpt"],
                    excerpt_sha256=sha256_text(resolution["excerpt"]), block_ids=resolution["block_ids"],
                    content_sha256=record["content_sha256"], raw_sha256=record["acquisition"]["raw_sha256"], basis=basis))
                if update_citations and resolution["outcome"] != "same-snapshot":
                    first = int(resolution["block_ids"][0].rsplit(":b", 1)[1])
                    last = int(resolution["block_ids"][-1].rsplit(":b", 1)[1])
                    citation.document_id, citation.first_block, citation.last_block = document_id, first, last
                if update_citations or citation.anchor is None:
                    first = int(resolution["block_ids"][0].rsplit(":b", 1)[1])
                    last = int(resolution["block_ids"][-1].rsplit(":b", 1)[1])
                    citation.anchor = Anchor(**make_anchor(record, first, last))
            if fact_evidence is None:
                dropped.append(fact_report)
                continue
            evidence.extend(fact_evidence)
            concept_excerpts.extend(item.original_excerpt for item in fact_evidence)
            provenance = curated_fact.provenance.model_copy(
                update=dict(basis=strongest_basis([item.basis for item in fact_evidence], policy)))
            fact_report["basis"] = provenance.basis.label if provenance.basis else None
            facts.append(FactRecord(fact_id=curated_fact.fact_id, concept_id=curated_concept.concept_id,
                                    statement=curated_fact.statement, language=curated_fact.language,
                                    jurisdiction=curated_fact.jurisdiction, condition=curated_fact.condition,
                                    valid_from=curated_fact.valid_from, valid_through=curated_fact.valid_through,
                                    evidence_ids=[e.evidence_id for e in fact_evidence], provenance=provenance))
            concept_facts.append(facts[-1])
            report_facts.append(fact_report)
        if not concept_facts:
            dropped.append(dict(concept_id=curated_concept.concept_id, reason="every fact of the concept was dropped"))
            continue
        missing = missing_source_terms(curated_concept.source_terms, concept_excerpts)
        if missing:
            term_issues.append(f"{curated_concept.concept_id}: {missing}")
        aliases = list(curated_concept.aliases) + [t for t in curated_concept.source_terms if t not in curated_concept.aliases]
        schema = {name: curation.context_fields[name] for fact in concept_facts for name in (fact.condition or {})
                  if name in curation.context_fields}
        for name in curated_concept.required_context:
            if name in curation.context_fields:
                schema[name] = curation.context_fields[name]
        concepts.append(Concept(concept_id=curated_concept.concept_id, topic_id=curated_concept.topic_id,
                                label=curated_concept.label, description=curated_concept.description,
                                aliases=aliases, questions=curated_concept.questions,
                                jurisdictions=sorted({f.jurisdiction for f in concept_facts}),
                                required_context=curated_concept.required_context, context_schema=schema,
                                fact_ids=[f.fact_id for f in concept_facts],
                                required_user_facts=curated_concept.required_user_facts,
                                decision_rule=curated_concept.decision_rule, notes=curated_concept.notes,
                                not_served=curated_concept.not_served))
    if term_issues:
        raise BuildError("Source terms must occur verbatim in the concept's cited excerpts; not found: " + "; ".join(term_issues))
    if question_issues:
        raise BuildError("Every concept needs a sample question in each of the curation's question_languages; missing: "
                         + "; ".join(question_issues))
    if not facts:
        raise BuildError("No fact could be resolved; nothing to release")
    snapshot_date = max(d.accessed_on for d in documents.values())
    manifest = Manifest(
        release_id=release_id, pack=curation.pack, title=curation.title, created_at=created_at or datetime.now(UTC),
        scope_statement=curation.scope_statement, out_of_scope=curation.out_of_scope,
        out_of_scope_response=curation.out_of_scope_response,
        jurisdictions=sorted({f.jurisdiction for f in facts}), languages=sorted({e.language for e in evidence}),
        freshness=Freshness(snapshot_date=snapshot_date, max_age_days=curation.freshness_max_age_days,
                            stale_from=snapshot_date + timedelta(days=curation.freshness_max_age_days)),
        provenance_kinds=dict(Counter(f.provenance.kind for f in facts)),
        review_statuses=dict(Counter(f.provenance.review_status for f in facts)),
        institution_levels=dict(Counter(institutions[d.institution_id].level for d in documents.values() if d.institution_id)),
        basis_kinds=dict(Counter(f.provenance.basis.kind for f in facts if f.provenance.basis)),
        ranking_policy=policy if (institutions or curation.ranking_policy) else None,
        limitations=curation.limitations, acceptance_suite_sha256=acceptance_suite_sha256,
        content_sha256="0" * 64)
    release = Release(manifest=manifest, documents=sorted(documents.values(), key=lambda d: d.document_id),
                      topics=topics, concepts=concepts, facts=facts, evidence=evidence,
                      institutions=sorted(institutions.values(), key=lambda i: i.institution_id),
                      place_register=place_register, knowledge_graph=knowledge_graph)
    release.manifest.content_sha256 = content_hash(release)
    issues = validate_release(release, text_dir)
    if issues:
        raise BuildError("Built release does not validate: " + "; ".join(issues[:5]))
    cited_ids = {d.institution_id for d in documents.values()}
    report = dict(release_id=release_id, built_at=release.manifest.created_at.isoformat(), text_dataset=str(Path(text_dir).resolve()),
                  concepts=len(concepts), facts=len(facts), evidence=len(evidence), documents=len(documents),
                  citation_outcomes=dict(outcomes), dropped=dropped, facts_resolved=report_facts,
                  source_terms=sum(len(c.source_terms) for c in curation.concepts),
                  questions=sum(len(c.questions) for c in curation.concepts),
                  institutions=[dict(institution_id=i.institution_id, name=i.name, level=i.level, jurisdiction=i.jurisdiction,
                                     documents=sum(1 for d in documents.values() if d.institution_id == i.institution_id),
                                     from_catalogue=i.institution_id.startswith("catalogue-"))
                                for i in release.institutions],
                  institutions_unused=[i.institution_id for i in curation.institutions if i.institution_id not in cited_ids],
                  documents_attributed=sorted(document_report.values(), key=lambda d: d["document_id"]),
                  basis_kinds=release.manifest.basis_kinds,
                  place_register=None if place_register is None else dict(
                      url=place_register.url, accessed_on=place_register.accessed_on.isoformat(),
                      raw_sha256=place_register.raw_sha256, places=len(place_register.places),
                      aliases=sum(len(place.aliases) for place in place_register.places)),
                  knowledge_graph=None if knowledge_graph is None else dict(
                      graph_id=knowledge_graph.graph_id, content_sha256=knowledge_graph.content_sha256,
                      nodes=len(knowledge_graph.nodes), edges=len(knowledge_graph.edges),
                      review_statuses=knowledge_graph.review_statuses,
                      bridged_topics={t.topic_id: t.graph_nodes for t in topics if t.graph_nodes}),
                  validation_issues=issues)
    return release, report
