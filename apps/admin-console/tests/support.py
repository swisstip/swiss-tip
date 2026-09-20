"""A synthetic pack for the console tests: three documents, two concepts, four facts, one stored check.

The pack is built by the existing pipeline, so the files the console reads are
the files the pipeline writes and nothing is hand-forged. No network, no
browser; the pages are bytes in this module.
"""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from swisstip.builder.pipeline import Pipeline

HOST = "www.sem.example"
PAGES = {
    "https://www.sem.example/registration.html": (
        b"<html lang=\"en\"><head><title>Registration</title></head><body>"
        b"<nav class=\"mod-breadcrumb\"><a href=\"/\">Home</a></nav>"
        b"<main><h1>Registration</h1><h2>Deadline</h2>"
        b"<p>Register within 14 days of arrival.</p>"
        b"<p>Register before starting work.</p>"
        b"<table><tr><th>Permit</th><th>Days</th></tr><tr><td>B</td><td>14</td></tr></table>"
        b"</main></body></html>"),
    "https://www.sem.example/work.html": (
        b"<html lang=\"en\"><head><title>Work</title></head><body><main><h1>Work</h1>"
        b"<p>Third-country nationals need a work permit before starting work.</p>"
        b"<p>The cantonal migration office issues the permit.</p>"
        b"</main></body></html>"),
    "https://www.sem.example/zurich.html": (
        b"<html lang=\"en\"><head><title>Zurich</title></head><body><main><h1>Zurich</h1>"
        b"<p>In Canton Zurich registration is made at the municipal residents office.</p>"
        b"</main></body></html>"),
}

CURATION = """
schema_version: swiss-tip-curation/v1
pack: test-pack
title: Test pack
scope_statement: Registration and work permits for foreign nationals, federal rules and Canton Zurich.
out_of_scope: [Fees, Processing times]
out_of_scope_response: Say that this service does not cover the question and quote the scope statement.
limitations: [Assistant-authored, not reviewed by a person.]
freshness_max_age_days: 60
publishers:
  www.sem.example: State Secretariat for Migration SEM
context_fields:
  population:
    enum: [eu_efta, third_country]
    description: Citizenship group of the person.
topics:
  - {topic_id: residence, title: Residence, description: Permits and registration.}
concepts:
  - concept_id: registration-deadline
    topic_id: residence
    label: Registration deadline
    description: When EU/EFTA nationals register after arrival.
    aliases: [register arrival, Anmeldung]
    questions: ["By when must I register?"]
    required_context: [population]
    facts:
      - fact_id: registration-deadline-1
        statement: Register within 14 days of arrival.
        jurisdiction: CH
        condition: {population: eu_efta}
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: test}
        evidence:
          - {document_id: DOC_REGISTRATION, first_block: 4, last_block: 4}
      - fact_id: registration-deadline-2
        statement: Register before starting work.
        jurisdiction: CH
        condition: {population: eu_efta}
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: test}
        evidence:
          - {document_id: DOC_REGISTRATION, first_block: 5, last_block: 5}
      - fact_id: registration-deadline-3
        statement: In Canton Zurich registration is made at the municipal residents office.
        jurisdiction: CH-ZH
        condition: {population: eu_efta}
        provenance: {kind: curated-statement, review_status: assistant-authored-unreviewed, author: test}
        evidence:
          - {document_id: DOC_ZURICH, first_block: 2, last_block: 2}
  - concept_id: work-permit
    topic_id: residence
    label: Work permit for third-country nationals
    description: Third-country nationals need a work permit before starting work.
    facts:
      - fact_id: work-permit-1
        statement: Third-country nationals need a work permit before starting work.
        jurisdiction: CH
        condition: {population: third_country}
        provenance: {kind: model-candidate, review_status: model-candidate-automated-review, author: a model}
        evidence:
          - {document_id: DOC_WORK, first_block: 2, last_block: 3}
"""

