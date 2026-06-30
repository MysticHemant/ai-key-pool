"""Executive report sections for AI Key Pool.

Defines the new report structure with:
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
"""

import json
from typing import Optional
from ..utils.logger import get_logger


logger = get_logger("report_sections")


def build_executive_report(
    merged_findings: list[dict],
    verified_claims: list,
    unverified_claims: list,
    open_questions: list,
    resolved_questions: list,
    contradictions: list,
    quality_metrics: dict,
    history: list,
    iteration: int,
    configured_providers: list[str] = None,
    discovery_results: dict = None,
    provider_health: dict = None,
) -> dict:
    """Build the complete executive report with all sections.

    Args:
        merged_findings: Merged and deduplicated findings
        verified_claims: List of verified claims
        unverified_claims: List of unverified claims
        open_questions: List of open questions
        resolved_questions: List of resolved questions
        contradictions: List of contradictions
        quality_metrics: Quality metrics dict
        history: Research history
        iteration: Current iteration number
        configured_providers: List of configured provider names
        discovery_results: Discovery results dict
        provider_health: Provider health status dict

    Returns:
        Dict with all report sections
    """
    configured_set = set(configured_providers or [])

    # Build each section
    executive_summary = _build_executive_summary(
        merged_findings, verified_claims, unverified_claims,
        open_questions, quality_metrics, iteration,
    )

    top_developments = _build_top_developments(merged_findings)

    business_impact = _build_business_impact(merged_findings)

    new_models = _build_new_models_section(merged_findings)

    provider_comparison = _build_provider_comparison(
        merged_findings, configured_set, provider_health,
    )

    verified_findings = _build_verified_findings_section(verified_claims)

    contradictions_section = _build_contradictions_section(contradictions)

    open_questions_section = _build_open_questions_section(open_questions)

    action_items = _build_action_items(merged_findings, configured_set)

    suggested_providers = _build_suggested_providers(
        discovery_results, configured_set,
    )

    provider_health_section = _build_provider_health_section(
        configured_set, provider_health,
    )

    statistics = _build_statistics(
        merged_findings, verified_claims, unverified_claims,
        open_questions, contradictions, quality_metrics, iteration,
    )

    return {
        "executive_summary": executive_summary,
        "top_5_developments": top_developments,
        "highest_business_impact": business_impact,
        "new_models": new_models,
        "provider_comparison": provider_comparison,
        "verified_findings": verified_findings,
        "contradictions": contradictions_section,
        "open_questions": open_questions_section,
        "action_items": action_items,
        "suggested_providers": suggested_providers,
        "provider_health": provider_health_section,
        "statistics": statistics,
    }


def _build_executive_summary(
    findings: list,
    verified_claims: list,
    unverified_claims: list,
    open_questions: list,
    quality_metrics: dict,
    iteration: int,
) -> str:
    """Build the executive summary paragraph."""
    total_findings = len(findings)
    high_conf = len([f for f in findings if f.get("confidence") == "high"])
    providers_found = set(f.get("provider", "") for f in findings if f.get("provider"))
    categories = set(f.get("category", "") for f in findings if f.get("category"))

    summary = (
        f"Research completed over {iteration} iteration{'s' if iteration != 1 else ''}, "
        f"analyzing {total_findings} findings from {len(providers_found)} providers. "
        f"{high_conf} findings have high confidence. "
    )

    if verified_claims:
        summary += f"{len(verified_claims)} claims verified. "
    if unverified_claims:
        summary += f"{len(unverified_claims)} claims awaiting verification. "
    if open_questions:
        summary += f"{len(open_questions)} open questions remain. "

    overall_quality = quality_metrics.get("overall_quality", 0)
    if overall_quality > 0:
        summary += f"Overall research quality: {overall_quality}/100."

    return summary


