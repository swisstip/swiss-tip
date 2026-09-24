import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from swisstip.mcp_evals.adapters import CommandAdapter
from swisstip.mcp_evals.adapters.events import parse_codex_events, parse_opencode_events
from swisstip.mcp_evals.loader import load_cases, load_config
from swisstip.mcp_evals.metrics import _expected_output, judge_result_to_dict, merge_judge_scores, score_case
from swisstip.mcp_evals.models import AgentResult, EvalCase, HarnessConfig
from swisstip.mcp_evals.persistence import save_case
from swisstip.mcp_evals.runner import run


class EvaluationFrameworkTests(unittest.TestCase):
    def test_loads_yaml_cases_and_config(self):
        root = Path(__file__).parents[1] / "evals"
        self.assertEqual(load_cases(root / "cases" / "residence-permits.yaml")[0].case_id, "UAT-2e")
        config = load_config(root / "config.yaml")
        self.assertEqual(config.configs["codex-gpt-5-5"].model, "gpt-5.5")
        self.assertEqual(len(config.case_files), 4)

    def test_command_adapter_normalizes_generic_json_output(self):
        config = HarnessConfig(name="test", harness="test", model="test",
                               command=[sys.executable, "-c",
                                        "import json; print(json.dumps({'answer':'ok','tool_calls':[{'name':'search'}]}))"])
        result = CommandAdapter(config).run(EvalCase(case_id="x", question="question"))
        self.assertEqual(result.answer, "ok")
        self.assertEqual(result.tool_calls[0].name, "search")

    def test_command_adapter_normalizes_codex_jsonl_output(self):
        config = load_config(Path(__file__).parents[1] / "evals" / "config.yaml").configs["codex-gpt-5-5"]
        script = ("import json, sys\n"
                  "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'draft'}}))\n"
                  "print(json.dumps({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'tool': 'resolve', "
                  "'arguments': {'concept_ids': ['x']}, "
                  "'result': {'content': [{'type': 'text', 'text': 'evidence'}]}}}))\n"
                  "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', "
                  "'text': 'Final answer, see https://example.org/page.'}}))\n")
        config.command = [sys.executable, "-c", script]
        result = CommandAdapter(config).run(EvalCase(case_id="x", question="question"))
        self.assertEqual(result.answer, "Final answer, see https://example.org/page.")
        self.assertEqual(result.tool_calls[0].name, "resolve")
        self.assertEqual(result.citations, ["https://example.org/page"])
        self.assertEqual(result.retrieval_context, ["evidence"])

    def test_parse_codex_events_falls_back_to_raw_text_when_not_json(self):
        answer, calls, citations, context = parse_codex_events("plain text reply")
        self.assertEqual((answer, calls, citations, context), ("plain text reply", [], [], []))

    def test_parse_opencode_events_extracts_text_and_tool_parts(self):
        stdout = "\n".join([
            json.dumps({"part": {"id": "p1", "type": "tool", "tool": "resolve",
                                 "state": {"status": "completed", "input": {"concept_ids": ["x"]},
                                           "output": "evidence"}}}),
            json.dumps({"part": {"id": "p2", "type": "text", "text": "Final answer, see https://example.org/page."}}),
        ])
        answer, calls, citations, context = parse_opencode_events(stdout)
        self.assertEqual(answer, "Final answer, see https://example.org/page.")
        self.assertEqual(calls[0].name, "resolve")
        self.assertEqual(citations, ["https://example.org/page"])
        self.assertEqual(context, ["evidence"])

    def test_parse_opencode_events_unwraps_execute_sandboxed_tool_calls(self):
        # Apertus (configured without native function-calling) has OpenCode run every MCP
        # call from inside a generic `execute` JS sandbox; the real tool lives under
        # state.metadata.metadata.toolCalls, not the top-level `tool` field.
        stdout = json.dumps({"part": {"id": "p1", "type": "tool", "tool": "execute",
                                      "state": {"status": "completed", "input": {"code": "..."},
                                                "output": "evidence",
                                                "metadata": {"metadata": {"toolCalls": [
                                                    {"tool": "swisstip.resolve", "status": "completed",
                                                     "input": {"concept_ids": ["x"]}}]}}}}})
        _, calls, _, context = parse_opencode_events(stdout)
        self.assertEqual(calls[0].name, "resolve")
        self.assertEqual(calls[0].arguments, {"concept_ids": ["x"]})
        self.assertEqual(context, ["evidence"])

    def test_scores_claims_citations_tools_and_forbidden_claims(self):
        case = EvalCase(case_id="x", question="q", expected_claims=["Zurich"],
                        required_citations=["official"], expected_tools=["resolve"], must_not_claim=["Bern"])
        result = AgentResult(case_id="x", configuration="test", answer="Zurich", citations=["official"],
                             tool_calls=[{"name": "resolve"}])
        score = score_case(case, result)
        self.assertEqual((score.fact_accuracy, score.citation_recall, score.tool_selection), (None, 1, 1))
        self.assertEqual(score.unsupported_claim_rate, 0)

    def test_deepeval_case_includes_expected_claims_for_semantic_judge(self):
        case = EvalCase(case_id="x", question="q", expected_answer="Expected answer",
                        expected_claims=["The move is reported within 14 days."])
        result = AgentResult(case_id="x", configuration="test", answer="Report it within two weeks.")

        expected_output = _expected_output(case)
        self.assertIn("Expected answer", expected_output)
        self.assertIn("The move is reported within 14 days.", expected_output)

    def test_persists_debug_artifacts(self):
        case = EvalCase(case_id="x", question="q")
        result = AgentResult(case_id="x", configuration="test", answer="a")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_case(root, case, result, score_case(case, result))
            self.assertEqual(json.loads((root / "cases/x/test/trial-001/result.json").read_text())["answer"], "a")

    def test_merge_judge_scores_attaches_metrics_by_metadata(self):
        from dataclasses import dataclass

        @dataclass
        class FakeMetric:
            name: str
            score: float
            success: bool
            reason: str
            threshold: float = 0.5

        @dataclass
        class FakeTestResult:
            name: str
            success: bool
            metadata: dict
            metrics_data: list

        @dataclass
        class FakeJudgeResult:
            test_results: list

        case = EvalCase(case_id="x", question="q", expected_claims=["a claim not present"])
        result = AgentResult(case_id="x", configuration="test", answer="a")
        score = score_case(case, result)
        self.assertIsNone(score.fact_accuracy)

        judge_result = FakeJudgeResult(test_results=[
            FakeTestResult(name="test_case_0", success=True, metadata={"case_id": "x", "configuration": "test"},
                           metrics_data=[FakeMetric(name="Fact Accuracy [GEval]", score=1.0, success=True,
                                                    reason="covers the claim's meaning"),
                                        FakeMetric(name="Answer Relevancy", score=0.9, success=True, reason="ok")]),
        ])
        merge_judge_scores([score], judge_result)

        self.assertEqual(score.fact_accuracy, 1.0)
        self.assertEqual(score.answer_relevancy, 0.9)

        rendered = judge_result_to_dict(judge_result)
        self.assertEqual(rendered[0]["metrics_data"][0]["score"], 1.0)

    def test_runner_reports_progress(self):
        command = json.dumps([sys.executable, "-c",
                              "import json; print(json.dumps({'answer': 'ok'}))"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.yaml"
            config_path.write_text(
                f"configs:\n  test:\n    harness: test\n    model: test\n    command: {command}\n"
                "benchmark:\n  trials: 1\n  concurrency: 1\n",
                encoding="utf-8")
            cases_path = root / "cases.yaml"
            cases_path.write_text("cases:\n  - case_id: example\n    question: question\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                run(config_path, cases_path, [], [], None, None, None, root / "results")

        messages = output.getvalue()
        self.assertIn("Starting evaluation: 1 configuration(s), 1 case(s), 1 trial(s)", messages)
        self.assertIn("Running test/example trial 1/1...", messages)
        self.assertIn("Completed test/example trial 1/1", messages)
        self.assertIn("Evaluation completed:", messages)


if __name__ == "__main__":
    unittest.main()