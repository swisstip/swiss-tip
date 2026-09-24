import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from swisstip.concepts.budget import Budget, BudgetExceeded, check_plan, retry_delay
from swisstip.concepts.checkpoints import (CheckpointProvider, atomic_write_json, checkpoint_key,
                                          digest, read_json)
from swisstip.concepts.providers.base import (Completion, IdentityError, InvalidCompletion,
                                             ProviderError, ProviderResponseError, ProviderTransportError)


LIMITS = dict(max_model_requests_per_run=20, max_model_requests_per_page=12,
              max_total_input_characters=1000)
REQUEST = dict(system_prompt="trusted prompt", user_prompt="untrusted input", response_schema={"type": "object"})
CONTEXT = dict(document_id="doc-test", content_sha256="abc", profile="fake", model="model",
               generation_settings={"temperature": 0.0})
COMPLETION = Completion('{"concepts":[]}', "fake", "model", requested_model="model", observed_model="model",
                        prompt_tokens=10, output_tokens=5, request_id="r1", finish_reason="stop")


class ScriptedProvider:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = 0

    def generate_structured(self, **request):
        self.calls += 1
        result = self.responses.pop(0) if self.responses else COMPLETION
        if isinstance(result, Exception):
            raise result
        return result


class BudgetTests(unittest.TestCase):
    def test_offline_plan_refuses_character_and_request_excess(self):
        check_plan(dict(request_count=20, characters=1000), LIMITS)
        for plan in (dict(request_count=21, characters=1000), dict(request_count=1, characters=1001)):
            with self.subTest(plan=plan), self.assertRaises(BudgetExceeded):
                check_plan(plan, LIMITS)

    def test_reservations_are_atomic_between_workers(self):
        budget = Budget(dict(LIMITS, max_model_requests_per_run=3))

        def attempt(i):
            try:
                budget.reserve(f"doc-{i}")
                return True
            except BudgetExceeded:
                return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            accepted = list(pool.map(attempt, range(20)))
        self.assertEqual(sum(accepted), 3)
        self.assertEqual(budget.snapshot()["network_attempts"], 3)

    def test_soft_limits_use_known_tokens_and_elapsed_time(self):
        now = [10.0]
        budget = Budget(dict(LIMITS, max_prompt_tokens_per_run=10), clock=lambda: now[0])
        budget.record(replace(COMPLETION, prompt_tokens=None))
        budget.record(COMPLETION)
        self.assertIsNone(budget.snapshot()["prompt_tokens"])
        self.assertEqual(budget.snapshot()["recorded_prompt_tokens"], 10)
        with self.assertRaisesRegex(BudgetExceeded, "token"):
            budget.reserve("doc")
        budget = Budget(LIMITS, max_minutes=1, clock=lambda: now[0])
        now[0] = 71
        with self.assertRaisesRegex(BudgetExceeded, "Wall-clock"):
            budget.reserve("doc")

    def test_retry_after_seconds_date_and_refusal(self):
        settings = dict(backoff_seconds=2, max_backoff_seconds=30, max_retry_after_seconds=300)
        self.assertEqual(retry_delay(ProviderError("slow", status_code=429, retry_after="19"), 0, settings), 19)
        now = datetime(2026, 9, 13, tzinfo=UTC)
        error = ProviderError("slow", status_code=503, retry_after="Sun, 13 Sep 2026 00:00:20 GMT")
        self.assertEqual(retry_delay(error, 0, settings, now=now), 20)
        for error in (ProviderError("bad", status_code=400),
                      ProviderError("slow", status_code=429, retry_after="301"),
                      ProviderError("slow", status_code=429, retry_after="inf")):
            self.assertIsNone(retry_delay(error, 0, settings))
        self.assertEqual(retry_delay(ProviderTransportError("timeout"), 0, settings), 2)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wrapper(self, provider=None, budget=None, **kwargs):
        return CheckpointProvider(provider or ScriptedProvider(), self.root, CONTEXT,
                                  budget or Budget(LIMITS), **kwargs)

    def test_roundtrip_second_call_uses_disk_and_timeout_is_not_identity(self):
        provider = ScriptedProvider()
        first = self.wrapper(provider)
        self.assertEqual(first.generate_structured(**REQUEST), COMPLETION)
        key = first.last_request_key
        self.assertTrue((self.root / f"{key}.json").exists())
        second = self.wrapper(provider)
        self.assertEqual(second.generate_structured(**REQUEST), COMPLETION)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.budget.snapshot()["checkpoint_hits"], 1)
        self.assertEqual(checkpoint_key(REQUEST, CONTEXT), checkpoint_key(REQUEST, dict(CONTEXT, timeout_seconds=900)))
        second.generate_structured(**dict(REQUEST, system_prompt="changed"))
        self.assertEqual(provider.calls, 2)

    def test_different_snapshot_generation_and_profile_change_identity(self):
        original = checkpoint_key(REQUEST, CONTEXT)
        for field, value in (("content_sha256", "changed"), ("profile", "other"),
                             ("generation_settings", {"temperature": 1})):
            self.assertNotEqual(original, checkpoint_key(REQUEST, dict(CONTEXT, **{field: value})))

    def test_invalid_json_is_saved_before_validation_then_quarantined(self):
        bad = replace(COMPLETION, content="invalid JSON")
        provider = ScriptedProvider([InvalidCompletion("invalid", completion=bad), COMPLETION])
        wrapper = self.wrapper(provider)
        self.assertEqual(wrapper.generate_structured(**REQUEST), bad)
        key = wrapper.last_request_key
        self.assertEqual(read_json(self.root / f"{key}.json")["completion"]["content"], "invalid JSON")
        wrapper.mark_invalid(key, "invalid JSON")
        self.assertTrue((self.root / f"{key}.invalid.json").exists())
        self.assertEqual(wrapper.generate_structured(**REQUEST), COMPLETION)
        self.assertEqual(provider.calls, 2)

    def test_tampered_checkpoint_not_used_but_wrong_model_is_fatal(self):
        wrapper = self.wrapper()
        wrapper.generate_structured(**REQUEST)
        path = self.root / f"{wrapper.last_request_key}.json"
        saved = read_json(path)
        saved["completion"]["content"] = "changed without digest"
        atomic_write_json(path, saved)
        provider = ScriptedProvider()
        self.wrapper(provider).generate_structured(**REQUEST)
        self.assertEqual(provider.calls, 1)
        saved = read_json(path)
        saved["completion"]["observed_model"] = "substituted-model"
        saved.pop("sha256")
        saved["sha256"] = digest(saved)
        atomic_write_json(path, saved)
        with self.assertRaises(IdentityError):
            self.wrapper(provider).generate_structured(**REQUEST)
        self.assertEqual(provider.calls, 1)

    def test_identity_mismatch_is_never_checkpointed(self):
        provider = ScriptedProvider([replace(COMPLETION, observed_model="wrong")])
        wrapper = self.wrapper(provider)
        with self.assertRaises(IdentityError):
            wrapper.generate_structured(**REQUEST)
        self.assertFalse(list(self.root.glob("*.json")))
        self.assertEqual(wrapper.budget.snapshot()["network_attempts"], 1)
        self.assertEqual(wrapper.budget.snapshot()["identity_mismatches"], 1)

    def test_retry_is_counted_honours_header_and_refuses_beyond_cap(self):
        provider = ScriptedProvider([ProviderError("slow", status_code=429, retry_after="20"), COMPLETION])
        sleeps = []
        wrapper = self.wrapper(provider, retries=dict(max_retries=2), sleep=sleeps.append)
        wrapper.generate_structured(**REQUEST)
        self.assertEqual(sleeps, [15, 5])
        self.assertEqual(wrapper.budget.snapshot()["network_attempts"], 2)
        self.assertEqual(wrapper.budget.snapshot()["retry_attempts"], 1)
        self.assertEqual(wrapper.budget.snapshot()["prompt_tokens"], 10)
        limited = Budget(dict(LIMITS, max_model_requests_per_run=1))
        provider = ScriptedProvider([ProviderError("slow", status_code=429), COMPLETION])
        wrapper = self.wrapper(provider, limited, fresh_inference=True, sleep=sleeps.append)
        with self.assertRaises(BudgetExceeded):
            wrapper.generate_structured(**REQUEST)
        self.assertEqual(provider.calls, 1)

    def test_write_failure_stops_all_workers(self):
        wrapper = self.wrapper()
        with patch("swisstip.concepts.checkpoints.atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ProviderError, "Cannot save"):
                wrapper.generate_structured(**REQUEST)
        with self.assertRaises(BudgetExceeded):
            wrapper.budget.reserve("another-doc")
        self.assertEqual(wrapper.budget.snapshot()["prompt_tokens"], 10)

    def test_exchange_placeholders_never_enter_checkpoints(self):
        provider = ScriptedProvider([replace(COMPLETION, awaiting_response=True, observed_model=None)])
        wrapper = self.wrapper(provider)
        self.assertTrue(wrapper.generate_structured(**REQUEST).awaiting_response)
        self.assertFalse(list(self.root.glob("*.json")))
        self.assertEqual(wrapper.budget.snapshot()["prompt_tokens"], 0)

    def test_malformed_checkpoint_shapes_are_quarantined(self):
        key = checkpoint_key(REQUEST, CONTEXT)
        path = self.root / f"{key}.json"
        for malformed in (None, [], "text", {"sha256": "bad", "request": REQUEST, "context": None}):
            with self.subTest(malformed=malformed):
                atomic_write_json(path, malformed)
                provider = ScriptedProvider()
                wrapper = self.wrapper(provider)
                self.assertEqual(wrapper.generate_structured(**REQUEST), COMPLETION)
                self.assertEqual(provider.calls, 1)
        self.assertEqual(len(list(self.root.glob("*.invalid.json"))), 4)

    def test_failed_corrupt_checkpoint_quarantine_stops_every_worker(self):
        key = checkpoint_key(REQUEST, CONTEXT)
        path = self.root / f"{key}.json"
        atomic_write_json(path, None)
        provider = ScriptedProvider()
        wrapper = self.wrapper(provider)
        with patch("swisstip.concepts.checkpoints.quarantine", side_effect=OSError("access denied")):
            with self.assertRaisesRegex(ProviderError, "Cannot preserve"):
                wrapper.generate_structured(**REQUEST)
        self.assertEqual(provider.calls, 0)
        with self.assertRaises(BudgetExceeded):
            wrapper.budget.reserve("another-document")

    def test_other_worker_stop_blocks_checkpoint_hits(self):
        self.wrapper().generate_structured(**REQUEST)
        provider = ScriptedProvider()
        wrapper = self.wrapper(provider)
        wrapper.budget.stop("Other worker failed")
        with self.assertRaisesRegex(BudgetExceeded, "Other worker failed"):
            wrapper.generate_structured(**REQUEST)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(wrapper.budget.snapshot()["checkpoint_hits"], 0)

    def test_refused_completion_is_kept_invalid_before_job_stop(self):
        refusal = ProviderResponseError("Provider returned a refusal or tool call", completion=COMPLETION)
        provider = ScriptedProvider([refusal])
        wrapper = self.wrapper(provider)
        with self.assertRaisesRegex(ProviderResponseError, "refusal"):
            wrapper.generate_structured(**REQUEST)
        key = wrapper.last_request_key
        self.assertFalse((self.root / f"{key}.json").exists())
        saved = read_json(self.root / f"{key}.invalid.json")
        self.assertEqual(saved["completion"]["content"], COMPLETION.content)
        self.assertIn("refusal", saved["failure"])
        self.assertEqual(wrapper.budget.snapshot()["prompt_tokens"], 10)
        with self.assertRaises(BudgetExceeded):
            wrapper.budget.reserve("another-document")
        # The valid-looking content of the refusal is never a checkpoint hit.
        next_provider = ScriptedProvider()
        self.assertEqual(self.wrapper(next_provider).generate_structured(**REQUEST), COMPLETION)
        self.assertEqual(next_provider.calls, 1)

    def test_serialization_failure_stops_all_workers(self):
        wrapper = self.wrapper(ScriptedProvider([replace(COMPLETION, content="\ud800")]))
        with self.assertRaisesRegex(ProviderError, "Cannot save"):
            wrapper.generate_structured(**REQUEST)
        with self.assertRaises(BudgetExceeded):
            wrapper.budget.reserve("another-document")


if __name__ == "__main__":
    unittest.main()
