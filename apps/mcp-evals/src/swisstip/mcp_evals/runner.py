"""The local `./eval` command."""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from .adapters import CommandAdapter
from .html_report import write_html_report
from .loader import load_cases, load_config
from .metrics import (default_deepeval_metrics, deepeval_test_case, evaluate_with_deepeval,
                      judge_result_to_dict, merge_judge_scores, score_case)
from .models import AgentResult, CaseScore, EvalCase
from .persistence import save_case, save_score
from .report import format_means, format_summary, summarize_scores


def run(config_path: Path, cases_path: Path, selected_configs: list[str], case_ids: list[str], category: str | None,
    difficulty: str | None, trials: int | None, results_dir: Path, judge: bool = False,
    threshold: float = 0.5, judge_concurrency: int = 3, judge_throttle: float = 0) -> Path:
    config = load_config(config_path)
    if cases_path.is_file():
        loaded_cases = load_cases(cases_path)
    else:
        case_files = config.case_files or ["benchmark.yaml"]
        loaded_cases = [case for case_file in case_files for case in load_cases(cases_path / case_file)]
    cases = [case for case in loaded_cases if (not category or case.category == category)
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
    trial_records = []
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
                scores.append(score)
                trial_records.append((case, harness.name, trial))
                completed_trials += 1
                print(f"Completed {harness.name}/{case.case_id} trial {trial}/{trials or config.trials} "
                      f"({result.duration_ms:.0f} ms, {completed_trials}/{total_trials})", flush=True)
                if judge:
                    judge_cases.append(deepeval_test_case(case, result))
    if judge and judge_cases:
        print(f"Running judge metrics for {len(judge_cases)} result(s)...", flush=True)
        judge_result = evaluate_with_deepeval(judge_cases, default_deepeval_metrics(),
                                              max_concurrent=judge_concurrency, throttle_value=judge_throttle)
        merge_judge_scores(scores, judge_result)
        for score, (case, configuration, trial) in zip(scores, trial_records):
            save_score(run_dir, case, configuration, score, trial)
        (run_dir / "deepeval.json").write_text(json.dumps(judge_result_to_dict(judge_result), indent=2),
                                               encoding="utf-8")
        print("Judge metrics completed", flush=True)
    (run_dir / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "comparison.json").write_text(json.dumps([score.model_dump() for score in scores], indent=2),
                                             encoding="utf-8")
    summary = summarize_scores(scores, threshold)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_html_report(run_dir, summary, scores)
    print(format_means(summary), flush=True)
    print(format_summary(summary), flush=True)
    print(f"Evaluation completed: {run_dir}", flush=True)
    return run_dir


def rejudge(run_dir: Path, threshold: float = 0.5, judge_concurrency: int = 3,
           judge_throttle: float = 0) -> Path:
    """Re-run only the DeepEval judge metrics against a completed run's saved trials, without
    re-invoking the harnesses. Useful after a judge failure (e.g. a rate limit) left `request.json`
    and `result.json` on disk but no judge scores."""
    trial_dirs = sorted((run_dir / "cases").glob("*/*/trial-*"))
    if not trial_dirs:
        raise RuntimeError(f"no saved trials found under {run_dir / 'cases'}")
    cases_and_results = []
    scores = []
    for trial_dir in trial_dirs:
        case = EvalCase(**json.loads((trial_dir / "request.json").read_text(encoding="utf-8")))
        result = AgentResult(**json.loads((trial_dir / "result.json").read_text(encoding="utf-8")))
        score = CaseScore(**json.loads((trial_dir / "scores.json").read_text(encoding="utf-8")))
        cases_and_results.append((case, result, trial_dir))
        scores.append(score)
    print(f"Running judge metrics for {len(cases_and_results)} saved trial(s)...", flush=True)
    judge_cases = [deepeval_test_case(case, result) for case, result, _ in cases_and_results]
    judge_result = evaluate_with_deepeval(judge_cases, default_deepeval_metrics(),
                                          max_concurrent=judge_concurrency, throttle_value=judge_throttle)
    merge_judge_scores(scores, judge_result)
    for score, (_, _, trial_dir) in zip(scores, cases_and_results):
        (trial_dir / "scores.json").write_text(json.dumps(score.model_dump(), indent=2), encoding="utf-8")
    (run_dir / "deepeval.json").write_text(json.dumps(judge_result_to_dict(judge_result), indent=2),
                                           encoding="utf-8")
    (run_dir / "comparison.json").write_text(json.dumps([score.model_dump() for score in scores], indent=2),
                                             encoding="utf-8")
    summary = summarize_scores(scores, threshold)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_html_report(run_dir, summary, scores)
    print("Judge metrics completed", flush=True)
    print(format_means(summary), flush=True)
    print(format_summary(summary), flush=True)
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
    parser.add_argument("--judge-concurrency", type=int, default=3,
                        help="max parallel DeepEval judge calls, to stay under the judge model's "
                             "rate limit (default: 3)")
    parser.add_argument("--judge-throttle", type=float, default=0,
                        help="seconds to wait between starting DeepEval judge calls (default: 0)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="score below which a case counts as failing in summary.json (default: 0.5)")
    parser.add_argument("--report", type=Path,
                        help="regenerate report.html for an existing run directory (reads its summary.json "
                             "and comparison.json) and exit")
    parser.add_argument("--rejudge", type=Path,
                        help="re-run DeepEval judge metrics for an existing run directory (reads its saved "
                             "cases/*/*/trial-*/request.json and result.json), overwrite its scores, and exit")
    args = parser.parse_args(argv)
    if args.inspect:
        import subprocess
        return subprocess.run(["deepeval", "inspect"], check=False).returncode
    if args.rejudge:
        try:
            rejudge(args.rejudge, args.threshold, args.judge_concurrency, args.judge_throttle)
        except RuntimeError as error:
            parser.error(str(error))
        return 0
    if args.report:
        summary = json.loads((args.report / "summary.json").read_text(encoding="utf-8"))
        scores = [CaseScore(**row) for row in json.loads((args.report / "comparison.json").read_text(encoding="utf-8"))]
        path = write_html_report(args.report, summary, scores)
        print(f"Report: {path}")
        return 0
    selected_configs = [] if args.configuration == "all" else [args.configuration]
    cases_path = args.cases
    try:
        run_dir = run(args.config, cases_path, selected_configs, args.case_ids, args.category,
                      args.difficulty, args.trials, args.results, args.judge, args.threshold,
                      args.judge_concurrency, args.judge_throttle)
    except RuntimeError as error:
        parser.error(str(error))
    print(f"Results: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())