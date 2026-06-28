"""Configuration validator for AI Key Pool.

Validates all secrets during startup. Detects typos, missing secrets,
and configuration problems. Never exposes secret values.
"""

import os
import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from ..utils.logger import get_logger


logger = get_logger("config_validator")

# Known environment variable patterns
KNOWN_SECRET_VARS = {
    "AIKEYPOOL_MASTER_KEY": "Master key for API authentication",
    "AIKEYPOOL_ACTIVE_PROVIDER": "Currently active AI provider",
    "AIKEYPOOL_RETRY_COUNT": "Number of retries before giving up",
    "AIKEYPOOL_MAX_CONSECUTIVE_FAILURES": "Max failures before disabling a key",
    "AIKEYPOOL_LOG_LEVEL": "Logging level",
    "AIKEYPOOL_DATA_DIR": "Path to data directory",
    "SMTP_HOST": "SMTP server hostname",
    "SMTP_PORT": "SMTP server port",
    "SMTP_USER": "SMTP username (sender address)",
    "SMTP_PASSWORD": "SMTP password",
    "EMAIL_RECIPIENT": "Email recipient address",
}

# Provider secret pattern: AIKEYPOOL_PROVIDER_<NAME>_KEYS
PROVIDER_SECRET_PREFIX = "AIKEYPOOL_PROVIDER_"
PROVIDER_SECRET_SUFFIX = "_KEYS"

# Common typo patterns
TYPO_SUGGESTIONS = {
    "AIKEYPOOL_PROVIDER_GROQ_KEY": "AIKEYPOOL_PROVIDER_GROQ_KEYS",
    "AIKEYPOOL_PROVIDER_OPENROUTER_KEY": "AIKEYPOOL_PROVIDER_OPENROUTER_KEYS",
    "AIKEYPOOL_PROVIDER_GITHUB_MODELS_KEY": "AIKEYPOOL_PROVIDER_GITHUB_MODELS_KEYS",
    "AIKEYPOOL_PROVIDER_OPENAI_KEY": "AIKEYPOOL_PROVIDER_OPENAI_KEYS",
    "AIKEYPOOL_PROVIDER_ANTHROPIC_KEY": "AIKEYPOOL_PROVIDER_ANTHROPIC_KEYS",
    "AIKEYPOOL_PROVIDER_TOGETHER_KEY": "AIKEYPOOL_PROVIDER_TOGETHER_KEYS",
    "AIKEYPOOL_PROVIDER_FIREWORKS_KEY": "AIKEYPOOL_PROVIDER_FIREWORKS_KEYS",
    "AIKEYPOOL_PROVIDER_COHERE_KEY": "AIKEYPOOL_PROVIDER_COHERE_KEYS",
    "AIKEYPOOL_PROVIDER_MISTRAL_KEY": "AIKEYPOOL_PROVIDER_MISTRAL_KEYS",
    "AIKEYPOOL_PROVIDER_CEREBRAS_KEY": "AIKEYPOOL_PROVIDER_CEREBRAS_KEYS",
    "AIKEYPOOL_PROVIDER_DEEPINFRA_KEY": "AIKEYPOOL_PROVIDER_DEEPINFRA_KEYS",
    "AIKEYPOOL_PROVIDER_GROQ_API_KEY": "AIKEYPOOL_PROVIDER_GROQ_KEYS",
    "AIKEYPOOL_PROVIDER_OPENROUTER_API_KEY": "AIKEYPOOL_PROVIDER_OPENROUTER_KEYS",
    "AIKEYPOOL_PROVIDER_GITHUB_MODELS_API_KEY": "AIKEYPOOL_PROVIDER_GITHUB_MODELS_KEYS",
}


@dataclass
class SecretHealth:
    """Health status for a single secret."""
    name: str
    description: str
    configured: bool
    has_value: bool
    warning: Optional[str] = None
    suggestion: Optional[str] = None


