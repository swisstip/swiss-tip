"""Run a local agent CLI and normalize its structured output."""

import json
import os
import subprocess
import time

from ..models import AgentResult, EvalCase, HarnessConfig, ToolCall
from .events import parse_codex_events, parse_opencode_events, parse_pi_events

_EVENT_PARSERS = {"codex": parse_codex_events, "opencode": parse_opencode_events, "pi": parse_pi_events}


class CommandAdapter:
    def __init__(self, config: HarnessConfig):
        self.config = config

    def run(self, case: EvalCase) -> AgentResult:
        command = [part.replace("{question}", case.question).replace("{case_id}", case.case_id)
                   .replace("{model}", self.config.model) for part in self.config.command]
        environment = os.environ.copy()
        environment.update(self.config.environment)
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   env=environment, timeout=self.config.timeout_seconds, check=False)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        answer, calls, citations, context = self._parse_output(completed.stdout)
        return AgentResult(case_id=case.case_id, configuration=self.config.name, answer=answer,
                           tool_calls=calls, citations=citations, retrieval_context=context,
                           stdout=completed.stdout, stderr=completed.stderr, duration_ms=duration_ms,
                           exit_code=completed.returncode)

    def _parse_output(self, stdout: str) -> tuple[str, list[ToolCall], list[str], list[str]]:
        event_parser = _EVENT_PARSERS.get(self.config.harness)
        if event_parser:
            return event_parser(stdout)
        return self._parse_generic_json(stdout)

    @staticmethod
    def _parse_generic_json(stdout: str) -> tuple[str, list[ToolCall], list[str], list[str]]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout.strip(), [], [], []
        if isinstance(payload, str):
            return payload, [], [], []
        calls = [ToolCall.model_validate(call) for call in payload.get("tool_calls", [])]
        return (str(payload.get("answer", "")), calls, list(payload.get("citations", [])),
                list(payload.get("retrieval_context", [])))