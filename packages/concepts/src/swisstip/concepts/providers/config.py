"""Repository-root TOML configuration, with credentials read only at creation."""

from copy import deepcopy
import os
from pathlib import Path
import re
import tomllib

from .base import bounded_number, required_string, validated_url

CONFIG_SCHEMA = "swisstip.semantic-models/v2"
DEFAULT_GENERATION = {"temperature": 0.0, "max_output_tokens": 8192}
DEFAULT_EXTRACTION = {
    "chunk_content_characters": 6400, "chunk_overlap_characters": 400,
    "max_concepts_per_chunk": 6,
    "max_model_requests_per_page": 12, "max_model_requests_per_run": 400,
    "max_total_input_characters": 1_200_000, "max_characters_per_document": 120_000,
    "max_review_input_characters": 64_000, "review_fallback_batch_size": 2,
    "classify_basis": True,
}
OPTIONAL_EXTRACTION = {"max_prompt_tokens_per_run", "max_pages_per_run", "max_minutes"}
DEFAULT_RETRIES = {"max_retries": 3, "backoff_seconds": 2.0, "max_backoff_seconds": 30.0,
                   "max_retry_after_seconds": 300.0}
_PROFILE_KEYS = {
    "deepseek": {"base_url", "timeout_seconds"},
    "huggingface": {"base_url", "timeout_seconds", "provider", "response_mode", "bill_to"},
    "groq": {"base_url", "timeout_seconds", "response_mode"},
    "ollama": {"base_url", "timeout_seconds", "num_ctx", "keep_alive"},
    "exchange": {"provider"},
}
_ENVIRONMENT_KEYS = {"deepseek": "DEEPSEEK_API_KEY", "huggingface": "HF_TOKEN", "groq": "GROQ_API_KEY"}


