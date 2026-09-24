"""Provider contract and bounded, strict JSON HTTP transport without redirects."""

import http.client
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

MAX_BYTES = 1_000_000
MAX_ERROR_BYTES = 4096
USER_AGENT = "SwissTIP/0.1"


@dataclass(frozen=True)
class Completion:
    content: str
    provider: str
    model: str
    requested_model: str | None = None
    observed_model: str | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    awaiting_response: bool = False

    def __post_init__(self):
        # Checkpoints are external JSON too. Type annotations alone must not
        # permit a forged token count or identity to reach the run statistics.
        if not isinstance(self.content, str):
            raise ValueError("Completion content must be a string")
        for field in ("provider", "model"):
            required_string(f"Completion {field}", getattr(self, field))
        for field in ("requested_model", "observed_model", "request_id", "finish_reason"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"Completion {field} must be a non-empty string or null")
        for field in ("prompt_tokens", "output_tokens"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"Completion {field} must be a non-negative integer or null")
        if type(self.awaiting_response) is not bool:
            raise ValueError("Completion awaiting_response must be boolean")


class Provider(Protocol):
    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_schema: Mapping[str, object]) -> Completion: ...


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 retry_after: str | None = None, request_id: str | None = None,
                 completion: Completion | None = None,
                 upstream_error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id
        self.completion = completion
        self.upstream_error_code = upstream_error_code


class ProviderResponseError(ProviderError):
    """A response does not satisfy the provider's completion contract."""


class ProviderTransportError(ProviderError):
    """A request or response could not be transported."""

    transient = True


class InvalidCompletion(ProviderResponseError):
    """Raw assistant content is retained for a chunk-level validation failure."""


class IdentityError(ProviderResponseError):
    """The response cannot be attributed to the requested model."""


class IncompleteCompletion(ProviderResponseError):
    def __init__(self, completion: Completion):
        super().__init__(f"Incomplete completion (finish_reason={completion.finish_reason!r})",
                         completion=completion, request_id=completion.request_id)
        self.finish_reason = completion.finish_reason


class RateLimitError(ProviderError):
    """HTTP 429, including the provider's bounded Retry-After metadata."""


class TokenLimitError(RateLimitError):
    """The upstream provider reports exhaustion of its model token allowance."""


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate object key")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON contains a non-finite number")
    return number


def _invalid_constant(value):
    raise ValueError("JSON contains a non-finite number")