@dataclass
class ConfigValidationReport:
    """Full configuration validation report."""
    timestamp: str
    master_key: SecretHealth
    active_provider: SecretHealth
    provider_secrets: list[SecretHealth]
    smtp_secrets: list[SecretHealth]
    warnings: list[str]
    errors: list[str]
    typo_suggestions: list[dict]
    providers_detected: list[str]
    providers_configured: list[str]
    is_valid: bool
    total_secrets_checked: int
    total_secrets_ok: int

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        def _secret(s: SecretHealth) -> dict:
            d = {
                "name": s.name,
                "configured": s.configured,
                "has_value": s.has_value,
            }
            if s.warning:
                d["warning"] = s.warning
            if s.suggestion:
                d["suggestion"] = s.suggestion
            return d

        return {
            "timestamp": self.timestamp,
            "is_valid": self.is_valid,
            "total_secrets_checked": self.total_secrets_checked,
            "total_secrets_ok": self.total_secrets_ok,
            "master_key": _secret(self.master_key),
            "active_provider": _secret(self.active_provider),
            "provider_secrets": [_secret(s) for s in self.provider_secrets],
            "smtp_secrets": [_secret(s) for s in self.smtp_secrets],
            "providers_detected": self.providers_detected,
            "providers_configured": self.providers_configured,
            "warnings": self.warnings,
            "errors": self.errors,
            "typo_suggestions": self.typo_suggestions,
        }


def _check_secret(name: str, description: str) -> SecretHealth:
    """Check if a secret is configured and has a non-empty value.

    Args:
        name: Environment variable name
        description: Human-readable description

    Returns:
        SecretHealth with status
    """
    value = os.environ.get(name, "")
    configured = name in os.environ
    has_value = bool(value.strip()) if configured else False

    warning = None
    if configured and not has_value:
        warning = f"{name} is set but empty"

    return SecretHealth(
        name=name,
        description=description,
        configured=configured,
        has_value=has_value,
        warning=warning,
    )


def _detect_provider_secrets() -> tuple[list[str], list[SecretHealth]]:
    """Discover all AIKEYPOOL_PROVIDER_*_KEYS secrets from environment.

    Returns:
        Tuple of (provider_names, list of SecretHealth)
    """
    providers = []
    secrets = []

    for key, value in os.environ.items():
        if key.startswith(PROVIDER_SECRET_PREFIX) and key.endswith(PROVIDER_SECRET_SUFFIX):
            provider_name = key[len(PROVIDER_SECRET_PREFIX):-len(PROVIDER_SECRET_SUFFIX)].lower()
            has_value = bool(value.strip())
            providers.append(provider_name)

            secrets.append(SecretHealth(
                name=key,
                description=f"API keys for provider: {provider_name}",
                configured=True,
                has_value=has_value,
                warning=None if has_value else f"{key} is set but empty",
            ))

    return sorted(providers), secrets


def _detect_typos() -> list[dict]:
    """Check for potential typos in environment variable names.

    Returns:
        List of typo suggestion dicts
    """
    suggestions = []
    env_keys = set(os.environ.keys())

    # Check for _KEY instead of _KEYS
    for bad_name, good_name in TYPO_SUGGESTIONS.items():
        if bad_name in env_keys and good_name not in env_keys:
            suggestions.append({
                "detected": bad_name,
                "suggestion": good_name,
                "reason": f"Did you mean {good_name} instead of {bad_name}?",
            })

    # Check for common misspellings
    aikeypool_vars = [k for k in env_keys if k.startswith("AIKEYPOOL")]
    for var in aikeypool_vars:
        if "PROVIDER" in var and not var.endswith("_KEYS") and var not in KNOWN_SECRET_VARS:
            if var not in TYPO_SUGGESTIONS:
                # Might be a typo for a provider key
                suggestions.append({
                    "detected": var,
                    "suggestion": f"{var}_KEYS (if this is a provider key)",
                    "reason": "Provider secrets should end with _KEYS",
                })

    return suggestions


