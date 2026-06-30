"""Tests for the Report Sections module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.maintenance.report_sections import build_executive_report


def _minimal_findings():
    """Create minimal findings for testing."""
    return [
        {
            "name": "Groq",
            "provider": "groq",
            "type": "provider",
            "category": "models",
            "title": "New model release",
            "description": "Groq released a new model",
            "confidence": "high",
            "source": "test",
            "action": "monitor",
        }
    ]


def test_build_executive_report_minimal():
    """Test build_executive_report with minimal inputs."""
    report = build_executive_report(
        merged_findings=[],
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={},
        history=[],
        iteration=1,
    )
    assert "executive_summary" in report
    assert "top_5_developments" in report
    assert "highest_business_impact" in report
    assert "new_models" in report
    assert "provider_comparison" in report
    assert "verified_findings" in report
    assert "contradictions" in report
    assert "open_questions" in report
    assert "action_items" in report
    assert "suggested_providers" in report
    assert "provider_health" in report
    assert "statistics" in report


def test_build_executive_report_statistics():
    """Test statistics section has correct fields."""
    report = build_executive_report(
        merged_findings=_minimal_findings(),
        verified_claims=["claim1"],
        unverified_claims=["claim2"],
        open_questions=["question1"],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={"quality": 0.8},
        history=[],
        iteration=3,
        configured_providers=["groq"],
    )
    stats = report["statistics"]
    assert stats["iterations"] == 3
    assert stats["total_findings"] == 1
    assert stats["verified_claims"] == 1
    assert stats["unverified_claims"] == 1
    assert stats["open_questions"] == 1


def test_build_executive_report_top_5():
    """Test top_5_developments limits to 5 items."""
    findings = []
    for i in range(10):
        findings.append({
            "name": f"Provider {i}",
            "provider": f"provider_{i}",
            "type": "provider",
            "category": "models",
            "title": f"Finding {i}",
            "description": f"Description {i}",
            "confidence": "high",
            "source": "test",
            "action": "monitor",
        })
    report = build_executive_report(
        merged_findings=findings,
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={},
        history=[],
        iteration=1,
    )
    assert len(report["top_5_developments"]) <= 5


def test_build_executive_report_with_contradictions():
    """Test contradictions section."""
    contradictions = [
        {
            "claim": "Groq increased rate limits",
            "prev_evidence": "Old rate limit was 100 rpm",
            "current_evidence": "New rate limit is 200 rpm",
            "resolution_status": "resolved",
        }
    ]
    report = build_executive_report(
        merged_findings=[],
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=contradictions,
        quality_metrics={},
        history=[],
        iteration=1,
    )
    assert len(report["contradictions"]) == 1
    assert report["contradictions"][0]["claim"] == "Groq increased rate limits"


def test_build_executive_report_with_discovery():
    """Test suggested_providers from discovery results."""
    discovery_results = {
        "suggestions": [
            {
                "name": "NewProvider",
                "endpoint": "https://api.newprovider.com/v1/chat/completions",
                "models": ["model-1"],
                "free_tier": True,
                "source": "cool-ai-stuff",
                "confidence": "medium",
            }
        ]
    }
    report = build_executive_report(
        merged_findings=[],
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={},
        history=[],
        iteration=1,
        discovery_results=discovery_results,
    )
    assert len(report["suggested_providers"]) == 1
    assert report["suggested_providers"][0]["name"] == "newprovider"


def test_build_executive_report_with_provider_health():
    """Test provider_health section."""
    provider_health = {
        "groq": "healthy",
        "openrouter": "degraded",
    }
    report = build_executive_report(
        merged_findings=[],
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={},
        history=[],
        iteration=1,
        configured_providers=["groq", "openrouter"],
        provider_health=provider_health,
    )
    assert len(report["provider_health"]) == 2


def test_build_executive_report_action_items_limit():
    """Test action_items is limited to 15."""
    findings = []
    for i in range(20):
        findings.append({
            "name": f"Provider {i}",
            "provider": f"provider_{i}",
            "type": "provider",
            "category": "models",
            "title": f"Finding {i}",
            "description": f"Description {i}",
            "confidence": "high",
            "source": "test",
            "action": "add_key",
        })
    report = build_executive_report(
        merged_findings=findings,
        verified_claims=[],
        unverified_claims=[],
        open_questions=[],
        resolved_questions=[],
        contradictions=[],
        quality_metrics={},
        history=[],
        iteration=1,
    )
    assert len(report["action_items"]) <= 15


def run_all():
    """Run all report sections tests."""
    tests = [
        test_build_executive_report_minimal,
        test_build_executive_report_statistics,
        test_build_executive_report_top_5,
        test_build_executive_report_with_contradictions,
        test_build_executive_report_with_discovery,
        test_build_executive_report_with_provider_health,
        test_build_executive_report_action_items_limit,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} report sections tests passed!")
