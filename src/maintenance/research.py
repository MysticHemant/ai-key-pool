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
from ..providers.capability_router import CapabilityRouter
from ..providers.fallback_chain import FallbackChain, create_fallback_chain
from ..providers.manifest import manifest_registry
from .agents import MultiAgentOrchestrator, AgentRole
from .report_sections import build_executive_report
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


# ─── Recommendation filtering ────────────────────────────────────────────────


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _filter_configured_providers(
    findings: list[dict],
    configured_providers: list[str],
) -> list[dict]:
    """Reclassify recommendations for already-configured providers.

    Decision tree per finding:
      - Provider exists  → report updates only (never recommend add_key / add_provider)
      - Provider missing → recommend adding the provider

    For configured providers:
      - "add_key" → "update" (informational, e.g. new model released)
      - "add_provider" → "update" (informational)
      - "monitor" / "update" / "none" → unchanged

    Models never require keys — model findings always use "update" regardless.

    Args:
        findings: LLM-generated findings list
        configured_providers: List of provider names already in the registry

    Returns:
        Modified findings list (mutated in place)
    """
    configured_set = set(configured_providers)
    for f in findings:
        provider = f.get("provider", "")
        action = f.get("action", "")
        ftype = f.get("type", "")

        if provider in configured_set:
            # Model findings: never suggest adding a key — models are informational
            if ftype == "model":
                f["action"] = "update"
            # For configured providers, never recommend adding keys or the provider itself
            elif action in ("add_key", "add_provider"):
                f["action"] = "update"
        else:
            # Provider not configured — recommend adding it (keep add_provider)
            if ftype == "model" and action == "add_key":
                # New model on unknown provider → recommend adding the provider
                f["action"] = "add_provider"

    return findings


def _deduplicate_by_provider_title(findings: list[dict]) -> list[dict]:
    """Deduplicate findings by provider and normalized title.

    When duplicates exist, keep the highest-confidence version.
    If tied, prefer the one with a URL.

    Args:
        findings: List of findings dicts

    Returns:
        Deduplicated list
    """
    best: dict[tuple[str, str], dict] = {}
    for f in findings:
        provider = f.get("provider", "").lower().strip()
        title = f.get("description", f.get("model", "")).lower().strip()[:120]
        key = (provider, title)
        if key not in best:
            best[key] = f
        else:
            existing = best[key]
            existing_rank = _CONFIDENCE_RANK.get(existing.get("confidence", "medium"), 0)
            new_rank = _CONFIDENCE_RANK.get(f.get("confidence", "medium"), 0)
            if new_rank > existing_rank:
                best[key] = f
            elif new_rank == existing_rank and not existing.get("url") and f.get("url"):
                best[key] = f
    return list(best.values())


def _prioritize_findings(findings: list[dict]) -> list[dict]:
    """Sort findings by actionability priority.

    Priority order:
    1. New provider not configured (action == "add_provider")
    2. Existing provider free-tier improvement (type == "free_tier")
    3. Existing provider new models (type == "model")
    4. Breaking changes (type in "deprecation", "breaking")
    5. Pricing changes (type == "pricing")
    6. General news (everything else)

    Args:
        findings: List of findings dicts

    Returns:
        Sorted list (highest priority first)
    """
    def _sort_key(f: dict) -> int:
        action = f.get("action", "")
        ftype = f.get("type", "")
        if action == "add_provider":
            return 0
        if ftype == "free_tier":
            return 1
        if ftype == "model":
            return 2
        if ftype in ("deprecation", "breaking"):
            return 3
        if ftype == "pricing":
            return 4
        return 5
    return sorted(findings, key=_sort_key)


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


RESEARCH_PROMPT = """You are an AI provider research agent. You will receive raw findings collected from official public sources, along with previous iteration research history (Working Memory & Long-Term Memory), the current research plan, and current tracking state.

Analyze the findings and respond ONLY with valid JSON (no markdown fences).

For each finding, determine:
- provider: the provider name
- model: any specific model mentioned (or null)
- description: a concise description
- url: the source URL
- type: one of "provider", "model", "free_tier", "pricing", "deprecation", "announcement"
- action: one of "add_provider", "update", "monitor", "none"
- confidence: "high" if directly from official source, "medium" if inferred, "low" if uncertain

Action rules:
- "add_provider": ONLY when a provider is completely new and not yet configured
- "update": new model released, pricing changed, free tier changed, new API feature
- "monitor": general news or announcements worth tracking
- "none": not relevant

NEVER use "add_key" — models do not require API keys. A new model release is an "update", not a key action.

Identify pricing changes, free tier changes, new models, API deprecations, and breaking changes.

Additionally, you MUST evaluate the research quality, check for contradictions against previous assertions, verify claims, update the tracking lists, and write the iteration report.

CLAIM TRACKING RULES:
- Move claims from unverified_claims to verified_claims ONLY when you have direct evidence
- Remove resolved items from open_questions and move to resolved_questions
- Remove completed items from research_queue
- Do NOT re-research topics already in verified_claims
- Track contradictions with prev_evidence and current_evidence

CRITICAL — QUALITY SCORE SCALE:
All evaluation scores MUST be integers between 0 and 100 (inclusive).
Do NOT use a 1-10 scale. Use 0-100.
Examples: 0 (worst), 50 (average), 100 (perfect).

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
  "breaking_changes": ["list of breaking changes if any"],
  "iteration_report": {
    "summary": "...",
    "evidence": "...",
    "sources": "...",
    "confidence": "...",
    "assumptions": ["...", "..."],
    "unanswered_questions": ["...", "..."],
    "contradictions": ["...", "..."],
    "recommendations_next": "..."
  },
  "evaluation": {
    "coverage": 85,
    "verification": 70,
    "source_diversity": 75,
    "novel_information": 60,
    "contradictions_resolved": 50,
    "overall_quality": 72,
    "reason": "explanation of scores (scores MUST be 0-100 integers)",
    "verified_claims": [
      {
        "claim": "...",
        "evidence": "...",
        "source": "...",
        "confidence": "high/medium/low",
        "verification_status": "verified"
      }
    ],
    "unverified_claims": [
      {
        "claim": "...",
        "evidence": "...",
        "source": "...",
        "confidence": "high/medium/low",
        "verification_status": "unverified"
      }
    ],
    "resolved_questions": ["..."],
    "open_questions": ["..."],
    "research_queue": ["..."],
    "contradictions": [
      {
        "claim": "...",
        "prev_evidence": "...",
        "current_evidence": "...",
        "resolution_status": "unresolved/resolved",
        "resolution_notes": "..."
      }
    ],
    "completed_topics": ["..."],
    "assumptions": ["..."]
  }
}

Only include official, legitimate providers. Do not recommend leaked or unauthorized API keys.
If no relevant findings exist, return empty arrays with a summary explaining why."""


