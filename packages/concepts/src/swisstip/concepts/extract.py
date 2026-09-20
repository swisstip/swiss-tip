"""Per-record extraction, independent review, conflict-preserving merge and audit."""

import hashlib
import json
from datetime import UTC, datetime

from .chunks import pack_chunks
from .prompts import load_prompts
from .providers.base import IncompleteCompletion, InvalidCompletion, ProviderError
from .schemas import basis_schema, extraction_schema, review_schema
from .sections import build_sections, section_summary, terminology_key
from .validation import ValidationError, parse_bases, parse_proposals, parse_verdicts, validate_proposal


DEFAULT_SETTINGS = dict(chunk_content_characters=6400, chunk_overlap_characters=400,
                        max_concepts_per_chunk=6, max_review_input_characters=64000,
                        review_fallback_batch_size=2, classify_basis=True)
BASIS_FIELDS = ("kind", "level", "norm", "refers_to", "reason")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sum_usage(completions, field: str) -> int | None:
    values = [getattr(completion, field, None) for completion in completions]
    return None if any(value is None for value in values) else sum(values)


def _unique(values, key):
    result, seen = [], set()
    for value in values:
        identity = key(value)
        if identity not in seen:
            seen.add(identity)
            result.append(value)
    return result


def merge_candidates(content_sha256: str, drafts: list[dict]) -> tuple[list[dict], list[str]]:
    merged, warnings = {}, []
    for draft in drafts:
        key = (terminology_key(draft["preferred_label"]), terminology_key(draft["scope"]),
               draft["concept_type"], draft["granularity"], draft["description"], draft["primary_section_id"])
        if key not in merged:
            merged[key] = dict(draft)
            continue
        existing = merged[key]
        for field, limit, identity in (
            ("alternative_labels", 10, terminology_key), ("user_questions", 5, terminology_key),
            ("evidence", 5, lambda value: (value["block_id"], value["start"], value["end"])),
            ("relations", 10, lambda value: (value["relation_type"], terminology_key(value["target_label"]))),
        ):
            union = _unique(existing[field] + draft[field], identity)
            if len(union) > limit:
                warnings.append(f"merged candidate {draft['preferred_label']!r} exceeded {field} limit; extra values ignored")
            existing[field] = union[:limit]
        existing["confidence"] = max(existing["confidence"], draft["confidence"])
        if existing.get("basis") is None and draft.get("basis") is not None:
            existing["basis"] = draft["basis"]
    candidates = []
    for key, draft in merged.items():
        identity = "\n".join((content_sha256, *key))
        candidates.append(dict(draft, candidate_id="candidate-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                               validation_state="CANDIDATE"))
    return candidates, warnings


def _section_context(record: dict, chunk: dict, sections: dict, drafts: list[dict]) -> list[dict]:
    """The primary sections the drafts cite, as the review and the basis classification see them."""
    primary_ids = {draft["primary_section_id"] for draft in drafts}
    context = []
    for fragment in chunk["sections"]:
        section_id = fragment["section_id"]
        if section_id not in primary_ids:
            continue
        section = sections[section_id]
        spans = [span for span in chunk["evidence_spans"] if span["section_id"] == section_id]
        first, last = min(span["start"] for span in spans), max(span["end"] for span in spans)
        parts = []
        for block in section["context_blocks"]:
            locator = block.get("source_locator") or {}
            if locator.get("is_footnote") or block.get("is_footnote"):
                parts.append(block["text"])
            elif block["end"] > first and block["start"] < last:
                parts.append(record["content_text"][max(first, block["start"]):min(last, block["end"])])
        context.append(dict(section_id=section_id, heading_path=section["heading_path"], text="\n\n".join(parts),
                            fragment_start=fragment["fragment_start"], context_may_be_partial=(
                                first > section["span_blocks"][0]["start"] or last < section["span_blocks"][-1]["end"])))
    return context


def _review_payload(record: dict, chunk: dict, sections: dict, drafts: list[dict]) -> dict:
    return dict(untrusted_review=dict(title=record.get("title"),
                                     language=record.get("language_declared") or record.get("language_hint"),
                                     primary_sections=_section_context(record, chunk, sections, drafts),
                                     proposals=[dict(review_id=index, candidate=draft)
                                                for index, draft in enumerate(drafts, 1)]))


def _basis_payload(record: dict, chunk: dict, sections: dict, drafts: list[dict]) -> dict:
    """The retained proposals with their quotes, for the classification of what each quote is."""
    content = record["content_text"]
    return dict(untrusted_basis=dict(title=record.get("title"), source_url=record.get("source_url"),
                                    language=record.get("language_declared") or record.get("language_hint"),
                                    primary_sections=_section_context(record, chunk, sections, drafts),
                                    proposals=[dict(basis_id=index, preferred_label=draft["preferred_label"],
                                                    heading_path=sections[draft["primary_section_id"]]["heading_path"],
                                                    quotes=[content[item["start"]:item["end"]] for item in draft["evidence"]])
                                               for index, draft in enumerate(drafts, 1)]))


def extract_record(record: dict, provider, *, prompts=None, settings: dict | None = None,
                   job_id: str = "", profile: str = "", model: str = "") -> dict:
    """Return a complete audit, including partial work if a provider stops the job.

    A contextual checkpoint wrapper may implement ``set_request_context``,
    ``last_request_key``, ``mark_invalid`` and ``record_review_fallback``. Raw
    providers and offline fakes only need ``generate_structured``.
    """
    prompts = load_prompts() if prompts is None else prompts
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    sections = build_sections(record)
    chunks = pack_chunks(record, sections, chunk_content_characters=settings["chunk_content_characters"],
                         chunk_overlap_characters=settings["chunk_overlap_characters"])
    section_map = {section["section_id"]: section for section in sections}
    completions, retained, rejected, reviews, failures, chunk_reports, warnings = [], [], [], [], [], [], []
    stopped, extraction_requests, review_requests, review_fallbacks, accepted = False, 0, 0, 0, 0
    basis_requests = 0

    def mark_invalid(key, reason):
        if hasattr(provider, "mark_invalid"):
            provider.mark_invalid(key, reason)

    def request(kind, payload, schema, chunk_report, proposal_count=0):
        nonlocal extraction_requests, review_requests, basis_requests
        if hasattr(provider, "set_request_context"):
            provider.set_request_context(document_id=record["document_id"], title=record.get("title"),
                                         chunk_index=chunk_report["chunk_index"], chunk_count=len(chunks),
                                         proposal_count=proposal_count, kind=kind)
        if kind == "review":
            review_requests += 1
        elif kind == "basis":
            basis_requests += 1
        else:
            extraction_requests += 1
        try:
            completion = provider.generate_structured(system_prompt=getattr(prompts, kind).text,
                                                       user_prompt=canonical_json(payload), response_schema=schema)
        except (IncompleteCompletion, InvalidCompletion) as exc:
            # Direct adapters preserve raw incomplete/invalid responses on errors;
            # the recoverable wrapper normally returns these after checkpointing.
            completion = getattr(exc, "completion", None)
            if completion is None:
                raise
        completions.append(completion)
        chunk_report["_completions"].append(completion)
        key = getattr(provider, "last_request_key", None)
        if key:
            chunk_report["request_keys"].append(key)
        return completion, key

    def review_batch(drafts, chunk, chunk_report):
        nonlocal review_fallbacks
        payload = _review_payload(record, chunk, section_map, drafts)
        if len(canonical_json(payload)) > settings["max_review_input_characters"]:
            raise ValidationError("review input exceeds max_review_input_characters")
        completion, key = request("review", payload, review_schema(len(drafts)), chunk_report, len(drafts))
        if getattr(completion, "awaiting_response", False):
            raise ValidationError("awaiting_response")
        finish = completion.finish_reason
        if finish == "length" and len(drafts) > settings["review_fallback_batch_size"]:
            review_fallbacks += 1
            if hasattr(provider, "record_review_fallback"):
                provider.record_review_fallback()
            middle = (len(drafts) + 1) // 2
            left = review_batch(drafts[:middle], chunk, chunk_report)
            right = review_batch(drafts[middle:], chunk, chunk_report)
            return left + [dict(verdict, review_id=verdict["review_id"] + middle) for verdict in right]
        try:
            if finish not in ("stop", "eos_token", "stop_sequence"):
                raise ValidationError(f"review finish_reason is {finish!r}")
            return parse_verdicts(completion.content, len(drafts))
        except ValidationError as exc:
            mark_invalid(key, str(exc))
            raise

    def basis_batch(drafts, chunk, chunk_report):
        """One call per chunk for what the retained proposals' evidence is: an act and its article, a directive,
        the authority's own guidance, a directory entry or a portal summary."""
        payload = _basis_payload(record, chunk, section_map, drafts)
        if len(canonical_json(payload)) > settings["max_review_input_characters"]:
            raise ValidationError("basis input exceeds max_review_input_characters")
        completion, key = request("basis", payload, basis_schema(len(drafts)), chunk_report, len(drafts))
        if getattr(completion, "awaiting_response", False):
            raise ValidationError("awaiting_response")
        try:
            if completion.finish_reason not in ("stop", "eos_token", "stop_sequence"):
                raise ValidationError(f"basis finish_reason is {completion.finish_reason!r}")
            return parse_bases(completion.content, len(drafts))
        except ValidationError as exc:
            mark_invalid(key, str(exc))
            raise

    for chunk in chunks:
        report = dict(chunk_index=chunk["chunk_index"], section_ids=chunk["section_ids"],
                      characters=chunk["characters"], status=chunk["status"], request_keys=[], _completions=[])
        chunk_reports.append(report)
        if chunk["status"] == "skipped_link_only":
            continue
        if stopped:
            report["status"] = "failed"
            failures.append(dict(chunk_index=chunk["chunk_index"], reason="job_stopped", request_key=None))
            continue
        try:
            catalogue = chunk["evidence_spans"]
            payload = dict(untrusted_page=dict(document_id=record["document_id"], title=record.get("title"),
                                               language=record.get("language_declared") or record.get("language_hint"),
                                               chunk_index=chunk["chunk_index"], chunk_count=len(chunks), sections=chunk["sections"],
                                               evidence_spans=[{key: span[key] for key in ("evidence_id", "section_id", "text")}
                                                               for span in catalogue]))
            completion, key = request("extraction", payload, extraction_schema(settings["max_concepts_per_chunk"],
                                                                               [span["evidence_id"] for span in catalogue],
                                                                               chunk["section_ids"]), report)
            if getattr(completion, "awaiting_response", False):
                report["status"] = "awaiting_response"
                failures.append(dict(chunk_index=chunk["chunk_index"], reason="awaiting_response", request_key=key))
                continue
            try:
                if completion.finish_reason not in ("stop", "eos_token", "stop_sequence"):
                    raise ValidationError(f"extraction finish_reason is {completion.finish_reason!r}")
                proposals = parse_proposals(completion.content, settings["max_concepts_per_chunk"])
            except ValidationError as exc:
                mark_invalid(key, str(exc))
                report["status"] = "failed"
                failures.append(dict(chunk_index=chunk["chunk_index"], reason=str(exc), request_key=key,
                                     raw_completion=completion.content))
                continue
            drafts, raw_proposals, indices = [], [], []
            for proposal_index, proposal in enumerate(proposals, 1):
                try:
                    draft = validate_proposal(proposal, catalogue, chunk["section_ids"])
                except ValidationError as exc:
                    rejected.append(dict(stage="structural", reason=str(exc), chunk_index=chunk["chunk_index"],
                                         proposal_index=proposal_index, proposal=proposal))
                else:
                    drafts.append(draft)
                    raw_proposals.append(proposal)
                    indices.append(proposal_index)
            accepted += len(drafts)
            report["status"] = "extracted"
            if not drafts:
                continue
            try:
                verdicts = review_batch(drafts, chunk, report)
            except ProviderError as exc:
                for proposal, index in zip(raw_proposals, indices, strict=True):
                    rejected.append(dict(stage="review", reason=f"review_failed: {exc}", chunk_index=chunk["chunk_index"],
                                         proposal_index=index, proposal=proposal))
                raise
            except ValidationError as exc:
                reason = "awaiting_response" if str(exc) == "awaiting_response" else "review_unparseable"
                report["status"] = "awaiting_response" if reason == "awaiting_response" else "failed"
                failures.append(dict(chunk_index=chunk["chunk_index"], reason=reason, detail=str(exc),
                                     request_key=getattr(provider, "last_request_key", None)))
                for proposal, index in zip(raw_proposals, indices, strict=True):
                    rejected.append(dict(stage="review", reason=reason, chunk_index=chunk["chunk_index"],
                                         proposal_index=index, proposal=proposal))
                continue
            supported = []
            for draft, proposal, index, verdict in zip(drafts, raw_proposals, indices, verdicts, strict=True):
                reviews.append(dict(verdict, chunk_index=chunk["chunk_index"], proposal_index=index,
                                    preferred_label=draft["preferred_label"], primary_section_id=draft["primary_section_id"]))
                if verdict["decision"] == "supported":
                    retained.append(dict(draft, heading_path=section_map[draft["primary_section_id"]]["heading_path"],
                                         review={key: verdict[key] for key in ("decision", "issue", "reason")}, basis=None))
                    supported.append((retained[-1], draft))
                else:
                    rejected.append(dict(stage="review", reason=f"{verdict['issue']}: {verdict['reason']}",
                                         chunk_index=chunk["chunk_index"], proposal_index=index, proposal=proposal))
            if settings["classify_basis"] and supported:
                try:
                    bases = basis_batch([draft for _, draft in supported], chunk, report)
                except ValidationError as exc:
                    # The candidates stay; without a basis the build gives them the page default.
                    reason = "awaiting_response" if str(exc) == "awaiting_response" else "basis_unparseable"
                    if reason == "awaiting_response":
                        report["status"] = "awaiting_response"
                    failures.append(dict(chunk_index=chunk["chunk_index"], reason=reason, detail=str(exc),
                                         request_key=getattr(provider, "last_request_key", None)))
                else:
                    for (candidate, _), basis in zip(supported, bases, strict=True):
                        candidate["basis"] = {field: basis[field] for field in BASIS_FIELDS}
        except ProviderError as exc:
            stopped = True
            report["status"] = "failed"
            failures.append(dict(chunk_index=chunk["chunk_index"], reason=str(exc), error_type=type(exc).__name__,
                                 request_key=getattr(provider, "last_request_key", None)))

    candidates, merge_warnings = merge_candidates(record["content_sha256"], retained)
    warnings.extend(merge_warnings)
    if not any(section["kept"] for section in sections):
        warnings.append("no_content_sections")
    for report in chunk_reports:
        calls = report.pop("_completions")
        report.update(prompt_tokens=_sum_usage(calls, "prompt_tokens"), output_tokens=_sum_usage(calls, "output_tokens"))
    kept = {section["section_id"] for section in sections if section["kept"] and not section["link_only"]}
    cited = {candidate["primary_section_id"] for candidate in candidates}
    identities = [{name: getattr(completion, name, None) for name in
                   ("provider", "model", "requested_model", "observed_model", "request_id")} for completion in completions]
    return dict(schema_version="swisstip.concept-candidates/v1", document_id=record["document_id"],
                source_url=record.get("source_url"), document_url=record.get("document_url"), title=record.get("title"),
                language=record.get("language_declared") or record.get("language_hint"), content_sha256=record["content_sha256"],
                raw_sha256=record.get("acquisition", {}).get("raw_sha256", record.get("raw_sha256")),
                extractor_version=record.get("extractor_version"), job_id=job_id, profile=profile,
                provider=identities[0]["provider"] if identities else getattr(provider, "provider", None),
                model=model or (identities[0]["model"] if identities else getattr(provider, "model", None)),
                model_identities=identities, prompts=prompts.to_dict(), settings=settings,
                sections=[section_summary(section) for section in sections], chunks=chunk_reports,
                candidates=candidates, rejected=rejected, reviews=reviews, failures=failures,
                request_count=len(completions), prompt_tokens=_sum_usage(completions, "prompt_tokens"),
                output_tokens=_sum_usage(completions, "output_tokens"), job_stopped=stopped,
                quality_metrics=dict(accepted_proposals=len(retained), rejected_proposals=len(rejected),
                                     content_section_count=len(kept), excluded_section_count=len(sections) - len(kept),
                                     cited_section_count=len(cited), empty_question_count=0,
                                     generation_request_count=extraction_requests,
                                     skipped_chunk_count=sum(c["status"] == "skipped_link_only" for c in chunks),
                                     review_request_count=review_requests, model_reviewed_count=len(reviews),
                                     semantic_rejection_count=sum(v["decision"] != "supported" for v in reviews),
                                     semantic_review="separate_model_assessment" if reviews else "not_performed",
                                     proposals_accepted=accepted,
                                     proposals_rejected=sum(item["stage"] == "structural" for item in rejected),
                                     retained_candidates=len(candidates), sections_kept=len(kept),
                                     sections_excluded=len(sections) - len(kept), sections_cited=len(cited),
                                     uncited_section_ids=sorted(kept - cited),
                                     empty_questions=sum(not candidate["user_questions"] for candidate in candidates),
                                     review_requests=review_requests, review_fallbacks=review_fallbacks,
                                     semantic_rejections=sum(item["stage"] == "review" for item in rejected),
                                     basis_request_count=basis_requests,
                                     candidates_with_basis=sum(candidate.get("basis") is not None for candidate in candidates),
                                     basis_classification="separate_model_assessment" if basis_requests else "not_performed",
                                     confidence_interpretation="uncalibrated_model_assessment",
                                     evidence_validation="source_location_only; semantic_support_requires_review"),
                output_sha256=hashlib.sha256(canonical_json(candidates).encode("utf-8")).hexdigest(),
                generated_at=datetime.now(UTC).isoformat(), warnings=warnings)
