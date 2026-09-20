"""Shared request ceilings and bounded retries; no provider is created here."""

import math
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .providers.base import ProviderError

TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


class BudgetExceeded(ProviderError):
    """The job cannot make another request within its declared ceilings."""


def check_plan(plan: dict, limits: dict) -> None:
    """Reject an oversized plan before any provider is constructed."""
    if plan["request_count"] > limits["max_model_requests_per_run"]:
        raise BudgetExceeded(f"Plan needs {plan['request_count']} requests; ceiling is "
                             f"{limits['max_model_requests_per_run']}")
    if plan["characters"] > limits["max_total_input_characters"]:
        raise BudgetExceeded(f"Plan contains {plan['characters']} input characters; ceiling is "
                             f"{limits['max_total_input_characters']}")


def retry_delay(error: ProviderError, retry: int, settings: dict, *, now=None) -> float | None:
    """None means refuse retry (including an excessive Retry-After)."""
    status = getattr(error, "status_code", None)
    if status not in TRANSIENT_HTTP and not getattr(error, "transient", False):
        return None
    delay = min(settings.get("max_backoff_seconds", 30), settings.get("backoff_seconds", 2) * 2 ** retry)
    header = getattr(error, "retry_after", None)
    if header is not None:
        try:
            requested = float(header)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(str(header))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=UTC)
                requested = (date - (now or datetime.now(UTC))).total_seconds()
            except (TypeError, ValueError, OverflowError):
                requested = 0
        if not math.isfinite(requested) or requested > settings.get("max_retry_after_seconds", 300):
            return None
        delay = max(delay, requested)
    return max(0, delay)


class Budget:
    """One lock protects the run budget across all document workers."""

    def __init__(self, limits: dict, *, max_minutes: float | None = None, clock=time.monotonic):
        self.limits = dict(limits)
        self.max_minutes = max_minutes
        self.clock = clock
        self.started = clock()
        self.lock = threading.RLock()
        self.stopped_reason = None
        self.attempts_by_document = {}
        self.stats = dict(network_attempts=0, retry_attempts=0, checkpoint_hits=0,
                          incomplete_completions=0, review_fallbacks=0, identity_mismatches=0,
                          prompt_tokens=0, output_tokens=0, recorded_prompt_tokens=0,
                          recorded_output_tokens=0, completions_without_usage=0)

    def _check(self, document_id: str) -> None:
        if self.stopped_reason:
            raise BudgetExceeded(self.stopped_reason)
        if self.stats["network_attempts"] >= self.limits["max_model_requests_per_run"]:
            raise BudgetExceeded("Model request ceiling reached (including failed attempts and retries)")
        if self.attempts_by_document.get(document_id, 0) >= self.limits["max_model_requests_per_page"]:
            raise BudgetExceeded(f"Model request ceiling reached for {document_id}")
        tokens = self.limits.get("max_prompt_tokens_per_run")
        if tokens is not None and self.stats["recorded_prompt_tokens"] >= tokens:
            raise BudgetExceeded("Prompt token ceiling reached")
        if self.max_minutes is not None and self.clock() - self.started >= self.max_minutes * 60:
            raise BudgetExceeded("Wall-clock ceiling reached")

    def available(self, document_id: str) -> None:
        with self.lock:
            try:
                self._check(document_id)
            except BudgetExceeded as exc:
                self.stop(str(exc))
                raise

    def ensure_running(self) -> None:
        """Stop cached work too after another worker has made the job fail."""
        with self.lock:
            if self.stopped_reason:
                raise BudgetExceeded(self.stopped_reason)

    def reserve(self, document_id: str, *, retry: bool = False) -> None:
        with self.lock:
            try:
                self._check(document_id)
            except BudgetExceeded as exc:
                self.stop(str(exc))
                raise
            self.stats["network_attempts"] += 1
            self.stats["retry_attempts"] += int(retry)
            self.attempts_by_document[document_id] = self.attempts_by_document.get(document_id, 0) + 1

    def stop(self, reason: str) -> None:
        with self.lock:
            self.stopped_reason = self.stopped_reason or reason

    def increment(self, name: str, amount: int = 1) -> None:
        with self.lock:
            self.stats[name] = self.stats.get(name, 0) + amount

    def record(self, completion) -> None:
        with self.lock:
            missing = False
            for field in ("prompt_tokens", "output_tokens"):
                value = getattr(completion, field)
                if value is None:
                    self.stats[field] = None
                    missing = True
                else:
                    self.stats[f"recorded_{field}"] += value
                    if self.stats[field] is not None:
                        self.stats[field] += value
            self.stats["completions_without_usage"] += int(missing)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.stats, elapsed_seconds=round(self.clock() - self.started, 3),
                        stopped_reason=self.stopped_reason,
                        attempts_by_document=dict(self.attempts_by_document),
                        token_accounting="completed_responses_only; failed_attempt_usage_unknown")

    def wait(self, seconds: float, document_id: str, *, sleep=time.sleep, log=lambda message: None) -> None:
        while seconds > 0:
            self.available(document_id)
            interval = min(seconds, 15)
            sleep(interval)
            seconds -= interval
            log(f"Retry wait: {seconds:g} seconds remaining")