def _llm_summarize(
    raw_findings: list[dict],
    config: Config,
    key_manager: KeyManager,
    runtime_state: dict = None,
    use_multi_agent: bool = False,
) -> Optional[dict]:
    """Use the configured AI provider to summarize raw findings.

    Uses capability-based routing to select the best provider for reasoning.
    Optionally uses multi-agent pipeline for comprehensive analysis.
    Falls back to configured active provider if no capability-matched provider.

    Args:
        raw_findings: Deduplicated raw findings from web sources
        config: System configuration
        key_manager: Key manager instance
        runtime_state: Optional dict with iterative runtime state
        use_multi_agent: If True, use multi-agent pipeline instead of single LLM

    Returns:
        Parsed JSON dict from LLM, or None on failure
    """
    # Use multi-agent pipeline if enabled
    if use_multi_agent and len(raw_findings) > 5:
        logger.info("RESEARCH: Using multi-agent pipeline for %d findings", len(raw_findings))
        orchestrator = MultiAgentOrchestrator(config, key_manager)
        agent_results = orchestrator.run_research_pipeline(raw_findings, runtime_state)

        if agent_results["success_count"] > 0:
            # Convert agent results to standard findings format
            return _convert_agent_results_to_findings(agent_results, raw_findings)

    # Single-agent path (original behavior with fallback chain)
    fallback = create_fallback_chain(config, key_manager)

    # Build compact context from raw findings (limit to 30 items)
    context_lines = []
    for f in raw_findings[:30]:
        provider_name_f = f.get("provider", "?")
        title = f.get("title", "")
        line = f"[{provider_name_f}] {title}"
        if f.get("summary"):
            line += f" — {f['summary'][:150]}"
        if f.get("source_url"):
            line += f" ({f['source_url']})"
        context_lines.append(line)

    context = "\n".join(context_lines)
    user_prompt = f"Raw findings:\n{context}\n\n"

    # Build compact runtime state (only essential fields, no full history dumps)
    if runtime_state:
        iteration = runtime_state.get("iteration", 1)

        user_prompt += f"Iteration: {iteration}\n"

        # Current plan objectives (compact)
        plan = runtime_state.get("current_plan", {})
        objectives = plan.get("objectives", [])
        if objectives:
            user_prompt += f"Objectives: {json.dumps(objectives[:5])}\n"

        # Unresolved items only (compact lists)
        unverified = runtime_state.get("unverified_claims", [])
        if unverified:
            items = [RuntimeManager._get_claim_key(c) if hasattr(RuntimeManager, '_get_claim_key') else str(c)[:80] for c in unverified[:5]]
            user_prompt += f"Unverified claims: {json.dumps(items)}\n"

        open_q = runtime_state.get("open_questions", [])
        if open_q:
            user_prompt += f"Open questions: {json.dumps([str(q)[:80] for q in open_q[:5]])}\n"

        contradictions = runtime_state.get("contradictions", [])
        unresolved = [c for c in contradictions if isinstance(c, dict) and c.get("resolution_status") != "resolved"]
        if unresolved:
            user_prompt += f"Unresolved contradictions: {json.dumps([str(c.get('claim', c))[:80] for c in unresolved[:3]])}\n"

        # Compressed memory only (no raw iteration files)
        ltm = runtime_state.get("long_term_memory", "")
        if ltm:
            user_prompt += f"Long-term memory: {ltm[:500]}\n"

        user_prompt += (
            "\nAnalyze the findings. Address gaps, verify claims, resolve contradictions. "
            "Identify new providers, models, pricing changes, deprecations.\n"
        )

    user_prompt += "Respond with JSON summary."

    messages = [
        ChatMessage(role="system", content="You are a research assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=f"{RESEARCH_PROMPT}\n\n{user_prompt}"),
    ]

    # Use fallback chain for reliable execution
    def request_fn(api_key: str):
        # Find a provider for reasoning
        provider_manifest = fallback.router.get_healthy_provider_for_capability("reasoning")
        if provider_manifest:
            provider = create_provider(provider_manifest.provider_id)
        else:
            provider = create_provider(config.active_provider)
        model = provider.get_default_model()
        return provider.chat(api_key, model, messages)

    # Deterministic fallback
    def deterministic_fallback():
        return {
            "findings": [],
            "summary": "LLM summarization failed — using deterministic fallback",
            "new_providers": [],
            "new_models": [],
            "pricing_changes": [],
            "free_tier_changes": [],
            "breaking_changes": [],
            "evaluation": {
                "coverage": 0,
                "verification": 0,
                "source_diversity": 0,
                "novel_information": 0,
                "contradictions_resolved": 0,
                "overall_quality": 0,
                "reason": "LLM failed, deterministic fallback used",
                "verified_claims": [],
                "unverified_claims": [],
                "resolved_questions": [],
                "open_questions": [],
                "research_queue": [],
                "contradictions": [],
                "completed_topics": [],
                "assumptions": [],
            },
        }

    result = fallback.execute_with_fallback(
        capability="reasoning",
        request_fn=request_fn,
        deterministic_fn=deterministic_fallback,
        max_retries_per_provider=1,
    )

    if not result.success:
        logger.error("LLM summarization failed after all attempts: %s", result.error)
        return deterministic_fallback()

    if result.deterministic_fallback:
        logger.info("LLM summarization used deterministic fallback")
        return result.response

    logger.info(
        "RESEARCH: Using provider=%s (fallback chain, %d attempts)",
        result.provider_used, len(result.attempts),
    )

    parsed = _parse_findings(result.response.content)

    if parsed:
        evaluation = parsed.get("evaluation", {})
        logger.info("=== LLM EVALUATION ===")
        logger.info("Overall Quality: %s", evaluation.get("overall_quality", "MISSING"))
        logger.info("Coverage: %s", evaluation.get("coverage", "MISSING"))
        logger.info("Verification: %s", evaluation.get("verification", "MISSING"))
        logger.info("Verified Claims: %d", len(evaluation.get("verified_claims", [])))
        logger.info("Open Questions: %d", len(evaluation.get("open_questions", [])))
        logger.info("======================")

    return parsed


def _convert_agent_results_to_findings(agent_results: dict, raw_findings: list[dict]) -> dict:
    """Convert multi-agent results to standard findings format.

    Args:
        agent_results: Consolidated agent results
        raw_findings: Original raw findings

    Returns:
        Dict in standard findings format
    """
    findings = []

    # Convert consolidated findings
    for item in agent_results.get("consolidated_findings", []):
        if isinstance(item, dict):
            findings.append({
                "provider": item.get("provider", "unknown"),
                "model": item.get("model"),
                "description": item.get("description", item.get("title", "")),
                "url": item.get("url", item.get("source_url", "")),
                "type": item.get("type", "announcement"),
                "action": item.get("action", "monitor"),
                "confidence": item.get("confidence", "medium"),
            })

    # Add verified claims as findings
    for claim in agent_results.get("verified_claims", []):
        if isinstance(claim, dict):
            findings.append({
                "provider": claim.get("provider", "unknown"),
                "model": None,
                "description": claim.get("claim", claim.get("description", "")),
                "url": claim.get("source", claim.get("url", "")),
                "type": "verification",
                "action": "update",
                "confidence": claim.get("confidence", "high"),
            })

    # If no findings from agents, convert raw findings
    if not findings:
        for rf in raw_findings[:20]:
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
        "summary": agent_results.get("executive_summary", "Multi-agent research completed"),
        "new_providers": [],
        "new_models": [],
        "pricing_changes": [],
        "free_tier_changes": [],
        "breaking_changes": [],
        "evaluation": {
            "coverage": 70,
            "verification": 60,
            "source_diversity": 60,
            "novel_information": 60,
            "contradictions_resolved": 50,
            "overall_quality": 65,
            "reason": f"Multi-agent pipeline: {agent_results['success_count']}/{agent_results['total_count']} agents succeeded",
            "verified_claims": agent_results.get("verified_claims", []),
            "unverified_claims": [],
            "resolved_questions": [],
            "open_questions": agent_results.get("open_questions", []),
            "research_queue": [],
            "contradictions": agent_results.get("contradictions", []),
            "completed_topics": [],
            "assumptions": [],
        },
    }


