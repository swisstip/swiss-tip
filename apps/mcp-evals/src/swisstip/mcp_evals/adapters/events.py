"""Normalize the structured event streams emitted by local agent harnesses.

Codex (`codex exec --json`) and OpenCode (`opencode run --format json`) both
print one JSON event per line instead of a single JSON object, so the plain
`json.loads(stdout)` used for a generic harness never matches and the judge
would otherwise score the raw event transcript as if it were the answer.
Each parser below reads that JSONL stream and extracts the final natural
language answer, the MCP tool calls, any cited URLs and the tool-result text
DeepEval's contextual metrics can check the answer against.
"""

import json
import re
from typing import Any

from ..models import ToolCall

_URL_RE = re.compile(r"https?://[^\s)\]}\"'>]+")


def _iter_json_lines(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _tool_result_text(result: Any) -> str:
    if not isinstance(result, dict):
        return "" if result is None else str(result)
    parts = [item.get("text", "") for item in result.get("content", []) if isinstance(item, dict)]
    return "\n".join(part for part in parts if part)


def _extract_citations(answer: str) -> list[str]:
    return [url.rstrip(".,;:") for url in _URL_RE.findall(answer)]


def parse_codex_events(stdout: str) -> tuple[str, list[ToolCall], list[str], list[str]]:
    """Parse `codex exec --json` output: one `agent_message`/`mcp_tool_call` item per line."""
    events = _iter_json_lines(stdout)
    if not events:
        return stdout.strip(), [], [], []
    messages: list[str] = []
    calls: list[ToolCall] = []
    context: list[str] = []
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if event.get("type") != "item.completed":
            continue
        if item.get("type") == "agent_message":
            text = item.get("text", "")
            if text:
                messages.append(text)
        elif item.get("type") == "mcp_tool_call":
            result_text = _tool_result_text(item.get("result"))
            calls.append(ToolCall(name=str(item.get("tool", "")), arguments=item.get("arguments") or {},
                                  result=item.get("result")))
            if result_text:
                context.append(result_text)
    answer = messages[-1] if messages else ""
    return answer, calls, _extract_citations(answer), context


def _sandboxed_tool_calls(state: dict[str, Any]) -> list[ToolCall]:
    """Unwrap MCP calls made through OpenCode's `execute` JS sandbox.

    Some models (e.g. Apertus, configured without native function-calling)
    have OpenCode run every MCP tool from inside a generic `execute` tool
    part instead of calling it directly, so `tool.get("tool")` is always
    `"execute"`. The real tool name, arguments and per-call status survive
    under `state.metadata.metadata.toolCalls`; use those instead when present.
    """
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    inner_metadata = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    inner_calls = inner_metadata.get("toolCalls")
    if not isinstance(inner_calls, list):
        return []
    output = state.get("output") if isinstance(state.get("output"), str) else None
    return [ToolCall(name=str(call.get("tool", "")).rsplit(".", 1)[-1], arguments=call.get("input") or {},
                     result=output)
            for call in inner_calls if isinstance(call, dict)]


def parse_opencode_events(stdout: str) -> tuple[str, list[ToolCall], list[str], list[str]]:
    """Parse `opencode run --format json` output: one message-part event per line.

    OpenCode reports parts of type `text` (the assistant's reply) and `tool`
    (an MCP call, with its arguments and output under `state`). A part is
    replayed with updated content as it streams, so only the last snapshot
    per part id is kept.
    """
    events = _iter_json_lines(stdout)
    if not events:
        return stdout.strip(), [], [], []
    texts: dict[str, str] = {}
    tools: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        part = event.get("part")
        if not isinstance(part, dict):
            properties = event.get("properties")
            part = properties.get("part") if isinstance(properties, dict) else None
        if not isinstance(part, dict):
            continue
        part_id = str(part.get("id", part.get("type", len(order))))
        if part_id not in order:
            order.append(part_id)
        if part.get("type") == "text":
            texts[part_id] = part.get("text", "")
        elif part.get("type") == "tool":
            tools[part_id] = part
    answer = "\n".join(texts[part_id] for part_id in order if part_id in texts).strip()
    calls: list[ToolCall] = []
    context: list[str] = []
    for part_id in order:
        tool = tools.get(part_id)
        if not tool:
            continue
        state = tool.get("state") if isinstance(tool.get("state"), dict) else {}
        if state.get("status") not in (None, "completed"):
            continue
        sandboxed = _sandboxed_tool_calls(state)
        output = state.get("output")
        if sandboxed:
            calls.extend(sandboxed)
        else:
            calls.append(ToolCall(name=str(tool.get("tool", "")), arguments=state.get("input") or {}, result=output))
        if isinstance(output, str) and output:
            context.append(output)
    return answer, calls, _extract_citations(answer), context
