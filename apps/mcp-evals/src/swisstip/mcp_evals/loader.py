"""Load local benchmark configuration and cases from YAML."""

from pathlib import Path
from typing import Any

import yaml

from .models import EvalCase, HarnessConfig, RunConfig


def _read(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_cases(path: Path) -> list[EvalCase]:
    data = _read(path)
    rows = data.get("cases", data) if isinstance(data, dict) else data
    return [EvalCase.model_validate(row) for row in rows]


def load_config(path: Path) -> RunConfig:
    data = _read(path)
    configs = {name: HarnessConfig(name=name, **value) for name, value in data.get("configs", {}).items()}
    benchmark = data.get("benchmark", {})
    return RunConfig(configs=configs, **benchmark)