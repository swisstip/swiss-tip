"""Human acceptance of observed acquisition and extraction exceptions.

Approval closes review work for the recorded observation. It never changes
download evidence, extraction eligibility or the review status of a fact.
"""

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

from .acquisition import read_json


REVIEW_SCHEMA = "swisstip.run-review-decisions/v1"
REVIEW_FILE = "review-decisions.json"
REVIEW_FIELDS = ("review_status", "decision", "reviewed_by", "reviewed_on", "reason")
OBSERVATION_FIELDS = ("url", "status", "gap", "kind", "source_ids", "scan_statuses",
                      "attempt_outcomes", "latest_snapshots")
TEXT_FIELDS = ("document_id", "raw_sha256", "status", "representation",
               "pdf_pages_without_text", "exclusion_reasons")


def fingerprint(value: dict, fields: tuple[str, ...]) -> str:
    content = json.dumps({key: value.get(key) for key in fields}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def observation_fingerprint(row: dict) -> str:
    return fingerprint(row, OBSERVATION_FIELDS)


def text_fingerprint(entry: dict) -> str:
    return fingerprint(entry, TEXT_FIELDS)


def review_metadata(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("A review decision must be an object")
    for field in REVIEW_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"Review decision needs a non-empty {field}")
    if value["review_status"] != "human-reviewed" or value["decision"] != "approved":
        raise ValueError("An accepted exception must be human-reviewed and approved")
    if date.fromisoformat(value["reviewed_on"]).isoformat() != value["reviewed_on"]:
        raise ValueError("reviewed_on must be an ISO date")
    return {field: value[field] for field in REVIEW_FIELDS}


def load_decisions(run: Path, catalogue_sha256: str | None = None) -> dict:
    path = run / REVIEW_FILE
    if not path.is_file():
        return {"acquisition": [], "text_documents": []}
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError(f"Unsupported review decisions schema in {path}")
    recorded_catalogue = data.get("catalogue_sha256")
    if not isinstance(recorded_catalogue, str) or len(recorded_catalogue) != 64 or any(c not in "0123456789abcdef" for c in recorded_catalogue):
        raise ValueError("Review decisions need the source catalogue SHA-256")
    if catalogue_sha256 is not None and recorded_catalogue != catalogue_sha256:
        raise ValueError("Review decisions belong to a different source catalogue")
    for collection, identity in (("acquisition", "url"), ("text_documents", "document_id")):
        values = data.get(collection, [])
        if not isinstance(values, list):
            raise ValueError(f"Review decisions {collection} must be a list")
        seen = set()
        for value in values:
            review_metadata(value)
            if not isinstance(value.get(identity), str) or not value[identity].strip():
                raise ValueError(f"Review decision needs {identity}")
            digest = value.get("observation_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Review decision needs an observation SHA-256")
            key = (value[identity], digest)
            if key in seen:
                raise ValueError(f"Duplicate review decision for {value[identity]}")
            seen.add(key)
    if data.get("scope") is not None:
        review_metadata(data["scope"])
    return data


def target_review(row: dict, decisions: dict) -> dict | None:
    digest = observation_fingerprint(row)
    return next((review_metadata(value) for value in decisions.get("acquisition", [])
                 if value["url"] == row["url"] and value["observation_sha256"] == digest), None)


def text_review(entry: dict, decisions: dict) -> dict | None:
    digest = text_fingerprint(entry)
    return next((review_metadata(value) for value in decisions.get("text_documents", [])
                 if value["document_id"] == entry["document_id"] and value["observation_sha256"] == digest), None)


def apply_review_decisions(report: dict, run: Path) -> dict:
    # Import here so the classifier can call this function without a module cycle.
    from .gap_report import INFORMATIONAL

    decisions = load_decisions(run, report["catalogue_sha256"])
    approved = {(value["url"], value["observation_sha256"]): review_metadata(value)
                for value in decisions.get("acquisition", [])}
    rows = []
    for original in report["targets"]:
        row = dict(original)
        actionable = row["gap"] != "none" and row["gap"] not in INFORMATIONAL
        review = approved.get((row["url"], observation_fingerprint(row))) if actionable else None
        disposition = "approved" if review else "open" if actionable else "informational" if row["gap"] in INFORMATIONAL else "available"
        row.update(review=review, disposition=disposition, outstanding=actionable and review is None)
        rows.append(row)
    open_rows = [row for row in rows if row["outstanding"]]
    unapproved = [row for row in rows if row["disposition"] != "approved"]
    counts = {**report["counts"],
              "gap": dict(Counter(row["gap"] for row in unapproved)),
              "gap_by_kind": {kind: dict(Counter(row["gap"] for row in unapproved if row["kind"] == kind))
                              for kind in sorted({row["kind"] for row in rows})},
              "retriable": sum(bool(row["retriable"]) for row in open_rows),
              "not_retriable": sum(not row["retriable"] for row in open_rows),
              "outstanding": len(open_rows),
              "approved": sum(row["disposition"] == "approved" for row in rows)}
    return {**report, "targets": rows, "counts": counts,
            "retriable": [row["url"] for row in open_rows if row["retriable"]],
            "not_retriable": [row["url"] for row in open_rows if not row["retriable"]],
            "approved": [row["url"] for row in rows if row["disposition"] == "approved"],
            "review_decisions_file": REVIEW_FILE if decisions.get("schema_version") else None,
            "review_decisions_sha256": hashlib.sha256((run / REVIEW_FILE).read_bytes()).hexdigest() if decisions.get("schema_version") else None,
            "scope_review": review_metadata(decisions["scope"]) if decisions.get("scope") else None}