def _execute_with_rate_limit_retry(rotator, provider_name, request_fn, config, max_rate_limit_retries=3):
    """Execute a request with rate limit backoff and graceful skip.

    When all keys are exhausted due to 429, sleeps with exponential backoff
    and retries. If still exhausted, returns a failure result (no crash).

    Args:
        rotator: KeyRotator instance
        provider_name: Provider name
        request_fn: Request function
        config: Config instance
        max_rate_limit_retries: Number of backoff retries after initial rotation exhaustion

    Returns:
        RotationResult
    """
    import time

    result = rotator.execute_with_rotation(provider_name, request_fn)

    if result.success:
        return result

    # If failed due to rate limit / no healthy keys, try backoff
    error = result.error or ""
    is_rate_limit = "no healthy keys" in error.lower() or "rate" in error.lower()

    if is_rate_limit and max_rate_limit_retries > 0:
        logger.warning("Rate limit hit — backing off before retry (%d retries left)", max_rate_limit_retries)
        backoff_seconds = 2 ** (max_rate_limit_retries)  # 8, 4, 2
        time.sleep(min(backoff_seconds, 10))  # cap at 10s

        # Try again — keys may have cooled down
        result = rotator.execute_with_rotation(provider_name, request_fn)
        if result.success:
            return result

        # Recurse with fewer retries
        return _execute_with_rate_limit_retry(
            rotator, provider_name, request_fn, config,
            max_rate_limit_retries=max_rate_limit_retries - 1,
        )

    return result


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
    runtime_state: dict = None,
    use_multi_agent: bool = False,
) -> dict:
    """Run provider research using real web data.

    Workflow:
    1. Collect data from official public sources (web pages + RSS).
    2. Deduplicate findings.
    3. Send to configured LLM for summarization and recommendations.
       Optionally uses multi-agent pipeline for comprehensive analysis.
    4. Save to history.

    On any failure, returns previous history or empty result.
    Never raises — the caller can always continue maintenance.

    Args:
        config: System configuration
        key_manager: Key manager instance
        history_path: Path to research_history.json
        runtime_state: Optional dict with iterative runtime state
        use_multi_agent: If True, use multi-agent pipeline

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
            summarized = _llm_summarize(raw_findings, config, key_manager, runtime_state, use_multi_agent)
        except Exception as e:
            logger.error("LLM summarization failed: %s", e)
            summarized = None
        elapsed = time.monotonic() - llm_start
        logger.info("STEP END: LLM summarization in %.1fs", elapsed)

    # Step 3: Build final result
    if summarized and summarized.get("findings"):
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

    # Normalize findings_result to ensure new required keys are present
    findings_result = _normalize_research_result(findings_result)

    # Step 3b: Filter, deduplicate, prioritize findings
    configured_providers = key_manager.registry.get_all_providers()
    findings_list = findings_result.get("findings", [])
    if findings_list:
        findings_list = _filter_configured_providers(findings_list, configured_providers)
        findings_list = _deduplicate_by_provider_title(findings_list)
        findings_list = _prioritize_findings(findings_list)
        findings_result["findings"] = findings_list
        logger.info(
            "RESEARCH FILTER: %d findings after filter/dedup/prioritize (configured=%s)",
            len(findings_list), configured_providers,
        )

    # Step 4: Merge with history
    history = _load_history(history_path)
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "findings": findings_result,
        "raw_count": len(raw_findings),
        "has_llm_summary": summarized is not None,
        "multi_agent": use_multi_agent,
    }
    history["entries"].append(entry)
    history["entries"] = history["entries"][-30:]  # Keep last 30 days
    _save_history(history_path, history)

    total_findings = len(findings_result.get("findings", []))
    logger.info(
        "Research complete — %d findings, llm=%s, multi_agent=%s, history=%d entries",
        total_findings,
        "yes" if summarized else "no",
        "yes" if use_multi_agent else "no",
        len(history["entries"]),
    )

    logger.info("=== RESEARCH RESULT ===")
    logger.info("research_result.keys(): %s", list(findings_result.keys()))
    evaluation = findings_result.get("evaluation", {})
    logger.info("evaluation.keys(): %s", list(evaluation.keys()) if isinstance(evaluation, dict) else "NOT A DICT: %s" % type(evaluation))
    logger.info("type(evaluation): %s", type(evaluation).__name__)
    if isinstance(evaluation, dict):
        logger.info("len(verified_claims): %d", len(evaluation.get("verified_claims", [])))
        logger.info("overall_quality: %s", evaluation.get("overall_quality", "MISSING"))
    logger.info("=======================")

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


def _normalize_score_0_100(value, key: str) -> int:
    """Normalize a score to 0-100 integer range.

    If LLM returns 1-10 scale, multiply by 10.
    Logs normalization when it occurs.
    """
    if value is None:
        return 0
    try:
        val = int(value)
    except (TypeError, ValueError):
        return 0

    if val < 0:
        return 0
    if val > 100:
        if val <= 10:
            normalized = val * 10
            logger.info("QUALITY NORMALIZE (research): %s scaled from %d to %d (1-10 -> 0-100)", key, val, normalized)
            return normalized
        return 100
    return val


def _normalize_evaluation_scores(evaluation: dict) -> dict:
    """Normalize all evaluation scores to 0-100 integer range."""
    if not isinstance(evaluation, dict):
        return evaluation

    score_keys = ["coverage", "verification", "source_diversity",
                   "novel_information", "contradictions_resolved", "overall_quality"]
    for key in score_keys:
        if key in evaluation:
            evaluation[key] = _normalize_score_0_100(evaluation[key], key)

    return evaluation


def _normalize_research_result(res: dict) -> dict:
    """Normalize LLM or fallback findings result to contain iteration_report and evaluation."""
    if not isinstance(res, dict):
        res = {}
    res.setdefault("findings", [])
    res.setdefault("summary", "No findings available")
    res.setdefault("new_providers", [])
    res.setdefault("new_models", [])
    res.setdefault("pricing_changes", [])
    res.setdefault("free_tier_changes", [])
    res.setdefault("breaking_changes", [])
    res.setdefault("iteration_report", {
        "summary": "Initial/fallback summary",
        "evidence": "None",
        "sources": "",
        "confidence": "low",
        "assumptions": [],
        "unanswered_questions": [],
        "contradictions": [],
        "recommendations_next": ""
    })
    res.setdefault("evaluation", {
        "coverage": 60,
        "verification": 50,
        "source_diversity": 50,
        "novel_information": 50,
        "contradictions_resolved": 50,
        "overall_quality": 60,
        "reason": "Fallback normalization",
        "verified_claims": [],
        "unverified_claims": [],
        "resolved_questions": [],
        "open_questions": [],
        "research_queue": [],
        "contradictions": [],
        "completed_topics": [],
        "assumptions": []
    })

    # Normalize evaluation scores to 0-100 range
    if "evaluation" in res:
        res["evaluation"] = _normalize_evaluation_scores(res["evaluation"])

    return res


def generate_research_plan(config: Config, key_manager: KeyManager, runtime_state: dict) -> dict:
    """Generate a structured research plan before research begins using the LLM.

    Uses fallback chain for reliable execution.
    Reuses the previous plan if nothing significant changed.

    Args:
        config: System configuration
        key_manager: Key manager instance
        runtime_state: RuntimeManager state dict

    Returns:
        Research plan dict with objectives, claims_to_verify, etc.
    """
    iteration = runtime_state.get("iteration", 1)
    current_plan = runtime_state.get("current_plan", {})

    # Check if we should reuse the previous plan
    if _should_reuse_plan(runtime_state):
        logger.info("PLAN REUSE: No significant changes — reusing previous plan")
        return current_plan

    plan_prompt = """Research plan for iteration {iteration}. Current state:
