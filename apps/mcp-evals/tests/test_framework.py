import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from swisstip.mcp_evals.adapters import CommandAdapter
from swisstip.mcp_evals.loader import load_cases, load_config
from swisstip.mcp_evals.metrics import score_case
from swisstip.mcp_evals.models import AgentResult, EvalCase
from swisstip.mcp_evals.persistence import save_case
from swisstip.mcp_evals.runner import run


class EvaluationFrameworkTests(unittest.TestCase):
    def test_loads_yaml_cases_and_config(self):
        root = Path(__file__).parents[1] / "evals"
        self.assertEqual(load_cases(root / "cases" / "benchmark.yaml")[0].case_id, "example")
        self.assertEqual(load_config(root / "config.yaml").configs["codex-gpt"].model, "gpt-5.5")

    def test_command_adapter_normalizes_json_output(self):
        config = load_config(Path(__file__).parents[1] / "evals" / "config.yaml").configs["codex-gpt"]
        config.command = [sys.executable, "-c", "import json; print(json.dumps({'answer':'ok','tool_calls':[{'name':'search'}]}))"]
        result = CommandAdapter(config).run(EvalCase(case_id="x", question="question"))
        self.assertEqual(result.answer, "ok")
        self.assertEqual(result.tool_calls[0].name, "search")

    def test_scores_claims_citations_tools_and_forbidden_claims(self):
        case = EvalCase(case_id="x", question="q", expected_claims=["Zurich"],
                        required_citations=["official"], expected_tools=["resolve"], must_not_claim=["Bern"])
        result = AgentResult(case_id="x", configuration="test", answer="Zurich", citations=["official"],
                             tool_calls=[{"name": "resolve"}])
        score = score_case(case, result)
        self.assertEqual((score.fact_accuracy, score.citation_recall, score.tool_selection), (1, 1, 1))
        self.assertEqual(score.unsupported_claim_rate, 0)

    def test_persists_debug_artifacts(self):
        case = EvalCase(case_id="x", question="q")
        result = AgentResult(case_id="x", configuration="test", answer="a")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_case(root, case, result, score_case(case, result))
            self.assertEqual(json.loads((root / "cases/x/test/trial-001/result.json").read_text())["answer"], "a")

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