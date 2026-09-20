"""All adapter tests inject bounded in-memory HTTP responses; no network."""

from copy import deepcopy
import io
import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from swisstip.concepts.providers.base import (
    Completion, IdentityError, IncompleteCompletion, InvalidCompletion, MAX_BYTES,
    NoRedirectHandler, ProviderError, ProviderResponseError, ProviderTransportError,
    RateLimitError, TokenLimitError, enforce_model_identity, strict_json_loads,
)
from swisstip.concepts.providers.deepseek import DeepSeekProvider
from swisstip.concepts.providers.groq import GroqProvider
from swisstip.concepts.providers.huggingface import HuggingFaceProvider
from swisstip.concepts.providers.ollama import OllamaProvider

SCHEMA = {"type": "object", "properties": {"concepts": {"type": "array"}}, "required": ["concepts"]}
REQUEST = dict(system_prompt="Select concepts.", user_prompt="Page text.", response_schema=SCHEMA)


def envelope(model, **updates):
    return dict(model=model, id="completion-id", choices=[{
        "finish_reason": "stop", "message": {"role": "assistant", "content": '{"concepts": []}'}}],
        usage={"prompt_tokens": 12, "completion_tokens": 4}, **updates)


class Response:
    def __init__(self, data, status=200, headers=None):
        self.body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.headers = headers or {}
        self.status = status
        self.closed = False
        self.read_limit = None

    def getcode(self):
        return self.status

    def read(self, limit):
        self.read_limit = limit
        return self.body[:limit]

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class ProviderTests(unittest.TestCase):
    def adapter(self, name, response=None, **kwargs):
        classes = {"deepseek": DeepSeekProvider, "groq": GroqProvider,
                   "huggingface": HuggingFaceProvider, "ollama": OllamaProvider}
        models = {"deepseek": "deepseek-flash", "groq": "openai/gpt-oss-120b",
                  "huggingface": "swiss-ai/Apertus-70B-Instruct-2509", "ollama": "apertus:8b"}
        model = models[name]
        data = envelope(model)
        if name == "ollama":
            data = dict(model=model, done=True, done_reason="stop", message={"content": '{"concepts": []}'},
                        prompt_eval_count=12, eval_count=4)
        opener = Opener(response or Response(data, headers={"X-Request-Id": "header-id"}))
        options = dict(model=model, opener=opener, max_output_tokens=25, temperature=0.2)
        if name != "ollama":
            options["token"] = "test-secret"
        if name == "huggingface":
            options["provider"] = "publicai"
        return classes[name](**(options | kwargs)), opener

    def test_each_request_and_usage_contract(self):
        for name in ("deepseek", "groq", "huggingface", "ollama"):
            with self.subTest(adapter=name):
                provider, opener = self.adapter(name)
                completion = provider.generate_structured(**REQUEST)
                request, timeout = opener.requests[0]
                payload = json.loads(request.data)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("User-agent"), "SwissTIP/0.1")
                self.assertFalse(payload["stream"])
                self.assertEqual(timeout, 180)
                self.assertEqual(completion.prompt_tokens, 12)
                self.assertEqual(completion.output_tokens, 4)
                self.assertEqual(completion.request_id, "header-id")
                self.assertEqual(completion.observed_model, provider.model)
                self.assertTrue(opener.response.closed)
                self.assertEqual(opener.response.read_limit, MAX_BYTES + 1)
                if name == "ollama":
                    self.assertTrue(request.full_url.endswith("/api/chat"))
                    self.assertIsNone(request.get_header("Authorization"))
                    self.assertFalse(payload["think"])
                    self.assertEqual(payload["format"], SCHEMA)
                    self.assertEqual(payload["options"], {"num_ctx": 8192, "num_predict": 25, "temperature": 0.2})
                    self.assertEqual(payload["keep_alive"], "5m")
                else:
                    self.assertTrue(request.full_url.endswith("/chat/completions"))
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
                    if name == "deepseek":
                        self.assertEqual(payload["response_format"], {"type": "json_object"})
                        self.assertEqual(payload["thinking"], {"type": "disabled"})
                        self.assertIn('"required":["concepts"]', payload["messages"][0]["content"])
                        self.assertEqual(payload["max_tokens"], 25)
                    else:
                        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
                        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)
                        if name == "groq":
                            self.assertEqual(payload["reasoning_effort"], "low")
                            self.assertEqual(payload["max_completion_tokens"], 25)
                        else:
                            self.assertEqual(payload["model"], provider.model + ":publicai")
                            self.assertTrue(payload["disable_fallbacks"])
                            self.assertEqual(payload["cache"], {"no-cache": True, "no-store": True})

    def test_publicai_prompt_only_and_billing(self):
        provider, opener = self.adapter("huggingface", response_mode="prompt_only", bill_to="test-org")
        provider.generate_structured(**REQUEST)
        request = opener.requests[0][0]
        payload = json.loads(request.data)
        self.assertNotIn("response_format", payload)
        self.assertIn("JSON Schema", payload["messages"][0]["content"])
        self.assertEqual(request.get_header("X-hf-bill-to"), "test-org")

    def test_publicai_aliases_are_exact_and_provider_scoped(self):
        profile = {"adapter": "huggingface", "provider": "publicai", "model": "swiss-ai/Apertus-70B-Instruct-2509"}
        for observed in (profile["model"], profile["model"] + ":publicai", "swiss-ai/apertus-70b-instruct"):
            enforce_model_identity(Completion("{}", "publicai", profile["model"], observed_model=observed), profile)
        for observed in (None, "aisingapore/Qwen-SEA-LION-v4-32B-IT", "swiss-ai/apertus-8b-instruct"):
            with self.subTest(observed=observed), self.assertRaises(IdentityError):
                enforce_model_identity(Completion("{}", "publicai", profile["model"], observed_model=observed), profile)
        with self.assertRaises(IdentityError):
            enforce_model_identity(Completion("{}", "other", profile["model"], observed_model="swiss-ai/apertus-70b-instruct"))

    def test_completion_checks_and_raw_invalid_content(self):
        mutations = [
            (lambda data: data.update(model="substituted"), IdentityError),
            (lambda data: data.pop("model"), IdentityError),
            (lambda data: data["choices"].append(deepcopy(data["choices"][0])), ProviderResponseError),
            (lambda data: data.update(choices=[]), ProviderResponseError),
            (lambda data: data["choices"][0]["message"].update(refusal="refused"), ProviderResponseError),
            (lambda data: data["choices"][0]["message"].update(tool_calls=[{}]), ProviderResponseError),
            (lambda data: data["choices"][0]["message"].update(content="[]"), InvalidCompletion),
            (lambda data: data["choices"][0]["message"].update(content='{"x":1,"x":2}'), InvalidCompletion),
            (lambda data: data["choices"][0]["message"].update(content=""), InvalidCompletion),
        ]
        for name in ("deepseek", "groq", "huggingface"):
            provider, _ = self.adapter(name)
            for mutate, error in mutations:
                with self.subTest(adapter=name, mutation=mutations.index((mutate, error))):
                    data = envelope(provider.model)
                    mutate(data)
                    tested, _ = self.adapter(name, Response(data))
                    with self.assertRaises(error):
                        tested.generate_structured(**REQUEST)
            data = envelope(provider.model)
            data["choices"][0].update(finish_reason="length")
            data["choices"][0]["message"]["content"] = '{"concepts": ['
            tested, _ = self.adapter(name, Response(data))
            with self.assertRaises(IncompleteCompletion) as caught:
                tested.generate_structured(**REQUEST)
            self.assertEqual(caught.exception.completion.content, '{"concepts": [')
            self.assertEqual(caught.exception.completion.output_tokens, 4)

    def test_ollama_requires_done_and_stop(self):
        for update in ({"done": False}, {"done_reason": "length"}, {"model": "substituted"}):
            with self.subTest(update=update):
                data = {"model": "apertus:8b", "done": True, "done_reason": "stop", "message": {"content": "{}"}} | update
                provider, _ = self.adapter("ollama", Response(data))
                with self.assertRaises(ProviderResponseError) as caught:
                    provider.generate_structured(**REQUEST)
                if update.get("done") is False:
                    self.assertNotEqual(caught.exception.completion.finish_reason, "stop")

    def test_huggingface_extra_finish_reasons(self):
        for finish in ("eos_token", "stop_sequence"):
            data = envelope("swiss-ai/Apertus-70B-Instruct-2509")
            data["choices"][0]["finish_reason"] = finish
            provider, _ = self.adapter("huggingface", Response(data))
            self.assertEqual(provider.generate_structured(**REQUEST).finish_reason, finish)

    def test_strict_wire_json(self):
        for body in (b'{"model":"a","model":"b"}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'{"x":"\xff"}', b'[]'):
            with self.subTest(body=body):
                provider, _ = self.adapter("deepseek", Response(body))
                with self.assertRaises(ProviderResponseError):
                    provider.generate_structured(**REQUEST)

    def test_request_and_response_caps(self):
        for name in ("deepseek", "groq", "huggingface", "ollama"):
            with self.subTest(adapter=name):
                provider, opener = self.adapter(name)
                with self.assertRaises(ProviderError):
                    provider.generate_structured(**(REQUEST | {"user_prompt": "x" * MAX_BYTES}))
                self.assertEqual(opener.requests, [])
                provider, opener = self.adapter(name, Response(b"x" * (MAX_BYTES + 1)))
                with self.assertRaises(ProviderResponseError):
                    provider.generate_structured(**REQUEST)
                self.assertTrue(opener.response.closed)

    def test_redirects_are_refused_and_never_retried_here(self):
        handler = NoRedirectHandler()
        request = urllib.request.Request("https://example.test", headers={"Authorization": "Bearer secret"})
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://other.test"))
        response = Response(b"", status=302, headers={"Location": "https://other.test"})
        provider, opener = self.adapter("deepseek", response)
        with self.assertRaises(ProviderError) as caught:
            provider.generate_structured(**REQUEST)
        self.assertEqual(caught.exception.status_code, 302)
        self.assertEqual(len(opener.requests), 1)
        with patch("swisstip.concepts.providers.base.urllib.request.build_opener", return_value=Opener(Response(envelope("deepseek-flash")))) as build:
            DeepSeekProvider(model="deepseek-flash", token="secret").generate_structured(**REQUEST)
        self.assertIsInstance(build.call_args.args[0], NoRedirectHandler)

    def test_safe_bounded_hf_429_classification(self):
        cases = [(b'{"error":{"code":"maximum_token_reached","message":"private text"}}', TokenLimitError, "maximum_token_reached"),
                 (b'{"error":{"message":"rate_limit_exceeded"}}', RateLimitError, "rate_limit_exceeded"),
                 (b'{"error":{"code":"maximum_token_reached","code":"duplicate"}}', RateLimitError, None),
                 (b'{"error":{"code":"maximum_token_reached"}}' + b" " * 4096, RateLimitError, None),
                 (b"\xff", RateLimitError, None)]
        for body, expected, code in cases:
            with self.subTest(code=code, length=len(body)):
                response = Response(body, 429, {"retry-after": "12", "x-request-id": "rate-id"})
                provider, opener = self.adapter("huggingface", response)
                with self.assertRaises(expected) as caught:
                    provider.generate_structured(**REQUEST)
                self.assertEqual(caught.exception.upstream_error_code, code)
                self.assertEqual(caught.exception.retry_after, "12")
                self.assertNotIn("private text", str(caught.exception))
                self.assertEqual(response.read_limit, 4097)
                self.assertEqual(len(opener.requests), 1)

    def test_httperror_and_transport_metadata(self):
        error = urllib.error.HTTPError("https://example.test", 503, "Service unavailable", {"Retry-After": "5"}, io.BytesIO(b"private"))
        provider, _ = self.adapter("deepseek", error)
        with self.assertRaises(ProviderError) as caught:
            provider.generate_structured(**REQUEST)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.retry_after, "5")
        provider, _ = self.adapter("deepseek", TimeoutError("private"))
        with self.assertRaises(ProviderTransportError) as caught:
            provider.generate_structured(**REQUEST)
        self.assertTrue(caught.exception.transient)
        self.assertNotIn("private", str(caught.exception))

    def test_missing_usage_is_never_invented(self):
        data = envelope("deepseek-flash")
        data["usage"] = {"prompt_tokens": True, "completion_tokens": -1}
        provider, _ = self.adapter("deepseek", Response(data))
        completion = provider.generate_structured(**REQUEST)
        self.assertIsNone(completion.prompt_tokens)
        self.assertIsNone(completion.output_tokens)

    def test_checkpoint_completion_field_types_are_validated(self):
        for updates in ({"content": {}}, {"model": []}, {"provider": ""}, {"prompt_tokens": True},
                        {"output_tokens": "25"}, {"output_tokens": -1}, {"observed_model": 1},
                        {"finish_reason": False}, {"request_id": []}, {"awaiting_response": "false"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                Completion(**({"content": "{}", "provider": "deepseek", "model": "deepseek-flash"} | updates))


if __name__ == "__main__":
    unittest.main()
