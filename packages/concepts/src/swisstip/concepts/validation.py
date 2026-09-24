"""Strict local parsing, source-span validation, and review verdict contracts."""

import json
import math
import re

from .schemas import BASIS_KINDS, BASIS_LEVELS, CONCEPT_TYPES, GRANULARITY_LEVELS, ISSUES, NORM_KINDS, RELATION_TYPES
from .sections import terminology_key


class ValidationError(ValueError):
    pass


def strict_json(content: str):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValidationError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def constant(value):
        raise ValidationError(f"non-finite JSON number: {value}")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValidationError("non-finite JSON number")
        return number

    try:
        return json.loads(content, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    except ValidationError:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("completion is not valid JSON") from exc


def parse_proposals(content: str, maximum: int = 6) -> list:
    payload = strict_json(content)
    if not isinstance(payload, dict) or set(payload) != {"concepts"}:
        raise ValidationError("completion must contain only a concepts array")
    if not isinstance(payload["concepts"], list) or len(payload["concepts"]) > maximum:
        raise ValidationError(f"concepts must be an array of at most {maximum} proposals")
    return payload["concepts"]


def _string(value, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValidationError(f"{name} must contain 1-{maximum} characters")
    return normalized


def _strings(value, name: str, maximum: int, length: int, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValidationError(f"{name} must contain {minimum}-{maximum} items")
    return [_string(item, name, length) for item in value]


def _enum(value, name: str, allowed) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{name} is not an allowed value")
    return value


def _confidence(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValidationError(f"{name} must be a number between 0 and 1")
    return float(value)


def validate_proposal(raw, catalogue: dict | list, section_ids) -> dict:
    required = {"preferred_label", "alternative_labels", "concept_type", "granularity", "description",
                "scope", "user_questions", "confidence", "evidence", "relations", "primary_section_id"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValidationError("properties do not match the candidate schema")
    draft = {name: _string(raw[name], name, limit) for name, limit in
             (("preferred_label", 200), ("description", 1200), ("scope", 500))}
    scope = draft["scope"]
    if terminology_key(scope) == "page" or re.search(r"section-\d+|\s>\s+(?![=]?\s*\d)\S", scope, re.I):
        raise ValidationError("scope must describe applicability, not a section or breadcrumb")
    draft.update(alternative_labels=_strings(raw["alternative_labels"], "alternative_labels", 10, 200),
                 user_questions=_strings(raw["user_questions"], "user_questions", 5, 300, 1),
                 concept_type=_enum(raw["concept_type"], "concept_type", CONCEPT_TYPES),
                 granularity=_enum(raw["granularity"], "granularity", GRANULARITY_LEVELS),
                 confidence=_confidence(raw["confidence"], "confidence"),
                 primary_section_id=_enum(raw["primary_section_id"], "primary_section_id", section_ids))
    if isinstance(catalogue, list):
        catalogue = {span["evidence_id"]: span for span in catalogue}
    evidence = raw["evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 5:
        raise ValidationError("evidence must contain 1-5 items")
    selected, seen = [], set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"evidence_id"}:
            raise ValidationError("evidence must select only an evidence_id")
        identity = item["evidence_id"]
        if not isinstance(identity, str) or identity not in catalogue:
            raise ValidationError("unknown evidence_id in supplied chunk")
        span = catalogue[identity]
        if span["section_id"] != draft["primary_section_id"]:
            raise ValidationError("evidence must belong to the primary section")
        if identity not in seen:
            seen.add(identity)
            selected.append({key: value for key, value in span.items() if key not in {"text", "section_id"}})
    draft["evidence"] = selected
    relations = raw["relations"]
    if not isinstance(relations, list) or len(relations) > 10:
        raise ValidationError("relations must be an array of at most 10 items")
    draft["relations"] = []
    for item in relations:
        if not isinstance(item, dict) or set(item) != {"relation_type", "target_label", "confidence"}:
            raise ValidationError("relation properties do not match schema")
        draft["relations"].append(dict(relation_type=_enum(item["relation_type"], "relation_type", RELATION_TYPES),
                                       target_label=_string(item["target_label"], "target_label", 200),
                                       confidence=_confidence(item["confidence"], "relation.confidence")))
    return draft


def parse_verdicts(content: str, count: int) -> list[dict]:
    payload = strict_json(content)
    if not isinstance(payload, dict) or set(payload) != {"verdicts"}:
        raise ValidationError("semantic review must contain only verdicts")
    verdicts = payload["verdicts"]
    if not isinstance(verdicts, list) or len(verdicts) != count:
        raise ValidationError("semantic review must cover every proposal")
    seen = set()
    for verdict in verdicts:
        if not isinstance(verdict, dict) or set(verdict) != {"review_id", "decision", "issue", "reason"}:
            raise ValidationError("semantic review verdict properties do not match schema")
        identity = verdict["review_id"]
        if type(identity) is not int or identity not in range(1, count + 1) or identity in seen:
            raise ValidationError("semantic review has a duplicate or unknown review_id")
        seen.add(identity)
        decision = _enum(verdict["decision"], "decision", ("supported", "unsupported", "uncertain"))
        issue = _enum(verdict["issue"], "issue", ISSUES)
        if (decision == "supported") != (issue == "none"):
            raise ValidationError("semantic review decision contradicts its issue")
        if not isinstance(verdict["reason"], str) or not verdict["reason"].strip() or len(verdict["reason"]) > 500:
            raise ValidationError("semantic review reason must contain 1-500 characters")
    return sorted(verdicts, key=lambda verdict: verdict["review_id"])


def _optional_string(value, name: str, maximum: int) -> str | None:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string, empty when absent")
    normalized = " ".join(value.split())
    if len(normalized) > maximum:
        raise ValidationError(f"{name} must contain at most {maximum} characters")
    return normalized or None


def parse_bases(content: str, count: int) -> list[dict]:
    """One basis per retained proposal: kind, level, the norm for a law or directive, a reference, a reason."""
    payload = strict_json(content)
    if not isinstance(payload, dict) or set(payload) != {"bases"}:
        raise ValidationError("basis classification must contain only bases")
    bases = payload["bases"]
    if not isinstance(bases, list) or len(bases) != count:
        raise ValidationError("basis classification must cover every retained proposal")
    seen, result = set(), []
    for item in bases:
        if not isinstance(item, dict) or set(item) != {"basis_id", "kind", "level", "norm", "refers_to", "reason"}:
            raise ValidationError("basis properties do not match schema")
        identity = item["basis_id"]
        if type(identity) is not int or identity not in range(1, count + 1) or identity in seen:
            raise ValidationError("basis classification has a duplicate or unknown basis_id")
        seen.add(identity)
        kind = _enum(item["kind"], "kind", BASIS_KINDS)
        norm = _optional_string(item["norm"], "norm", 200)
        if kind in NORM_KINDS and norm is None:
            raise ValidationError(f"a basis of kind {kind} must name its norm")
        result.append(dict(basis_id=identity, kind=kind, level=_enum(item["level"], "level", BASIS_LEVELS), norm=norm,
                           refers_to=_optional_string(item["refers_to"], "refers_to", 200),
                           reason=_string(item["reason"], "reason", 300)))
    return sorted(result, key=lambda basis: basis["basis_id"])
