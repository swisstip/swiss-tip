"""DeepSeek JSON-object completions with disabled thinking."""

from .base import ChatProvider, decode_chat, schema_prompt


class DeepSeekProvider(ChatProvider):
    adapter = provider = "deepseek"
    default_base_url = "https://api.deepseek.com"

    def generate_structured(self, *, system_prompt, user_prompt, response_schema):
        messages = self.messages(system_prompt, user_prompt, response_schema)
        messages[0]["content"] = schema_prompt(system_prompt, response_schema)
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
                   "max_tokens": self.max_output_tokens, "temperature": self.temperature}
        data, headers = self.post(payload)
        return decode_chat(data, headers, provider=self.provider, model=self.model)
