"""Local Ollama schema completions using the same bounded HTTP transport."""

from .base import (Completion, IncompleteCompletion, ProviderResponseError, bounded_number,
                   check_content, enforce_model_identity, header, optional_count, post_json,
                   required_string, validated_url)


class OllamaProvider:
    adapter = provider = "ollama"

    def __init__(self, *, model, base_url="http://127.0.0.1:11434", timeout_seconds=180,
                 max_output_tokens=8192, max_tokens=None, temperature=0.0,
                 num_ctx=8192, keep_alive="5m", opener=None):
        self.model = required_string("model", model)
        self.base_url = validated_url(base_url, local=True)
        self.timeout_seconds = bounded_number("timeout_seconds", timeout_seconds, minimum=0.001, maximum=300)
        self.max_output_tokens = bounded_number("max_output_tokens", max_output_tokens if max_tokens is None else max_tokens,
                                               minimum=1, integer=True)
        self.temperature = bounded_number("temperature", temperature, minimum=0, maximum=2)
        self.num_ctx = bounded_number("num_ctx", num_ctx, minimum=1, integer=True)
        self.keep_alive = required_string("keep_alive", keep_alive)
        self._opener = opener

    def generate_structured(self, *, system_prompt, user_prompt, response_schema):
        if not isinstance(system_prompt, str) or not system_prompt.strip() or not isinstance(user_prompt, str) or not user_prompt.strip():
            raise ValueError("System and user prompts must be non-empty strings")
        payload = {"model": self.model, "stream": False, "think": False,
                   "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                   "format": dict(response_schema), "keep_alive": self.keep_alive,
                   "options": {"num_ctx": self.num_ctx, "num_predict": self.max_output_tokens, "temperature": self.temperature}}
        data, headers = post_json(self.base_url + "/api/chat", payload, provider=self.provider,
                                  timeout_seconds=self.timeout_seconds, opener=self._opener)
        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderResponseError("Ollama returned no assistant message")
        completion = Completion(
            content=message.get("content") if isinstance(message.get("content"), str) else "",
            provider=self.provider, model=self.model, requested_model=self.model,
            observed_model=data.get("model") if isinstance(data.get("model"), str) else None,
            prompt_tokens=optional_count(data.get("prompt_eval_count")), output_tokens=optional_count(data.get("eval_count")),
            request_id=header(headers, "X-Request-Id"),
            finish_reason=(data.get("done_reason") if isinstance(data.get("done_reason"), str) else None)
            if data.get("done") is True else "incomplete")
        enforce_model_identity(completion)
        if message.get("tool_calls") or message.get("refusal"):
            raise ProviderResponseError("Ollama returned a refusal or tool call", completion=completion)
        if data.get("done") is not True or completion.finish_reason != "stop":
            raise IncompleteCompletion(completion)
        check_content(completion)
        return completion
