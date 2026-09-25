"""Deterministic checks and the optional DeepEval bridge."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .models import AgentResult, CaseScore, EvalCase

# DeepEval suffixes a GEval metric's name with " [GEval]"; map the base name
# to the CaseScore field(s) it should populate.
_JUDGE_METRIC_FIELDS: dict[str, tuple[str, str | None]] = {
    "Fact Accuracy": ("fact_accuracy", None),
    "Answer Relevancy": ("answer_relevancy", None),
    "Faithfulness": ("faithfulness", None),
    "Contextual Precision": ("contextual_precision", None),
    "Contextual Recall": ("contextual_recall", None),
}


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
                     citation_precision=round(precision, 4),
                     citation_recall=round(recall, 4), tool_selection=round(tool_score, 4),
                     unsupported_claim_rate=round(unsupported_rate, 4), mcp_calls=len(result.tool_calls),
                     latency_ms=result.duration_ms, reasons=reasons)


def _expected_output(case: EvalCase) -> str:
    if not case.expected_claims:
        return case.expected_answer
    return case.expected_answer + "\n\nExpected claims:\n" + "\n".join(
        f"- {claim}" for claim in case.expected_claims)


def deepeval_test_case(case: EvalCase, result: AgentResult) -> Any:
    """Convert a normalized result to DeepEval's test case type on demand."""
    try:
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for judge metrics; install apps/mcp-evals") from exc
    return LLMTestCase(input=case.question, actual_output=result.answer, expected_output=_expected_output(case),
                       retrieval_context=result.retrieval_context,
                       metadata={"case_id": case.case_id, "configuration": result.configuration,
                                "tool_calls": [call.model_dump() for call in result.tool_calls]})


def evaluate_with_deepeval(cases: list[Any], metrics: list[Any], max_concurrent: int = 3,
                           throttle_value: float = 0) -> Any:
    # DeepEval's own default (20) fires enough parallel GEval calls to trip the judge model's
    # tokens-per-minute rate limit; keep it modest and let callers raise it if their quota allows.
    try:
        from deepeval import evaluate
        from deepeval.evaluate.configs import AsyncConfig, DisplayConfig
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for judge metrics; install apps/mcp-evals") from exc
    async_config = AsyncConfig(max_concurrent=max_concurrent, throttle_value=throttle_value)
    return evaluate(test_cases=cases, metrics=metrics, display_config=DisplayConfig(print_results=False),
                    async_config=async_config)


def default_deepeval_metrics() -> list[Any]:
    """Return the standard LLM-judge metrics without importing DeepEval at startup."""
    try:
        from deepeval.metrics import (AnswerRelevancyMetric, ContextualPrecisionMetric,
                                      ContextualRecallMetric, FaithfulnessMetric, GEval)
        from deepeval.test_case import LLMTestCaseParams
    except ImportError as exc:
        raise RuntimeError("DeepEval is required for --judge; install apps/mcp-evals") from exc
    fact_accuracy = GEval(
        name="Fact Accuracy",
        criteria=("Assess whether the actual answer accurately states every expected claim. "
                  "Treat paraphrases, equivalent wording, dates, calculations and negation as equivalent "
                  "when their meaning is preserved. Give partial credit when only some claims are covered. "
                  "Penalize contradictions and unsupported claims. Do not require exact wording."),
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT,
                           LLMTestCaseParams.EXPECTED_OUTPUT],
    )
    return [fact_accuracy, AnswerRelevancyMetric(include_reason=True), FaithfulnessMetric(include_reason=True),
            ContextualPrecisionMetric(include_reason=True), ContextualRecallMetric(include_reason=True)]


def merge_judge_scores(scores: list[CaseScore], judge_result: Any) -> None:
    """Copy DeepEval's per-metric scores onto the matching `CaseScore`, in place."""
    by_key = {(score.case_id, score.configuration): score for score in scores}
    for index, test_result in enumerate(judge_result.test_results):
        metadata = test_result.metadata or {}
        key = (metadata.get("case_id"), metadata.get("configuration"))
        score = by_key.get(key) if key in by_key else (scores[index] if index < len(scores) else None)
        if score is None or not test_result.metrics_data:
            continue
        for metric in test_result.metrics_data:
            fields = _JUDGE_METRIC_FIELDS.get(metric.name.removesuffix(" [GEval]"))
            if not fields:
                continue
            score_field, reason_field = fields
            setattr(score, score_field, round(metric.score, 4) if metric.score is not None else None)
            if reason_field:
                setattr(score, reason_field, metric.reason)


def judge_result_to_dict(judge_result: Any) -> dict[str, list[dict[str, Any]]]:
    """Render DeepEval's result in the shape `deepeval inspect` reads (`TraceApi`/`BaseApiSpan`:
    `testCases[].trace` with camelCase fields, spans bucketed under `llmSpans`)."""
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def metrics_data(test_result: Any) -> list[dict[str, Any]]:
        return [
            {"name": metric.name, "score": metric.score, "success": metric.success,
             "reason": metric.reason, "threshold": metric.threshold}
            for metric in (test_result.metrics_data or [])
        ]

    return {
        "testCases": [
            {
                "name": test_result.name,
                "input": getattr(test_result, "input", None),
                "actualOutput": getattr(test_result, "actual_output", None),
                "expectedOutput": getattr(test_result, "expected_output", None),
                "context": getattr(test_result, "context", None),
                "retrievalContext": getattr(test_result, "retrieval_context", None),
                "success": test_result.success,
                "metricsData": metrics_data(test_result),
                "trace": {
                    "uuid": str(uuid4()),
                    "name": test_result.name,
                    "status": "SUCCESS" if test_result.success else "ERRORED",
                    "startTime": timestamp,
                    "endTime": timestamp,
                    "input": getattr(test_result, "input", None),
                    "output": getattr(test_result, "actual_output", None),
                    "metadata": test_result.metadata,
                    "metricsData": metrics_data(test_result),
                    "llmSpans": [{
                        "uuid": str(uuid4()),
                        "name": "MCP evaluation",
                        "status": "SUCCESS" if test_result.success else "ERRORED",
                        "type": "llm",
                        "startTime": timestamp,
                        "endTime": timestamp,
                        "input": getattr(test_result, "input", None),
                        "output": getattr(test_result, "actual_output", None),
                        "metricsData": metrics_data(test_result),
                    }],
                },
            }
            for test_result in judge_result.test_results
        ]
    }