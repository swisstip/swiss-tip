"""Adopt a record of the predecessor's text datasets into a run of this repository.

The old `swisstip.source-intermediate/v1` records already carry block hashes,
offsets and the raw hash of the response they describe. Adoption rewrites the
identity and provenance fields to the new run (document ID, block IDs,
attribution, catalogue hash, paths, attempt) and leaves blocks, offsets and
hashes untouched. The old extractor version stays on the record and
`imported_text` names where it came from, so a consumer can tell an adopted
record from a fresh one.
"""

from copy import deepcopy
from datetime import UTC, datetime

from . import SCHEMA_VERSION
from .html_blocks import main_headings
from .records import EXCLUDED_PAGE_KINDS, EXCLUDING_REVIEW_FLAGS, attach_offsets, language_hint, representation_group, title_for
from .run_reader import document_id

DROPPED_FIELDS = ("semantic_status",)


def adopt(old: dict, item: dict, *, run_name: str, catalogue_sha256: str | None, manifest_digest: str,
          dataset_name: str, record_file: str, imported_at: str | None = None) -> dict:
    raw_sha256 = old.get("acquisition", {}).get("raw_sha256")
    if raw_sha256 != item["raw_sha256"]:
        raise ValueError(f"Old record {old.get('document_id')} describes other bytes than snapshot {item['relative_path']}")
    if old.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unexpected record schema {old.get('schema_version')!r}")
    record = deepcopy(old)
    for field in DROPPED_FIELDS:
        record.pop(field, None)
    old_id = old["document_id"]
    new_id = document_id(item["source_url"], raw_sha256)
    snapshot = item["snapshot"]
    record.update(
        document_id=new_id, corpus_id=run_name, catalogue_sha256=catalogue_sha256,
        source_url=item["source_url"], document_url=item["document_url"], requested_url=item["requested_url"],
        version_uri=item.get("version_uri"), attribution=item["attribution"],
        source_registry=item.get("registry_entries", []), catalogue_references=item.get("references", []),
        discovery_provenance=item.get("discoveries", []) or old.get("discovery_provenance", []),
        acquisition=dict(path=item["relative_path"], attempt=item.get("attempt"), manifest_path=item["pointer"],
                         manifest_sha256=manifest_digest, raw_sha256=raw_sha256, bytes=old["acquisition"].get("bytes"),
                         retrieved_at=snapshot.get("retrieved_at") or old["acquisition"].get("retrieved_at"),
                         requested_url=item["requested_url"], declared_content_type=snapshot.get("content_type"),
                         review_flags=snapshot.get("review_flags", []),
                         http_status=snapshot.get("status") or item.get("http_status"), imported_from=item.get("imported_from")),
        representation_group=representation_group(item["source_url"]),
        imported_text=dict(dataset=dataset_name, document_id=old_id, extractor_version=old.get("extractor_version"),
                           record_file=record_file, imported_at=imported_at or datetime.now(UTC).isoformat()),
    )
    for block in record.get("blocks", []):
        prefix, _, suffix = block["block_id"].rpartition(":b")
        if prefix != old_id:
            raise ValueError(f"Block {block['block_id']} does not belong to {old_id}")
        block["block_id"] = f"{new_id}:b{suffix}"
        block.setdefault("source_locator", {})
    if record.get("representation") == "html" and record.get("blocks"):
        record["main_headings"] = main_headings(record["blocks"])
    record["language_hint"] = language_hint(item)
    record["title"] = title_for(record.get("html_title"), record["catalogue_references"], record.get("main_headings", []),
                                item["source_url"])
    if record.get("status") != "extraction_failed":
        flags = snapshot.get("review_flags", [])
        excluded_flags = sorted(set(flags) & EXCLUDING_REVIEW_FLAGS)
        excluded = record.get("page_kind") in EXCLUDED_PAGE_KINDS or bool(excluded_flags)
        record["eligible_for_processing"] = bool(record.get("blocks")) and not excluded
        record["status"] = ("excluded_source_response" if excluded else
                            "extracted" if record.get("blocks") else "no_extractable_text")
        record["exclusion_reasons"] = ([record["page_kind"]] if record.get("page_kind") in EXCLUDED_PAGE_KINDS else []) + excluded_flags
    previous_hash = record.get("content_sha256")
    attach_offsets(record)
    if record["content_sha256"] != previous_hash:
        raise ValueError(f"Old record {old_id} does not round-trip its own content hash")
    record.update(html_counterparts=[], preferred_representation=record["eligible_for_processing"],
                  identical_raw_snapshots=[], identical_text_records=[])
    return record
