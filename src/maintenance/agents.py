"""Multi-agent research system for AI Key Pool.

Defines agent roles and executes multi-agent research pipelines.
Each agent specializes in a specific research task and is assigned
to the best available provider based on capabilities.
"""

import json
from enum import Enum
from typing import Optional, Callable
from dataclasses import dataclass, field

from ..providers.base_provider import ChatMessage, BaseProvider
from ..providers.capability_router import CapabilityRouter
from ..providers.provider_factory import create_provider
from ..key_pool import KeyManager
from ..utils.config import Config
from ..utils.logger import get_logger


logger = get_logger("agents")


class AgentRole(Enum):
    """Research agent roles."""
    RESEARCHER = "researcher"
    VERIFIER = "verifier"
    CRITIC = "critic"
    CONTRADICTION_DETECTOR = "contradiction_detector"
    EVIDENCE_COLLECTOR = "evidence_collector"
    WRITER = "writer"


# Capability requirements for each role
ROLE_CAPABILITIES = {
    AgentRole.RESEARCHER: ["reasoning", "coding"],
    AgentRole.VERIFIER: ["reasoning"],
    AgentRole.CRITIC: ["reasoning"],
    AgentRole.CONTRADICTION_DETECTOR: ["reasoning"],
    AgentRole.EVIDENCE_COLLECTOR: ["reasoning", "search"],
    AgentRole.WRITER: ["reasoning"],
}

# Role prompts
ROLE_PROMPTS = {
    AgentRole.RESEARCHER: """You are a Research Agent. Your job is to collect and organize information
from the provided raw findings. Focus on:
- Identifying key provider updates, model releases, and pricing changes
- Organizing findings by provider and category
- Noting any gaps in information that need follow-up
- Providing concise, factual summaries

Respond with JSON containing:
{
  "organized_findings": [...],
  "gaps_identified": [...],
  "priority_items": [...],
  "summary": "..."
}""",

    AgentRole.VERIFIER: """You are a Verification Agent. Your job is to verify claims and
validate information accuracy. Focus on:
- Checking if claims are supported by evidence
- Identifying unsupported assertions
- Cross-referencing information across sources
- Assessing confidence levels

Respond with JSON containing:
{
  "verified_claims": [...],
  "unverified_claims": [...],
  "verification_notes": [...],
  "confidence_assessment": "..."
}""",

    AgentRole.CRITIC: """You are a Critic Agent. Your job is to identify weaknesses,
biases, and gaps in the research. Focus on:
- Questioning assumptions
- Identifying potential biases in sources
- Highlighting missing perspectives
- Suggesting areas for deeper investigation

Respond with JSON containing:
{
  "critiques": [...],
  "biases_identified": [...],
  "missing_perspectives": [...],
  "improvement_suggestions": [...]
}""",

    AgentRole.CONTRADICTION_DETECTOR: """You are a Contradiction Detection Agent. Your job is to
find conflicting information across findings. Focus on:
- Identifying direct contradictions
- Noting inconsistencies in provider claims
- Flagging conflicting data points
- Suggesting resolution approaches

Respond with JSON containing:
{
  "contradictions": [...],
  "inconsistencies": [...],
  "resolution_suggestions": [...],
  "severity_assessment": "..."
}""",

    AgentRole.EVIDENCE_COLLECTOR: """You are an Evidence Collection Agent. Your job is to
gather supporting evidence for claims. Focus on:
- Finding additional sources for claims
- Strengthening weak evidence
- Providing context for findings
- Building a stronger evidence base

Respond with JSON containing:
{
  "supporting_evidence": [...],
  "additional_sources": [...],
  "context_notes": [...],
  "evidence_strength": "..."
}""",

    AgentRole.WRITER: """You are a Writer Agent. Your job is to synthesize all research
into a clear, professional report. Focus on:
- Writing concise, executive-level summaries
- Organizing findings logically
- Highlighting key insights and action items
- Maintaining professional tone

Respond with JSON containing:
{
  "executive_summary": "...",
  "key_findings": [...],
  "action_items": [...],
  "conclusion": "..."
}""",
}


