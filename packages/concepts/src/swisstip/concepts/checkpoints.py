"""Verified request checkpoints, atomically persisted before parsing a completion."""

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from . import CHECKPOINT_SCHEMA
from .budget import Budget, retry_delay
from .providers.base import (Completion, IdentityError, IncompleteCompletion, InvalidCompletion,
                             ProviderError, ProviderResponseError, enforce_model_identity, strict_json_loads)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path):
    return strict_json_loads(Path(path).read_bytes())


def atomic_write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checkpoint_key(request: dict, context: dict) -> str:
    # Transport settings such as timeout and backoff deliberately do not enter identity.
    fields = ("document_id", "content_sha256", "profile", "model", "generation_settings")
    normalized = {field: context.get(field) for field in fields}
    normalized["version"] = CHECKPOINT_SCHEMA
    return digest(dict(request=request, context=normalized))


def quarantine(path: Path) -> Path:
    destination = path.with_suffix(".invalid.json")
    if destination.exists():
        destination = path.with_suffix(f".{time.time_ns()}.invalid.json")
    path.replace(destination)
    return destination


class CheckpointProvider:
    """A per-document wrapper sharing the job's thread-safe Budget."""

    def __init__(self, provider, checkpoint_dir: Path, context: dict, budget: Budget, *,
                 retries: dict | None = None, fresh_inference: bool = False, log=None,
                 profile_config: dict | None = None, search_dirs=(), sleep=time.sleep):
        self.provider = provider
        self.directory = Path(checkpoint_dir)
        self.context = dict(context)
        self.budget = budget
        self.retries = retries or {}
        self.fresh = fresh_inference
        self.log = log or (lambda message: None)
        self.profile_config = profile_config or {"model": context.get("model")}
        self.search_dirs = [Path(p) for p in search_dirs if Path(p) != self.directory]
        self.sleep = sleep
        self.last_request_key = None
        self.paths = {}
        self.request_context = {}

    def set_request_context(self, **context) -> None:
        self.request_context = context

    def record_review_fallback(self) -> None:
        self.budget.increment("review_fallbacks")

    def mark_invalid(self, key: str, reason: str = "invalid completion") -> None:
        path = self.paths.get(key, self.directory / f"{key}.json")
        if path.exists():
            try:
                quarantine(path)
            except OSError as exc:
                self.budget.stop("Cannot preserve invalid completion")
                raise ProviderError("Cannot preserve invalid completion") from exc
        self.log(f"invalid checkpoint {key}: {reason}")

    def _read(self, path: Path, key: str) -> Completion | None:
        try:
            saved = read_json(path)
            if not isinstance(saved, dict):
                raise ValueError("Checkpoint must be a JSON object")
            stored_hash = saved.pop("sha256")
            if not isinstance(saved.get("request"), dict) or not isinstance(saved.get("context"), dict):
                raise ValueError("Checkpoint request and context must be objects")
            if (saved["schema_version"] != CHECKPOINT_SCHEMA or saved["key"] != key
                    or stored_hash != digest(saved)
                    or checkpoint_key(saved["request"], saved["context"]) != key):
                raise ValueError("Checkpoint integrity mismatch")
            completion = Completion(**saved["completion"])
            if getattr(completion, "awaiting_response", False):
                raise ValueError("An exchange placeholder is not a completion")
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            try:
                quarantine(path)
            except OSError as exc:
                self.budget.stop("Cannot preserve invalid checkpoint")
                raise ProviderError("Cannot preserve invalid checkpoint") from exc
            return None
        enforce_model_identity(completion, self.profile_config)
        self.paths[key] = path
        return completion

    def generate_structured(self, *, system_prompt: str, user_prompt: str, response_schema: dict) -> Completion:
        self.budget.ensure_running()
        request = dict(system_prompt=system_prompt, user_prompt=user_prompt, response_schema=response_schema)
        key = checkpoint_key(request, self.context)
        self.last_request_key = key
        path = self.directory / f"{key}.json"
        if not self.fresh:
            for directory in [self.directory, *self.search_dirs]:
                source = directory / f"{key}.json"
                if source.exists():
                    try:
                        completion = self._read(source, key)
                    except IdentityError as exc:
                        self.budget.increment("identity_mismatches")
                        self.budget.stop(str(exc))
                        raise
                    if completion is not None:
                        self.budget.ensure_running()
                        self.budget.increment("checkpoint_hits")
                        self.log(f"checkpoint hit {key}")
                        return completion
        setter = getattr(self.provider, "set_request_context", None)
        if setter:
            setter(**{**self.context, **self.request_context, "key": key})
        document_id = self.context["document_id"]
        for retry in range(self.retries.get("max_retries", 3) + 1):
            self.budget.reserve(document_id, retry=retry > 0)
            self.log(f"request {key} document {document_id} retry {retry}")
            rejected_completion = None
            try:
                try:
                    completion = self.provider.generate_structured(**request)
                except (IncompleteCompletion, InvalidCompletion) as exc:
                    completion = exc.completion
                except ProviderResponseError as exc:
                    if isinstance(exc, IdentityError) or exc.completion is None:
                        raise
                    # A refusal or tool response still consumed a completion.
                    # Keep its raw content for the audit, but never replay it as
                    # an otherwise valid extraction after a restart.
                    completion = exc.completion
                    rejected_completion = exc
                if getattr(completion, "awaiting_response", False):
                    return completion
                enforce_model_identity(completion, self.profile_config)
            except IdentityError as exc:
                self.budget.increment("identity_mismatches")
                self.budget.stop(str(exc))
                raise
            except ProviderError as exc:
                delay = retry_delay(exc, retry, self.retries)
                if delay is None or retry >= self.retries.get("max_retries", 3):
                    self.budget.stop(str(exc))
                    raise
                self.budget.wait(delay, document_id, sleep=self.sleep, log=self.log)
                continue
            self.budget.record(completion)
            if completion.finish_reason not in ("stop", "eos_token", "stop_sequence"):
                self.budget.increment("incomplete_completions")
            saved = dict(schema_version=CHECKPOINT_SCHEMA, key=key, context=self.context,
                         request=request, completion=asdict(completion))
            destination = path
            if rejected_completion is not None:
                saved["failure"] = str(rejected_completion)
                destination = path.with_suffix(".invalid.json")
                if destination.exists():
                    destination = path.with_suffix(f".{time.time_ns()}.invalid.json")
            try:
                saved["sha256"] = digest(saved)
                atomic_write_json(destination, saved)
            except (OSError, ValueError, TypeError) as exc:
                self.budget.stop("Cannot save model completion; job stopped to preserve paid work")
                raise ProviderError("Cannot save model completion; job stopped to preserve paid work") from exc
            if rejected_completion is not None:
                self.budget.stop(str(rejected_completion))
                raise rejected_completion
            self.paths[key] = path
            return completion
        raise ProviderError("Invalid retry configuration")
