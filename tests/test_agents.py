"""Tests for the Multi-Agent Research module."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.maintenance.agents import (
    AgentRole, ROLE_CAPABILITIES, ROLE_PROMPTS,
    ResearchAgent, MultiAgentOrchestrator, AgentResult,
)


def test_agent_role_enum():
    """Test AgentRole enum values."""
    assert AgentRole.RESEARCHER.value == "researcher"
    assert AgentRole.VERIFIER.value == "verifier"
    assert AgentRole.CRITIC.value == "critic"
    assert AgentRole.CONTRADICTION_DETECTOR.value == "contradiction_detector"
    assert AgentRole.EVIDENCE_COLLECTOR.value == "evidence_collector"
    assert AgentRole.WRITER.value == "writer"


def test_role_capabilities_defined():
    """Test ROLE_CAPABILITIES has entries for all roles."""
    for role in AgentRole:
        assert role in ROLE_CAPABILITIES
        assert len(ROLE_CAPABILITIES[role]) > 0


def test_role_prompts_defined():
    """Test ROLE_PROMPTS has entries for all roles."""
    for role in AgentRole:
        assert role in ROLE_PROMPTS
        assert len(ROLE_PROMPTS[role]) > 0


def test_agent_result_defaults():
    """Test AgentResult default values."""
    r = AgentResult(role=AgentRole.RESEARCHER, provider_id="groq", success=True)
    assert r.role == AgentRole.RESEARCHER
    assert r.provider_id == "groq"
    assert r.success is True
    assert r.response is None
    assert r.error is None
    assert r.duration_seconds == 0.0


def test_agent_result_with_values():
    """Test AgentResult with custom values."""
    r = AgentResult(
        role=AgentRole.WRITER,
        provider_id="openrouter",
        success=False,
        error="Provider failed",
        duration_seconds=1.5,
    )
    assert r.success is False
    assert r.error == "Provider failed"
    assert r.duration_seconds == 1.5


def test_research_agent_init():
    """Test ResearchAgent initializes correctly."""
    config = MagicMock()
    config.active_provider = "groq"
    config.retry_count = 3
    km = MagicMock()

    agent = ResearchAgent(AgentRole.RESEARCHER, config, km)
    assert agent.role == AgentRole.RESEARCHER
    assert agent.config is config
    assert agent.key_manager is km


def test_multi_agent_orchestrator_init():
    """Test MultiAgentOrchestrator initializes correctly."""
    config = MagicMock()
    config.active_provider = "groq"
    config.retry_count = 3
    km = MagicMock()

    orchestrator = MultiAgentOrchestrator(config, km)
    assert orchestrator.config is config
    assert orchestrator.key_manager is km


def test_multi_agent_orchestrator_has_agents():
    """Test MultiAgentOrchestrator can create agents for all roles."""
    config = MagicMock()
    config.active_provider = "groq"
    config.retry_count = 3
    km = MagicMock()

    orchestrator = MultiAgentOrchestrator(config, km)
    # Test that we can create a ResearchAgent for each role
    for role in AgentRole:
        agent = ResearchAgent(role, config, km)
        assert agent.role == role
        assert agent.required_capabilities == ROLE_CAPABILITIES.get(role, ["reasoning"])


def test_multi_agent_build_context():
    """Test _build_context produces a string."""
    config = MagicMock()
    config.active_provider = "groq"
    config.retry_count = 3
    km = MagicMock()

    orchestrator = MultiAgentOrchestrator(config, km)
    findings = [{"name": "Test", "provider": "groq", "title": "Test finding"}]
    context = orchestrator._build_context(findings)
    assert isinstance(context, str)
    assert "Test" in context


def test_multi_agent_build_role_context():
    """Test _build_role_context produces role-specific context."""
    config = MagicMock()
    config.active_provider = "groq"
    config.retry_count = 3
    km = MagicMock()

    orchestrator = MultiAgentOrchestrator(config, km)
    context = orchestrator._build_role_context(
        AgentRole.RESEARCHER,
        "Raw findings here",
        {},
    )
    assert isinstance(context, str)
    assert "Raw findings here" in context


def test_role_capabilities_researcher():
    """Test RESEARCHER role requires reasoning and coding."""
    caps = ROLE_CAPABILITIES[AgentRole.RESEARCHER]
    assert "reasoning" in caps
    assert "coding" in caps


def test_role_capabilities_verifier():
    """Test VERIFIER role requires reasoning."""
    caps = ROLE_CAPABILITIES[AgentRole.VERIFIER]
    assert "reasoning" in caps


def test_role_capabilities_writer():
    """Test WRITER role requires reasoning."""
    caps = ROLE_CAPABILITIES[AgentRole.WRITER]
    assert "reasoning" in caps


def run_all():
    """Run all agents tests."""
    tests = [
        test_agent_role_enum,
        test_role_capabilities_defined,
        test_role_prompts_defined,
        test_agent_result_defaults,
        test_agent_result_with_values,
        test_research_agent_init,
        test_multi_agent_orchestrator_init,
        test_multi_agent_orchestrator_has_agents,
        test_multi_agent_build_context,
        test_multi_agent_build_role_context,
        test_role_capabilities_researcher,
        test_role_capabilities_verifier,
        test_role_capabilities_writer,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} agents tests passed!")
