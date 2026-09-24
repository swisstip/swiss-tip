"""Deterministic checks and the optional DeepEval bridge."""

from typing import Any

from .models import AgentResult, CaseScore, EvalCase


def _coverage(expected: list[str], actual: str) -> float:
    if not expected:
        return 1.0
    return sum(1 for phrase in expected if phrase.casefold() in actual.casefold()) / len(expected)


def score_case(case: EvalCase, result: AgentResult) -> CaseScore:
    answer = result.answer
    supported = _coverage(case.expected_claims, answer)
    precision = (sum(1 for citation in result.citations
                     if any(citation.casefold() in expected.casefold() for expected in case.required_citations))
                 / len(result.citations) if result.citations and case.required_citations else 1.0)
    recall = _coverage(case.required_citations, " ".join(result.citations))
    tool_names = [call.name for call in result.tool_calls]
    tool_score = _coverage(case.expected_tools, " ".join(tool_names))
    unsupported = sum(1 for phrase in case.must_not_claim if phrase.casefold() in answer.casefold())
    unsupported_rate = unsupported / len(case.must_not_claim) if case.must_not_claim else 0.0
    reasons = []
    if result.exit_code:
        reasons.append(f"harness exited with {result.exit_code}")
    if supported < 1:
        reasons.append("one or more expected claims were absent")
    if unsupported_rate:
        reasons.append("answer contains a forbidden claim")
    return CaseScore(case_id=case.case_id, configuration=result.configuration,
                     fact_accuracy=round(supported, 4), citation_precision=round(precision, 4),
                     citation_recall=round(recall, 4), tool_selection=round(tool_score, 4),
                     unsupported_claim_rate=round(unsupported_rate, 4), mcp_calls=len(result.tool_calls),
                     latency_ms=result.duration_ms, reasons=reasons)


def deepeval_test_case(case: EvalCase, result: AgentResult) -> Any:
    """Convert a normalized result to DeepEval's test case type on demand."""
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for judge metrics; install apps/mcp-evals") from exc
    return LLMTestCase(input=case.question, actual_output=result.answer, expected_output=case.expected_answer,
                       retrieval_context=result.retrieval_context,
                       additional_metadata={"case_id": case.case_id, "configuration": result.configuration,
                                            "tool_calls": [call.model_dump() for call in result.tool_calls]})


def evaluate_with_deepeval(cases: list[Any], metrics: list[Any]) -> Any:
    try:
        from deepeval import evaluate
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for judge metrics; install apps/mcp-evals") from exc
    return evaluate(test_cases=cases, metrics=metrics, print_results=False)


def default_deepeval_metrics() -> list[Any]:
    """Return the standard LLM-judge metrics without importing DeepEval at startup."""
    try:
        from deepeval.metrics import (AnswerRelevancyMetric, ContextualPrecisionMetric,
                                      ContextualRecallMetric, FaithfulnessMetric)
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for --judge; install apps/mcp-evals") from exc
    return [AnswerRelevancyMetric(include_reason=True), FaithfulnessMetric(include_reason=True),
            ContextualPrecisionMetric(include_reason=True), ContextualRecallMetric(include_reason=True)]