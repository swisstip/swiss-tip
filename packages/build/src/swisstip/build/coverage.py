"""Curation coverage: every content section of every candidate record is cited by a fact or dispositioned by name.

The acquisition side reports the pages that failed to download, and the
release validator checks that every document the release lists is cited. What
neither could say was that a page had been saved, extracted and forgotten: the
release's document list is derived from the citations, so the check was
circular, and nothing joined the text dataset with the release. The Zurich
pack carried 29 catalogued German pages and five federal acts that no fact
cited for a week, and one gap was even written into an acceptance case as an
expectation. This module makes that state visible and, when a pack asks for it,
a build error.

The unit is the *section*, not the document: a page can be cited for one
paragraph while the paragraph that answers the question two headings above it
is never read (the state list of the ZAS page was exactly that). A section
counts as cited when any block of it is cited by a fact, and as dispositioned
when a curator wrote down, by name and with a reason, why it is not:

| kind | means | needs |
| --- | --- | --- |
| `out_of_scope` | the manifest's `out_of_scope` excludes this | `out_of_scope_entry`, a phrase of one of those entries |
| `duplicate` | another cited record carries the same content | - |
| `navigation` | a hub or link page whose children are cited | - |
| `deferred` | content the pack could curate and has not yet | `reaffirm_by`, after which the disposition expires |

Three classes are settled without a curator, and reported as such:

- a *reference document* (a plugin document: an act, an ordinance, a treaty
  the Fedlex plugin resolved) is one unit, cited when any article is cited. A
  curator cites articles from a statute; asking for a disposition of every
  uncited article would produce nothing but rubber stamps;
- *boilerplate*: a section whose text recurs, word for word, in
  `BOILERPLATE_MIN_DOCUMENTS` or more candidate records (the contact card, the
  telephone hours, the "no e-mail address" note that every page of a site
  repeats) is not asked for, unless a fact cites it on that page;
- an *oversized* page (more than `OVERSIZE_SECTIONS` content sections, a
  tariff table rendered as hundreds of heading runs) is judged by its top two
  heading levels instead of its deepest, so that it has tens of units, not
  hundreds.

A disposition names a `document_id` (all sections, or the `section_ids` it
lists) or a `url_prefix` rule with the `known_documents` it covers. A document
under a rule that the rule does not know yet is reported as `new_under_rule`,
so a page that appears later under an old prefix is seen once by a person
before it is swallowed. `document_id` binds the source URL and the raw bytes,
so a page downloaded anew loses its disposition (`stale`) and is judged again.

The report also rolls up by catalogue source: a source that produced no cited
section and no disposition "gave us nothing", which is the sentence an
operator understands. `coverage_policy` in the curation decides whether an
unclean report fails the build (`enforce`) or is only written (`report`, the
default, so that a pack with history can write its dispositions first).
"""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from swisstip.core.release import Release
from swisstip.extraction.sections import build_sections, candidate_status, is_content

COVERAGE_SCHEMA_VERSION = "swiss-tip-curation-coverage/v1"
REPORT_SCHEMA_VERSION = "swiss-tip-curation-coverage-report/v1"
POLICIES = ("report", "enforce")
DispositionKind = Literal["out_of_scope", "duplicate", "navigation", "deferred"]
# Records judged as one unit: the statutes the Fedlex plugin resolved, which a curator cites from article by article.
REFERENCE_KINDS = frozenset({"plugin-document"})
# A section whose text recurs in this many candidate records is site boilerplate, not content to disposition.
BOILERPLATE_MIN_DOCUMENTS = 5
# A page with more content sections than this is judged by its top two heading levels.
OVERSIZE_SECTIONS = 100


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Disposition(Strict):
    kind: DispositionKind
    reason: str = Field(min_length=1, description="Why these sections are not cited, in one or two sentences.")
    author: str = Field(min_length=1)
    date: date
    document_id: str | None = Field(default=None, description="The record; all its content sections unless section_ids narrows it.")
    section_ids: list[str] = Field(default_factory=list, description="Sections of document_id (section-0001, ...).")
    url_prefix: str | None = Field(default=None, description=(
        "A rule for every candidate record whose source URL starts with this prefix, for example a whole action plan."))
    known_documents: list[str] = Field(default_factory=list, description=(
        "The records the rule covered when it was written; a record under the prefix that is not listed is reported "
        "as new_under_rule until someone looks and adds it."))
    out_of_scope_entry: str | None = Field(default=None, description=(
        "For kind out_of_scope: a phrase of the manifest's out_of_scope entry that excludes this content."))
    reaffirm_by: date | None = Field(default=None, description="For kind deferred: the date the disposition expires.")

    @model_validator(mode="after")
    def target_and_kind_are_complete(self) -> "Disposition":
        if bool(self.document_id) == bool(self.url_prefix):
            raise ValueError("a disposition names exactly one of document_id and url_prefix")
        if self.section_ids and not self.document_id:
            raise ValueError("section_ids need a document_id")
        if self.known_documents and not self.url_prefix:
            raise ValueError("known_documents belong to a url_prefix rule")
        if self.kind == "out_of_scope" and not (self.out_of_scope_entry or "").strip():
            raise ValueError("kind out_of_scope needs out_of_scope_entry")
        if self.kind == "deferred" and self.reaffirm_by is None:
            raise ValueError("kind deferred needs reaffirm_by")
        return self


