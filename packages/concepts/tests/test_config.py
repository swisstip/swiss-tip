"""Configuration validation and explicit, lazy secret selection."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swisstip.concepts.providers.config import create_provider, default_config_path, load_config, validate_config


class ConfigTests(unittest.TestCase):
    def test_committed_config_is_cwd_independent_and_offline(self):
        source = default_config_path()
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            try:
                os.chdir(directory)
                config = load_config()
            finally:
                os.chdir(before)
        self.assertTrue(source.is_file())
        self.assertEqual(config["active_profile"], "deepseek_flash")
        self.assertEqual(set(config["profiles"]), {"deepseek_flash", "deepseek_pro", "apertus_70b",
                                                   "groq_gpt_oss_120b", "ollama_apertus_8b", "assistant_exchange"})
        self.assertEqual(config["profiles"]["deepseek_pro"]["model"], "deepseek-v4-pro")
        self.assertEqual(config["profiles"]["apertus_70b"]["status"], "untested-live")
        self.assertNotIn("max_prompt_tokens_per_run", config["extraction"])
        self.assertNotIn("max_minutes", config["extraction"])
        self.assertNotIn("max_pages_per_run", config["extraction"])

    def test_unknown_keys_and_embedded_secrets_are_rejected(self):
        config = load_config()
        for area, key in ((None, "unknown"), ("generation", "unknown"), ("extraction", "unknown"), ("retries", "unknown")):
            with self.subTest(area=area):
                changed = deepcopy(config)
                (changed if area is None else changed[area])[key] = 1
                with self.assertRaises(ValueError):
                    validate_config(changed)
        for key in ("api_key", "token", "unknown"):
            changed = deepcopy(config)
            changed["profiles"]["deepseek_flash"][key] = "never-a-real-secret"
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_config(changed)

    def test_predecessor_bounds(self):
        cases = [("extraction", "chunk_content_characters", 499),
                 ("extraction", "chunk_overlap_characters", 3201),
                 ("extraction", "max_concepts_per_chunk", 101),
                 ("extraction", "max_model_requests_per_page", 401),
                 ("extraction", "review_fallback_batch_size", 11),
                 ("extraction", "max_total_input_characters", True),
                 ("extraction", "max_characters_per_document", 0),
                 ("extraction", "max_prompt_tokens_per_run", -1),
                 ("retries", "max_retries", 6), ("retries", "backoff_seconds", 61),
                 ("retries", "max_backoff_seconds", 1), ("retries", "max_retry_after_seconds", 3601),
                 ("generation", "temperature", float("nan")), ("generation", "max_output_tokens", 0)]
        for area, key, value in cases:
            with self.subTest(key=key, value=value):
                changed = load_config()
                changed[area][key] = value
                with self.assertRaises(ValueError):
                    validate_config(changed)

    def test_invalid_profiles_and_urls(self):
        for updates in ({"adapter": "missing"}, {"model": ""}, {"base_url": "http://api.example.test"},
                        {"base_url": "https://user:password@example.test"}, {"base_url": "https://example.test?token=x"},
                        {"base_url": "https://example.test/#fragment"}, {"base_url": "https://example.test:invalid"},
                        {"timeout_seconds": float("inf")}):
            with self.subTest(updates=updates):
                changed = load_config()
                changed["profiles"]["deepseek_flash"].update(updates)
                with self.assertRaises(ValueError):
                    validate_config(changed)
        config = load_config()
        config["active_profile"] = "missing"
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_only_selected_fixed_environment_variable_is_read(self):
        config = load_config()
        variables = {"deepseek_flash": "DEEPSEEK_API_KEY", "apertus_70b": "HF_TOKEN", "groq_gpt_oss_120b": "GROQ_API_KEY"}
        for name, variable in variables.items():
            with self.subTest(profile=name), patch("swisstip.concepts.providers.config.os.environ.get", return_value="test-secret") as get:
                provider = create_provider(config["profiles"][name], config["generation"])
                get.assert_called_once_with(variable)
                self.assertEqual(provider.model, config["profiles"][name]["model"])
        with tempfile.TemporaryDirectory() as directory, patch("swisstip.concepts.providers.config.os.environ.get") as get:
            create_provider(config["profiles"]["assistant_exchange"], exchange_dir=directory)
            create_provider(config["profiles"]["ollama_apertus_8b"])
            get.assert_not_called()

    def test_missing_selected_key_has_no_fallback(self):
        config = load_config()
        with patch.dict(os.environ, {"HF_TOKEN": "other-profile-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                create_provider(config["profiles"]["deepseek_flash"])

    def test_override_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "custom.toml"
            source.write_text('schema_version = "swisstip.semantic-models/v2"\nactive_profile = "local"\n'
                              '[profiles.local]\nadapter = "ollama"\nmodel = "test"\n', encoding="utf-8")
            config = load_config(source)
            self.assertEqual(config["generation"]["max_output_tokens"], 8192)
            self.assertEqual(config["extraction"]["chunk_content_characters"], 6400)
            self.assertEqual(config["retries"]["max_retries"], 3)
            self.assertEqual(config["active_profile"], "local")


if __name__ == "__main__":
    unittest.main()
