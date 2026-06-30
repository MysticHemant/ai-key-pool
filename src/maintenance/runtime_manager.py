"""Runtime manager for stateful, iterative AI research workflow."""

import json
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from ..utils.logger import get_logger

logger = get_logger("runtime_manager")

# Quality metric keys that must be valid integers in 0-100 range
QUALITY_METRIC_KEYS = [
    "coverage", "verification", "source_diversity",
    "novel_information", "contradictions_resolved", "overall_quality",
]


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

    def _normalize_score_to_100(self, value, key: str) -> int:
        """Normalize a quality score to 0-100 range.

        Clamps to 0-100. For 1-10 scale detection, use _validate_quality_metrics.
        """
        if value is None:
            logger.warning("QUALITY NORMALIZE: %s is None, defaulting to 0", key)
            return 0

        try:
            val = int(value)
        except (TypeError, ValueError):
            logger.warning("QUALITY NORMALIZE: %s has non-numeric value %r, defaulting to 0", key, value)
            return 0

        if val < 0:
            logger.warning("QUALITY NORMALIZE: %s is %d (< 0), clamping to 0", key, val)
            return 0

        if val > 100:
            logger.warning("QUALITY NORMALIZE: %s is %d (> 100), clamping to 100", key, val)
            return 100

        return val

    def _validate_quality_metrics(self, metrics: dict) -> dict:
        """Validate and normalize quality metrics to 0-100 integer range.

        Detects if LLM returned 1-10 scale by checking if ALL numeric scores are <= 10.
        If so, normalizes all scores by multiplying by 10.

        Returns validated metrics dict. Never allows invalid values into RuntimeManager state.
        """
        if not isinstance(metrics, dict):
            logger.warning("QUALITY VALIDATE: metrics is not a dict (%s), using defaults", type(metrics).__name__)
            return self._default_quality_metrics("metrics was not a dict")

        # First pass: extract and validate raw numeric values
        raw_values = {}
        all_numeric = True
        for key in QUALITY_METRIC_KEYS:
            raw_value = metrics.get(key)
            if raw_value is None:
                raw_values[key] = 0
                all_numeric = False
                continue
            try:
                raw_values[key] = int(raw_value)
            except (TypeError, ValueError):
                raw_values[key] = 0
                all_numeric = False

        # Detect 1-10 scale: if all non-zero values are <= 10, assume 1-10 scale
        non_zero_values = [v for v in raw_values.values() if v > 0]
        is_1_10_scale = non_zero_values and all(v <= 10 for v in non_zero_values)

        # Second pass: normalize
        validated = {}
        for key in QUALITY_METRIC_KEYS:
            val = raw_values[key]
            original = val

            if is_1_10_scale and val > 0:
                val = val * 10
                logger.info("QUALITY NORMALIZE: %s scaled from %d to %d (1-10 -> 0-100)", key, original, val)

            # Clamp to 0-100
            val = max(0, min(100, val))
            validated[key] = val

        # Ensure reason is present
        validated["reason"] = metrics.get("reason", "")

        if is_1_10_scale:
            logger.info(
                "QUALITY VALIDATE: Detected 1-10 scale, normalized all scores to 0-100. "
                "Coverage=%d, Verification=%d, SourceDiversity=%d, OverallQuality=%d",
                validated["coverage"], validated["verification"],
                validated["source_diversity"], validated["overall_quality"],
            )

        return validated

    def _default_quality_metrics(self, reason: str) -> dict:
        """Return safe default quality metrics."""
        return {
            "coverage": 0,
            "verification": 0,
            "source_diversity": 0,
            "novel_information": 0,
            "contradictions_resolved": 0,
            "overall_quality": 0,
            "reason": reason,
        }

    def should_send_email(self) -> bool:
        """Decide if workflow requirements are met to send the final email.

        Completion requires ONE of:
        A) Quality-based: overall_quality >= threshold AND coverage >= threshold AND verification >= threshold
           AND minimum number of verified claims (>= 3)
        B) Iteration-based: iteration >= max_iterations (guaranteed completion)

        This prevents premature completion while guaranteeing eventual completion.
        """
        metrics = self.state.get("quality_metrics", {})
        overall_quality = metrics.get("overall_quality", self.state.get("quality_score", 0))
        coverage = metrics.get("coverage", 0)
        verification = metrics.get("verification", 0)

        iteration = self.state.get("iteration", 1)
        max_iter = self.state.get("max_iterations", self.max_iterations)

        quality_target = self.config.research_quality_threshold
        min_ver = self.config.min_verification_score
        min_cov = getattr(self.config, 'min_coverage', 80)
        verified_count = len(self.state.get("verified_claims", []))
        min_verified_claims = 3

        # Path A: Quality-based completion
        quality_met = (
            overall_quality >= quality_target
            and coverage >= min_cov
            and verification >= min_ver
            and verified_count >= min_verified_claims
        )

        # Path B: Guaranteed completion (max iterations reached)
        max_reached = iteration >= max_iter

        return quality_met or max_reached

    def log_completion_decision(self) -> None:
        """Log detailed email/completion decision diagnostics."""
        metrics = self.state.get("quality_metrics", {})
        overall_quality = metrics.get("overall_quality", 0)
        coverage = metrics.get("coverage", 0)
        verification = metrics.get("verification", 0)

        iteration = self.state.get("iteration", 1)
        max_iter = self.state.get("max_iterations", self.max_iterations)
        verified_count = len(self.state.get("verified_claims", []))
        open_count = len(self.state.get("open_questions", []))

        quality_target = self.config.research_quality_threshold
        min_ver = self.config.min_verification_score
        min_cov = getattr(self.config, 'min_coverage', 80)
        min_verified_claims = 3

        quality_met = (
            overall_quality >= quality_target
            and coverage >= min_cov
            and verification >= min_ver
            and verified_count >= min_verified_claims
        )
        max_reached = iteration >= max_iter
        should_send = self.should_send_email()

        logger.info("=" * 50)
        logger.info("FINAL RUNTIME DIAGNOSTICS")
        logger.info("")
        logger.info("Iteration: %d / %d", iteration, max_iter)
        logger.info("Overall Quality: %d", overall_quality)
        logger.info("Coverage: %d", coverage)
        logger.info("Verification: %d", verification)
        logger.info("Verified Claims: %d", verified_count)
        logger.info("Open Questions: %d", open_count)
        logger.info("")
        if should_send:
            logger.info("Decision: Send Final Report")
            logger.info("")
            logger.info("Reason:")
            if quality_met:
                logger.info("  Quality threshold reached.")
            if max_reached:
                logger.info("  Maximum iterations reached.")
        else:
            logger.info("Decision: Continue Research")
            logger.info("")
            logger.info("Reason:")
            missing = []
            if overall_quality < quality_target:
                missing.append("Overall quality below threshold (%d)." % quality_target)
            if coverage < min_cov:
                missing.append("Coverage below threshold (%d)." % min_cov)
            if verification < min_ver:
                missing.append("Verification below threshold (%d)." % min_ver)
            if verified_count < min_verified_claims:
                missing.append("Verified claims below minimum (%d)." % min_verified_claims)
            if not max_reached:
                missing.append("Iteration %d < max %d." % (iteration, max_iter))
            for reason in missing:
                logger.info("  %s", reason)
        logger.info("")
        logger.info("=" * 50)

    @staticmethod
    def _get_claim_key(claim) -> str:
        """Extract a stable string identifier from a claim.

        Supports both formats:
        - Legacy string: "Claim A" -> "Claim A"
        - Structured dict: {"claim": "Claim A", ...} -> "Claim A"

        Returns str(claim) as fallback for any other type.
        """
        if isinstance(claim, dict):
            return str(claim.get("claim", claim.get("id", claim.get("text", ""))))
        return str(claim)

    @staticmethod
    def _get_claim_map(claims: list) -> dict[str, any]:
        """Build a mapping from claim key -> claim object.

        Preserves metadata (confidence, source, evidence, etc.).
        If multiple claims share the same key, the last one wins.
        """
        result = {}
        for claim in claims:
            if isinstance(claim, dict):
                key = str(claim.get("claim", claim.get("id", claim.get("text", ""))))
            else:
                key = str(claim)
            result[key] = claim
        return result

    def _update_claim_tracking(self, evaluation: dict) -> None:
        """Update claim tracking lists from evaluation.

        Uses claim text/claim field as stable identifier for deduplication.
        Never builds sets directly from dictionaries.
        Preserves claim metadata (confidence, source, evidence, etc.).
        """
        # Build maps from current state (key -> claim object)
        prev_verified_map = self._get_claim_map(self.state.get("verified_claims", []))
        prev_resolved_map = self._get_claim_map(self.state.get("resolved_questions", []))
        prev_completed_map = self._get_claim_map(self.state.get("completed_topics", []))

        # Get new evaluation lists
        new_verified = evaluation.get("verified_claims", [])
        new_unverified = evaluation.get("unverified_claims", [])
        new_resolved = evaluation.get("resolved_questions", [])
        new_open = evaluation.get("open_questions", [])
        new_completed = evaluation.get("completed_topics", [])
        new_queue = evaluation.get("research_queue", [])
        new_contradictions = evaluation.get("contradictions", [])

        # Merge verified claims (keep metadata from new, fall back to existing)
        for claim in new_verified:
            key = self._get_claim_key(claim)
            if key:
                prev_verified_map[key] = claim

        # Merge resolved questions (keep metadata if present)
        for q in new_resolved:
            key = self._get_claim_key(q)
            if key:
                prev_resolved_map[key] = q

        # Merge completed topics
        for t in new_completed:
            key = self._get_claim_key(t)
            if key:
                prev_completed_map[key] = t

        # Build key sets for filtering (only string keys, no dicts)
        verified_keys = set(prev_verified_map.keys())
        resolved_keys = set(prev_resolved_map.keys())
        completed_keys = set(prev_completed_map.keys())

        # Filter unverified claims: remove those now verified
        filtered_unverified = []
        for claim in new_unverified:
            claim_key = self._get_claim_key(claim)
            if claim_key not in verified_keys:
                filtered_unverified.append(claim)

        # Filter open questions: remove those now resolved
        filtered_open = []
        for q in new_open:
            q_key = self._get_claim_key(q)
            if q_key not in resolved_keys:
                filtered_open.append(q)

        # Filter research queue: remove completed items
        filtered_queue = []
        for q in new_queue:
            q_key = self._get_claim_key(q)
            if q_key not in completed_keys and q_key not in verified_keys:
                filtered_queue.append(q)

        # Update state with merged lists (preserve metadata)
        self.state["verified_claims"] = list(prev_verified_map.values())
        self.state["unverified_claims"] = filtered_unverified
        self.state["resolved_questions"] = list(prev_resolved_map.values())
        self.state["open_questions"] = filtered_open
        self.state["completed_topics"] = list(prev_completed_map.values())
        self.state["research_queue"] = filtered_queue
        self.state["contradictions"] = new_contradictions if isinstance(new_contradictions, list) else []

        logger.info(
            "CLAIM TRACKING: verified=%d, unverified=%d, resolved=%d, open=%d, queue=%d, contradictions=%d",
            len(self.state["verified_claims"]),
            len(self.state["unverified_claims"]),
            len(self.state["resolved_questions"]),
            len(self.state["open_questions"]),
            len(self.state["research_queue"]),
            len(self.state["contradictions"]),
        )

    def update_state(self, evaluation: dict, findings: list) -> None:
        """Update scores and tracking details from the latest iteration's evaluation."""
        # Validate and normalize quality metrics
        raw_metrics = evaluation.get("evaluation", evaluation)
        if "evaluation" in evaluation:
            raw_metrics = evaluation["evaluation"]

        validated_metrics = self._validate_quality_metrics(raw_metrics)
        self.state["quality_metrics"] = validated_metrics

        # Update legacy scalar scores
        self.state["quality_score"] = validated_metrics["overall_quality"]
        self.state["coverage_score"] = validated_metrics["coverage"]
        self.state["confidence_score"] = validated_metrics["source_diversity"]

        # Update claim tracking with proper promotion/removal
        self._update_claim_tracking(evaluation)

        # Compatibility keys
        self.state["research_questions"] = self.state["open_questions"]
        self.state["assumptions"] = evaluation.get("assumptions", self.state.get("assumptions", []))

        # Log entry to history
        self.state["history"].append({
            "iteration": self.state["iteration"],
            "quality_metrics": self.state["quality_metrics"],
            "findings_count": len(findings),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Check completion
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
