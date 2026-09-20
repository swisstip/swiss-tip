"""Groq GPT-OSS strict schema completions."""

from .base import ChatProvider, decode_chat, schema_response_format


class GroqProvider(ChatProvider):
    adapter = provider = "groq"
    default_base_url = "https://api.groq.com/openai/v1"

    def __init__(self, *, response_mode="json_schema", **kwargs):
        super().__init__(**kwargs)
        if self.model not in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"} or response_mode != "json_schema":
            raise ValueError("Groq extraction requires GPT-OSS and json_schema response mode")

    def generate_structured(self, *, system_prompt, user_prompt, response_schema):
        payload = {"model": self.model, "messages": self.messages(system_prompt, user_prompt, response_schema),
                   "stream": False, "response_format": schema_response_format(response_schema),
                   "reasoning_effort": "low", "max_completion_tokens": self.max_output_tokens,
                   "temperature": self.temperature}
        data, headers = self.post(payload)
        return decode_chat(data, headers, provider=self.provider, model=self.model)