CHECKS = """
schema_version: swiss-tip-checks/v1
pack: test-pack
checks:
  - check_id: eu-registration
    title: EU/EFTA national registering in Canton Zurich
    tool: resolve
    request:
      concept_ids: [registration-deadline]
      jurisdiction: {country_code: CH, canton_code: CH-ZH}
      as_of: "2026-09-12"
      context: {population: eu_efta}
    expect:
      status: {registration-deadline: SUPPORTED}
      fact_ids: [registration-deadline-1, registration-deadline-3]
samples: {}
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def catalogue(urls: list[str]) -> dict:
    return {
        "schema_version": "source-catalog/v1", "artifact_id": "test-sources", "version": "draft-1",
        "knowledge_space_id": "test", "title": "Test sources", "status": "SOURCES_ONLY",
        "scope": {"country_code": "CH", "canton_codes": ["CH-ZH"], "description": "Test", "exclusions": []},
        "planning_topics": [{"topic_id": "registration-moving", "label": "Registration"}],
        "language_discovery": {"preferred_seed_language": "de", "seed_languages_are_hints": True},
        "crawl_profiles": {"smoke": {"max_depth": 0, "max_pages": 1, "max_requests": 5, "max_total_bytes": 3000000,
                                     "max_response_bytes": 2000000, "max_duration_seconds": 60,
                                     "request_timeout_seconds": 15, "delay_seconds": 2, "max_redirects": 2,
                                     "max_links_per_page": 200, "max_queued_urls": 50, "max_failures": 2}},
        "scan_sets": {"smoke": ["sem-registration"]}, "build_notes": [],
        "sources": [
            {"definition": {"source_id": f"sem-{Path(url).stem}", "start_url": url, "allowed_hosts": [HOST],
                            "allowed_path_prefixes": [f"/{Path(url).name}"],
                            "canonical_authority": "State Secretariat for Migration SEM",
                            "jurisdiction": "CH" if "zurich" not in url else "CH-ZH", "language": "en"},
             "title": f"Test source {Path(url).stem}",
             "authority_level": "federal" if "zurich" not in url else "cantonal",
             "source_kind": "official_guidance", "priority": "P0", "topic_hints": ["registration-moving"],
             "discovery": {"method": "official_search_result", "reference_url": url, "located_on": "2026-09-01"},
             "scan_status": "ready", "notes": "Synthetic source for the console tests."}
            for url in urls],
        "parallel_page_groups": [],
        "evidence_selection_policy": {"status": "PLANNED_NOT_IMPLEMENTED"},
    }


def write_run(root: Path, pack: str, catalogue_bytes: bytes) -> None:
    """A download run as `download_cli` writes one: one saved attempt per page, a plan and a summary."""
    run = root / ".local" / pack
    targets, results = [], []
    for url, page in PAGES.items():
        url_id = sha256(url.encode())
        attempt = run / "pages" / url_id / "attempt-001"
        attempt.mkdir(parents=True)
        (attempt / "response.html").write_bytes(page)
        snapshot = dict(relative_path=f"pages/{url_id}/attempt-001/response.html", requested_url=url, final_url=url,
                        content_type="text/html", sha256=sha256(page), bytes_downloaded=len(page),
                        retrieved_at="2026-09-11T06:00:00+00:00", review_flags=[])
        manifest = dict(url=url, url_id=url_id, references=[], registry_entries=[], snapshots=[snapshot],
                        status="saved", http_status=200, started_at="2026-09-11T06:00:00+00:00",
                        finished_at="2026-09-11T06:00:01+00:00")
        (attempt / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (attempt.parent / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        source_id = f"sem-{Path(url).stem}"
        targets.append(dict(
            url=url, url_id=url_id,
            references=[dict(label=Path(url).stem, catalogue_line=1, catalogue="sources.json")],
            registry_entries=[dict(definition=dict(source_id=source_id, start_url=url, allowed_hosts=[HOST],
                                                   allowed_path_prefixes=[f"/{Path(url).name}"],
                                                   canonical_authority="State Secretariat for Migration SEM",
                                                   jurisdiction="CH" if "zurich" not in url else "CH-ZH",
                                                   language="en"), scan_status="ready")]))
        results.append(dict(url=url, status="saved"))
    (run / "plan.json").write_text(json.dumps(dict(
        schema_version="swisstip.catalogue-download-plan/v1", catalogue=str(root / "releases" / pack / "sources.json"),
        catalogue_sha256=sha256(catalogue_bytes), targets=targets, allowed_redirect_hosts=[HOST])), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps(dict(
        schema_version="swisstip.catalogue-download/v1", catalogue_sha256=sha256(catalogue_bytes),
        target_count=len(targets), counts=dict(saved=len(targets)), results=results)), encoding="utf-8")


def build_pack(root: Path, pack: str = "test-pack", *, with_checks: bool = True) -> dict:
    """Write the pack and run the pipeline over it; returns the pipeline report."""
    pack_dir = root / "releases" / pack
    pack_dir.mkdir(parents=True)
    data = json.dumps(catalogue(list(PAGES)), indent=2).encode()
    (pack_dir / "sources.json").write_bytes(data)
    write_run(root, pack, data)
    report = Pipeline(root, pack, log=lambda message: None).run_stages("extract", "validate-text")
    index = json.loads((root / ".local" / pack / "text" / "index.json").read_text(encoding="utf-8"))
    by_url = {entry["source_url"]: entry["document_id"] for entry in index}
    curation = CURATION
    for name, url in (("DOC_REGISTRATION", "https://www.sem.example/registration.html"),
                      ("DOC_WORK", "https://www.sem.example/work.html"),
                      ("DOC_ZURICH", "https://www.sem.example/zurich.html")):
        curation = curation.replace(name, by_url[url])
    (pack_dir / "curation.yaml").write_text(curation, encoding="utf-8", newline="\n")
    if with_checks:
        (pack_dir / "checks.yaml").write_text(CHECKS, encoding="utf-8", newline="\n")
    report = Pipeline(root, pack, release_id=f"{pack}-2026-09-12-v1", update_curation=True,
                      log=lambda message: None).run_stages("build", "health")
    if report["exit_code"]:
        raise AssertionError(f"the fixture pipeline failed: {json.dumps(report['stages'], indent=2)}")
    return report


class PackFixture:
    """A temporary repository root with one built pack.

    The pack is built once per test class; `restore()` puts the files back as
    the pipeline left them, so a test that writes does not reach the next one.
    """

    def __init__(self, pack: str = "test-pack", *, with_checks: bool = True):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.root = self.base / "repository"
        self.pristine = self.base / "pristine"
        self.pack = pack
        self.root.mkdir()
        self.report = build_pack(self.root, pack, with_checks=with_checks)
        shutil.copytree(self.root, self.pristine)
        self.pack_dir = self.root / "releases" / pack
        self.run_dir = self.root / ".local" / pack
        index = json.loads((self.run_dir / "text" / "index.json").read_text(encoding="utf-8"))
        self.documents = {entry["source_url"]: entry["document_id"] for entry in index}

    def restore(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.copytree(self.pristine, self.root)

    def document(self, name: str) -> str:
        return self.documents[f"https://{HOST}/{name}.html"]

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)
        self.directory.cleanup()