@dataclass
class AgentResult:
    """Result from an agent execution."""
    role: AgentRole
    provider_id: str
    success: bool
    response: Optional[dict] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class ResearchAgent:
    """A single research agent with a specific role."""

    def __init__(
        self,
        role: AgentRole,
        config: Config,
        key_manager: KeyManager,
    ):
        self.role = role
        self.config = config
        self.key_manager = key_manager
        self.router = CapabilityRouter(config, key_manager)
        self.required_capabilities = ROLE_CAPABILITIES.get(role, ["reasoning"])

    def execute(self, context: str) -> AgentResult:
        """Execute this agent's task.

        Args:
            context: Context string with relevant findings/data

        Returns:
            AgentResult with the agent's output
        """
        import time
        start = time.monotonic()

        # Find best provider for this role's capabilities
        provider_manifest = None
        for cap in self.required_capabilities:
            provider_manifest = self.router.get_healthy_provider_for_capability(cap)
            if provider_manifest:
                break

        if not provider_manifest:
            # Fallback to active provider
            provider_name = self.config.active_provider
            try:
                provider = create_provider(provider_name)
            except Exception as e:
                return AgentResult(
                    role=self.role,
                    provider_id=provider_name,
                    success=False,
                    error=f"No provider available: {e}",
                    duration_seconds=time.monotonic() - start,
                )
        else:
            provider_name = provider_manifest.provider_id
            provider = create_provider(provider_name)

        # Build messages
        system_prompt = ROLE_PROMPTS.get(self.role, "You are a research assistant.")
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=context),
        ]

        model = provider.get_default_model()

        logger.info("AGENT %s: Executing with provider=%s model=%s", self.role.value, provider_name, model)

        # Execute with capability routing
        result = self.router.execute_with_capability_routing(
            self.required_capabilities[0],
            lambda api_key: provider.chat(api_key, model, messages),
        )

        duration = time.monotonic() - start

        if not result["success"]:
            return AgentResult(
                role=self.role,
                provider_id=provider_name,
                success=False,
                error=result.get("error", "Unknown error"),
                duration_seconds=duration,
            )

        # Parse response
        try:
            content = result["response"].content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])
            parsed = json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            parsed = {"raw_response": result["response"].content}

        return AgentResult(
            role=self.role,
            provider_id=provider_name,
            success=True,
            response=parsed,
            duration_seconds=duration,
        )


