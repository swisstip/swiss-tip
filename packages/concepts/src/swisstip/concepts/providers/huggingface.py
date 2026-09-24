"""Hugging Face router, including the verified PublicAI identity policy."""

from .base import ChatProvider, decode_chat, required_string, schema_prompt, schema_response_format

# Carried over from the predecessor. PublicAI provider IDs checked against the
# HF model API inferenceProviderMapping on 2026-09-07. Exact revision and size
# matter: never infer equivalence by lowercasing an arbitrary response model.
APPROVED_MODEL_ALIASES = {
    ("publicai", "swiss-ai/Apertus-8B-Instruct-2509"): frozenset({"swiss-ai/apertus-8b-instruct"}),
    ("publicai", "swiss-ai/Apertus-70B-Instruct-2509"): frozenset({"swiss-ai/apertus-70b-instruct"}),
}


def is_approved_model_identity(provider, model, observed):
    return isinstance(observed, str) and (
        observed in {model, f"{model}:{provider}"}
        or observed in APPROVED_MODEL_ALIASES.get((provider, model), ()))


class HuggingFaceProvider(ChatProvider):
    adapter = "huggingface"
    default_base_url = "https://router.huggingface.co/v1"

    def __init__(self, *, provider, response_mode="json_schema", bill_to=None, **kwargs):
        super().__init__(**kwargs)
        self.provider = required_string("provider", provider)
        if ":" in self.provider or ":" in self.model:
            raise ValueError("Hugging Face model and provider must be separate identifiers")
        if response_mode not in {"json_schema", "prompt_only"}:
            raise ValueError("response_mode must be json_schema or prompt_only")
        self.response_mode = response_mode
        self.bill_to = required_string("bill_to", bill_to) if bill_to is not None else None

    def generate_structured(self, *, system_prompt, user_prompt, response_schema):
        messages = self.messages(system_prompt, user_prompt, response_schema)
        payload = {"model": f"{self.model}:{self.provider}", "messages": messages,
                   "stream": False, "max_tokens": self.max_output_tokens, "temperature": self.temperature}
        if self.response_mode == "json_schema":
            payload["response_format"] = schema_response_format(response_schema)
        else:
            messages[0]["content"] = schema_prompt(system_prompt, response_schema)
        if self.provider == "publicai":
            payload.update(disable_fallbacks=True, cache={"no-cache": True, "no-store": True})
        data, headers = self.post(payload, {"X-HF-Bill-To": self.bill_to} if self.bill_to else None)
        return decode_chat(data, headers, provider=self.provider, model=self.model,
                           requested_model=payload["model"], accepted_finish_reasons=("stop", "eos_token", "stop_sequence"),
                           identity_profile={"adapter": self.adapter, "provider": self.provider, "model": self.model})