def _build_top_developments(findings: list) -> list[dict]:
    """Build the Top 5 Industry Developments section."""
    # Sort by importance and confidence
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
    conf_rank = {"high": 3, "medium": 2, "low": 1}

    sorted_findings = sorted(
        findings,
        key=lambda x: (
            importance_rank.get(x.get("importance", "none"), 99),
            -conf_rank.get(x.get("confidence", "medium"), 0),
        ),
    )

    developments = []
    for f in sorted_findings[:5]:
        developments.append({
            "title": f.get("claim", f.get("description", ""))[:100],
            "provider": f.get("provider", ""),
            "category": f.get("category", "general"),
            "confidence": f.get("confidence", "medium"),
            "source": f.get("source", ""),
            "why_it_matters": _generate_impact_statement(f),
        })

    return developments


def _build_business_impact(findings: list) -> list[dict]:
    """Build the Highest Business Impact section."""
    impact_items = []
    for f in findings:
        importance = f.get("importance", "none")
        if importance in ("add_provider", "update", "deprecation", "breaking"):
            impact_items.append({
                "title": f.get("claim", f.get("description", ""))[:100],
                "provider": f.get("provider", ""),
                "impact_type": importance,
                "confidence": f.get("confidence", "medium"),
                "recommended_action": _get_action_recommendation(f),
            })

    return impact_items[:10]  # Limit to top 10


def _build_new_models_section(findings: list) -> list[dict]:
    """Build the New Models Released section."""
    models = []
    for f in findings:
        if f.get("category") == "model" or f.get("type") == "model":
            models.append({
                "model": f.get("model", f.get("claim", ""))[:100],
                "provider": f.get("provider", ""),
                "description": f.get("claim", f.get("description", ""))[:200],
                "confidence": f.get("confidence", "medium"),
                "source": f.get("source", ""),
            })

    return models


def _build_provider_comparison(
    findings: list,
    configured_set: set,
    provider_health: dict = None,
) -> list[dict]:
    """Build the Provider Comparison section."""
    providers = {}
    for f in findings:
        provider = f.get("provider", "")
        if not provider:
            continue
        if provider not in providers:
            providers[provider] = {
                "name": provider,
                "configured": provider in configured_set,
                "findings_count": 0,
                "categories": set(),
                "health": (provider_health or {}).get(provider, "unknown"),
            }
        providers[provider]["findings_count"] += 1
        cat = f.get("category", "")
        if cat:
            providers[provider]["categories"].add(cat)

    # Convert sets to lists for JSON
    result = []
    for p in providers.values():
        p["categories"] = sorted(p["categories"])
        result.append(p)

    return sorted(result, key=lambda x: x["findings_count"], reverse=True)


def _build_verified_findings_section(verified_claims: list) -> list[dict]:
    """Build the Verified Findings section."""
    findings = []
    for claim in verified_claims:
        if isinstance(claim, dict):
            findings.append({
                "claim": claim.get("claim", str(claim)),
                "evidence": claim.get("evidence", ""),
                "source": claim.get("source", ""),
                "confidence": claim.get("confidence", "medium"),
            })
        else:
            findings.append({
                "claim": str(claim),
                "evidence": "",
                "source": "",
                "confidence": "medium",
            })

    return findings


def _build_contradictions_section(contradictions: list) -> list[dict]:
    """Build the Contradictions section."""
    items = []
    for c in contradictions:
        if isinstance(c, dict):
            items.append({
                "claim": c.get("claim", str(c)),
                "prev_evidence": c.get("prev_evidence", ""),
                "current_evidence": c.get("current_evidence", ""),
                "resolution_status": c.get("resolution_status", "unresolved"),
                "resolution_notes": c.get("resolution_notes", ""),
            })
        else:
            items.append({
                "claim": str(c),
                "prev_evidence": "",
                "current_evidence": "",
                "resolution_status": "unresolved",
                "resolution_notes": "",
            })

    return items


def _build_open_questions_section(open_questions: list) -> list[str]:
    """Build the Open Questions section."""
    questions = []
    for q in open_questions:
        if isinstance(q, dict):
            questions.append(q.get("question", q.get("claim", str(q)))[:200])
        else:
            questions.append(str(q)[:200])

    return questions