def strict_json_loads(value: str | bytes):
    """Reject duplicate keys, non-finite numbers and malformed UTF-8."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict")
    return json.loads(value, object_pairs_hook=_object_pairs,
                      parse_float=_finite_float, parse_constant=_invalid_constant)


def json_object(value: str | bytes) -> dict:
    try:
        result = strict_json_loads(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProviderResponseError("Provider returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ProviderResponseError("Provider response must be a JSON object")
    return result


def required_string(name, value, *, whitespace=False):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or (not whitespace and any(c.isspace() for c in value))):
        raise ValueError(f"{name} must be a non-empty value without invalid whitespace")
    return value


def validated_url(value: str, *, local=False) -> str:
    value = required_string("base_url", value).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url has an invalid port") from exc
    if (parsed.scheme not in ({"http", "https"} if local else {"https"})
            or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("base_url must be an absolute HTTPS URL without credentials, query or fragment"
                         if not local else "base_url must be an absolute HTTP(S) URL without credentials, query or fragment")
    return value


def bounded_number(name, value, *, minimum=0, maximum=math.inf, integer=False):
    if (type(value) not in ({int} if integer else {int, float})
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be {'an integer' if integer else 'a number'} between {minimum} and {maximum}")
    return value


def optional_count(value):
    return value if type(value) is int and value >= 0 else None


def header(headers, name):
    if not hasattr(headers, "items"):
        return None
    for key, value in headers.items():
        if key.lower() == name.lower() and isinstance(value, str) and value:
            return value
    return None


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _raise_http_error(status, headers, body, *, provider):
    code = None
    if status == 429:
        if isinstance(body, bytes) and len(body) <= MAX_ERROR_BYTES:
            try:
                error = json_object(body).get("error")
                if isinstance(error, dict):
                    for candidate in ("maximum_token_reached", "rate_limit_exceeded"):
                        if error.get("code") == candidate or (
                                isinstance(error.get("message"), str)
                                and re.search(r"(?<!\w)" + candidate + r"(?!\w)", error["message"])):
                            code = candidate
                            break
            except ProviderResponseError:
                pass
    error_type = TokenLimitError if code == "maximum_token_reached" else RateLimitError if status == 429 else ProviderError
    message = f"{provider} returned HTTP {status}"
    if code:
        message += f" ({code})"
    raise error_type(message, status_code=status, retry_after=header(headers, "Retry-After"),
                     request_id=header(headers, "X-Request-Id"), upstream_error_code=code)


def post_json(url, payload, *, provider, token=None, extra_headers=None,
              timeout_seconds=180, opener=None):
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Request must contain only JSON-serializable values") from exc
    if len(body) > MAX_BYTES:
        raise ProviderError("Provider request exceeded the 1 MB limit")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT}
    if token is not None:
        headers["Authorization"] = "Bearer " + required_string("token", token)
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = opener or urllib.request.build_opener(NoRedirectHandler())
    try:
        response = opener.open(request, timeout=timeout_seconds)
        try:
            status = int(response.getcode())
            response_headers = response.headers
            raw = response.read((MAX_BYTES if 200 <= status < 300 else MAX_ERROR_BYTES) + 1)
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        try:
            try:
                raw = exc.read(MAX_ERROR_BYTES + 1)
            except (OSError, http.client.HTTPException):
                raw = b""
        finally:
            exc.close()
        _raise_http_error(exc.code, exc.headers, raw, provider=provider)
    except (OSError, http.client.HTTPException, ValueError, TypeError, AttributeError) as exc:
        raise ProviderTransportError(f"{provider} request could not be completed") from exc
    if not 200 <= status < 300:
        _raise_http_error(status, response_headers, raw, provider=provider)
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ProviderResponseError("Provider response exceeded the 1 MB limit or was not bytes")
    return json_object(raw), response_headers


def enforce_model_identity(completion: Completion, profile: Mapping | None = None):
    model = profile.get("model", completion.model) if isinstance(profile, Mapping) else completion.model
    provider = (profile.get("provider") or profile.get("adapter") or completion.provider) if isinstance(profile, Mapping) else completion.provider
    if completion.model != model or completion.provider != provider:
        raise IdentityError("Completion configured identity does not match the selected profile", completion=completion)
    if provider == "publicai" or isinstance(profile, Mapping) and profile.get("adapter") == "huggingface":
        from .huggingface import is_approved_model_identity
        accepted = is_approved_model_identity(provider, model, completion.observed_model)
    else:
        accepted = completion.observed_model == model
    if not accepted:
        raise IdentityError("Provider returned an unexpected or missing model identity", completion=completion)


def check_content(completion):
    if not isinstance(completion.content, str) or not completion.content.strip():
        raise InvalidCompletion("Provider returned empty assistant content", completion=completion)
    try:
        json_object(completion.content)
    except ProviderResponseError as exc:
        raise InvalidCompletion("Provider returned invalid assistant JSON", completion=completion) from exc


def decode_chat(payload, headers, *, provider, model, requested_model=None,
                accepted_finish_reasons=("stop",), identity_profile=None):
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderResponseError("Provider must return exactly one completion")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ProviderResponseError("Provider returned no assistant message")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    completion = Completion(
        content=message.get("content") if isinstance(message.get("content"), str) else "",
        provider=provider, model=model, requested_model=requested_model or model,
        observed_model=payload.get("model") if isinstance(payload.get("model"), str) else None,
        prompt_tokens=optional_count(usage.get("prompt_tokens", usage.get("input_tokens"))),
        output_tokens=optional_count(usage.get("completion_tokens", usage.get("output_tokens"))),
        request_id=header(headers, "X-Request-Id") or (payload.get("id") if isinstance(payload.get("id"), str) else None),
        finish_reason=choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None)
    enforce_model_identity(completion, identity_profile)
    if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
        raise ProviderResponseError("Provider returned a refusal or tool call", completion=completion)
    if completion.finish_reason not in accepted_finish_reasons:
        raise IncompleteCompletion(completion)
    check_content(completion)
    return completion


class ChatProvider:
    adapter = ""
    provider = ""
    default_base_url = ""

    def __init__(self, *, model, token=None, api_key=None, base_url=None, timeout_seconds=180,
                 max_output_tokens=8192, max_tokens=None, temperature=0.0, opener=None):
        self.model = required_string("model", model)
        self._token = required_string("token", token if token is not None else api_key)
        self.base_url = validated_url(base_url or self.default_base_url)
        self.timeout_seconds = bounded_number("timeout_seconds", timeout_seconds, minimum=0.001, maximum=300)
        self.max_output_tokens = bounded_number("max_output_tokens", max_output_tokens if max_tokens is None else max_tokens,
                                               minimum=1, integer=True)
        self.temperature = bounded_number("temperature", temperature, minimum=0, maximum=2)
        self._opener = opener

    def messages(self, system_prompt, user_prompt, response_schema):
        if not isinstance(system_prompt, str) or not system_prompt.strip() or not isinstance(user_prompt, str) or not user_prompt.strip():
            raise ValueError("System and user prompts must be non-empty strings")
        if not isinstance(response_schema, Mapping) or not response_schema:
            raise ValueError("response_schema must be a non-empty mapping")
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def post(self, payload, extra_headers=None):
        return post_json(self.base_url + "/chat/completions", payload, provider=self.provider,
                         token=self._token, timeout_seconds=self.timeout_seconds,
                         opener=self._opener, extra_headers=extra_headers)


def schema_prompt(system_prompt, response_schema):
    return (system_prompt + "\nReturn only a complete JSON object, without Markdown fences or commentary. "
            "Use compact JSON without indentation or blank lines. "
            "The following trusted JSON Schema defines the output contract:\n"
            + json.dumps(dict(response_schema), ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def schema_response_format(response_schema):
    return {"type": "json_schema", "json_schema": {"name": "swisstip_structured_response",
                                                     "schema": dict(response_schema), "strict": True}}
