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
) -> Optional[dict]:
    """Use the configured AI provider to summarize raw findings.

    Args:
        raw_findings: Deduplicated raw findings from web sources
        config: System configuration
        key_manager: Key manager instance
        runtime_state: Optional dict with iterative runtime state

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
    user_prompt = f"Here are the raw findings from official sources:\n\n{context}\n\n"

    # Read previous iterations
    if runtime_state:
        iteration = runtime_state.get("iteration", 1)
        research_dir = config.data_dir / "research"
        threshold = config.memory_compression_threshold

        # Working Memory (recent iterations)
        working_memory_start = max(1, iteration - threshold)
        prev_iters = []
        for i in range(working_memory_start, iteration):
            p = research_dir / f"iteration_{i}.md"
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8")
                    prev_iters.append(f"=== WORKING MEMORY: ITERATION {i} ===\n{content}")
                except Exception as e:
                    logger.warning("Could not read working memory file %s: %s", p, e)

        working_mem_str = "\n\n".join(prev_iters) if prev_iters else "None"
        ltm_str = runtime_state.get("long_term_memory", "")
        if not ltm_str:
            ltm_str = "None"

        plan_str = json.dumps(runtime_state.get("current_plan", {}), indent=2)

        user_prompt += "=== CURRENT PLAN ===\n"
        user_prompt += f"{plan_str}\n\n"

        user_prompt += "=== RUNTIME STATE ===\n"
        user_prompt += f"Current Iteration: {runtime_state.get('iteration', 1)}\n"
        user_prompt += f"Verified Claims: {runtime_state.get('verified_claims', [])}\n"
        user_prompt += f"Unverified Claims: {runtime_state.get('unverified_claims', [])}\n"
        user_prompt += f"Open Questions: {runtime_state.get('open_questions', [])}\n"
        user_prompt += f"Research Queue: {runtime_state.get('research_queue', [])}\n"
        user_prompt += f"Contradictions Tracker: {runtime_state.get('contradictions', [])}\n\n"

        user_prompt += "=== LONG-TERM MEMORY (COMPRESSED SUMMARY OF PAST WORK) ===\n"
        user_prompt += f"{ltm_str}\n\n"

        user_prompt += "=== WORKING MEMORY (RECENT RESEARCH ITERATIONS) ===\n"
        user_prompt += f"{working_mem_str}\n\n"

        user_prompt += (
            "Based on the plan, working memory, long-term memory, claims, and open questions, "
            "perform the next level of research. Specifically address:\n"
            "- What information is still missing?\n"
            "- Which assumptions may be incorrect?\n"
            "- Which claims require verification?\n"
            "- Which sources disagree?\n"
            "- What should be researched next?\n"
            "- How can this report become more complete?\n\n"
        )

    user_prompt += "Analyze and produce the JSON summary."

    messages = [
        ChatMessage(role="system", content="You are a research assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=f"{RESEARCH_PROMPT}\n\n{user_prompt}"),
    ]

    # Use the provider's own default model — never hardcode OpenAI model names
    model = provider.get_default_model()
    logger.info("RESEARCH: Using provider=%s model=%s", provider_name, model)

    def request_fn(api_key: str):
        return provider.chat(api_key, model, messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)

    if not result.success or not result.response:
        logger.error("LLM summarization failed: %s", result.error)
        return None

    parsed = _parse_findings(result.response.content)

    if parsed:
        evaluation = parsed.get("evaluation", {})
        logger.info("=== LLM EVALUATION ===")
        logger.info("Overall Quality: %s", evaluation.get("overall_quality", "MISSING"))
        logger.info("Coverage: %s", evaluation.get("coverage", "MISSING"))
        logger.info("Verification: %s", evaluation.get("verification", "MISSING"))
        logger.info("Verified Claims: %d", len(evaluation.get("verified_claims", [])))
        logger.info("Open Questions: %d", len(evaluation.get("open_questions", [])))
        logger.info("Keys in parsed result: %s", list(parsed.keys()))
        logger.info("Keys in evaluation: %s", list(evaluation.keys()))
        logger.info("======================")

    return parsed


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
        runtime_state: Optional dict with iterative runtime state

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
            summarized = _llm_summarize(raw_findings, config, key_manager, runtime_state)
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

    Uses Runtime State to create targeted objectives based on current gaps.
    """
    iteration = runtime_state.get("iteration", 1)

    plan_prompt = """You are a research planning agent. Generate a structured research plan for iteration {iteration} of AI provider research.

    Analyze the current state and generate TARGETED objectives that address specific gaps:
    - Verified Claims: {verified_claims}
    - Unverified Claims: {unverified_claims}
    - Open Questions: {open_questions}
    - Research Queue: {research_queue}
    - Contradictions: {contradictions}
    - Long-Term Memory Summary: {long_term_memory}

    OBJECTIVE GENERATION RULES:
    Based on the state above, generate objectives like:
    - "Verify unresolved claims from previous iteration" (if unverified_claims is non-empty)
    - "Resolve detected contradictions" (if contradictions is non-empty)
    - "Validate assumptions with independent sources" (if there are unverified claims)
    - "Increase source diversity by checking additional provider blogs" (always relevant)
    - "Investigate missing evidence for [specific claim]" (if claims lack evidence)
    - "Improve confidence of weak conclusions" (always relevant)
    - "Answer open questions: [specific question]" (if open_questions is non-empty)

    PREVENT REPETITION:
    - Do NOT research already completed topics or verified claims
    - Focus on open_questions and research_queue items
    - Prioritize contradictions that need resolution

    Respond ONLY with a valid JSON object (no markdown fences) containing:
    {{
      "objectives": ["targeted objective 1", "targeted objective 2", "targeted objective 3"],
      "claims_to_verify": ["specific claim from unverified_claims to check"],
      "questions_to_answer": ["specific open_question to answer"],
      "sources_to_search": ["specific provider blogs or RSS feeds to pay attention to"],
      "expected_deliverables": ["deliverable 1", "deliverable 2"]
    }}
    """

    rotator = KeyRotator(config, key_manager)
    provider_name = config.active_provider
    try:
        provider = create_provider(provider_name)
    except Exception as e:
        logger.error("Cannot create provider for planning: %s", e)
        return _default_plan()

    formatted_prompt = plan_prompt.format(
        iteration=iteration,
        verified_claims=json.dumps(runtime_state.get("verified_claims", [])),
        unverified_claims=json.dumps(runtime_state.get("unverified_claims", [])),
        open_questions=json.dumps(runtime_state.get("open_questions", [])),
        research_queue=json.dumps(runtime_state.get("research_queue", [])),
        contradictions=json.dumps(runtime_state.get("contradictions", [])),
        long_term_memory=runtime_state.get("long_term_memory", "")
    )

    messages = [
        ChatMessage(role="system", content="You are a research planning assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=formatted_prompt),
    ]
    model = provider.get_default_model()

    logger.info("PLANNER: Generating plan for iteration %d using %s", iteration, provider_name)

    def request_fn(api_key: str):
        return provider.chat(api_key, model, messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)
    if not result.success or not result.response:
        logger.error("LLM planner failed: %s", result.error)
        return _default_plan()

    parsed = _parse_findings(result.response.content)
    if parsed:
        return parsed
    return _default_plan()


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
    """Summarize older iterations to keep working memory small while preserving knowledge."""
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

    compression_prompt = """You are a knowledge consolidation agent. Combine the existing Long-Term Memory summary with the new older research iteration reports.

    Your goal is to produce a single, highly compressed, factual summary of the historic findings. Keep it extremely concise but do not lose key verified claims, timeline events, or resolutions of past issues.

    Existing Long-Term Memory:
    {current_ltm}

    New Older Iteration Reports to consolidate:
    {combined_older}

    Respond with the consolidated, polished summary in markdown format.
    """

    rotator = KeyRotator(config, key_manager)
    provider_name = config.active_provider
    try:
        provider = create_provider(provider_name)
    except Exception as e:
        logger.error("Cannot create provider for memory compression: %s", e)
        return current_ltm

    messages = [
        ChatMessage(role="system", content="You are a knowledge consolidation assistant. Respond only with markdown summary."),
        ChatMessage(role="user", content=compression_prompt.format(current_ltm=current_ltm, combined_older=combined_older)),
    ]
    model = provider.get_default_model()

    logger.info("MEMORY COMPRESSOR: Consolidating iterations 1 to %d into Long-Term Memory", compress_limit)

    def request_fn(api_key: str):
        return provider.chat(api_key, model, messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)
    if not result.success or not result.response:
        logger.error("Memory consolidation failed: %s", result.error)
        return current_ltm

    return result.response.content.strip()


def generate_final_report(config: Config, key_manager: KeyManager, runtime_state: dict) -> dict:
    """Consolidate all iteration reports and memory into a single, polished final report using the LLM.

    Loads every iteration, merges findings, removes duplicates, resolves contradictions,
    ranks by confidence, generates executive summary, detailed report, and action items.
    """
    iteration = runtime_state.get("iteration", 1)
    research_dir = config.data_dir / "research"

    # Load all iterations
    iters_content = []
    for i in range(1, iteration + 1):
        p = research_dir / f"iteration_{i}.md"
        if p.exists():
            try:
                iters_content.append(f"=== ITERATION {i} ===\n{p.read_text(encoding='utf-8')}")
            except Exception as e:
                logger.error("Could not read iteration %d file: %s", i, e)

    combined_context = "\n\n".join(iters_content)
    ltm = runtime_state.get("long_term_memory", "")

    # Load claim tracking state
    verified_claims = runtime_state.get("verified_claims", [])
    unverified_claims = runtime_state.get("unverified_claims", [])
    contradictions = runtime_state.get("contradictions", [])

    # Prompt the LLM to consolidate
    merge_prompt = """You are a senior AI research analyst. You are given a series of research iterations and a long-term memory summary about AI provider updates, model releases, and pricing changes.

    Your task is to:
    1. Merge all findings from ALL iterations into a single comprehensive view.
    2. Remove duplicate findings across iterations.
    3. Resolve any contradictions between different iterations (favoring more recent, verified findings).
    4. Rank findings by confidence level (high > medium > low).
    5. Generate a single highly polished, comprehensive final report containing:

    SECTIONS REQUIRED:
    - Executive Summary (2-3 paragraph overview of all research)
    - Key Provider Updates & Model Releases (ranked by confidence)
    - Pricing & Free-Tier Changes
    - Breaking Changes & Deprecations
    - Verified Claims (list all verified_claims with evidence)
    - Unresolved Gaps & Future Questions (from unverified_claims and open_questions)
    - Contradictions Detected (list contradictions with resolution status)
    - Action Items (prioritized list of recommended next steps)

    Verified Claims from state: {verified_claims}
    Unverified Claims from state: {unverified_claims}
    Contradictions from state: {contradictions}

    Respond ONLY with a valid JSON object (no markdown fences) containing:
    {{
      "summary": "Executive Summary + Detailed Report in Markdown format",
      "findings": [
        {{
          "provider": "...",
          "model": "...",
          "description": "...",
          "url": "...",
          "type": "...",
          "action": "...",
          "confidence": "..."
        }}
      ],
      "new_providers": ["..."],
      "new_models": ["..."],
      "pricing_changes": ["..."],
      "free_tier_changes": ["..."],
      "breaking_changes": ["..."],
      "action_items": ["prioritized action 1", "prioritized action 2"]
    }}
    """

    rotator = KeyRotator(config, key_manager)
    provider_name = config.active_provider
    try:
        provider = create_provider(provider_name)
    except Exception as e:
        logger.error("Cannot create provider to generate final report: %s", e)
        return _build_raw_fallback_final(combined_context)

    messages = [
        ChatMessage(role="system", content="You are a report consolidation assistant. Respond only with valid JSON."),
        ChatMessage(role="user", content=merge_prompt.format(
            verified_claims=verified_claims,
            unverified_claims=unverified_claims,
            contradictions=contradictions,
        ) + f"\n\nLong-Term Memory:\n{ltm}\n\nIterations:\n{combined_context}"),
    ]
    model = provider.get_default_model()

    def request_fn(api_key: str):
        return provider.chat(api_key, model, messages)

    result = rotator.execute_with_rotation(provider_name, request_fn)
    if not result.success or not result.response:
        logger.error("LLM final report consolidation failed: %s", result.error)
        return _build_raw_fallback_final(combined_context)

    parsed = _parse_findings(result.response.content)
    if parsed:
        return parsed
    return _build_raw_fallback_final(combined_context)


def _build_raw_fallback_final(combined_context: str) -> dict:
    """Fallback when final consolidation fails."""
    return {
        "findings": [],
        "summary": f"# Consolidated Research Report (Fallback)\n\nFailed to consolidate via LLM. Here is the raw history:\n\n{combined_context}",
        "new_providers": [],
        "new_models": [],
        "pricing_changes": [],
        "free_tier_changes": [],
        "breaking_changes": [],
    }
