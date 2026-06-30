"""Runtime manager for stateful, iterative AI research workflow."""

import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from ..utils.logger import get_logger

logger = get_logger("runtime_manager")


class RuntimeManager:
    """Manages the persistent state and execution decisions for the research workflow."""

    def __init__(self, data_dir: Path, config=None):
        self.data_dir = Path(data_dir)
        self.state_file = self.data_dir / "research_runtime.json"
        
        if config is None:
            from ..utils.config import load_config
            self.config = load_config()
        else:
            self.config = config
            
        self.max_iterations = self.config.research_max_iterations
        self.state = {}
        self.load_state()

    def load_state(self) -> dict:
        """Load state from persistent json file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    self.state = json.load(f)
            except Exception as e:
                logger.error("Failed to load runtime state: %s. Reinitializing.", e)
                self.state = {}

        if not self.state or not self.state.get("cycle_id"):
            self.reset_state()
        return self.state

    def reset_state(self) -> None:
        """Reset the state for a brand new cycle."""
        self.state = {
            "cycle_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8],
            "iteration": 1,
            "max_iterations": self.max_iterations,
            "status": "researching",
            "quality_score": 0,
            "coverage_score": 0,
            "confidence_score": 0,
            "completed_topics": [],
            "research_questions": [],
            "assumptions": [],
            "history": [],
            "final_report_ready": False,
            
            # Upgraded state keys for the self-improving agent
            "verified_claims": [],
            "unverified_claims": [],
            "resolved_questions": [],
            "open_questions": [],
            "research_queue": [],
            "contradictions": [],
            "long_term_memory": "",
            "current_plan": {},
            
            # Structured quality metrics
            "quality_metrics": {
                "coverage": 0,
                "verification": 0,
                "source_diversity": 0,
                "novel_information": 0,
                "contradictions_resolved": 0,
                "overall_quality": 0,
                "reason": "Initial state"
            }
        }
        self.save_state()

    def save_state(self) -> None:
        """Save the current state to the persistent state file."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error("Failed to save runtime state: %s", e)

    def determine_current_iteration(self) -> int:
        """Get the current iteration index."""
        return self.state.get("iteration", 1)

    def should_send_email(self) -> bool:
        """Decide if workflow requirements are met to send the final email."""
        metrics = self.state.get("quality_metrics", {})
        overall_quality = metrics.get("overall_quality", self.state.get("quality_score", 0))
        verification = metrics.get("verification", 0)
        source_diversity = metrics.get("source_diversity", 0)

        iteration = self.state.get("iteration", 1)
        max_iter = self.state.get("max_iterations", self.max_iterations)

        quality_target = self.config.research_quality_threshold
        min_ver = self.config.min_verification_score
        min_div = self.config.min_source_diversity

        quality_met = (
            overall_quality >= quality_target and
            verification >= min_ver and
            source_diversity >= min_div
        )

        return quality_met or iteration >= max_iter

    def update_state(self, evaluation: dict, findings: list) -> None:
        """Update scores and tracking details from the latest iteration's evaluation."""
        self.state["quality_score"] = evaluation.get("overall_quality", evaluation.get("quality_score", 0))
        self.state["coverage_score"] = evaluation.get("coverage", evaluation.get("coverage_score", 0))
        self.state["confidence_score"] = evaluation.get("source_diversity", evaluation.get("confidence_score", 0))

        # Store structured quality metrics
        self.state["quality_metrics"] = {
            "coverage": evaluation.get("coverage", 0),
            "verification": evaluation.get("verification", 0),
            "source_diversity": evaluation.get("source_diversity", 0),
            "novel_information": evaluation.get("novel_information", 0),
            "contradictions_resolved": evaluation.get("contradictions_resolved", 0),
            "overall_quality": evaluation.get("overall_quality", 0),
            "reason": evaluation.get("reason", "")
        }

        # Update tracking lists from evaluation outputs
        self.state["verified_claims"] = evaluation.get("verified_claims", self.state.get("verified_claims", []))
        self.state["unverified_claims"] = evaluation.get("unverified_claims", self.state.get("unverified_claims", []))
        self.state["resolved_questions"] = evaluation.get("resolved_questions", self.state.get("resolved_questions", []))
        self.state["open_questions"] = evaluation.get("open_questions", self.state.get("open_questions", []))
        self.state["research_queue"] = evaluation.get("research_queue", self.state.get("research_queue", []))
        self.state["contradictions"] = evaluation.get("contradictions", self.state.get("contradictions", []))

        # Compatibility keys
        self.state["completed_topics"] = evaluation.get("completed_topics", self.state.get("completed_topics", []))
        self.state["research_questions"] = self.state["open_questions"]
        self.state["assumptions"] = evaluation.get("assumptions", self.state.get("assumptions", []))

        # Log entry to history
        self.state["history"].append({
            "iteration": self.state["iteration"],
            "quality_metrics": self.state["quality_metrics"],
            "findings_count": len(findings),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        if self.should_send_email():
            self.state["status"] = "completed"
            self.state["final_report_ready"] = True
        self.save_state()

    def increment_iteration(self) -> None:
        """Move to the next iteration step."""
        self.state["iteration"] = self.state.get("iteration", 1) + 1
        self.save_state()

    def archive_cycle(self) -> None:
        """Archive all files and state associated with the completed cycle, then reset."""
        cycle_id = self.state.get("cycle_id", "unknown")
        archive_dir = self.data_dir / "archive" / cycle_id
        archive_dir.mkdir(parents=True, exist_ok=True)

        # Archive the state file
        if self.state_file.exists():
            shutil.copy2(self.state_file, archive_dir / "research_runtime.json")

        # Archive the iteration files
        research_dir = self.data_dir / "research"
        if research_dir.exists():
            archive_research_dir = archive_dir / "research"
            archive_research_dir.mkdir(parents=True, exist_ok=True)
            for f in research_dir.glob("iteration_*.md"):
                shutil.copy2(f, archive_research_dir / f.name)
                try:
                    f.unlink()
                except Exception as e:
                    logger.warning("Could not delete iteration file %s: %s", f, e)

        logger.info("Archived cycle %s to %s", cycle_id, archive_dir)
        self.reset_state()
