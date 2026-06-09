"""Runtime configuration for prr.

The Finding schema is the durable review contract; this module owns operational
defaults such as model choice, thresholds, caps, and ignored paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_MODEL = "qwen2.5-coder:14b"
DEFAULT_IGNORE_PATHS = [
    ".git/**",
    ".venv/**",
    ".uv-cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "__pycache__/**",
]


class ConfigError(RuntimeError):
    """Raised when a config file cannot be loaded or validated."""


class PrrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_MODEL
    ollama_host: str | None = None
    severity_threshold: Literal["info", "warning", "error"] = "info"
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    max_comments_per_file: int = Field(default=20, ge=1)
    ignore_paths: list[str] = Field(default_factory=lambda: DEFAULT_IGNORE_PATHS.copy())


def load_config(config_path: str | Path | None = None) -> PrrConfig:
    """Load config.yaml, returning defaults when no config file is present."""
    path = Path(config_path) if config_path is not None else Path("config.yaml")
    if not path.exists():
        if config_path is None:
            return PrrConfig()
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse config file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a YAML object")

    try:
        return PrrConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config file {path}: {exc}") from exc
