"""GitHub Discovery Module for AI Key Pool.

Periodically searches GitHub for new AI providers, OpenAI-compatible APIs,
and repositories that publish legitimate free AI providers.
"""

import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..providers.manifest import manifest_registry
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("discovery")


# Source repositories for provider discovery
DISCOVERY_SOURCES = [
    {
        "name": "cool-ai-stuff",
        "url": "https://raw.githubusercontent.com/zukixa/cool-ai-stuff/main/README.md",
        "description": "Curated list of free AI APIs and resources",
    },
    {
        "name": "free-llm-api-resources",
        "url": "https://raw.githubusercontent.com/cheahjs/free-llm-api-resources/main/README.md",
        "description": "Free LLM API resources and providers",
    },
]

# Known API patterns to detect
API_PATTERNS = [
    # OpenAI-compatible endpoints
    r"https?://[a-zA-Z0-9.-]+/v1/chat/completions",
    r"https?://[a-zA-Z0-9.-]+/openai/v1/chat/completions",
    r"https?://[a-zA-Z0-9.-]+/api/v1/chat/completions",
    # Provider-specific patterns
    r"https?://api\.[a-zA-Z0-9.-]+\.com/.*chat.*completions",
]

# Known free providers (for filtering)
KNOWN_FREE_PROVIDERS = {
    "groq", "cerebras", "sambanova", "together", "fireworks",
    "deepinfra", "novita", "chutes", "openrouter", "github_models",
    "siliconflow", "cloudflare", "mistral", "nvidia",
}

# Blocklist (not real providers or not OpenAI-compatible)
BLOCKLIST = {
    "anthropic", "google", "meta", "microsoft", "amazon",
    "huggingface", "replicate", "stability", "cohere",
}


def discover_providers(config: Config = None) -> dict:
    """Run GitHub discovery to find new AI providers.

    Fetches README files from curated repositories, parses for
    provider names, API endpoints, and model lists.

    Args:
        config: Optional configuration

    Returns:
        Dict with discovery results
    """
    start_time = time.monotonic()
    logger.info("DISCOVERY: Starting GitHub provider discovery")

    all_suggestions = []
    sources_checked = 0
    sources_succeeded = 0

    for source in DISCOVERY_SOURCES:
        sources_checked += 1
        logger.info("DISCOVERY: Checking source %s", source["name"])

        content = _fetch_source(source["url"])
        if not content:
            logger.warning("DISCOVERY: Failed to fetch %s", source["name"])
            continue

        sources_succeeded += 1
        suggestions = _parse_source(content, source["name"])
        all_suggestions.extend(suggestions)

        logger.info("DISCOVERY: Found %d suggestions from %s", len(suggestions), source["name"])

    # Filter out already-configured providers
    configured = set(manifest_registry.list_provider_ids())
    new_suggestions = [
        s for s in all_suggestions
        if s["name"].lower() not in configured
        and s["name"].lower() not in BLOCKLIST
    ]

    # Deduplicate by name
    unique_suggestions = _deduplicate_suggestions(new_suggestions)

    duration = time.monotonic() - start_time

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources_checked": sources_checked,
        "sources_succeeded": sources_succeeded,
        "total_suggestions": len(all_suggestions),
        "new_suggestions": len(unique_suggestions),
        "configured_providers": sorted(configured),
        "suggestions": unique_suggestions,
        "duration_seconds": round(duration, 2),
    }

    logger.info(
        "DISCOVERY: Complete — %d new suggestions from %d sources in %.1fs",
        len(unique_suggestions), sources_succeeded, duration,
    )

    return result