class CoverageDispositions(Strict):
    schema_version: Literal["swiss-tip-curation-coverage/v1"] = COVERAGE_SCHEMA_VERSION
    pack: str
    dispositions: list[Disposition] = Field(default_factory=list)


def load_dispositions(path: Path) -> CoverageDispositions:
    return CoverageDispositions.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def dump_dispositions(dispositions: CoverageDispositions) -> str:
    return yaml.safe_dump(dispositions.model_dump(mode="json", exclude_none=True, exclude_defaults=True) | dict(
        schema_version=dispositions.schema_version, pack=dispositions.pack), allow_unicode=True, sort_keys=False, width=110)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def block_number(block_id: str) -> int:
    return int(block_id.rsplit(":b", 1)[1])


def cited_blocks(release: Release) -> dict[str, set[int]]:
    """Block numbers cited by at least one fact, per document."""
    cited: dict[str, set[int]] = defaultdict(set)
    for evidence in release.evidence:
        for block_id in evidence.block_ids:
            cited[evidence.document_id].add(block_number(block_id))
    return cited


def catalogue_sources(catalogue: dict | None) -> dict[str, str]:
    """source_id -> title of every catalogue entry."""
    if not catalogue:
        return {}
    return {entry["definition"]["source_id"]: entry.get("title") or entry["definition"]["source_id"]
            for entry in catalogue.get("sources", []) if isinstance(entry, dict) and "definition" in entry}


def candidate_entries(index: list[dict]) -> list[dict]:
    """The curation candidates of a text index; an index without the flag (an older dataset) is classified here."""
    result = []
    for entry in index:
        if "curation_candidate" in entry:
            if entry["curation_candidate"]:
                result.append(entry)
        elif candidate_status(entry)[0]:
            result.append(entry)
    return result


def section_text(section: dict) -> str:
    return " ".join(" ".join(block["text"].split()) for block in section["span_blocks"]).lower()


def unit_sections(record: dict, entry: dict) -> tuple[list[dict], str]:
    """The units of a record a curator answers for, and how they were formed (section, rolled_up or document).

    Every unit carries section_id, heading_path, first_block, last_block, characters, text and section_ids (the
    content sections it stands for)."""
    sections = [dict(section_id=s["section_id"], heading_path=s["heading_path"], first_block=s["first_block"],
                     last_block=s["last_block"], characters=s["characters"], text=section_text(s),
                     section_ids=[s["section_id"]]) for s in build_sections(record) if is_content(s)]
    if not sections:
        return [], "section"
    if (entry.get("attribution_kind") or (entry.get("attribution") or {}).get("kind")) in REFERENCE_KINDS:
        return [dict(section_id="document", heading_path=[record.get("title") or entry.get("title") or ""],
                     first_block=sections[0]["first_block"], last_block=sections[-1]["last_block"],
                     characters=sum(s["characters"] for s in sections), text="",
                     section_ids=[s["section_id"] for s in sections])], "document"
    if len(sections) <= OVERSIZE_SECTIONS:
        return sections, "section"
    rolled: list[dict] = []
    for section in sections:
        key = section["heading_path"][:2]
        if rolled and rolled[-1]["heading_path"] == key:
            last = rolled[-1]
            last["last_block"] = section["last_block"]
            last["characters"] += section["characters"]
            last["section_ids"].append(section["section_id"])
        else:
            rolled.append(dict(section_id=section["section_id"], heading_path=key, first_block=section["first_block"],
                               last_block=section["last_block"], characters=section["characters"], text="",
                               section_ids=[section["section_id"]]))
    return rolled, "rolled_up"


