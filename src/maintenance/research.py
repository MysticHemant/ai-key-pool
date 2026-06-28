"""Daily research module for AI Key Pool.

Collects real information from official public sources (web pages, RSS feeds),
then uses the configured AI provider to summarize and generate recommendations.
Never hallucinates missing news — only reports what is actually found online.
"""

import json
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from ..key_pool import KeyRotator, KeyManager
from ..providers.base_provider import ChatMessage
from ..providers.provider_factory import create_provider
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("research")

# ─── Source definitions ──────────────────────────────────────────────────────

NEWS_SOURCES = [
    {
        "provider": "openai",
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog",
        "selectors": ["article", "h2", "h3"],
    },
    {
        "provider": "anthropic",
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "selectors": ["article", "h2", "h3"],
    },
    {
        "provider": "google",
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/",
        "selectors": ["article", "h2", "h3"],
    },
    {
        "provider": "mistral",
        "name": "Mistral AI News",
        "url": "https://mistral.ai/news/",
        "selectors": ["article", "h2", "h3"],
    },
]

RSS_SOURCES = [
    {
        "provider": "github",
        "name": "GitHub Models",
        "url": "https://github.blog/feed/",
    },
    {
        "provider": "huggingface",
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog/feed.xml",
    },
    {
        "provider": "together",
        "name": "Together AI Blog",
        "url": "https://www.together.ai/blog/rss.xml",
    },
    {
        "provider": "cohere",
        "name": "Cohere Blog",
        "url": "https://cohere.com/blog/rss.xml",
    },
]

# Direct announcement pages (fetched and parsed for headlines)
ANNOUNCEMENT_PAGES = [
    {
        "provider": "groq",
        "name": "Groq Blog",
        "url": "https://groq.com/blog/",
        "selectors": ["article", "h2", "h3", "h4"],
    },
    {
        "provider": "fireworks",
        "name": "Fireworks AI Blog",
        "url": "https://fireworks.ai/blog",
        "selectors": ["article", "h2", "h3"],
    },
    {
        "provider": "aws",
        "name": "AWS Bedrock Blog",
        "url": "https://aws.amazon.com/blogs/machine-learning/",
        "selectors": ["article", "h2", "h3"],
    },
    {
        "provider": "cloudflare",
        "name": "Cloudflare AI Blog",
        "url": "https://blog.cloudflare.com/tag/ai/",
        "selectors": ["article", "h2", "h3"],
    },
]

CUTOFF_DAYS = 30


# ─── Web fetching ────────────────────────────────────────────────────────────