def _fetch_source(url: str, timeout: float = 30.0) -> Optional[str]:
    """Fetch content from a URL.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Response text or None on failure
    """
    headers = {
        "User-Agent": "AIKeyPool-DiscoveryBot/1.0 (github.com/ai-key-pool)",
        "Accept": "text/plain,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except httpx.TimeoutException:
        logger.warning("DISCOVERY: Timeout fetching %s", url)
    except httpx.HTTPStatusError as e:
        logger.warning("DISCOVERY: HTTP %d fetching %s", e.response.status_code, url)
    except httpx.RequestError as e:
        logger.warning("DISCOVERY: Request error fetching %s: %s", url, e)
    return None


def _parse_source(content: str, source_name: str) -> list[dict]:
    """Parse provider information from source content.

    Args:
        content: Source content (README markdown)
        source_name: Name of the source

    Returns:
        List of provider suggestions
    """
    suggestions = []

    # Extract API endpoints
    endpoints = set()
    for pattern in API_PATTERNS:
        matches = re.findall(pattern, content)
        endpoints.update(matches)

    # Extract provider names from common patterns
    # Look for provider names in headers, lists, and descriptions
    provider_sections = re.findall(
        r'#{1,4}\s+(?:\[([^\]]+)\]|[A-Z][a-zA-Z\s]+(?:AI|API|LLM|Inference|Cloud))',
        content
    )

    # Also look for common provider name patterns
    provider_names = re.findall(
        r'\b(?:Groq|Together|Fireworks|Mistral|Cerebras|DeepInfra|Novita|Chutes|'
        r'SambaNova|SiliconFlow|NVIDIA|Cloudflare|OpenRouter|GitHub)\b',
        content,
        re.IGNORECASE
    )

    # Combine and deduplicate
    all_names = set()
    for name in provider_sections:
        if name:
            all_names.add(name.strip())
    for name in provider_names:
        all_names.add(name.lower().replace(" ", "_"))

    # For each detected name, try to find associated endpoint
    for name in all_names:
        name_lower = name.lower().replace(" ", "_")
        if name_lower in BLOCKLIST:
            continue

        # Find endpoint near the name
        endpoint = _find_endpoint_for_provider(content, name)

        # Extract models if mentioned
        models = _extract_models_for_provider(content, name)

        # Determine if free tier is mentioned
        has_free_tier = _has_free_tier(content, name)

        suggestion = {
            "name": name_lower,
            "display_name": name,
            "endpoint": endpoint or "",
            "models": models,
            "free_tier": has_free_tier,
            "source": source_name,
            "confidence": "high" if endpoint else "medium",
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        suggestions.append(suggestion)

    # Also create suggestions for endpoints without detected names
    for endpoint in endpoints:
        # Try to extract provider name from endpoint
        provider_from_endpoint = _extract_provider_from_endpoint(endpoint)
        if provider_from_endpoint and provider_from_endpoint not in BLOCKLIST:
            # Check if we already have a suggestion for this provider
            existing = [s for s in suggestions if s["name"] == provider_from_endpoint]
            if not existing:
                suggestion = {
                    "name": provider_from_endpoint,
                    "display_name": provider_from_endpoint.replace("_", " ").title(),
                    "endpoint": endpoint,
                    "models": [],
                    "free_tier": False,
                    "source": source_name,
                    "confidence": "high",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
                suggestions.append(suggestion)

    return suggestions


def _find_endpoint_for_provider(content: str, provider_name: str) -> Optional[str]:
    """Find API endpoint for a specific provider in the content.

    Args:
        content: Source content
        provider_name: Provider name to search for

    Returns:
        API endpoint URL or None
    """
    # Look for endpoint near provider name
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if provider_name.lower() in line.lower():
            # Check surrounding lines for URLs
            context = "\n".join(lines[max(0, i-3):min(len(lines), i+5)])
            for pattern in API_PATTERNS:
                match = re.search(pattern, context)
                if match:
                    return match.group(0)
    return None


def _extract_models_for_provider(content: str, provider_name: str) -> list[str]:
    """Extract model names for a provider.

    Args:
        content: Source content
        provider_name: Provider name

    Returns:
        List of model names
    """
    models = []
    # Look for model patterns near provider name
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if provider_name.lower() in line.lower():
            context = "\n".join(lines[max(0, i-2):min(len(lines), i+5)])
            # Common model patterns
            model_matches = re.findall(
                r'(?:model[:\s]+)?([a-zA-Z0-9_.-]+(?:-[0-9]+[bB]?(?:-instruct)?))',
                context,
                re.IGNORECASE
            )
            models.extend(model_matches[:5])  # Limit to 5 models

    return list(set(models))[:10]  # Deduplicate and limit


def _has_free_tier(content: str, provider_name: str) -> bool:
    """Check if a provider is mentioned as having a free tier.

    Args:
        content: Source content
        provider_name: Provider name

    Returns:
        True if free tier is mentioned
    """
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if provider_name.lower() in line.lower():
            context = "\n".join(lines[max(0, i-2):min(len(lines), i+5)]).lower()
            if any(term in context for term in ["free", "free tier", "free plan", "generous free"]):
                return True
    return False


def _extract_provider_from_endpoint(endpoint: str) -> Optional[str]:
    """Extract provider name from an API endpoint.

    Args:
        endpoint: API endpoint URL

    Returns:
        Provider name or None
    """
    # Common patterns
    patterns = [
        r"api\.([a-zA-Z0-9-]+)\.com",
        r"([a-zA-Z0-9-]+)\.ai/",
        r"([a-zA-Z0-9-]+)\.api\.",
    ]
    for pattern in patterns:
        match = re.search(pattern, endpoint)
        if match:
            name = match.group(1).lower()
            # Clean up common suffixes
            name = name.replace("-ai", "").replace("api", "")
            if name and len(name) > 2:
                return name
    return None


def _deduplicate_suggestions(suggestions: list[dict]) -> list[dict]:
    """Deduplicate suggestions by provider name.

    Args:
        suggestions: List of suggestions

    Returns:
        Deduplicated list
    """
    seen = {}
    for s in suggestions:
        name = s["name"]
        if name not in seen:
            seen[name] = s
        else:
            # Keep the one with higher confidence
            if s["confidence"] == "high" and seen[name]["confidence"] != "high":
                seen[name] = s
            # Keep the one with more models
            if len(s.get("models", [])) > len(seen[name].get("models", [])):
                seen[name] = s

    return list(seen.values())


def save_discovery_results(results: dict, data_dir: Path) -> None:
    """Save discovery results to disk.

    Args:
        results: Discovery results dict
        data_dir: Data directory path
    """
    output_path = data_dir / "discovery_suggestions.json"
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        logger.info("DISCOVERY: Saved results to %s", output_path)
    except Exception as e:
        logger.error("DISCOVERY: Failed to save results: %s", e)


def load_discovery_results(data_dir: Path) -> Optional[dict]:
    """Load discovery results from disk.

    Args:
        data_dir: Data directory path

    Returns:
        Discovery results dict or None
    """
    output_path = data_dir / "discovery_suggestions.json"
    if not output_path.exists():
        return None

    try:
        with open(output_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("DISCOVERY: Failed to load results: %s", e)
        return None
