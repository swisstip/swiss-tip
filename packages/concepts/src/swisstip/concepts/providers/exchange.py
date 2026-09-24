"""Assistant request/response files, sharing request identity with checkpoints."""

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import threading

from .base import (Completion, IdentityError, MAX_BYTES, ProviderError, ProviderResponseError,
                   check_content, enforce_model_identity, optional_count, required_string, strict_json_loads)

REQUEST_SCHEMA = "swisstip.assistant-extraction-request/v1"
RESPONSE_SCHEMA = "swisstip.assistant-extraction-response/v1"


class MissingExchangeResponse(ProviderError):
    """A file-exchange response is missing in fail mode."""


def _read(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Exchange file exceeded the 1 MB limit")
    value = strict_json_loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Exchange file must contain a JSON object")
    return value


def _response_completion(response, *, model, provider, key):
    if response.get("key", key) != key:
        raise ProviderResponseError("Exchange response key does not match the request")
    if response.get("schema_version", RESPONSE_SCHEMA) != RESPONSE_SCHEMA:
        raise ProviderResponseError("Unsupported exchange response schema")
    content = response.get("content")
    if not isinstance(content, str):
        raise ProviderResponseError("Exchange response content must be a string")
    if response.get("provider", provider) != provider:
        raise IdentityError("Exchange response provider does not match the selected profile")
    observed = response.get("observed_model", response.get("model"))
    finish_reason = response.get("finish_reason", "stop")
    completion = Completion(
        content=content, provider=provider, model=model,
        requested_model=f"{model}:{provider}",
        observed_model=observed if isinstance(observed, str) and observed else None,
        prompt_tokens=optional_count(response.get("prompt_tokens")),
        output_tokens=optional_count(response.get("output_tokens")),
        request_id=response.get("request_id") if isinstance(response.get("request_id"), str) else "assistant-" + key[:16],
        finish_reason=finish_reason if isinstance(finish_reason, str) and finish_reason else None)
    enforce_model_identity(completion, {"provider": provider, "model": model, "adapter": "exchange"})
    check_content(completion)
    return completion


class ExchangeProvider:
    adapter = "exchange"

    def __init__(self, exchange_dir, *, model="claude-fable-5-1", provider="anthropic-assistant", on_missing="capture"):
        if on_missing not in {"capture", "fail"}:
            raise ValueError("on_missing must be capture or fail")
        self.directory = Path(exchange_dir)
        self.model = required_string("model", model)
        self.provider = required_string("provider", provider)
        self.on_missing = on_missing
        self._local = threading.local()

    def set_request_context(self, **context):
        self._local.context = dict(context)

    @property
    def last_request_key(self):
        return getattr(self._local, "key", None)

    def generate_structured(self, *, system_prompt, user_prompt, response_schema):
        from ..checkpoints import atomic_write_json, checkpoint_key
        request = dict(system_prompt=system_prompt, user_prompt=user_prompt, response_schema=dict(response_schema))
        context = getattr(self._local, "context", {})
        key = context.get("key") or checkpoint_key(request, dict(context, model=self.model))
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Exchange request key must be a SHA-256 digest")
        self._local.key = key
        try:
            payload = strict_json_loads(user_prompt)
        except (ValueError, UnicodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        properties = response_schema.get("properties", {})
        kind = context.get("kind") or ("review" if "verdicts" in properties else "basis" if "bases" in properties else "extraction")
        if kind not in {"extraction", "review", "basis"}:
            raise ValueError("Exchange request kind must be extraction, review or basis")
        page = payload.get({"review": "untrusted_review", "basis": "untrusted_basis"}.get(kind, "untrusted_page"), {})
        if not isinstance(page, dict):
            page = {}
        request_path = self.directory / "requests" / f"{key}.json"
        response_path = self.directory / "responses" / f"{key}.json"
        proposal_count = context.get("proposal_count", len(page.get("proposals", [])) if kind != "extraction" else None)
        captured = dict(schema_version=REQUEST_SCHEMA, key=key, kind=kind,
                        document_id=context.get("document_id", page.get("document_id")),
                        title=context.get("title", page.get("title")),
                        chunk_index=context.get("chunk_index", page.get("chunk_index")),
                        chunk_count=context.get("chunk_count", page.get("chunk_count")),
                        proposal_count=proposal_count, captured_at=datetime.now(UTC).isoformat(),
                        model=self.model, provider=self.provider, context={k: v for k, v in context.items() if k != "key"}, **request)
        # A response copied in before capture still gets an auditable request.
        if not request_path.exists():
            if len(json.dumps(captured, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
                raise ProviderError("Exchange request exceeded the 1 MB limit")
            atomic_write_json(request_path, captured)
        (self.directory / "responses").mkdir(parents=True, exist_ok=True)
        if response_path.exists():
            try:
                response = _read(response_path)
            except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                raise ProviderResponseError("Cannot read exchange response") from exc
            return _response_completion(response, model=self.model, provider=self.provider, key=key)
        if self.on_missing == "fail":
            raise MissingExchangeResponse(f"Assistant completion missing for {kind} request {key[:16]}")
        if kind == "review":
            content = {"verdicts": [{"review_id": index, "decision": "uncertain", "issue": "insufficient_context",
                                     "reason": "Placeholder until the assistant review is authored."}
                                    for index in range(1, (proposal_count or 0) + 1)]}
        elif kind == "basis":
            content = {"bases": [{"basis_id": index, "kind": "guidance", "level": "federal", "norm": "", "refers_to": "",
                                  "reason": "Placeholder until the assistant classification is authored."}
                                 for index in range(1, (proposal_count or 0) + 1)]}
        else:
            content = {"concepts": []}
        return Completion(content=json.dumps(content), provider=self.provider, model=self.model,
                          requested_model=f"{self.model}:{self.provider}", observed_model=None,
                          request_id="placeholder-" + key[:16], finish_reason="awaiting_response", awaiting_response=True)


def validate_schema(value, schema, path="response"):
    """Validate the JSON Schema subset used by the bundled V3 contracts."""
    if not isinstance(schema, dict):
        raise ValueError("Response schema must be an object")
    kind = schema.get("type")
    types = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
             "string": lambda x: isinstance(x, str), "integer": lambda x: type(x) is int,
             "number": lambda x: type(x) in {int, float}, "boolean": lambda x: type(x) is bool,
             "null": lambda x: x is None}
    if kind is not None and (kind not in types or not types[kind](value)):
        raise ValueError(f"{path} must have type {kind}")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        if required - value.keys():
            raise ValueError(f"{path} is missing required properties")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError(f"{path} has unknown properties")
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], f"{path}.{key}")
    elif isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf")):
            raise ValueError(f"{path} has an invalid number of items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f"{path} has duplicate items")
        for index, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{path}[{index}]")
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            raise ValueError(f"{path} has an invalid string length")
    elif type(value) in {int, float}:
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            raise ValueError(f"{path} is outside its numeric bounds")


def check_exchange(exchange_dir: str | Path, *, quarantine=True) -> dict:
    """Revalidate every response, preserving invalid files beside the originals."""
    from ..checkpoints import atomic_write_json, quarantine as quarantine_file
    from ..validation import parse_bases, parse_verdicts, validate_proposal
    directory = Path(exchange_dir)
    counts = dict(extraction=0, review=0, basis=0, missing=0, invalid=0)
    invalid = []
    requests = {path.stem: path for path in (directory / "requests").glob("*.json")}
    responses = {path.stem: path for path in (directory / "responses").glob("*.json") if not path.name.endswith(".invalid.json")}
    counts["missing"] = len(requests.keys() - responses.keys())
    for key, path in sorted(responses.items()):
        kind = None
        try:
            if key not in requests:
                raise ValueError("Response has no corresponding request")
            request = _read(requests[key])
            if request.get("schema_version") != REQUEST_SCHEMA or request.get("key") != key:
                raise ValueError("Request schema or key is invalid")
            kind = request.get("kind")
            if kind not in {"extraction", "review", "basis"}:
                raise ValueError("Request kind is invalid")
            counts[kind] += 1
            response = _read(path)
            completion = _response_completion(response, model=request["model"], provider=request["provider"], key=key)
            if completion.finish_reason != "stop":
                raise ValueError("Exchange response is incomplete")
            value = strict_json_loads(completion.content)
            validate_schema(value, request["response_schema"])
            if kind == "review":
                parse_verdicts(completion.content, request["proposal_count"])
            elif kind == "basis":
                parse_bases(completion.content, request["proposal_count"])
            else:
                user = strict_json_loads(request["user_prompt"])
                page = user.get("untrusted_page", {}) if isinstance(user, dict) else {}
                catalogue = page.get("evidence_spans", [])
                section_ids = {item.get("section_id") for item in catalogue}
                # Core requests provide the section IDs, allowing the checker
                # to enforce ownership as well as the request's schema enums.
                if catalogue:
                    for proposal in value["concepts"]:
                        validate_proposal(proposal, catalogue, section_ids)
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, ProviderError, RecursionError) as exc:
            counts["invalid"] += 1
            item = dict(key=key, kind=kind, issues=[str(exc)])
            if quarantine:
                item["quarantined_path"] = str(quarantine_file(path))
            invalid.append(item)
    report = dict(checked_at=datetime.now(UTC).isoformat(), counts=counts, invalid=invalid)
    atomic_write_json(directory / "check-report.json", report)
    return report
