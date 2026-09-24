"""The local `./eval` command."""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from .adapters import CommandAdapter
from .loader import load_cases, load_config
from .metrics import (default_deepeval_metrics, deepeval_test_case, evaluate_with_deepeval,
                      score_case)
from .persistence import save_case


def run(config_path: Path, cases_path: Path, selected_configs: list[str], case_ids: list[str], category: str | None,
    difficulty: str | None, trials: int | None, results_dir: Path, judge: bool = False) -> Path:
    config = load_config(config_path)
    cases = [case for case in load_cases(cases_path) if (not category or case.category == category)
             and (not difficulty or case.difficulty == difficulty)]
    if case_ids:
        cases = [case for case in cases if case.case_id in case_ids]
    if selected_configs:
        unknown = [name for name in selected_configs if name not in config.configs]
        if unknown:
            raise ValueError(f"unknown configuration(s): {', '.join(unknown)}")
        config.configs = {name: config.configs[name] for name in selected_configs}
    missing_commands = {
        harness.name: harness.command[0]
        for harness in config.configs.values()
        if shutil.which(harness.command[0]) is None
    }
    if missing_commands:
        details = ", ".join(f"{command} ({name})" for name, command in missing_commands.items())
        raise RuntimeError(
            f"missing harness command(s): {details}. Install the CLI or run a harness whose command is available."
        )
    run_dir = results_dir / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    total_trials = sum((trials or config.trials) for _ in config.configs for _ in cases)
    print(f"Starting evaluation: {len(config.configs)} configuration(s), {len(cases)} case(s), "
          f"{total_trials} trial(s)", flush=True)
    scores = []
    judge_cases = []
    completed_trials = 0
    for harness in config.configs.values():
        adapter = CommandAdapter(harness)
        for case in cases:
            for trial in range(1, (trials or config.trials) + 1):
                print(f"Running {harness.name}/{case.case_id} trial {trial}/{trials or config.trials}...",
                      flush=True)
                result = adapter.run(case)
                score = score_case(case, result)
                save_case(run_dir, case, result, score, trial)
                scores.append(score.model_dump())
                completed_trials += 1
                print(f"Completed {harness.name}/{case.case_id} trial {trial}/{trials or config.trials} "
                      f"({result.duration_ms:.0f} ms, {completed_trials}/{total_trials})", flush=True)
                if judge:
                    judge_cases.append(deepeval_test_case(case, result))
    if judge and judge_cases:
        print(f"Running judge metrics for {len(judge_cases)} result(s)...", flush=True)
        judge_result = evaluate_with_deepeval(judge_cases, default_deepeval_metrics())
        (run_dir / "deepeval.json").write_text(json.dumps(str(judge_result), indent=2), encoding="utf-8")
        print("Judge metrics completed", flush=True)
    (run_dir / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "comparison.json").write_text(json.dumps(scores, indent=2), encoding="utf-8")
    print(f"Evaluation completed: {run_dir}", flush=True)
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", nargs="?", default="all")
    parser.add_argument("--config", type=Path, default=Path("evals/config.yaml"))
    parser.add_argument("--cases", type=Path, default=Path("evals/cases"))
    parser.add_argument("--category")
    parser.add_argument("--difficulty")
    parser.add_argument("--case", dest="case_ids", action="append", default=[])
    parser.add_argument("--trials", type=int)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--inspect", action="store_true", help="delegate to `deepeval inspect`")
    parser.add_argument("--judge", action="store_true", help="run DeepEval LLM-judge metrics")
    args = parser.parse_args(argv)
    if args.inspect:
        import subprocess
        return subprocess.run(["deepeval", "inspect"], check=False).returncode
    selected_configs = [] if args.configuration == "all" else [args.configuration]
    cases_path = args.cases if args.cases.is_file() else args.cases / "benchmark.yaml"
    try:
        run_dir = run(args.config, cases_path, selected_configs, args.case_ids, args.category,
                      args.difficulty, args.trials, args.results, args.judge)
    except RuntimeError as error:
        parser.error(str(error))
    print(f"Results: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())