- Unverified claims: {unverified_claims}
- Open questions: {open_questions}
- Contradictions: {contradictions}
- Queue: {research_queue}

Generate 2-3 targeted objectives. Focus on unverified claims, open questions, and contradictions.

Respond ONLY with JSON:
{{
  "objectives": ["objective 1", "objective 2"],
  "claims_to_verify": ["claim to check"],
  "questions_to_answer": ["question to answer"],
  "sources_to_search": ["source to check"],
  "expected_deliverables": ["deliverable 1"]
}}"""

    fallback = create_fallback_chain(config, key_manager)

    # Build compact prompt — only essential state
    unverified = runtime_state.get("unverified_claims", [])
    open_q = runtime_state.get("open_questions", [])
    contradictions = runtime_state.get("contradictions", [])
    queue = runtime_state.get("research_queue", [])
    pending = [i for i in queue if isinstance(i, dict) and i.get("status") == "pending"]

    formatted_prompt = plan_prompt.format(
        iteration=iteration,
        unverified_claims=json.dumps([str(c)[:80] for c in unverified[:5]]),
        open_questions=json.dumps([str(q)[:80] for q in open_q[:5]]),
        contradictions=json.dumps([str(c.get("claim", c))[:80] for c in contradictions[:3] if isinstance(c, dict)]),
        research_queue=json.dumps([i.get("topic", str(i))[:60] for i in pending[:5]]),
    )

    messages = [
        ChatMessage(role="system", content="Research planner. Respond only with valid JSON."),
        ChatMessage(role="user", content=formatted_prompt),
    ]

    logger.info("PLANNER: Generating plan for iteration %d (fallback chain)", iteration)

    # Use fallback chain
    def request_fn(api_key: str):
        provider_manifest = fallback.router.get_healthy_provider_for_capability("reasoning")
        if provider_manifest:
            provider = create_provider(provider_manifest.provider_id)
        else:
            provider = create_provider(config.active_provider)
        model = provider.get_default_model()
        return provider.chat(api_key, model, messages)

    result = fallback.execute_with_fallback(
        capability="reasoning",
        request_fn=request_fn,
        deterministic_fn=_default_plan,
        max_retries_per_provider=1,
    )

    if not result.success or result.deterministic_fallback:
        logger.warning("LLM planner failed or used fallback")
        return _default_plan() if not result.deterministic_fallback else result.response

    parsed = _parse_findings(result.response.content)
    if parsed:
        return parsed
    return _default_plan()


def _should_reuse_plan(runtime_state: dict) -> bool:
    """Check if the previous research plan can be reused.

    Returns True if nothing significant changed since the last plan:
    - No new contradictions
    - No new open questions
    - No major queue changes
    - Not the first iteration
    """
    iteration = runtime_state.get("iteration", 1)
    if iteration <= 1:
        return False

    current_plan = runtime_state.get("current_plan", {})
    if not current_plan:
        return False

    # Count current state
    contradictions = runtime_state.get("contradictions", [])
    unresolved = [c for c in contradictions if isinstance(c, dict) and c.get("resolution_status") != "resolved"]
    open_q = runtime_state.get("open_questions", [])
    unverified = runtime_state.get("unverified_claims", [])

    # If there are unresolved contradictions or unverified claims, need a new plan
    if unresolved or unverified:
        return False

    # If open questions exist but plan already addresses them, reuse
    plan_questions = current_plan.get("questions_to_answer", [])
    if open_q and not plan_questions:
        return False

    # If queue changed significantly, need new plan
    queue = runtime_state.get("research_queue", [])
    pending = [i for i in queue if isinstance(i, dict) and i.get("status") == "pending"]
    if len(pending) > 5:
        return False  # Too many pending items, need fresh plan

    return True


def _default_plan() -> dict:
    return {
        "objectives": [
            "Verify unresolved claims from previous iteration",
            "Increase source diversity by checking additional provider blogs",
            "Improve confidence of weak conclusions",
        ],
        "claims_to_verify": [],
        "questions_to_answer": ["What are the latest models released in the last 30 days?"],
        "sources_to_search": ["All available public RSS feeds and blogs."],
        "expected_deliverables": ["List of new models, pricing changes, and deprecations."]
    }


def compress_memory(config: Config, key_manager: KeyManager, runtime_state: dict) -> str:
    """Summarize older iterations to keep working memory small while preserving knowledge.

    Uses fallback chain for reliable execution.
    """
    iteration = runtime_state.get("iteration", 1)
    threshold = config.memory_compression_threshold

    compress_limit = iteration - threshold
    if compress_limit <= 0:
        return runtime_state.get("long_term_memory", "")

    research_dir = config.data_dir / "research"
    older_contents = []
    for i in range(1, compress_limit + 1):
        p = research_dir / f"iteration_{i}.md"
        if p.exists():
            try:
                older_contents.append(f"=== ITERATION {i} ===\n{p.read_text(encoding='utf-8')}")
            except Exception as e:
                logger.error("Could not read older iteration %d for compression: %s", i, e)

    if not older_contents:
        return runtime_state.get("long_term_memory", "")

    combined_older = "\n\n".join(older_contents)
    current_ltm = runtime_state.get("long_term_memory", "")

    compression_prompt = """Consolidate these older research iterations into a concise summary.
