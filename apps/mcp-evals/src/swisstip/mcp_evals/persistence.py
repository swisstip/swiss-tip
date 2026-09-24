"""Local, inspectable run artifacts."""

import json
from pathlib import Path

from .models import AgentResult, CaseScore, EvalCase, artifact_path


def save_case(root: Path, case: EvalCase, result: AgentResult, score: CaseScore, trial: int = 1) -> None:
    folder = artifact_path(root, case, result.configuration) / f"trial-{trial:03d}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "request.json").write_text(json.dumps(case.model_dump(), indent=2), encoding="utf-8")
    (folder / "answer.txt").write_text(result.answer, encoding="utf-8")
    (folder / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (folder / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    (folder / "tool_calls.json").write_text(json.dumps([call.model_dump() for call in result.tool_calls], indent=2), encoding="utf-8")
    (folder / "result.json").write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
    (folder / "scores.json").write_text(json.dumps(score.model_dump(), indent=2), encoding="utf-8")