def default_config_path() -> Path:
    """Locate the committed configuration from the package, never from cwd."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "semantic-models.toml"
        if candidate.is_file():
            return candidate
    raise ValueError("Repository config/semantic-models.toml not found; supply --config")


def _table(value, name, allowed):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a table")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{name} has unknown keys: {', '.join(sorted(unknown))}")
    return value


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"api_key", "token", "password", "secret"}:
                raise ValueError("Credentials must be supplied through the adapter's fixed environment variable")
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def validate_config(config: dict) -> dict:
    """Validate and return a detached configuration with documented defaults."""
    result = deepcopy(config)
    _no_secrets(result)
    _table(result, "configuration", {"schema_version", "active_profile", "generation", "extraction", "retries", "profiles"})
    if result.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError(f"configuration.schema_version must be {CONFIG_SCHEMA}")
    profiles = result.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles must define at least one profile")
    active = result.get("active_profile")
    if not isinstance(active, str) or active not in profiles:
        raise ValueError("active_profile must name a configured profile")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Profile names may contain only letters, numbers, hyphens and underscores")
        if not isinstance(profile, dict) or profile.get("adapter") not in _PROFILE_KEYS:
            raise ValueError(f"profiles.{name} has an unknown adapter")
        adapter = profile["adapter"]
        _table(profile, f"profiles.{name}", _PROFILE_KEYS[adapter] | {"adapter", "model", "status"})
        required_string(f"profiles.{name}.model", profile.get("model"))
        if "status" in profile and profile["status"] not in {"untested-live", "tested-live"}:
            raise ValueError(f"profiles.{name}.status must be untested-live or tested-live")
        if adapter != "exchange":
            if "base_url" in profile:
                validated_url(profile["base_url"], local=adapter == "ollama")
            bounded_number(f"profiles.{name}.timeout_seconds", profile.get("timeout_seconds", 180), minimum=0.001, maximum=300)
        if adapter in {"huggingface", "exchange"}:
            required_string(f"profiles.{name}.provider", profile.get("provider"))
        if adapter == "huggingface":
            if ":" in profile["model"] or ":" in profile["provider"]:
                raise ValueError("Hugging Face model and provider must be separate identifiers")
            if profile.get("response_mode", "json_schema") not in {"json_schema", "prompt_only"}:
                raise ValueError("Hugging Face response_mode must be json_schema or prompt_only")
            if "bill_to" in profile:
                required_string("bill_to", profile["bill_to"])
        if adapter == "groq" and (profile["model"] not in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
                                  or profile.get("response_mode", "json_schema") != "json_schema"):
            raise ValueError("Groq extraction requires GPT-OSS and json_schema response mode")
        if adapter == "ollama":
            bounded_number("num_ctx", profile.get("num_ctx", 8192), minimum=1, integer=True)
            required_string("keep_alive", profile.get("keep_alive", "5m"))
    for name, defaults in (("generation", DEFAULT_GENERATION), ("extraction", DEFAULT_EXTRACTION), ("retries", DEFAULT_RETRIES)):
        allowed = set(defaults) | (OPTIONAL_EXTRACTION if name == "extraction" else set())
        result[name] = defaults | _table(result.get(name, {}), name, allowed)
    generation, extraction, retries = result["generation"], result["extraction"], result["retries"]
    bounded_number("generation.temperature", generation["temperature"], maximum=2)
    bounded_number("generation.max_output_tokens", generation["max_output_tokens"], minimum=1, integer=True)
    for name, value in extraction.items():
        if name in OPTIONAL_EXTRACTION and value is None:
            continue
        if name == "classify_basis":
            if not isinstance(value, bool):
                raise ValueError("extraction.classify_basis must be true or false")
            continue
        bounded_number(f"extraction.{name}", value, minimum=0 if name == "chunk_overlap_characters" else 0.001 if name == "max_minutes" else 1,
                       integer=name != "max_minutes")
    if extraction["chunk_content_characters"] < 500:
        raise ValueError("chunk_content_characters must be at least 500")
    if extraction["chunk_overlap_characters"] > extraction["chunk_content_characters"] // 2:
        raise ValueError("chunk_overlap_characters must not exceed half the chunk size")
    if extraction["max_concepts_per_chunk"] > 100:
        raise ValueError("max_concepts_per_chunk must not exceed 100")
    if extraction["max_model_requests_per_page"] > extraction["max_model_requests_per_run"]:
        raise ValueError("Per-page request ceiling must not exceed per-run request ceiling")
    if extraction["review_fallback_batch_size"] > 10:
        raise ValueError("review_fallback_batch_size must not exceed 10")
    bounded_number("retries.max_retries", retries["max_retries"], maximum=5, integer=True)
    bounded_number("retries.backoff_seconds", retries["backoff_seconds"], minimum=0.001, maximum=60)
    bounded_number("retries.max_backoff_seconds", retries["max_backoff_seconds"], minimum=retries["backoff_seconds"], maximum=60)
    bounded_number("retries.max_retry_after_seconds", retries["max_retry_after_seconds"], minimum=0.001, maximum=3600)
    return result


def load_config(path: str | Path | None = None) -> dict:
    source = Path(path) if path is not None else default_config_path()
    try:
        with source.open("rb") as stream:
            return validate_config(tomllib.load(stream))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot read semantic-model configuration: {source}") from exc


def create_provider(profile: dict, generation: dict | None = None, *, exchange_dir=None,
                    on_missing="capture", opener=None):
    """Instantiate exactly the named profile, reading only its fixed secret."""
    selected = validate_config({"schema_version": CONFIG_SCHEMA, "active_profile": "selected",
                                "profiles": {"selected": profile}, "generation": generation or {}})
    arguments = {key: value for key, value in profile.items() if key not in {"adapter", "status"}}
    adapter = profile["adapter"]
    if adapter == "exchange":
        from .exchange import ExchangeProvider
        if exchange_dir is None:
            raise ValueError("exchange_dir is required for the exchange adapter")
        return ExchangeProvider(exchange_dir=exchange_dir, on_missing=on_missing, **arguments)
    arguments.update(selected["generation"], opener=opener)
    if adapter in _ENVIRONMENT_KEYS:
        variable = _ENVIRONMENT_KEYS[adapter]
        token = os.environ.get(variable)
        if not token:
            raise ValueError(f"Selected {adapter} profile requires {variable}")
        arguments["token"] = token
    if adapter == "deepseek":
        from .deepseek import DeepSeekProvider
        return DeepSeekProvider(**arguments)
    if adapter == "huggingface":
        from .huggingface import HuggingFaceProvider
        return HuggingFaceProvider(**arguments)
    if adapter == "groq":
        from .groq import GroqProvider
        return GroqProvider(**arguments)
    from .ollama import OllamaProvider
    return OllamaProvider(**arguments)
