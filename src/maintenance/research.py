"""Daily research module for AI Key Pool.

Uses the key pool itself to research new providers and free tiers.
Queries an AI provider via the KeyRotator for automatic rotation.
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from ..key_pool import KeyRotator, KeyManager
from ..providers.base_provider import ChatMessage
from ..providers.provider_factory import create_provider
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("research")

RESEARCH_PROMPT = """You are an AI provider research assistant. Analyze the current landscape of AI API providers and respond ONLY with valid JSON (no markdown).

Focus on:
1. Newly announced official AI providers (last 30 days)
2. New or changed free API tiers from official providers
3. New models released by official providers
4. Changes to existing free tiers

For each finding, provide:
- name: provider or model name
- type: "provider", "free_tier", "model", or "change"
- description: brief description
- url: official URL if known
- action: recommended action ("add_key", "monitor", "update", "none")

Respond with JSON in this exact format:
{
  "findings": [
    {"name": "...", "type": "...", "description": "...", "url": "...", "action": "..."}
  ],
  "summary": "Brief summary of changes"
}

Only include official, legitimate providers. Do not recommend leaked or unauthorized API keys."""


def research_providers(
    config: Config,
    key_manager: KeyManager,
    history_path: Path,
) -> dict:
    """Run provider research using the AI key pool.

    Args:
        config: System configuration
        key_manager: Key manager instance
        history_path: Path to research_history.json

    Returns:
        Research findings dict
    """
    rotator = KeyRotator(config, key_manager)
    provider_name = config.active_provider

    try:
        provider = create_provider(provider_name)
    except ValueError as e:
        logger.error("Cannot research — invalid provider: %s", e)
        return {"findings": [], "summary": f"Research failed: {e}", "error": str(e)}

    messages = [
        ChatMessage(role="system", content="You are a research assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=RESEARCH_PROMPT),
    ]

    def request_fn(api_key: str):
        return provider.chat(api_key, "gpt-4o-mini", messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)

    if not result.success or not result.response:
        logger.error("Research failed: %s", result.error)
        return {"findings": [], "summary": f"Research failed: {result.error}", "error": result.error}

    findings = _parse_findings(result.response.content)

    # Merge with history
    history = _load_history(history_path)
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "findings": findings,
        "key_used": result.key_used,
        "rotations": result.rotations,
    }
    history["entries"].append(entry)
    # Keep last 30 days
    history["entries"] = history["entries"][-30:]
    _save_history(history_path, history)

    logger.info("Research complete — %d findings", len(findings.get("findings", [])))
    return findings


def _parse_findings(content: str) -> dict:
    """Parse AI response into structured findings."""
    try:
        # Try to extract JSON from the response
        text = content.strip()
        if text.startswith("```"):
            # Remove markdown code fences
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse research response as JSON")
        return {"findings": [], "summary": "Failed to parse AI response"}


def _load_history(path: Path) -> dict:
    """Load research history from disk."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return {"entries": []}


def _save_history(path: Path, data: dict) -> None:
    """Save research history to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
