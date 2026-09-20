import json
from pathlib import Path
import tempfile
import unittest

from swisstip.runtime.search_cli import (ProviderFailure, load_cases, percentile, ranking_metrics,
                                        run_benchmark, summarize)
from test_service import sample_release


def case(case_id="sample", expected=None, language="en"):
    return {"case_id": case_id, "language": language, "query": "A novel arrival question",
            "expected_concept_ids": ["deadline"] if expected is None else expected,
            "evidence_fact_ids": ["deadline-1"] if expected is None else []}


def result(mode, ids):
    return {"retrieval_mode": mode, "results": [{"concept_id": cid, "score": 1} for cid in ids],
            "fallback_reason": "provider unavailable" if mode == "lexical-fallback" else None}


class BenchmarkTests(unittest.TestCase):
    def test_multiple_required_concepts_and_duplicate_hits_do_not_inflate_recall(self):
        metrics = ranking_metrics(["a", "b"], ["a", "a", "other", "b"])
        self.assertEqual(metrics["recall_at_3"], 0.5)
        self.assertEqual(metrics["recall_at_5"], 1)
        self.assertFalse(metrics["all_required_at_3"])
        self.assertTrue(metrics["all_required_at_5"])
        self.assertIsNone(metrics["top1_correct"])
        self.assertFalse(ranking_metrics(["a"], ["other", "a"])["top1_correct"])

    def test_unsupported_questions_have_separate_metrics(self):
        empty = ranking_metrics([], [])
        self.assertIsNone(empty["recall_at_5"])
        self.assertIsNone(empty["all_required_at_5"])
        self.assertTrue(empty["unsupported_empty"])
        irrelevant = ranking_metrics([], ["permit-b", "permit-l"])
        self.assertFalse(irrelevant["unsupported_empty"])
        self.assertEqual(irrelevant["unsupported_hit_count"], 2)

    def test_provider_fallback_is_not_a_hybrid_success_even_when_ranking_is_right(self):
        output = run_benchmark([case()], {
            "lexical": lambda query, limit: result("lexical", ["deadline"]),
            "hybrid": lambda query, limit: result("lexical-fallback", ["deadline"]),
        }, repeats=2)
        hybrid = output["aggregates"]["hybrid"]
        self.assertEqual(hybrid["attempted_runs"], 2)
        self.assertEqual(hybrid["successful_runs"], 0)
        self.assertEqual(hybrid["provider_failure_runs"], 2)
        self.assertEqual(hybrid["fallback_runs"], 2)
        self.assertIsNone(hybrid["recall_at_5"])
        self.assertIsNone(hybrid["latency_ms_p50"])
        self.assertEqual(output["aggregates"]["lexical"]["recall_at_5"], 1)
        self.assertEqual(output["samples"][1]["metrics"]["recall_at_5"], 1)

    def test_unexpected_mode_and_duplicate_results_are_errors(self):
        wrong_mode = run_benchmark([case()], {"hybrid": lambda query, limit: result("lexical", ["deadline"])}, 1)
        self.assertEqual(wrong_mode["aggregates"]["hybrid"]["error_runs"], 1)
        self.assertIsNone(wrong_mode["aggregates"]["hybrid"]["recall_at_5"])
        duplicates = run_benchmark([case()], {"lexical": lambda query, limit: result("lexical", ["deadline", "deadline"])}, 1)
        self.assertEqual(duplicates["aggregates"]["lexical"]["error_runs"], 1)

    def test_provider_exceptions_and_cold_unload_failures_are_separate_from_search_errors(self):
        def provider_error(query, limit):
            raise ProviderFailure("offline fake provider")

        output = run_benchmark([case()], {"semantic": provider_error}, 1)
        self.assertEqual(output["aggregates"]["semantic"]["provider_failure_runs"], 1)
        self.assertEqual(output["aggregates"]["semantic"]["error_runs"], 0)

        def cold_error(mode):
            raise ProviderFailure("offline fake unload")

        calls = []
        output = run_benchmark([case()], {"semantic": lambda query, limit: calls.append(query)}, 1, cold_error)
        self.assertEqual(calls, [])
        self.assertEqual(output["aggregates"]["semantic"]["provider_failure_runs"], 1)

    def test_language_aggregates_repeats_and_unsupported_denominators(self):
        questions = [case(), case("negative", [], "zh")]
        questions[1]["query"] = "Unrelated question"
        output = run_benchmark(questions, {
            "lexical": lambda query, limit: result("lexical", [] if query == "Unrelated question" else ["deadline"])
        }, repeats=3)
        aggregate = output["aggregates"]["lexical"]
        self.assertEqual(aggregate["successful_runs"], 6)
        self.assertEqual(aggregate["supported_runs"], 3)
        self.assertEqual(aggregate["unsupported_runs"], 3)
        self.assertEqual(aggregate["recall_at_3"], 1)
        self.assertEqual(aggregate["unsupported_empty_rate"], 1)
        self.assertIsNone(aggregate["by_language"]["zh"]["recall_at_3"])
        self.assertEqual(len(output["cases"]), 2)
        self.assertEqual(output["cases"][0]["modes"]["lexical"]["successful_runs"], 3)
        self.assertTrue(all(sample["latency_ms"] >= 0 for sample in output["samples"]))

    def test_nearest_rank_percentiles_and_empty_summaries(self):
        self.assertEqual(percentile([3, 1, 2, 4], 0.5), 2)
        self.assertEqual(percentile([3, 1, 2, 4], 0.95), 4)
        self.assertIsNone(percentile([], 0.5))
        self.assertIsNone(summarize([])["recall_at_3"])
        with self.assertRaises(ValueError):
            run_benchmark([case()], {"lexical": lambda query, limit: result("lexical", [])}, repeats=11)

    def test_dataset_validation_rejects_wrong_release_and_unsupported_gold(self):
        release = sample_release()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            dataset = {"release_id": release.manifest.release_id, "cases": [case()]}
            path.write_text(json.dumps(dataset), encoding="utf-8")
            self.assertEqual(load_cases(path, release)["cases"][0]["case_id"], "sample")
            for change in ({"expected_concept_ids": ["nonexistent"]}, {"evidence_fact_ids": ["uk-1"]},
                           {"evidence_fact_ids": ["nonexistent"]}, {"expected_concept_ids": [], "evidence_fact_ids": []}):
                dataset["cases"] = [{**case(), **change}]
                path.write_text(json.dumps(dataset), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_cases(path, release)
            dataset["cases"] = [case(), case()]
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path, release)
            dataset["cases"] = [case()]
            dataset["release_id"] = "wrong"
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path, release)
            # Opting in to a later release still checks every identifier.
            self.assertEqual(load_cases(path, release, allow_release_change=True)["release_id"], "wrong")
            dataset["cases"] = [{**case(), "expected_concept_ids": ["nonexistent"]}]
            path.write_text(json.dumps(dataset), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_cases(path, release, allow_release_change=True)


if __name__ == "__main__":
    unittest.main()