def validate_config(data_dir: Optional[Path] = None) -> ConfigValidationReport:
    """Validate all configuration and secrets.

    Checks every known environment variable. Detects typos, missing
    secrets, and empty values. Generates a configuration report.

    Args:
        data_dir: Optional path to write configuration_report.json

    Returns:
        ConfigValidationReport
    """
    warnings = []
    errors = []
    typo_suggestions = _detect_typos()

    # Add typo warnings
    for t in typo_suggestions:
        warnings.append(t["reason"])

    # Check master key
    master_key = _check_secret(
        "AIKEYPOOL_MASTER_KEY",
        "Master key for API authentication",
    )
    if not master_key.configured:
        errors.append("AIKEYPOOL_MASTER_KEY is not set — API will not start")
    elif not master_key.has_value:
        errors.append("AIKEYPOOL_MASTER_KEY is empty — API will not start")

    # Check active provider
    active_provider = _check_secret(
        "AIKEYPOOL_ACTIVE_PROVIDER",
        "Currently active AI provider",
    )
    if not active_provider.configured:
        warnings.append("AIKEYPOOL_ACTIVE_PROVIDER not set — will auto-detect from providers")

    # Discover provider secrets
    providers_detected, provider_secrets = _detect_provider_secrets()
    providers_configured = [p for p in providers_detected if any(
        s.has_value for s in provider_secrets if s.name == f"{PROVIDER_SECRET_PREFIX}{p.upper()}{PROVIDER_SECRET_SUFFIX}"
    )]

    if not providers_detected:
        warnings.append("No AIKEYPOOL_PROVIDER_*_KEYS secrets detected — registry will be empty")

    # Check SMTP secrets
    smtp_secrets = [
        _check_secret("SMTP_HOST", "SMTP server hostname"),
        _check_secret("SMTP_PORT", "SMTP server port"),
        _check_secret("SMTP_USER", "SMTP username (sender address)"),
        _check_secret("SMTP_PASSWORD", "SMTP password"),
        _check_secret("EMAIL_RECIPIENT", "Email recipient address"),
    ]

    smtp_configured = sum(1 for s in smtp_secrets if s.configured)
    smtp_complete = sum(1 for s in smtp_secrets if s.has_value)
    if smtp_configured > 0 and smtp_complete < 5:
        incomplete = [s.name for s in smtp_secrets if not s.has_value]
        warnings.append(
            f"SMTP partially configured — missing: {', '.join(incomplete)}. "
            "Email will be skipped."
        )

    # Count totals
    all_secrets = [master_key, active_provider] + provider_secrets + smtp_secrets
    total_checked = len(all_secrets)
    total_ok = sum(1 for s in all_secrets if s.has_value or (not s.configured and s.name not in ("AIKEYPOOL_MASTER_KEY",)))

    is_valid = len(errors) == 0

    report = ConfigValidationReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        master_key=master_key,
        active_provider=active_provider,
        provider_secrets=provider_secrets,
        smtp_secrets=smtp_secrets,
        warnings=warnings,
        errors=errors,
        typo_suggestions=typo_suggestions,
        providers_detected=providers_detected,
        providers_configured=providers_configured,
        is_valid=is_valid,
        total_secrets_checked=total_checked,
        total_secrets_ok=total_ok,
    )

    # Log summary
    logger.info("CONFIG VALIDATION: %d secrets checked, %d OK", total_checked, total_ok)
    if warnings:
        for w in warnings:
            logger.warning("CONFIG WARNING: %s", w)
    if errors:
        for e in errors:
            logger.error("CONFIG ERROR: %s", e)
    if typo_suggestions:
        for t in typo_suggestions:
            logger.warning("CONFIG TYPO: %s", t["reason"])

    # Write report to disk
    if data_dir:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            report_path = data_dir / "configuration_report.json"
            with open(report_path, "w") as f:
                json.dump(report.to_dict(), f, indent=2)
            logger.info("Configuration report written to %s", report_path)
        except Exception as e:
            logger.warning("Could not write configuration report: %s", e)

    return report