class MultiAgentOrchestrator:
    """Orchestrates multiple research agents for comprehensive analysis."""

    def __init__(self, config: Config, key_manager: KeyManager):
        self.config = config
        self.key_manager = key_manager

    def run_research_pipeline(
        self,
        raw_findings: list[dict],
        runtime_state: dict = None,
    ) -> dict:
        """Run the multi-agent research pipeline.

        Pipeline:
        1. Researcher: Organizes raw findings
        2. Evidence Collector: Supplements with evidence
        3. Verifier: Validates claims
        4. Contradiction Detector: Finds conflicts
        5. Critic: Identifies weaknesses
        6. Writer: Produces final report

        Args:
            raw_findings: Raw findings from web sources
            runtime_state: Optional runtime state for context

        Returns:
            Dict with consolidated agent results
        """
        logger.info("MULTI-AGENT: Starting research pipeline with %d findings", len(raw_findings))

        # Build initial context
        context = self._build_context(raw_findings, runtime_state)

        # Execute agents in sequence
        results = {}
        agent_sequence = [
            AgentRole.RESEARCHER,
            AgentRole.EVIDENCE_COLLECTOR,
            AgentRole.VERIFIER,
            AgentRole.CONTRADICTION_DETECTOR,
            AgentRole.CRITIC,
            AgentRole.WRITER,
        ]

        for role in agent_sequence:
            agent = ResearchAgent(role, self.config, self.key_manager)

            # Build role-specific context
            role_context = self._build_role_context(role, context, results)

            # Execute agent
            result = agent.execute(role_context)
            results[role.value] = result

            if result.success:
                logger.info(
                    "MULTI-AGENT: %s completed successfully with %s (%.1fs)",
                    role.value, result.provider_id, result.duration_seconds,
                )
                # Update context with this agent's output
                context = self._merge_agent_output(context, result)
            else:
                logger.warning(
                    "MULTI-AGENT: %s failed: %s",
                    role.value, result.error,
                )

        # Consolidate results
        consolidated = self._consolidate_results(results)

        logger.info(
            "MULTI-AGENT: Pipeline complete — %d/%d agents succeeded",
            sum(1 for r in results.values() if r.success),
            len(agent_sequence),
        )

        return consolidated

    def _build_context(self, raw_findings: list[dict], runtime_state: dict = None) -> str:
        """Build initial context from raw findings.

        Args:
            raw_findings: Raw findings list
            runtime_state: Optional runtime state

        Returns:
            Context string
        """
        lines = ["## Raw Findings\n"]
        for i, f in enumerate(raw_findings[:30], 1):
            provider = f.get("provider", "unknown")
            title = f.get("title", "")
            summary = f.get("summary", "")
            url = f.get("source_url", "")
            lines.append(f"{i}. [{provider}] {title}")
            if summary:
                lines.append(f"   Summary: {summary[:200]}")
            if url:
                lines.append(f"   Source: {url}")
            lines.append("")

        if runtime_state:
            lines.append("\n## Previous Research State\n")
            verified = runtime_state.get("verified_claims", [])
            if verified:
                lines.append(f"Verified claims: {len(verified)}")
            unverified = runtime_state.get("unverified_claims", [])
            if unverified:
                lines.append(f"Unverified claims: {len(unverified)}")
            open_q = runtime_state.get("open_questions", [])
            if open_q:
                lines.append(f"Open questions: {len(open_q)}")

        return "\n".join(lines)

    def _build_role_context(self, role: AgentRole, context: str, previous_results: dict) -> str:
        """Build context specific to a role.

        Args:
            role: Agent role
            context: Base context
            previous_results: Results from previous agents

        Returns:
            Role-specific context string
        """
        role_context = context + f"\n\n## Your Task as {role.value}\n"

        # Add relevant previous results
        if role == AgentRole.VERIFIER and "researcher" in previous_results:
            researcher_result = previous_results["researcher"]
            if researcher_result.success and researcher_result.response:
                role_context += f"\nResearcher organized findings:\n{json.dumps(researcher_result.response, indent=2)[:2000]}\n"

        elif role == AgentRole.CONTRADICTION_DETECTOR:
            for prev_role in ["researcher", "verifier"]:
                if prev_role in previous_results and previous_results[prev_role].success:
                    resp = previous_results[prev_role].response
                    if resp:
                        role_context += f"\n{prev_role.title()} output:\n{json.dumps(resp, indent=2)[:1000]}\n"

        elif role == AgentRole.WRITER:
            for prev_role in ["researcher", "verifier", "critic"]:
                if prev_role in previous_results and previous_results[prev_role].success:
                    resp = previous_results[prev_role].response
                    if resp:
                        role_context += f"\n{prev_role.title()} output:\n{json.dumps(resp, indent=2)[:1000]}\n"

        return role_context

    def _merge_agent_output(self, context: str, result: AgentResult) -> str:
        """Merge agent output into the context.

        Args:
            context: Current context
            result: Agent result to merge

        Returns:
            Updated context string
        """
        if result.response:
            context += f"\n\n## {result.role.value.title()} Output\n{json.dumps(result.response, indent=2)[:2000]}"
        return context

    def _consolidate_results(self, results: dict) -> dict:
        """Consolidate all agent results into a single output.

        Args:
            results: Dict mapping role name -> AgentResult

        Returns:
            Consolidated results dict
        """
        consolidated = {
            "agent_results": {},
            "success_count": 0,
            "total_count": len(results),
            "providers_used": set(),
            "consolidated_findings": [],
            "verified_claims": [],
            "contradictions": [],
            "open_questions": [],
            "action_items": [],
            "executive_summary": "",
        }

        for role_name, result in results.items():
            consolidated["agent_results"][role_name] = {
                "success": result.success,
                "provider": result.provider_id,
                "duration": result.duration_seconds,
                "error": result.error,
            }

            if result.success:
                consolidated["success_count"] += 1
                consolidated["providers_used"].add(result.provider_id)

                # Extract role-specific outputs
                if result.role == AgentRole.RESEARCHER:
                    consolidated["consolidated_findings"] = result.response.get("organized_findings", [])
                    consolidated["open_questions"] = result.response.get("gaps_identified", [])

                elif result.role == AgentRole.VERIFIER:
                    consolidated["verified_claims"] = result.response.get("verified_claims", [])

                elif result.role == AgentRole.CONTRADICTION_DETECTOR:
                    consolidated["contradictions"] = result.response.get("contradictions", [])

                elif result.role == AgentRole.WRITER:
                    consolidated["executive_summary"] = result.response.get("executive_summary", "")
                    consolidated["action_items"] = result.response.get("action_items", [])

        # Convert set to list for JSON serialization
        consolidated["providers_used"] = list(consolidated["providers_used"])

        return consolidated