def _build_action_items(findings: list, configured_set: set) -> list[dict]:
    """Build the Action Items section."""
    items = []
    seen_actions = set()

    for f in findings:
        importance = f.get("importance", "none")
        provider = f.get("provider", "")

        if importance == "add_provider" and provider not in configured_set:
            action_key = f"add_{provider}"
            if action_key not in seen_actions:
                items.append({
                    "priority": "high",
                    "action": f"Add {provider} provider",
                    "reason": f.get("claim", f.get("description", ""))[:150],
                    "category": f.get("category", ""),
                })
                seen_actions.add(action_key)

        elif importance == "deprecation" or importance == "breaking":
            action_key = f"urgent_{provider}_{hash(f.get('claim', ''))}"
            if action_key not in seen_actions:
                items.append({
                    "priority": "high",
                    "action": f"URGENT: {provider} breaking change",
                    "reason": f.get("claim", f.get("description", ""))[:150],
                    "category": f.get("category", ""),
                })
                seen_actions.add(action_key)

        elif importance == "update" and provider in configured_set:
            action_key = f"update_{provider}_{hash(f.get('claim', ''))}"
            if action_key not in seen_actions:
                items.append({
                    "priority": "medium",
                    "action": f"Review {provider} update",
                    "reason": f.get("claim", f.get("description", ""))[:150],
                    "category": f.get("category", ""),
                })
                seen_actions.add(action_key)

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: priority_order.get(x["priority"], 3))

    return items[:15]  # Limit to 15 items


def _build_suggested_providers(
    discovery_results: dict,
    configured_set: set,
) -> list[dict]:
    """Build the Suggested Providers to Add section."""
    if not discovery_results:
        return []

    suggestions = []
    for s in discovery_results.get("suggestions", []):
        name = s.get("name", "").lower()
        if name and name not in configured_set:
            suggestions.append({
                "name": s.get("display_name", name),
                "endpoint": s.get("endpoint", ""),
                "models": s.get("models", []),
                "free_tier": s.get("free_tier", False),
                "source": s.get("source", ""),
                "confidence": s.get("confidence", "medium"),
            })

    return suggestions


def _build_provider_health_section(
    configured_set: set,
    provider_health: dict = None,
) -> list[dict]:
    """Build the Current Provider Health section."""
    health_list = []
    for provider in sorted(configured_set):
        health_list.append({
            "provider": provider,
            "health": (provider_health or {}).get(provider, "unknown"),
        })

    return health_list


def _build_statistics(
    findings: list,
    verified_claims: list,
    unverified_claims: list,
    open_questions: list,
    contradictions: list,
    quality_metrics: dict,
    iteration: int,
) -> dict:
    """Build the Research Statistics section."""
    return {
        "iterations": iteration,
        "total_findings": len(findings),
        "high_confidence_findings": len([f for f in findings if f.get("confidence") == "high"]),
        "providers_analyzed": len(set(f.get("provider", "") for f in findings if f.get("provider"))),
        "verified_claims": len(verified_claims),
        "unverified_claims": len(unverified_claims),
        "open_questions": len(open_questions),
        "contradictions_detected": len(contradictions),
        "overall_quality": quality_metrics.get("overall_quality", 0),
        "coverage": quality_metrics.get("coverage", 0),
        "verification_score": quality_metrics.get("verification", 0),
    }


def _generate_impact_statement(finding: dict) -> str:
    """Generate a 'why it matters' statement for a finding."""
    category = finding.get("category", finding.get("type", ""))
    importance = finding.get("importance", "")
    provider = finding.get("provider", "")

    if importance == "add_provider":
        return f"New provider opportunity for {provider}"
    elif importance == "deprecation" or importance == "breaking":
        return f"May require code changes or migration for {provider}"
    elif category == "model":
        return f"New model availability from {provider}"
    elif category == "free_tier":
        return f"Cost optimization opportunity with {provider}"
    elif category == "pricing":
        return f"May affect budget planning for {provider}"

    return f"Relevant update from {provider}"


def _get_action_recommendation(finding: dict) -> str:
    """Get recommended action for a finding."""
    importance = finding.get("importance", "none")
    if importance == "add_provider":
        return "add_provider"
    elif importance == "deprecation" or importance == "breaking":
        return "urgent_review"
    elif importance == "update":
        return "review"
    elif importance == "free_tier":
        return "evaluate"
    return "monitor"
