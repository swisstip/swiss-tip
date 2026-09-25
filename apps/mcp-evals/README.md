# swisstip-mcp-evals

**Last update:** 24 September 2026

Local benchmark runner for comparing agent harnesses against the Swiss TIP MCP
server. The runner invokes a configured local command, normalizes its JSON
response, records the complete request and MCP trajectory, and writes
deterministic scores under `results/<run-id>/`.

## Setup

```shell
./.venv/bin/python -m pip install -e apps/mcp-evals
```

The command expects each harness to print either plain text or JSON in this
shape:

```json
{
  "answer": "...",
  "tool_calls": [{"name": "search", "arguments": {}, "result": {}}],
  "citations": ["https://..."],
  "retrieval_context": ["quoted evidence"]
}
```

Run all configured harnesses, one trial per case:

```shell
./eval all
./eval opencode-apertus --category simple
./eval codex-gpt --case example --trials 3
```

The offline scores cover fact accuracy, citation precision and recall, tool
selection, unsupported-claim rate, MCP call count and latency. Each case also
stores `answer.txt`, `stdout.txt`, `stderr.txt`, `tool_calls.json`,
`result.json` and `scores.json`.

For LLM-judge metrics, configure the DeepEval model credentials described by
DeepEval and run:

```shell
./eval all --judge
./eval inspect
```

`--judge` runs DeepEval's answer relevancy, faithfulness, contextual precision
and contextual recall metrics. It is intentionally opt-in because it may call
an external judge model; the normal runner is local and credential-free.

Every run writes a self-contained `report.html` next to `summary.json`, with a
mean/failing table per configuration and a per-case table linking to each
case's `answer.txt`. Open it directly in a browser. To regenerate it for an
older run (or after re-scoring) without re-running the harnesses:

```shell
./eval --report results/<run-id>
```

Cases live in `evals/cases/benchmark.yaml` and harness commands in
`evals/config.yaml`. Replace the example case with the pack's reviewed
acceptance questions before using benchmark results for comparison.