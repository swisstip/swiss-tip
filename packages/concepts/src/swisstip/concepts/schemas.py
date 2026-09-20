"""Provider schemas for the V3 proposals, their independent review and the basis classification."""

CONCEPT_TYPES = ("ENTITY", "PROCESS", "RULE", "SERVICE", "DOCUMENT", "OTHER")
BASIS_KINDS = ("act", "ordinance", "treaty", "directive", "guidance", "directory", "summary")
BASIS_LEVELS = ("federal", "cantonal", "municipal")
NORM_KINDS = ("act", "ordinance", "treaty", "directive")
GRANULARITY_LEVELS = ("DOMAIN", "TOPIC", "ANSWERABLE", "DETAIL")
RELATION_TYPES = ("BROADER", "NARROWER", "RELATED", "SAME_AS")
ISSUES = ("none", "unsupported_claim", "missing_condition", "wrong_scope", "wrong_language",
          "wrong_type", "unanswerable_question", "irrelevant", "insufficient_context")


def _object(properties: dict) -> dict:
    return dict(type="object", additionalProperties=False, required=list(properties), properties=properties)


def _string(maximum: int) -> dict:
    return dict(type="string", maxLength=maximum)


def extraction_schema(max_concepts: int, evidence_ids, section_ids) -> dict:
    confidence = dict(type="number", minimum=0, maximum=1)
    proposal = _object(dict(
        preferred_label=_string(200),
        alternative_labels=dict(type="array", maxItems=10, items=_string(200)),
        concept_type=dict(type="string", enum=list(CONCEPT_TYPES)),
        granularity=dict(type="string", enum=list(GRANULARITY_LEVELS)),
        description=_string(1200), scope=dict(_string(500), minLength=1),
        user_questions=dict(type="array", minItems=1, maxItems=5, items=_string(300)),
        confidence=confidence,
        evidence=dict(type="array", minItems=1, maxItems=5, items=_object(dict(
            evidence_id=dict(type="string", enum=list(evidence_ids))))),
        relations=dict(type="array", maxItems=10, items=_object(dict(
            relation_type=dict(type="string", enum=list(RELATION_TYPES)),
            target_label=_string(200), confidence=confidence))),
        primary_section_id=dict(type="string", enum=list(section_ids)),
    ))
    return _object(dict(concepts=dict(type="array", maxItems=max_concepts, items=proposal)))


concept_response_schema = extraction_schema


def review_schema(count: int) -> dict:
    return _object(dict(verdicts=dict(type="array", minItems=count, maxItems=count, items=_object(dict(
        review_id=dict(type="integer", enum=list(range(1, count + 1))),
        decision=dict(type="string", enum=["supported", "unsupported", "uncertain"]),
        issue=dict(type="string", enum=list(ISSUES)), reason=dict(_string(500), minLength=1),
    )))))


def basis_schema(count: int) -> dict:
    """One basis per retained proposal: what its evidence is. Empty strings stand for an absent norm or reference,
    because the exchange checker's schema subset has no union types."""
    return _object(dict(bases=dict(type="array", minItems=count, maxItems=count, items=_object(dict(
        basis_id=dict(type="integer", enum=list(range(1, count + 1))),
        kind=dict(type="string", enum=list(BASIS_KINDS)), level=dict(type="string", enum=list(BASIS_LEVELS)),
        norm=_string(200), refers_to=_string(200), reason=dict(_string(300), minLength=1),
    )))))
