"""Configuration system for AI Key Pool.

Loads configuration from environment variables with sensible defaults.
Secrets are never hardcoded.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class ProviderConfig:
    """Configuration for a single AI provider."""
    name: str
    keys: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration for AI Key Pool."""
    master_key: Optional[str] = None
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    active_provider: str = ""
    retry_count: int = 3
    max_consecutive_failures: int = 5
    data_dir: Path = DATA_DIR
    log_level: str = "INFO"
    research_max_iterations: int = 8
    research_quality_threshold: int = 90
    min_verification_score: int = 80
    min_source_diversity: int = 70
    min_coverage: int = 80
    memory_compression_threshold: int = 4
    research_planner_enabled: bool = True
    contradiction_detection_enabled: bool = True


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load configuration from environment and optional config file.

    Environment variables:
        AIKEYPOOL_MASTER_KEY: Master key for the system
        AIKEYPOOL_ACTIVE_PROVIDER: Currently active provider name
        AIKEYPOOL_RETRY_COUNT: Number of retries before giving up
        AIKEYPOOL_LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
        AIKEYPOOL_DATA_DIR: Path to data directory

    Args:
        config_path: Optional path to a JSON config file

    Returns:
        Config instance
    """
    def _bool_env(key: str, default: bool) -> bool:
        val = os.environ.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes")

    config = Config(
        master_key=os.environ.get("AIKEYPOOL_MASTER_KEY"),
        active_provider=os.environ.get("AIKEYPOOL_ACTIVE_PROVIDER", ""),
        retry_count=int(os.environ.get("AIKEYPOOL_RETRY_COUNT", "3")),
        max_consecutive_failures=int(os.environ.get("AIKEYPOOL_MAX_CONSECUTIVE_FAILURES", "5")),
        log_level=os.environ.get("AIKEYPOOL_LOG_LEVEL", "INFO"),
        research_max_iterations=int(os.environ.get("AIKEYPOOL_RESEARCH_MAX_ITERATIONS", "8")),
        research_quality_threshold=int(os.environ.get("AIKEYPOOL_RESEARCH_QUALITY_THRESHOLD", "90")),
        min_verification_score=int(os.environ.get("AIKEYPOOL_MIN_VERIFICATION_SCORE", "80")),
        min_source_diversity=int(os.environ.get("AIKEYPOOL_MIN_SOURCE_DIVERSITY", "70")),
        min_coverage=int(os.environ.get("AIKEYPOOL_MIN_COVERAGE", "80")),
        memory_compression_threshold=int(os.environ.get("AIKEYPOOL_MEMORY_COMPRESSION_THRESHOLD", "4")),
        research_planner_enabled=_bool_env("AIKEYPOOL_RESEARCH_PLANNER_ENABLED", True),
        contradiction_detection_enabled=_bool_env("AIKEYPOOL_CONTRADICTION_DETECTION_ENABLED", True),
    )

    data_dir = os.environ.get("AIKEYPOOL_DATA_DIR")
    if data_dir:
        config.data_dir = Path(data_dir)

    # Load from config file if provided
    if config_path and config_path.exists():
        with open(config_path, "r") as f:
            data = json.load(f)

        if "master_key" in data:
            config.master_key = data["master_key"]

        if "active_provider" in data:
            config.active_provider = data["active_provider"]

        if "retry_count" in data:
            config.retry_count = data["retry_count"]

        for provider_name, provider_data in data.get("providers", {}).items():
            config.providers[provider_name] = ProviderConfig(
                name=provider_name,
                keys=provider_data.get("keys", []),
            )

    # Load provider keys from environment
    # Format: AIKEYPOOL_PROVIDER_<NAME>_KEYS=key1,key2,key3
    for key, value in os.environ.items():
        if key.startswith("AIKEYPOOL_PROVIDER_") and key.endswith("_KEYS"):
            provider_name = key[len("AIKEYPOOL_PROVIDER_"):-len("_KEYS")].lower()
            keys = [k.strip() for k in value.split(",") if k.strip()]
            if provider_name not in config.providers:
                config.providers[provider_name] = ProviderConfig(name=provider_name)
            config.providers[provider_name].keys = keys

    # Set default active provider if not set
    if not config.active_provider and config.providers:
        config.active_provider = next(iter(config.providers))

    # Ensure data directory exists
    config.data_dir.mkdir(parents=True, exist_ok=True)

    return config
