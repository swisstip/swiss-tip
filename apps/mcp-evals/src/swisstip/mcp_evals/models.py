"""Data contracts shared by case loading, adapters and reporting."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvalCase(Strict):
    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str = ""
    category: str = "general"
    difficulty: str = "standard"
    expected_claims: list[str] = Field(default_factory=list)
    required_citations: list[str] = Field(default_factory=list)
    expected_tools: list[str] = Field(default_factory=list)
    must_not_claim: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HarnessConfig(Strict):
    name: str = Field(min_length=1)
    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=300, gt=0)


class ToolCall(Strict):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class AgentResult(Strict):
    case_id: str
    configuration: str
    answer: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    retrieval_context: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0
    exit_code: int = 0


class CaseScore(Strict):
    case_id: str
    configuration: str
    citation_precision: float
    citation_recall: float
    tool_selection: float
    unsupported_claim_rate: float
    mcp_calls: int
    latency_ms: float
    reasons: list[str] = Field(default_factory=list)
    # Populated only when `--judge` runs DeepEval's LLM-judge metrics.
    fact_accuracy: float | None = None
    answer_relevancy: float | None = None
    faithfulness: float | None = None
    contextual_precision: float | None = None
    contextual_recall: float | None = None


class RunConfig(Strict):
    configs: dict[str, HarnessConfig]
    trials: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)


def artifact_path(root: Path, case: EvalCase, configuration: str) -> Path:
    return root / "cases" / case.case_id / configuration