def build_coverage(release: Release, text: Path, *, policy: str = "report", dispositions: CoverageDispositions | None = None,
                   catalogue: dict | None = None, today: date | None = None, dispositions_sha256: str | None = None) -> dict:
    """The coverage report of a release over its text dataset."""
    if policy not in POLICIES:
        raise ValueError(f"coverage_policy must be one of {POLICIES}")
    today = today or date.today()
    text = Path(text)
    index = json.loads((text / "index.json").read_text(encoding="utf-8"))
    known_ids = {entry["document_id"] for entry in index}
    candidates = sorted(candidate_entries(index), key=lambda e: (e.get("source_url") or "", e["document_id"]))
    cited = cited_blocks(release)
    items = dispositions.dispositions if dispositions else []
    out_of_scope = [entry.lower() for entry in release.manifest.out_of_scope]

    # Dispositions by target. A section-level disposition lists its sections; a document-level one covers them all.
    by_document: dict[str, list[tuple[int, Disposition]]] = defaultdict(list)
    rules: list[tuple[int, Disposition]] = []
    findings: dict[str, list[dict]] = dict(stale=[], expired=[], unknown_out_of_scope_entry=[], new_under_rule=[], unused_rule=[])
    for number, item in enumerate(items):
        if item.kind == "deferred" and item.reaffirm_by and item.reaffirm_by < today:
            findings["expired"].append(dict(disposition=number, kind=item.kind, target=item.document_id or item.url_prefix,
                                            reaffirm_by=item.reaffirm_by.isoformat(), reason=item.reason))
        if item.kind == "out_of_scope":
            phrase = (item.out_of_scope_entry or "").strip().lower()
            if not any(phrase in entry for entry in out_of_scope):
                findings["unknown_out_of_scope_entry"].append(dict(disposition=number, out_of_scope_entry=item.out_of_scope_entry,
                                                                    target=item.document_id or item.url_prefix))
        if item.document_id:
            if item.document_id not in known_ids:
                findings["stale"].append(dict(disposition=number, document_id=item.document_id, kind=item.kind, reason=item.reason))
            by_document[item.document_id].append((number, item))
        else:
            rules.append((number, item))

    # Pass one: the units of every candidate, and which section texts recur across records (boilerplate).
    units_of: dict[str, tuple[list[dict], str]] = {}
    text_documents: dict[str, set[str]] = defaultdict(set)
    for entry in candidates:
        record = json.loads((text / entry["file"]).read_text(encoding="utf-8")) if entry.get("file") else {}
        units, unit_kind = unit_sections(record, entry)
        units_of[entry["document_id"]] = (units, unit_kind)
        for unit in units:
            if unit["text"]:
                text_documents[unit["text"]].add(entry["document_id"])
    boilerplate_texts = {t for t, docs in text_documents.items() if len(docs) >= BOILERPLATE_MIN_DOCUMENTS}

    # Pass two: classify every unit.
    documents = []
    unit_totals = Counter()
    per_source: dict[str, Counter] = defaultdict(Counter)
    source_documents: dict[str, set[str]] = defaultdict(set)
    rule_matches: dict[int, list[str]] = defaultdict(list)
    unit_kinds = Counter()
    for entry in candidates:
        document_id = entry["document_id"]
        units, unit_kind = units_of[document_id]
        unit_kinds[unit_kind] += 1
        cited_here = cited.get(document_id, set())
        matched_rules = [(number, rule) for number, rule in rules if (entry.get("source_url") or "").startswith(rule.url_prefix)]
        for number, rule in matched_rules:
            rule_matches[number].append(document_id)
            if document_id not in rule.known_documents:
                findings["new_under_rule"].append(dict(disposition=number, url_prefix=rule.url_prefix, document_id=document_id,
                                                       source_url=entry.get("source_url"), title=entry.get("title")))
        whole = [d for _, d in by_document.get(document_id, []) if not d.section_ids]
        partial = {section_id: d for _, d in by_document.get(document_id, []) for section_id in d.section_ids}
        statuses = []
        unclassified = []
        dispositions_used: Counter = Counter()
        for unit in units:
            blocks = range(unit["first_block"], unit["last_block"] + 1)
            named = [partial[s] for s in unit["section_ids"] if s in partial]
            if any(number in cited_here for number in blocks):
                statuses.append("cited")
            elif whole or matched_rules or named:
                statuses.append("dispositioned")
                kind = whole[0].kind if whole else named[0].kind if named else matched_rules[0][1].kind
                dispositions_used[kind] += 1
            elif unit["text"] and unit["text"] in boilerplate_texts:
                statuses.append("boilerplate")
            else:
                statuses.append("unclassified")
                unclassified.append(dict(section_id=unit["section_id"], heading_path=unit["heading_path"],
                                         first_block=unit["first_block"], last_block=unit["last_block"],
                                         characters=unit["characters"], section_ids=unit["section_ids"]))
        counts = Counter(statuses)
        asked = len(units) - counts["boilerplate"]
        if not units:
            status = "empty"
        elif counts["unclassified"]:
            status = "partly_cited" if counts["cited"] else "unclassified"
        elif asked == 0:
            status = "boilerplate_only"
        elif counts["cited"] == asked:
            status = "cited"
        elif counts["cited"]:
            status = "cited_and_dispositioned"
        else:
            status = "dispositioned"
        documents.append(dict(document_id=document_id, source_url=entry.get("source_url"), title=entry.get("title"),
                              source_ids=entry.get("source_ids", []), attribution_kind=entry.get("attribution_kind"),
                              unit=unit_kind, units=len(units), cited=counts["cited"], dispositioned=counts["dispositioned"],
                              boilerplate=counts["boilerplate"], unclassified=counts["unclassified"],
                              dispositions=dict(dispositions_used), status=status, unclassified_sections=unclassified))
        unit_totals.update(statuses)
        for source_id in entry.get("source_ids", []) or ["(discovered)"]:
            per_source[source_id].update(statuses)
            source_documents[source_id].add(document_id)
    for number, rule in rules:
        if not rule_matches.get(number):
            findings["unused_rule"].append(dict(disposition=number, url_prefix=rule.url_prefix, reason=rule.reason))

    titles = catalogue_sources(catalogue)
    sources = []
    for source_id in sorted(set(titles) | set(per_source)):
        counts = per_source.get(source_id, Counter())
        asked = counts["cited"] + counts["dispositioned"] + counts["unclassified"]
        if not source_documents.get(source_id):
            status = "no_documents"
        elif counts["unclassified"]:
            status = "partly_covered" if counts["cited"] or counts["dispositioned"] else "nothing"
        elif counts["cited"]:
            status = "cited"
        elif asked == 0:
            status = "boilerplate_only"
        else:
            status = "dispositioned"
        sources.append(dict(source_id=source_id, title=titles.get(source_id), documents=len(source_documents.get(source_id, ())),
                            units=asked, cited=counts["cited"], dispositioned=counts["dispositioned"],
                            boilerplate=counts["boilerplate"], unclassified=counts["unclassified"], status=status))

    document_statuses = Counter(d["status"] for d in documents)
    blocking = {name: len(values) for name, values in findings.items() if name != "unused_rule"}
    clean = unit_totals["unclassified"] == 0 and not any(blocking.values())
    return dict(
        schema_version=REPORT_SCHEMA_VERSION, pack=release.manifest.pack, release_id=release.manifest.release_id,
        content_sha256=release.manifest.content_sha256, text_index_sha256=sha256_file(text / "index.json"),
        dispositions_sha256=dispositions_sha256, dispositions=len(items), policy=policy, today=today.isoformat(),
        generated_at=datetime.now(UTC).isoformat(),
        rules=dict(reference_kinds=sorted(REFERENCE_KINDS), boilerplate_min_documents=BOILERPLATE_MIN_DOCUMENTS,
                   oversize_sections=OVERSIZE_SECTIONS),
        counts=dict(candidates=len(candidates), units=dict(unit_kinds),
                    content_sections=unit_totals["cited"] + unit_totals["dispositioned"] + unit_totals["unclassified"],
                    cited_sections=unit_totals["cited"], dispositioned_sections=unit_totals["dispositioned"],
                    unclassified_sections=unit_totals["unclassified"], boilerplate_sections=unit_totals["boilerplate"],
                    documents=dict(document_statuses), findings=blocking | dict(unused_rule=len(findings["unused_rule"]))),
        clean=clean, passed=clean if policy == "enforce" else True,
        documents=documents, sources=sources, findings=findings)