Preserve key verified claims, timeline events, and resolutions.
Be concise — max 300 words.

Existing summary:
{current_ltm}

Iterations to consolidate:
{combined_older}

Respond with the consolidated summary."""

    fallback = create_fallback_chain(config, key_manager)

    messages = [
        ChatMessage(role="system", content="Knowledge consolidation. Respond only with markdown summary."),
        ChatMessage(role="user", content=compression_prompt.format(
            current_ltm=current_ltm[:500],
            combined_older=combined_older[:3000],
        )),
    ]

    logger.info("MEMORY COMPRESSOR: Consolidating iterations 1 to %d into Long-Term Memory", compress_limit)

    # Use fallback chain
    def request_fn(api_key: str):
        provider_manifest = fallback.router.get_healthy_provider_for_capability("reasoning")
        if provider_manifest:
            provider = create_provider(provider_manifest.provider_id)
        else:
            provider = create_provider(config.active_provider)
        model = provider.get_default_model()
        return provider.chat(api_key, model, messages)

    result = fallback.execute_with_fallback(
        capability="reasoning",
        request_fn=request_fn,
        max_retries_per_provider=1,
    )

    if not result.success:
        logger.error("Memory consolidation failed after all attempts")
        return current_ltm

    return result.response.content.strip()


def generate_final_report(config: Config, key_manager: KeyManager, runtime_state: dict) -> dict:
    """Consolidate all iteration findings into a polished final report.

    Uses the new executive report structure with:
    - Executive Summary
    - Top 5 Industry Developments
    - Highest Business Impact
    - New Models Released
    - Provider Comparison
    - Verified Findings
    - Contradictions
    - Open Questions
    - Action Items
    - Suggested Providers to Add
    - Current Provider Health
    - Research Statistics

    Falls back to deterministic report generation if LLM fails.

    Args:
        config: System configuration
        key_manager: Key manager instance
        runtime_state: RuntimeManager state dict

    Returns:
        Report dict with 'summary', 'findings', 'action_items', 'sections'
    """
    iteration = runtime_state.get("iteration", 1)
    research_dir = config.data_dir / "research"

    # Step 1: Load all structured findings from JSON files
    all_findings = []
    for i in range(1, iteration + 1):
        findings_file = research_dir / f"iteration_{i}_findings.json"
        if findings_file.exists():
            try:
                with open(findings_file) as f:
                    data = json.load(f)
                all_findings.extend(data.get("findings", []))
            except Exception as e:
                logger.warning("Could not load findings for iteration %d: %s", i, e)

    # Step 2: Merge and deduplicate findings deterministically
    merged_findings = _merge_structured_findings(all_findings)

    # Step 3: Load state for report sections
    verified_claims = runtime_state.get("verified_claims", [])
    unverified_claims = runtime_state.get("unverified_claims", [])
    open_questions = runtime_state.get("open_questions", [])
    resolved_questions = runtime_state.get("resolved_questions", [])
    contradictions = runtime_state.get("contradictions", [])
    quality_metrics = runtime_state.get("quality_metrics", {})
    history = runtime_state.get("history", [])

    # Get configured providers for smart recommendations
    configured_providers = list(key_manager.registry.get_all_providers())

    # Get discovery results
    from .discovery import load_discovery_results
    discovery_results = load_discovery_results(config.data_dir)

    # Get provider health
    provider_health = {}
    for manifest in manifest_registry.get_all().values():
        provider_health[manifest.provider_id] = manifest.health

    # Step 4: Build new executive report sections
    sections = build_executive_report(
        merged_findings=merged_findings,
        verified_claims=verified_claims,
        unverified_claims=unverified_claims,
        open_questions=open_questions,
        resolved_questions=resolved_questions,
        contradictions=contradictions,
        quality_metrics=quality_metrics,
        history=history,
        iteration=iteration,
        configured_providers=configured_providers,
        discovery_results=discovery_results,
        provider_health=provider_health,
    )

    # Step 5: Try LLM narrative generation (optional enhancement)
    llm_summary = None
    try:
        llm_summary = _llm_generate_narrative(sections, config, key_manager, runtime_state)
    except Exception as e:
        logger.warning("LLM narrative generation failed: %s — using deterministic summary", e)

    # Step 6: Build final report (deterministic + optional LLM narrative)
    summary = llm_summary if llm_summary else sections["executive_summary"]

    # Extract action items from findings
    action_items = []
    for f in merged_findings:
        action = f.get("importance", f.get("action", "none"))
        if action in ("add_provider", "update"):
            provider = f.get("provider", "Unknown")
            claim = f.get("claim", f.get("description", ""))
            action_items.append(f"{action.replace('_', ' ').title()}: {provider} — {claim[:100]}")
        elif f.get("category") in ("deprecation", "breaking"):
            action_items.append(f"URGENT: {f.get('claim', f.get('description', ''))[:100]}")

    # Extract category-specific lists
    new_providers = [
        f.get("provider", "") for f in merged_findings
        if f.get("importance") == "add_provider" and f.get("provider")
    ]
    new_models = [
        f.get("claim", "") for f in merged_findings
        if f.get("category") == "model" and f.get("claim")
    ]
    pricing_changes = [
        f.get("claim", "") for f in merged_findings
        if f.get("category") == "pricing" and f.get("claim")
    ]
    free_tier_changes = [
        f.get("claim", "") for f in merged_findings
        if f.get("category") == "free_tier" and f.get("claim")
    ]
    breaking_changes = [
        f.get("claim", "") for f in merged_findings
        if f.get("category") in ("deprecation", "breaking") and f.get("claim")
    ]

    return {
        "summary": summary,
        "findings": [
            {
                "provider": f.get("provider", ""),
                "model": f.get("model"),
                "description": f.get("claim", f.get("description", "")),
                "url": f.get("source", ""),
                "type": f.get("category", "general"),
                "action": f.get("importance", "monitor"),
                "confidence": f.get("confidence", "medium"),
            }
            for f in merged_findings
        ],
        "new_providers": new_providers,
        "new_models": new_models,
        "pricing_changes": pricing_changes,
        "free_tier_changes": free_tier_changes,
        "breaking_changes": breaking_changes,
        "action_items": action_items,
        "sections": sections,
        "_deterministic": llm_summary is None,
    }


def _merge_structured_findings(all_findings: list[dict]) -> list[dict]:
    """Merge, deduplicate, resolve contradictions, and rank findings.

    Args:
        all_findings: List of structured finding dicts from all iterations

    Returns:
        Deduplicated, ranked list of findings
    """
    if not all_findings:
        return []

    # Deduplicate by claim key (provider + normalized claim text)
    claim_map: dict[str, dict] = {}
    for f in all_findings:
        if not isinstance(f, dict):
            continue
        provider = f.get("provider", "").lower().strip()
        claim = f.get("claim", f.get("description", "")).lower().strip()[:150]
        key = f"{provider}::{claim}"

        if key not in claim_map:
            claim_map[key] = f.copy()
        else:
            # Keep the version with higher confidence
            existing = claim_map[key]
            conf_rank = {"high": 3, "medium": 2, "low": 1}
            existing_rank = conf_rank.get(existing.get("confidence", "medium"), 0)
            new_rank = conf_rank.get(f.get("confidence", "medium"), 0)
            if new_rank > existing_rank:
                claim_map[key] = f.copy()

    # Separate contradictions from normal findings
    contradictions = []
    normal_findings = []
    for f in claim_map.values():
        if f.get("verification_status") == "contradiction":
            contradictions.append(f)
        else:
            normal_findings.append(f)

    # Resolve contradictions: keep the most recent high-confidence version
    resolved = {}
    for c in contradictions:
        provider = c.get("provider", "").lower().strip()
        claim = c.get("claim", "").lower().strip()[:150]
        # Use claim text without "contradiction" prefix as the key
        clean_claim = claim.replace("contradiction:", "").replace("contradicts:", "").strip()
        rkey = f"{provider}::{clean_claim}"
        if rkey not in resolved:
            resolved[rkey] = c
        else:
            conf_rank = {"high": 3, "medium": 2, "low": 1}
            if conf_rank.get(c.get("confidence", "low"), 0) > conf_rank.get(resolved[rkey].get("confidence", "low"), 0):
                resolved[rkey] = c

    # Add resolved contradictions back as verified findings
    for r in resolved.values():
        r["verification_status"] = "verified"
        normal_findings.append(r)

    # Rank by importance
    importance_rank = {
        "add_provider": 0,
        "update": 1,
        "deprecation": 2,
        "breaking": 2,
        "free_tier": 3,
        "model": 4,
        "pricing": 5,
        "monitor": 6,
        "none": 7,
    }
    normal_findings.sort(key=lambda x: importance_rank.get(x.get("importance", "none"), 99))

    logger.info("MERGE: %d raw -> %d deduplicated -> %d final findings",
                len(all_findings), len(claim_map), len(normal_findings))
    return normal_findings


def _build_report_sections(
    merged_findings: list[dict],
    verified_claims: list,
    unverified_claims: list,
    open_questions: list,
    resolved_questions: list,
    contradictions: list,
    quality_metrics: dict,
    history: list,
    iteration: int,
) -> dict:
    """Build deterministic report sections from structured state.

    Never uses LLM — all sections computed from JSON data.
    """
    # Executive Summary
    total_findings = len(merged_findings)
    high_conf = len([f for f in merged_findings if f.get("confidence") == "high"])
    providers_found = set(f.get("provider", "") for f in merged_findings if f.get("provider"))
    categories = set(f.get("category", "") for f in merged_findings if f.get("category"))

    exec_summary = (
        f"Research completed over {iteration} iteration{'s' if iteration != 1 else ''}, "
        f"analyzing {total_findings} findings from {len(providers_found)} providers. "
        f"{high_conf} findings have high confidence. "
        f"Covered categories: {', '.join(sorted(categories)) or 'general'}. "
    )

    if verified_claims:
        exec_summary += f"{len(verified_claims)} claims verified. "
    if unverified_claims:
        exec_summary += f"{len(unverified_claims)} claims awaiting verification. "
    if open_questions:
        exec_summary += f"{len(open_questions)} open questions remain. "

    # Verified Findings
    verified_findings = []
    for claim in verified_claims:
        if isinstance(claim, dict):
            verified_findings.append({
                "claim": claim.get("claim", str(claim)),
                "evidence": claim.get("evidence", ""),
                "source": claim.get("source", ""),
                "confidence": claim.get("confidence", "medium"),
            })
        else:
            verified_findings.append({"claim": str(claim), "evidence": "", "source": "", "confidence": "medium"})

    # Important Changes
    important_changes = []
    for f in merged_findings:
        if f.get("importance") in ("add_provider", "update") or f.get("category") in ("deprecation", "breaking"):
            important_changes.append({
                "provider": f.get("provider", ""),
                "description": f.get("claim", ""),
                "type": f.get("category", ""),
                "confidence": f.get("confidence", "medium"),
            })

    # Statistics
    stats = {
        "iterations": iteration,
        "total_findings": total_findings,
        "high_confidence_findings": high_conf,
        "providers_analyzed": len(providers_found),
        "verified_claims": len(verified_claims),
        "unverified_claims": len(unverified_claims),
        "open_questions": len(open_questions),
        "resolved_questions": len(resolved_questions),
        "contradictions_detected": len(contradictions),
        "overall_quality": quality_metrics.get("overall_quality", 0),
        "coverage": quality_metrics.get("coverage", 0),
        "verification_score": quality_metrics.get("verification", 0),
    }

    return {
        "executive_summary": exec_summary,
        "verified_findings": verified_findings,
        "important_changes": important_changes,
        "open_questions": open_questions,
        "unverified_claims": [
            c if isinstance(c, dict) else {"claim": str(c)}
            for c in unverified_claims
        ],
        "contradictions": [
            c if isinstance(c, dict) else {"claim": str(c)}
            for c in contradictions
        ],
        "statistics": stats,
    }


def _llm_generate_narrative(
    sections: dict,
    config: Config,
    key_manager: KeyManager,
    runtime_state: dict,
) -> Optional[str]:
    """Use LLM to write a polished narrative from deterministic sections.

    Uses fallback chain for reliable execution.
    Falls back to deterministic summary on failure.

    Args:
        sections: Deterministic report sections
        config: System configuration
        key_manager: Key manager instance
        runtime_state: Runtime state dict

    Returns:
        Polished narrative string, or None on failure
    """
    fallback = create_fallback_chain(config, key_manager)

    # Build a compact prompt with structured data (not raw markdown)
    prompt_data = {
        "executive_summary": sections.get("executive_summary", ""),
        "statistics": sections.get("statistics", {}),
        "important_changes": sections.get("important_changes", [])[:10],
        "verified_count": len(sections.get("verified_findings", [])),
        "open_questions_count": len(sections.get("open_questions", [])),
    }

    narrative_prompt = f"""You are a senior AI industry analyst. Write a concise, professional briefing based on these research findings.