def _fetch_url(url: str, timeout: float = 15.0) -> Optional[str]:
    """Fetch a URL and return the response text.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Response text or None on failure
    """
    headers = {
        "User-Agent": "AIKeyPool-ResearchBot/1.0 (github.com/ai-key-pool)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
    except httpx.HTTPStatusError as e:
        logger.warning("HTTP %d fetching %s", e.response.status_code, url)
    except httpx.RequestError as e:
        logger.warning("Request error fetching %s: %s", url, e)
    return None


def _extract_text_blocks(html: str, selectors: list[str]) -> list[str]:
    """Extract text content from HTML using CSS selectors.

    Args:
        html: Raw HTML string
        selectors: CSS selectors to match elements

    Returns:
        List of extracted text strings
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    texts = []
    for selector in selectors:
        for el in soup.select(selector):
            text = el.get_text(separator=" ", strip=True)
            if text and len(text) > 20:
                texts.append(text)

    return texts


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS/Atom feed and extract entries.

    Args:
        xml_text: Raw XML text

    Returns:
        List of dicts with 'title', 'link', 'published', 'summary'
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed — skipping RSS")
        return []

    feed = feedparser.parse(xml_text)
    entries = []
    for entry in feed.entries[:50]:  # limit to recent 50
        published = ""
        if hasattr(entry, "published"):
            published = entry.published
        elif hasattr(entry, "updated"):
            published = entry.updated

        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary
        elif hasattr(entry, "description"):
            summary = entry.description

        entries.append({
            "title": getattr(entry, "title", ""),
            "link": getattr(entry, "link", ""),
            "published": published,
            "summary": summary[:500] if summary else "",
        })
    return entries


# ─── Source collection ───────────────────────────────────────────────────────


def collect_web_news() -> list[dict]:
    """Collect news headlines from official provider web pages.

    Returns:
        List of raw findings dicts
    """
    findings = []
    for source in NEWS_SOURCES + ANNOUNCEMENT_PAGES:
        logger.info("Fetching %s (%s)", source["name"], source["url"])
        html = _fetch_url(source["url"])
        if not html:
            continue
        selectors = source.get("selectors", ["article", "h2"])
        texts = _extract_text_blocks(html, selectors)
        for text in texts[:20]:  # limit per source
            findings.append({
                "provider": source["provider"],
                "source_name": source["name"],
                "source_url": source["url"],
                "title": text[:200],
                "type": "web_news",
            })
    return findings


def collect_rss_news() -> list[dict]:
    """Collect news from RSS feeds.

    Returns:
        List of raw findings dicts
    """
    findings = []
    for source in RSS_SOURCES:
        logger.info("Fetching RSS %s (%s)", source["name"], source["url"])
        xml = _fetch_url(source["url"])
        if not xml:
            continue
        entries = _parse_rss(xml)
        for entry in entries:
            findings.append({
                "provider": source["provider"],
                "source_name": source["name"],
                "source_url": entry.get("link", source["url"]),
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "type": "rss_news",
            })
    return findings


def deduplicate_findings(findings: list[dict]) -> list[dict]:
    """Remove duplicate findings based on title similarity.

    Args:
        findings: Raw list of findings

    Returns:
        Deduplicated list
    """
    seen_hashes: set[str] = set()
    unique = []
    for f in findings:
        key = f.get("title", "").lower().strip()[:100]
        h = hashlib.md5(key.encode()).hexdigest()
        if h not in seen_hashes and key:
            seen_hashes.add(h)
            unique.append(f)
    return unique


def collect_all_sources() -> list[dict]:
    """Collect and deduplicate from all sources.

    Returns:
        Deduplicated list of raw findings
    """
    all_findings = []
    all_findings.extend(collect_web_news())
    all_findings.extend(collect_rss_news())
    unique = deduplicate_findings(all_findings)
    logger.info("Collected %d raw findings, %d unique", len(all_findings), len(unique))
    return unique


# ─── LLM summarization ──────────────────────────────────────────────────────


RESEARCH_PROMPT = """You are an AI provider research assistant. You will receive raw findings collected from official public sources (blogs, RSS feeds, announcement pages) about AI API providers.

Analyze the findings and respond ONLY with valid JSON (no markdown fences).

For each finding, determine:
- provider: the provider name
- model: any specific model mentioned (or null)
- description: a concise description
- url: the source URL
- type: one of "provider", "model", "free_tier", "pricing", "deprecation", "announcement"
- action: one of "add_key", "monitor", "update", "none"
- confidence: "high" if directly from official source, "medium" if inferred, "low" if uncertain

Also identify:
- pricing changes
- free tier changes
- new models
- API deprecations
- breaking changes

Respond with JSON in this exact format:
{
  "findings": [
    {
      "provider": "...",
      "model": "...",
      "description": "...",
      "url": "...",
      "type": "...",
      "action": "...",
      "confidence": "..."
    }
  ],
  "summary": "Brief summary of all changes",
  "new_providers": ["list of new provider names if any"],
  "new_models": ["list of new model names if any"],
  "pricing_changes": ["list of pricing changes if any"],
  "free_tier_changes": ["list of free tier changes if any"],
  "breaking_changes": ["list of breaking changes if any"]
}

Only include official, legitimate providers. Do not recommend leaked or unauthorized API keys.
If no relevant findings exist, return empty arrays with a summary explaining why."""


def _llm_summarize(
    raw_findings: list[dict],
    config: Config,
    key_manager: KeyManager,
) -> Optional[dict]:
    """Use the configured AI provider to summarize raw findings.

    Args:
        raw_findings: Deduplicated raw findings from web sources
        config: System configuration
        key_manager: Key manager instance

    Returns:
        Parsed JSON dict from LLM, or None on failure
    """
    rotator = KeyRotator(config, key_manager)
    provider_name = config.active_provider

    # Diagnostic: log registry state for this provider
    all_providers = key_manager.registry.get_all_providers()
    healthy_keys = key_manager.registry.get_healthy_keys(provider_name)
    all_provider_keys = key_manager.registry.get_keys_for_provider(provider_name)
    logger.info(
        "RESEARCH DIAGNOSTIC: provider=%s, all_providers=%s, healthy_keys=%d, all_keys=%d",
        provider_name, all_providers, len(healthy_keys), len(all_provider_keys),
    )
    for k in all_provider_keys:
        logger.info(
            "RESEARCH DIAGNOSTIC: key=%s status=%s failure_count=%d success_count=%d",
            k.key_id, k.status.value, k.failure_count, k.success_count,
        )

    try:
        provider = create_provider(provider_name)
    except ValueError as e:
        logger.error("Cannot summarize — invalid provider: %s", e)
        return None

    # Build context from raw findings
    context_lines = []
    for f in raw_findings[:60]:  # limit context size
        line = f"[{f.get('provider', '?')}] {f.get('title', '')}"
        if f.get("summary"):
            line += f" — {f['summary'][:200]}"
        if f.get("published"):
            line += f" (published: {f['published']})"
        if f.get("source_url"):
            line += f" ({f['source_url']})"
        context_lines.append(line)

    context = "\n".join(context_lines)
    user_prompt = f"Here are the raw findings from official sources:\n\n{context}\n\nAnalyze and produce the JSON summary."

    messages = [
        ChatMessage(role="system", content="You are a research assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=f"{RESEARCH_PROMPT}\n\n{user_prompt}"),
    ]

    def request_fn(api_key: str):
        return provider.chat(api_key, "gpt-4o-mini", messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)

    if not result.success or not result.response:
        logger.error("LLM summarization failed: %s", result.error)
        return None

    return _parse_findings(result.response.content)


def _parse_findings(content: str) -> Optional[dict]:
    """Parse LLM response into structured findings.

    Args:
        content: Raw LLM response text

    Returns:
        Parsed dict or None
    """
    try:
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response as JSON")
        return None


def _empty_research_result(reason: str) -> dict:
    """Return a minimal research result when collection or summarization fails.

    Args:
        reason: Explanation of why research could not proceed

    Returns:
        Empty findings dict
    """
    return {
        "findings": [],
        "summary": reason,
        "new_providers": [],
        "new_models": [],
        "pricing_changes": [],
        "free_tier_changes": [],
        "breaking_changes": [],
    }


# ─── History ─────────────────────────────────────────────────────────────────


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


# ─── Public API ──────────────────────────────────────────────────────────────


def research_providers(
    config: Config,
    key_manager: KeyManager,
    history_path: Path,
) -> dict:
    """Run provider research using real web data.

    Workflow:
    1. Collect data from official public sources (web pages + RSS).
    2. Deduplicate findings.
    3. Send to configured LLM for summarization and recommendations.
    4. Save to history.

    On any failure, returns previous history or empty result.
    Never raises — the caller can always continue maintenance.

    Args:
        config: System configuration
        key_manager: Key manager instance
        history_path: Path to research_history.json

    Returns:
        Research findings dict with 'findings', 'summary', etc.
    """
    start_time = time.monotonic()

    # Step 1: Collect from all sources
    logger.info("STEP START: Collecting from official sources")
    try:
        raw_findings = collect_all_sources()
    except Exception as e:
        logger.error("Source collection failed: %s — using previous history", e)
        raw_findings = []
    elapsed = time.monotonic() - start_time
    logger.info("STEP END: Source collection — %d findings in %.1fs", len(raw_findings), elapsed)

    # Step 2: Summarize with LLM
    summarized = None
    if raw_findings:
        logger.info("STEP START: LLM summarization of %d findings", len(raw_findings))
        llm_start = time.monotonic()
        try:
            summarized = _llm_summarize(raw_findings, config, key_manager)
        except Exception as e:
            logger.error("LLM summarization failed: %s", e)
            summarized = None
        elapsed = time.monotonic() - llm_start
        logger.info("STEP END: LLM summarization in %.1fs", elapsed)

    # Step 3: Build final result
    if summarized:
        findings_result = summarized
        findings_result["_raw_count"] = len(raw_findings)
        findings_result["_success"] = True
    elif raw_findings:
        # LLM failed but we have raw data — return raw findings in expected format
        findings_result = _build_raw_fallback(raw_findings)
        findings_result["_success"] = True
        findings_result["_llm_failed"] = True
    else:
        # No sources could be fetched
        findings_result = _empty_research_result(
            "No data could be collected from official sources. "
            "Check network connectivity and source availability."
        )
        findings_result["_success"] = False

    # Step 4: Merge with history
    history = _load_history(history_path)
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "findings": findings_result,
        "raw_count": len(raw_findings),
        "has_llm_summary": summarized is not None,
    }
    history["entries"].append(entry)
    history["entries"] = history["entries"][-30:]  # Keep last 30 days
    _save_history(history_path, history)

    total_findings = len(findings_result.get("findings", []))
    logger.info(
        "Research complete — %d findings, llm=%s, history=%d entries",
        total_findings,
        "yes" if summarized else "no",
        len(history["entries"]),
    )
    return findings_result


def _build_raw_fallback(raw_findings: list[dict]) -> dict:
    """Build a structured result from raw findings when LLM is unavailable.

    Args:
        raw_findings: Raw collected findings

    Returns:
        Structured findings dict
    """
    findings = []
    for rf in raw_findings:
        findings.append({
            "provider": rf.get("provider", "unknown"),
            "model": None,
            "description": rf.get("title", ""),
            "url": rf.get("source_url", ""),
            "type": "announcement",
            "action": "monitor",
            "confidence": "medium",
        })
    return {
        "findings": findings,
        "summary": f"Collected {len(findings)} findings from official sources (LLM summarization unavailable)",
        "new_providers": [],
        "new_models": [],
        "pricing_changes": [],
        "free_tier_changes": [],
        "breaking_changes": [],
    }