def coverage_counts(report: dict) -> dict:
    """The counts a readiness record carries: what was asked of the curator and what is open."""
    counts = report["counts"]
    return dict(policy=report["policy"], candidates=counts["candidates"], content_sections=counts["content_sections"],
                cited_sections=counts["cited_sections"], dispositioned_sections=counts["dispositioned_sections"],
                unclassified_sections=counts["unclassified_sections"], clean=report["clean"])


def render_markdown(report: dict) -> str:
    counts = report["counts"]
    lines = [f"# Curation coverage: {report['pack']}", "",
             f"Release `{report['release_id']}` (content `{report['content_sha256'][:12]}`), policy `{report['policy']}`, "
             f"{'clean' if report['clean'] else 'not clean'}, {report['dispositions']} disposition(s), generated {report['generated_at'][:19]}.", "",
             f"{counts['candidates']} candidate records ({counts['units']}) with {counts['content_sections']} units a curator answers for: "
             f"{counts['cited_sections']} cited, {counts['dispositioned_sections']} dispositioned, "
             f"**{counts['unclassified_sections']} unclassified**; {counts['boilerplate_sections']} boilerplate sections were not asked for. "
             f"Documents: {counts['documents']}. Findings: {counts['findings']}.", ""]
    lines += ["## Documents", "", "| Status | Document | Unit | Units | Cited | Dispositioned | Boilerplate | Unclassified | Sources |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    order = {"unclassified": 0, "partly_cited": 1, "empty": 2, "boilerplate_only": 3, "cited_and_dispositioned": 4, "dispositioned": 5, "cited": 6}
    for document in sorted(report["documents"], key=lambda d: (order.get(d["status"], 9), d["source_url"] or "")):
        title = (document["title"] or document["source_url"] or document["document_id"]).replace("|", "/")
        lines.append(f"| {document['status']} | [{title}]({document['source_url']}) `{document['document_id']}` | {document['unit']} | "
                     f"{document['units']} | {document['cited']} | {document['dispositioned']} | {document['boilerplate']} | "
                     f"{document['unclassified']} | {', '.join(document['source_ids'])} |")
    open_sections = [(d, s) for d in report["documents"] for s in d["unclassified_sections"]]
    if open_sections:
        lines += ["", "## Unclassified sections", "", "| Document | Section | Heading | Blocks | Characters |", "| --- | --- | --- | --- | ---: |"]
        for document, section in open_sections:
            heading = " > ".join(section["heading_path"]).replace("|", "/") or "(no heading)"
            lines.append(f"| `{document['document_id']}` | {section['section_id']} | {heading} | "
                         f"{section['first_block']}-{section['last_block']} | {section['characters']} |")
    lines += ["", "## Sources", "", "| Status | Source | Documents | Units | Cited | Dispositioned | Boilerplate | Unclassified |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for source in report["sources"]:
        lines.append(f"| {source['status']} | `{source['source_id']}` {source['title'] or ''} | {source['documents']} | "
                     f"{source['units']} | {source['cited']} | {source['dispositioned']} | {source['boilerplate']} | {source['unclassified']} |")
    for name, values in report["findings"].items():
        if values:
            lines += ["", f"## Finding: {name} ({len(values)})", ""]
            lines += [f"- {json.dumps(value, ensure_ascii=False)}" for value in values]
    return "\n".join(lines) + "\n"


def write_report(report: dict, path: Path) -> None:
    path = Path(path)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    path.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
