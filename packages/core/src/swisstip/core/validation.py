"""Validate a knowledge release: identities, references, hashes, and, when the text
dataset is at hand, every excerpt against the record it was cut from.

    swisstip-validate-release releases/<pack>/release.json --text .local/<pack>/text

The server runs the same checks once at startup and fails closed on any issue.

Codes, levels and basis words are checked against the release's place
hierarchy: the built-in Swiss one for a release that declares none, the
declared one otherwise. A release that declares its country
(swiss-tip-release/v3) is also checked for a well-formed hierarchy and for
its declared query and evidence languages; those issues follow all others,
and none of them can arise for a Swiss release.
"""

import argparse
import hashlib
import json
import re
import string
import sys
from datetime import timedelta
from pathlib import Path

from . import RELEASE_SCHEMA_VERSION_DECLARED
from .basis import DEFAULT_RANKING_POLICY, NORM_KINDS, basis_label, strongest_basis
from .hierarchy import SWISS_PATTERN, Hierarchy, hierarchy_of
from .release import Release, content_hash, load_release, sha256_text

JURISDICTION = SWISS_PATTERN  # the Swiss code pattern, kept under its old name for importers
LANGUAGE = re.compile(r"^[a-z]{2,3}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseInvalid(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__(f"{len(issues)} release issue(s): " + "; ".join(issues[:5]))
        self.issues = issues


def contains(outer: str, inner: str) -> bool:
    """Jurisdiction containment: a country contains every place below it (CH every canton and municipality), a
    place every place below it (a canton its municipalities, PL-12 its counties and communes)."""
    return inner == outer or inner.startswith(outer + "-")


def validate_release(release: Release, text_dir: Path | None = None, run_dir: Path | None = None) -> list[str]:
    issues: list[str] = []
    manifest = release.manifest
    hierarchy = hierarchy_of(release.place_register)
    if content_hash(release) != manifest.content_sha256:
        issues.append("manifest content_sha256 does not match the release body")
    if manifest.freshness.stale_from != manifest.freshness.snapshot_date + timedelta(days=manifest.freshness.max_age_days):
        issues.append("freshness.stale_from is not snapshot_date plus max_age_days")
    for name, items in (("documents", [d.document_id for d in release.documents]), ("topics", [t.topic_id for t in release.topics]),
                        ("concepts", [c.concept_id for c in release.concepts]), ("facts", [f.fact_id for f in release.facts]),
                        ("evidence", [e.evidence_id for e in release.evidence])):
        if len(set(items)) != len(items):
            issues.append(f"duplicate identifiers in {name}")
        for item in items:
            if not IDENTIFIER.match(item):
                issues.append(f"malformed identifier in {name}: {item!r}")
    documents = {d.document_id: d for d in release.documents}
    topics = {t.topic_id for t in release.topics}
    concepts = {c.concept_id: c for c in release.concepts}
    facts = {f.fact_id: f for f in release.facts}
    evidence = {e.evidence_id: e for e in release.evidence}
    for concept in release.concepts:
        if concept.topic_id not in topics:
            issues.append(f"concept {concept.concept_id} references unknown topic {concept.topic_id}")
        if not concept.label.strip() or not concept.description.strip():
            issues.append(f"concept {concept.concept_id} lacks a label or description")
        concept_facts = []
        for fact_id in concept.fact_ids:
            fact = facts.get(fact_id)
            if fact is None:
                issues.append(f"concept {concept.concept_id} references unknown fact {fact_id}")
            elif fact.concept_id != concept.concept_id:
                issues.append(f"fact {fact_id} belongs to {fact.concept_id}, not to {concept.concept_id}")
            else:
                concept_facts.append(fact)
        if sorted({f.jurisdiction for f in concept_facts}) != sorted(concept.jurisdictions):
            issues.append(f"concept {concept.concept_id} jurisdictions differ from its facts")
        for field in concept.required_context:
            if field not in concept.context_schema:
                issues.append(f"concept {concept.concept_id} requires context {field} without a schema")
    for fact in release.facts:
        if fact.concept_id not in concepts:
            issues.append(f"fact {fact.fact_id} references unknown concept {fact.concept_id}")
        elif fact.fact_id not in concepts[fact.concept_id].fact_ids:
            issues.append(f"fact {fact.fact_id} is not listed by its concept")
        if not hierarchy.pattern.match(fact.jurisdiction):
            issues.append(f"fact {fact.fact_id} has a malformed jurisdiction {fact.jurisdiction!r}")
        elif fact.jurisdiction not in manifest.jurisdictions:
            issues.append(f"fact {fact.fact_id} jurisdiction {fact.jurisdiction} is not declared in the manifest")
        if not fact.statement.strip():
            issues.append(f"fact {fact.fact_id} has an empty statement")
        if fact.valid_from and fact.valid_through and fact.valid_through < fact.valid_from:
            issues.append(f"fact {fact.fact_id} validity ends before it starts")
        concept = concepts.get(fact.concept_id)
        for field, value in (fact.condition or {}).items():
            spec = concept.context_schema.get(field) if concept else None
            if spec is None:
                issues.append(f"fact {fact.fact_id} condition field {field} has no schema on its concept")
            elif spec.enum is not None and value not in spec.enum:
                issues.append(f"fact {fact.fact_id} condition {field}={value} is outside the schema enum")
        for evidence_id in fact.evidence_ids:
            if evidence_id not in evidence:
                issues.append(f"fact {fact.fact_id} references unknown evidence {evidence_id}")
    cited = set()
    for item in release.evidence:
        cited.add(item.document_id)
        if item.document_id not in documents:
            issues.append(f"evidence {item.evidence_id} references unknown document {item.document_id}")
        if item.end_offset <= item.start_offset or len(item.original_excerpt) != item.end_offset - item.start_offset:
            issues.append(f"evidence {item.evidence_id} offsets do not match its excerpt length")
        if sha256_text(item.original_excerpt) != item.excerpt_sha256:
            issues.append(f"evidence {item.evidence_id} excerpt hash mismatch")
        if not LANGUAGE.match(item.language):
            issues.append(f"evidence {item.evidence_id} has a malformed language {item.language!r}")
        elif item.language not in manifest.languages:
            issues.append(f"evidence {item.evidence_id} language {item.language} is not declared in the manifest")
        document = documents.get(item.document_id)
        if document and document.content_sha256 != item.content_sha256:
            issues.append(f"evidence {item.evidence_id} content hash differs from document {document.document_id}")
        if document and document.raw_sha256 != item.raw_sha256:
            issues.append(f"evidence {item.evidence_id} raw hash differs from document {document.document_id}")
    if not all(any(e.evidence_id in f.evidence_ids for f in release.facts) for e in release.evidence):
        issues.append("evidence not cited by any fact")
    for document in release.documents:
        if document.document_id not in cited:
            issues.append(f"document {document.document_id} is not cited")
        if document.accessed_on > manifest.freshness.snapshot_date:
            issues.append(f"document {document.document_id} was accessed after the manifest snapshot date")
    kinds, statuses = {}, {}
    for fact in release.facts:
        kinds[fact.provenance.kind] = kinds.get(fact.provenance.kind, 0) + 1
        statuses[fact.provenance.review_status] = statuses.get(fact.provenance.review_status, 0) + 1
    if kinds != manifest.provenance_kinds or statuses != manifest.review_statuses:
        issues.append("manifest provenance or review counts differ from the facts")
    issues.extend(check_institutions_and_basis(release, documents, evidence, hierarchy))
    issues.extend(check_place_register(release, hierarchy))
    if text_dir is not None:
        issues.extend(check_against_text(release, Path(text_dir), Path(run_dir) if run_dir else None))
    issues.extend(check_country_profile(release))
    return issues


def check_institutions_and_basis(release: Release, documents: dict, evidence: dict,
                                 hierarchy: Hierarchy | None = None) -> list[str]:
    """The institution registry, the institution of every page, the basis of every excerpt and of every fact.

    Levels are the level ids of the release's hierarchy. A release without institutions and bases (built before
    16 September 2026) passes untouched."""
    if hierarchy is None:
        hierarchy = hierarchy_of(release.place_register)
    words = hierarchy.words
    national = hierarchy.levels[0].id if hierarchy.levels else None
    issues: list[str] = []
    manifest = release.manifest
    institutions = {i.institution_id: i for i in release.institutions}
    if len(institutions) != len(release.institutions):
        issues.append("duplicate institution identifiers")
    for institution in release.institutions:
        if not IDENTIFIER.match(institution.institution_id):
            issues.append(f"malformed institution identifier {institution.institution_id!r}")
        if not hierarchy.pattern.match(institution.jurisdiction):
            issues.append(f"institution {institution.institution_id} has a malformed jurisdiction {institution.jurisdiction!r}")
        elif hierarchy.level_id(institution.jurisdiction) != institution.level:
            issues.append(f"institution {institution.institution_id} is {institution.level} but speaks for {institution.jurisdiction}")
        if not institution.name.strip():
            issues.append(f"institution {institution.institution_id} has no name")
    cited_institutions = set()
    for document in release.documents:
        if document.institution_id is None:
            if institutions:
                issues.append(f"document {document.document_id} names no institution although the release has a registry")
        elif document.institution_id not in institutions:
            issues.append(f"document {document.document_id} names unknown institution {document.institution_id}")
        else:
            cited_institutions.add(document.institution_id)
    for institution_id in institutions:
        if institution_id not in cited_institutions:
            issues.append(f"institution {institution_id} is not cited by any document")
    for item in release.evidence:
        document = documents.get(item.document_id)
        if document and item.institution_id != document.institution_id:
            issues.append(f"evidence {item.evidence_id} names institution {item.institution_id}, its document {document.institution_id}")
        basis = item.basis
        if basis is None:
            if item.institution_id is not None:
                issues.append(f"evidence {item.evidence_id} has an institution but no basis")
            continue
        if basis.kind in NORM_KINDS and not (basis.norm or "").strip():
            issues.append(f"evidence {item.evidence_id} basis of kind {basis.kind} names no norm")
            continue
        if basis.level not in words:
            issues.append(f"evidence {item.evidence_id} basis level {basis.level!r} is not a level of this release")
            continue
        if basis.label != basis_label(basis.level, basis.kind, basis.norm, basis.refers_to, words):
            issues.append(f"evidence {item.evidence_id} basis label does not match its fields")
    policy = manifest.ranking_policy or DEFAULT_RANKING_POLICY
    levels: dict[str, int] = {}
    for document in release.documents:
        institution = institutions.get(document.institution_id or "")
        if institution:
            levels[institution.level] = levels.get(institution.level, 0) + 1
    basis_kinds: dict[str, int] = {}
    for fact in release.facts:
        cited = [evidence[e] for e in fact.evidence_ids if e in evidence]
        expected = strongest_basis([e.basis for e in cited], policy)
        if fact.provenance.basis != expected:
            issues.append(f"fact {fact.fact_id} provenance basis is not the strongest basis of its evidence")
        if fact.provenance.basis is not None:
            basis_kinds[fact.provenance.basis.kind] = basis_kinds.get(fact.provenance.basis.kind, 0) + 1
            # The country's own facts rest on the country's law: for CH, a federal basis.
            if fact.jurisdiction == hierarchy.country and fact.provenance.basis.level != national:
                issues.append(f"fact {fact.fact_id} is {national} but rests on a {fact.provenance.basis.level} basis")
        for item in cited:
            institution = institutions.get(item.institution_id or "")
            if institution and not contains(institution.jurisdiction, fact.jurisdiction):
                issues.append(f"fact {fact.fact_id} ({fact.jurisdiction}) cites {institution.institution_id}, "
                              f"which speaks for {institution.jurisdiction}")
    if levels != manifest.institution_levels:
        issues.append("manifest institution levels differ from the documents")
    if basis_kinds != manifest.basis_kinds:
        issues.append("manifest basis kinds differ from the facts")
    return issues


def check_place_register(release: Release, hierarchy: Hierarchy | None = None) -> list[str]:
    """The place register: unique codes well-formed for the release's hierarchy, a name on every place, every place
    below the country under its listed parent (a municipality under its canton, a commune under its county), and a
    place for every jurisdiction the release publishes, so that the server can name whatever it answers for. A
    release without a register passes untouched."""
    register = release.place_register
    if register is None:
        return []
    if hierarchy is None:
        hierarchy = hierarchy_of(register)
    issues: list[str] = []
    codes = [place.code for place in register.places]
    if len(set(codes)) != len(codes):
        issues.append("duplicate codes in the place register")
    listed = set(codes)
    for place in register.places:
        if not hierarchy.pattern.match(place.code):
            issues.append(f"place {place.code!r} has a malformed code")
        elif "-" in place.code and place.code.rsplit("-", 1)[0] not in listed:
            issues.append(f"place {place.code} lies in {place.code.rsplit('-', 1)[0]}, which the register does not list")
        if not place.name.strip() or any(not alias.strip() for alias in place.aliases):
            issues.append(f"place {place.code} has an empty name or alias")
    for jurisdiction in release.manifest.jurisdictions:
        if jurisdiction not in listed:
            issues.append(f"jurisdiction {jurisdiction} of the manifest is not in the place register")
    return issues


def check_country_profile(release: Release) -> list[str]:
    """What a release that declares its country must get right: a well-formed place hierarchy with exactly one
    country place that the manifest's jurisdictions lie in, swiss-tip-release/v3 if and only if a hierarchy is
    declared, and its query and evidence languages. A malformed hierarchy accepts no code (every code is reported
    malformed); these issues name the cause. A Swiss release declares none of it and passes untouched."""
    issues: list[str] = []
    manifest = release.manifest
    register = release.place_register
    declared = register.hierarchy if register is not None else None
    if declared is not None:
        levels = declared.levels
        ids = [level.id for level in levels]
        if len(levels) < 3:
            issues.append(f"the place hierarchy has {len(levels)} level(s); it needs at least three")
        if len(set(ids)) != len(ids):
            issues.append("duplicate level ids in the place hierarchy")
        for depth, level in enumerate(levels):
            if depth == 0 and level.segment is not None:
                issues.append(f"level {level.id} is the country and takes no code segment")
            elif depth > 0 and level.segment is None:
                issues.append(f"level {level.id} declares no code segment")
            elif depth > 0 and not compiles(level.segment):
                issues.append(f"level {level.id} code segment {level.segment!r} is not a valid width")
            if level.note is not None and depth != len(levels) - 1:
                issues.append(f"level {level.id} carries a note, which only the lowest level may")
            if not name_template(level.label):
                issues.append(f"level {level.id} label {level.label!r} carries a placeholder other than {{name}}")
        countries = [place.code for place in register.places if "-" not in place.code]
        if len(countries) != 1:
            issues.append(f"the place register lists {len(countries)} country places (codes without a dash); "
                          "a declared hierarchy needs exactly one")
        else:
            outside = [code for code in manifest.jurisdictions if code.split("-")[0] != countries[0]]
            if outside:
                issues.append(f"manifest jurisdictions {', '.join(outside)} do not lie in the register's country "
                              f"{countries[0]}")
        if manifest.schema_version != RELEASE_SCHEMA_VERSION_DECLARED:
            issues.append(f"a place register that declares a hierarchy requires {RELEASE_SCHEMA_VERSION_DECLARED}")
    elif manifest.schema_version == RELEASE_SCHEMA_VERSION_DECLARED:
        issues.append(f"{RELEASE_SCHEMA_VERSION_DECLARED} requires a place register with a hierarchy")
    # Both lists exist only in swiss-tip-release/v3 (a load error otherwise), so a Swiss release never reaches these.
    if manifest.query_languages is not None:
        issues.extend(language_list_issues("query_languages", manifest.query_languages))
        for fact in release.facts:
            if fact.language not in manifest.query_languages:
                issues.append(f"fact {fact.fact_id} language {fact.language} is not one of the manifest's query_languages")
    if manifest.evidence_languages is not None:
        issues.extend(language_list_issues("evidence_languages", manifest.evidence_languages))
        for item in release.evidence:
            if item.language.split("-")[0].lower() not in manifest.evidence_languages:
                issues.append(f"evidence {item.evidence_id} language {item.language} is not one of the manifest's "
                              "evidence_languages")
    return issues


def compiles(segment: str) -> bool:
    """A segment the level grammar admits can still fail to compile: [0-9]{9,1}."""
    try:
        re.compile(segment)
    except re.error:
        return False
    return True


def name_template(label: str) -> bool:
    """A label template carries no placeholder but a plain {name}: 'commune of {name}', 'national'."""
    try:
        fields = [(field, spec, conversion) for _, field, spec, conversion in string.Formatter().parse(label)
                  if field is not None]
    except ValueError:  # an unmatched brace
        return False
    return all(field == ("name", "", None) for field in fields)


def language_list_issues(name: str, codes: list[str]) -> list[str]:
    issues = []
    if not codes:
        issues.append(f"manifest {name} lists no language")
    if len(set(codes)) != len(codes):
        issues.append(f"duplicate languages in manifest {name}")
    for code in codes:
        if not LANGUAGE.match(code):
            issues.append(f"manifest {name} has a malformed language {code!r}")
    return issues


def check_against_text(release: Release, text_dir: Path, run_dir: Path | None) -> list[str]:
    issues = []
    records: dict[str, dict] = {}
    for document in release.documents:
        path = text_dir / "documents" / f"{document.document_id}.json"
        if not path.is_file():
            issues.append(f"text record {document.document_id} is missing from {text_dir}")
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        records[document.document_id] = record
        if record.get("content_sha256") != document.content_sha256:
            issues.append(f"text record {document.document_id} content hash differs from the release")
        if record.get("acquisition", {}).get("raw_sha256") != document.raw_sha256:
            issues.append(f"text record {document.document_id} raw hash differs from the release")
        if run_dir is not None:
            raw = run_dir / record.get("acquisition", {}).get("path", "")
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != document.raw_sha256:
                issues.append(f"saved response of {document.document_id} is missing or changed in {run_dir}")
    for item in release.evidence:
        record = records.get(item.document_id)
        if record is None:
            continue
        text = record.get("content_text", "")
        if text[item.start_offset:item.end_offset] != item.original_excerpt:
            issues.append(f"evidence {item.evidence_id} excerpt differs from the text record")
        covering = [b["block_id"] for b in record.get("blocks", []) if b["end"] > item.start_offset and b["start"] < item.end_offset]
        if covering != item.block_ids:
            issues.append(f"evidence {item.evidence_id} block ids differ from the text record")
    return issues


def assert_valid(release: Release, text_dir: Path | None = None, run_dir: Path | None = None) -> None:
    issues = validate_release(release, text_dir, run_dir)
    if issues:
        raise ReleaseInvalid(issues)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("release", type=Path)
    parser.add_argument("--text", type=Path, help="text dataset the release was built from")
    parser.add_argument("--run", type=Path, help="run directory, to re-hash the saved responses")
    args = parser.parse_args(argv)
    try:
        release = load_release(args.release)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    issues = validate_release(release, args.text, args.run)
    report = dict(release_id=release.manifest.release_id, valid=not issues, issues=issues,
                  documents=len(release.documents), topics=len(release.topics), concepts=len(release.concepts),
                  facts=len(release.facts), evidence=len(release.evidence),
                  provenance_kinds=release.manifest.provenance_kinds, review_statuses=release.manifest.review_statuses)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