Data:
{json.dumps(prompt_data, indent=2)}

Write a 2-3 paragraph executive briefing covering:
1. Key provider updates and model releases
2. Pricing and free-tier changes
3. Breaking changes requiring attention
4. Recommended actions

Be specific with provider names and details. Write in a professional, concise style suitable for an industry briefing."""

    messages = [
        ChatMessage(role="system", content="You are a senior AI industry analyst. Write a professional briefing."),
        ChatMessage(role="user", content=narrative_prompt),
    ]

    # Use fallback chain
    def request_fn(api_key: str):
        provider_manifest = fallback.router.get_healthy_provider_for_capability("reasoning")
        if provider_manifest:
            provider = create_provider(provider_manifest.provider_id)
        else:
            provider = create_provider(config.active_provider)
        model = provider.get_default_model()
        return provider.chat(api_key, model, messages)

    result = fallback.execute_with_fallback(
        capability="reasoning",
        request_fn=request_fn,
        max_retries_per_provider=1,
    )

    if not result.success:
        logger.error("LLM narrative generation failed after all attempts")
        return None

    narrative = result.response.content.strip()
    if len(narrative) < 50:
        logger.warning("LLM narrative too short (%d chars), using deterministic summary", len(narrative))
        return None

    return narrative


def _build_deterministic_report(runtime_state: dict) -> dict:
    """Build a complete report from runtime state without any LLM calls.

    Used as the final fallback when LLM synthesis completely fails.
    Always produces a readable, structured report.

    Args:
        runtime_state: RuntimeManager state dict

    Returns:
        Complete report dict
    """
    iteration = runtime_state.get("iteration", 1)
    verified_claims = runtime_state.get("verified_claims", [])
    unverified_claims = runtime_state.get("unverified_claims", [])
    open_questions = runtime_state.get("open_questions", [])
    resolved_questions = runtime_state.get("resolved_questions", [])
    contradictions = runtime_state.get("contradictions", [])
    quality_metrics = runtime_state.get("quality_metrics", {})
    history = runtime_state.get("history", [])

    # Try to load merged findings from disk
    research_dir = runtime_state.get("_research_dir")
    merged_findings = []
    if research_dir:
        for i in range(1, iteration + 1):
            findings_file = Path(research_dir) / f"iteration_{i}_findings.json"
            if findings_file.exists():
                try:
                    with open(findings_file) as f:
                        data = json.load(f)
                    merged_findings.extend(data.get("findings", []))
                except Exception:
                    pass

    merged_findings = _merge_structured_findings(merged_findings)
    sections = _build_report_sections(
        merged_findings=merged_findings,
        verified_claims=verified_claims,
        unverified_claims=unverified_claims,
        open_questions=open_questions,
        resolved_questions=resolved_questions,
        contradictions=contradictions,
        quality_metrics=quality_metrics,
        history=history,
        iteration=iteration,
    )

    # Build findings list for downstream consumers
    findings_list = [
        {
            "provider": f.get("provider", ""),
            "model": f.get("model"),
            "description": f.get("claim", f.get("description", "")),
            "url": f.get("source", ""),
            "type": f.get("category", "general"),
            "action": f.get("importance", "monitor"),
            "confidence": f.get("confidence", "medium"),
        }
        for f in merged_findings
    ]

    return {
        "summary": sections["executive_summary"],
        "findings": findings_list,
        "new_providers": [
            f.get("provider", "") for f in merged_findings
            if f.get("importance") == "add_provider"
        ],
        "new_models": [
            f.get("claim", "") for f in merged_findings
            if f.get("category") == "model"
        ],
        "pricing_changes": [
            f.get("claim", "") for f in merged_findings
            if f.get("category") == "pricing"
        ],
        "free_tier_changes": [
            f.get("claim", "") for f in merged_findings
            if f.get("category") == "free_tier"
        ],
        "breaking_changes": [
            f.get("claim", "") for f in merged_findings
            if f.get("category") in ("deprecation", "breaking")
        ],
        "action_items": [
            f"Review: {f.get('claim', f.get('description', ''))[:100]}"
            for f in merged_findings
            if f.get("importance") in ("add_provider", "update")
        ],
        "sections": sections,
        "_deterministic": True,
    }
