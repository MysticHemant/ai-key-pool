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

# Research queue item categories
QUEUE_CATEGORIES = [
    "exploration",       # Initial discovery of providers/models
    "verification",      # Verify unverified claims with direct evidence
    "contradiction_resolution",  # Resolve conflicting claims
    "gap_filling",       # Fill gaps in knowledge (open questions)
    "confidence_improvement",    # Strengthen weak conclusions
]

# Similarity threshold for repetition detection
REPETITION_SIMILARITY_THRESHOLD = 0.80


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
                logger.info("Loaded runtime iteration: %d", self.state.get("iteration", 1))
                logger.info("Loaded runtime max_iterations: %d", self.state.get("max_iterations", self.max_iterations))
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
            },

            # Iteration findings history (structured JSON)
            "findings_history": [],
        }
        self.save_state()

    def save_state(self) -> None:
        """Save the current state to the persistent state file."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.info("Saved runtime iteration: %d", self.state.get("iteration", 1))
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

        result = quality_met or max_reached

        logger.info("should_send_email() CONDITIONS:")
        logger.info("  overall_quality >= %d : %s", quality_target, overall_quality >= quality_target)
        logger.info("  coverage >= %d : %s", min_cov, coverage >= min_cov)
        logger.info("  verification >= %d : %s", min_ver, verification >= min_ver)
        logger.info("  verified_claims >= %d : %s", min_verified_claims, verified_count >= min_verified_claims)
        logger.info("  iteration >= max_iterations : %s (iteration=%d, max=%d)", max_reached, iteration, max_iter)
        logger.info("  quality_met (Path A) : %s", quality_met)
        logger.info("  max_reached (Path B) : %s", max_reached)
        logger.info("should_send_email() RESULT: %s", result)

        return result

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

        # Determine reason
        if should_send:
            if quality_met and max_reached:
                reason = "Quality threshold reached and maximum iterations reached."
            elif quality_met:
                reason = "Quality threshold reached."
            else:
                reason = "Maximum iterations reached."
        else:
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
            reason = " ".join(missing) if missing else "Unknown reason."

        logger.info("=== EMAIL DECISION ===")
        logger.info("Iteration: %d / %d", iteration, max_iter)
        logger.info("Overall Quality: %d", overall_quality)
        logger.info("Coverage: %d", coverage)
        logger.info("Verification: %d", verification)
        logger.info("Verified Claims: %d", verified_count)
        logger.info("Open Questions: %d", open_count)
        logger.info("Configured max_iterations: %d", self.config.research_max_iterations)
        logger.info("Runtime max_iterations: %d", max_iter)
        if should_send:
            logger.info("Decision: SEND EMAIL")
        else:
            logger.info("Decision: Continue Research")
        logger.info("Reason: %s", reason)
        logger.info("======================")

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
        logger.info("RuntimeManager received evaluation:")
        logger.info("  type(evaluation): %s", type(evaluation).__name__)
        if isinstance(evaluation, dict):
            logger.info("  evaluation.keys(): %s", list(evaluation.keys()))
            logger.info("  overall_quality: %s", evaluation.get("overall_quality", "MISSING"))
            logger.info("  coverage: %s", evaluation.get("coverage", "MISSING"))
            logger.info("  verification: %s", evaluation.get("verification", "MISSING"))
            logger.info("  verified_claims count: %d", len(evaluation.get("verified_claims", [])))
            logger.info("  open_questions count: %d", len(evaluation.get("open_questions", [])))
        else:
            logger.warning("  evaluation is not a dict: %s", repr(evaluation))

        # Validate and normalize quality metrics
        # Handle both flat (orchestrator passes evaluation dict directly)
        # and nested (evaluation contains an "evaluation" sub-dict) formats
        if "evaluation" in evaluation and isinstance(evaluation.get("evaluation"), dict):
            raw_metrics = evaluation["evaluation"]
            logger.info("  Using nested evaluation sub-dict")
        else:
            raw_metrics = evaluation
            logger.info("  Using evaluation dict directly (flat format)")

        validated_metrics = self._validate_quality_metrics(raw_metrics)
        self.state["quality_metrics"] = validated_metrics
        logger.info("  After validation: overall_quality=%d, coverage=%d, verification=%d",
                     validated_metrics["overall_quality"], validated_metrics["coverage"],
                     validated_metrics["verification"])

        # Update legacy scalar scores
        self.state["quality_score"] = validated_metrics["overall_quality"]
        self.state["coverage_score"] = validated_metrics["coverage"]
        self.state["confidence_score"] = validated_metrics["source_diversity"]

        # Update claim tracking with proper promotion/removal
        self._update_claim_tracking(evaluation)

        # Compatibility keys
        self.state["research_questions"] = self.state["open_questions"]
        self.state["assumptions"] = evaluation.get("assumptions", self.state.get("assumptions", []))

        # Save structured findings for this iteration
        self._save_iteration_findings(findings, validated_metrics)

        # Add new queue items from evaluation
        new_queue_items = evaluation.get("research_queue", [])
        if new_queue_items:
            self.add_queue_items(new_queue_items)

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

    def _save_iteration_findings(self, findings: list, quality_metrics: dict) -> None:
        """Save structured findings for this iteration to disk and state history.

        Writes JSON file to data/research/iteration_N_findings.json and
        appends a summary entry to state['findings_history'].
        """
        iteration = self.state.get("iteration", 1)
        research_dir = self.data_dir / "research"
        research_dir.mkdir(parents=True, exist_ok=True)

        # Build structured finding entries
        structured_findings = []
        for f in findings:
            if isinstance(f, dict):
                structured_findings.append({
                    "claim": f.get("description", f.get("claim", "")),
                    "evidence": f.get("evidence", ""),
                    "source": f.get("url", f.get("source", "")),
                    "confidence": f.get("confidence", "medium"),
                    "verification_status": f.get("verification_status", "unverified"),
                    "category": f.get("type", "general"),
                    "importance": f.get("action", "none"),
                    "provider": f.get("provider", ""),
                    "model": f.get("model"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        # Write findings JSON file
        findings_file = research_dir / f"iteration_{iteration}_findings.json"
        findings_data = {
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "findings": structured_findings,
            "quality_metrics": quality_metrics,
        }
        try:
            with open(findings_file, "w") as f:
                json.dump(findings_data, f, indent=2)
            logger.info("Saved structured findings to %s (%d findings)",
                         findings_file, len(structured_findings))
        except Exception as e:
            logger.error("Failed to save findings to %s: %s", findings_file, e)

        # Append summary to state history
        if "findings_history" not in self.state:
            self.state["findings_history"] = []
        self.state["findings_history"].append({
            "iteration": iteration,
            "findings_count": len(structured_findings),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file": str(findings_file.name),
        })

    def increment_iteration(self) -> None:
        """Move to the next iteration step."""
        prev = self.state.get("iteration", 1)
        self.state["iteration"] = prev + 1
        logger.info("Incremented runtime iteration: %d -> %d", prev, self.state["iteration"])
        self.save_state()

    # ─── Research Queue Management ───────────────────────────────────────

    def initialize_research_queue(self) -> None:
        """Seed the research queue for iteration 1 with exploration items.

        Called at the start of a new cycle to populate the queue
        with initial research objectives.
        """
        queue = self.state.get("research_queue", [])
        # Check if queue already has pending dict items
        has_dict_items = any(isinstance(i, dict) and i.get("status") == "pending" for i in queue)
        if has_dict_items:
            return

        queue = [
            {
                "topic": "Discover all available AI providers and their offerings",
                "category": "exploration",
                "priority": 1,
                "added_iteration": 1,
                "status": "pending",
            },
            {
                "topic": "Identify new model releases from major providers",
                "category": "exploration",
                "priority": 1,
                "added_iteration": 1,
                "status": "pending",
            },
            {
                "topic": "Check pricing and free-tier changes across providers",
                "category": "exploration",
                "priority": 2,
                "added_iteration": 1,
                "status": "pending",
            },
            {
                "topic": "Identify API deprecations and breaking changes",
                "category": "exploration",
                "priority": 2,
                "added_iteration": 1,
                "status": "pending",
            },
        ]
        self.state["research_queue"] = queue
        self.save_state()
        logger.info("RESEARCH QUEUE: Seeded with %d exploration items", len(queue))

    def consume_queue_items(self, count: int = 3) -> list[dict]:
        """Remove and return up to `count` pending items from the research queue.

        Returns the highest-priority pending items first.
        Handles both string and dict items.
        """
        queue = self.state.get("research_queue", [])
        pending = []
        for item in queue:
            if isinstance(item, dict) and item.get("status") == "pending":
                pending.append(item)
            elif isinstance(item, str):
                # Convert legacy string items to dict format
                pending.append({
                    "topic": item,
                    "status": "pending",
                    "priority": 2,
                    "category": "exploration",
                })

        pending.sort(key=lambda x: x.get("priority", 99))

        to_consume = pending[:count]
        consumed_topics = {item.get("topic", "") for item in to_consume}

        # Mark consumed items as done
        for item in queue:
            if isinstance(item, dict):
                if item.get("topic", "") in consumed_topics:
                    item["status"] = "completed"
            elif isinstance(item, str) and item in consumed_topics:
                # Convert string to dict and mark completed
                idx = queue.index(item)
                queue[idx] = {"topic": item, "status": "completed", "priority": 2, "category": "exploration"}

        self.state["research_queue"] = queue
        self.save_state()

        logger.info("RESEARCH QUEUE: Consumed %d items (%d remaining pending)",
                     len(to_consume),
                     len([i for i in queue if isinstance(i, dict) and i.get("status") == "pending"]))
        return to_consume

    def add_queue_items(self, items: list[dict]) -> None:
        """Add new items to the research queue.

        Deduplicates by topic text. Only adds items with status='pending'.
        Handles both string and dict items in the existing queue.
        """
        queue = self.state.get("research_queue", [])
        existing_topics = set()
        for item in queue:
            if isinstance(item, dict):
                existing_topics.add(item.get("topic", ""))
            elif isinstance(item, str):
                existing_topics.add(item)

        added = 0
        for item in items:
            if isinstance(item, str):
                topic = item
                item = {"topic": topic}
            elif isinstance(item, dict):
                topic = item.get("topic", "")
            else:
                continue

            if topic and topic not in existing_topics:
                if "status" not in item:
                    item["status"] = "pending"
                if "added_iteration" not in item:
                    item["added_iteration"] = self.state.get("iteration", 1)
                if "priority" not in item:
                    item["priority"] = 2
                if "category" not in item:
                    item["category"] = "gap_filling"
                queue.append(item)
                existing_topics.add(topic)
                added += 1

        self.state["research_queue"] = queue
        self.save_state()
        if added:
            logger.info("RESEARCH QUEUE: Added %d new items (%d total)", added, len(queue))

    def get_research_focus(self) -> dict:
        """Determine what this iteration should research based on queue and state.

        Returns a dict with:
        - objectives: list of specific research objectives
        - category: primary research category for this iteration
        - queue_items: items to consume this iteration
        """
        iteration = self.state.get("iteration", 1)
        max_iter = self.state.get("max_iterations", 8)

        queue = self.state.get("research_queue", [])
        pending = []
        for item in queue:
            if isinstance(item, dict) and item.get("status") == "pending":
                pending.append(item)
            elif isinstance(item, str):
                pending.append({
                    "topic": item,
                    "status": "pending",
                    "priority": 2,
                    "category": "exploration",
                })

        # Determine research phase based on iteration progress
        progress = iteration / max_iter if max_iter > 0 else 0

        if iteration == 1:
            # Phase 1: General exploration
            category = "exploration"
            objectives = [
                "Discover all available AI providers and their current offerings",
                "Identify recent model releases and announcements",
                "Collect pricing and free-tier information",
            ]
        elif progress < 0.3:
            # Phase 2: Investigate unanswered questions
            category = "gap_filling"
            open_q = self.state.get("open_questions", [])
            objectives = [f"Investigate: {q}" for q in open_q[:3]] or [
                "Identify information gaps in current research",
                "Research areas with low confidence findings",
            ]
        elif progress < 0.5:
            # Phase 3: Verify claims
            category = "verification"
            unverified = self.state.get("unverified_claims", [])
            objectives = [f"Verify: {self._get_claim_key(c)}" for c in unverified[:3]] or [
                "Verify claims with independent sources",
                "Cross-reference findings across multiple sources",
            ]
        elif progress < 0.7:
            # Phase 4: Resolve contradictions
            category = "contradiction_resolution"
            contradictions = self.state.get("contradictions", [])
            unresolved = [c for c in contradictions
                         if isinstance(c, dict) and c.get("resolution_status") != "resolved"]
            objectives = [f"Resolve: {c.get('claim', str(c))[:80]}" for c in unresolved[:3]] or [
                "Check for conflicting information across sources",
                "Validate consistency of findings",
            ]
        else:
            # Phase 5+: Improve confidence and fill remaining gaps
            category = "confidence_improvement"
            objectives = [
                "Strengthen weak conclusions with additional evidence",
                "Fill remaining knowledge gaps",
                "Final verification of key findings",
            ]

        # Consume queue items relevant to current category
        relevant_items = [item for item in pending if item.get("category") == category]
        queue_items = relevant_items[:3] if relevant_items else pending[:2]

        return {
            "objectives": objectives,
            "category": category,
            "queue_items": queue_items,
            "iteration": iteration,
            "progress": round(progress, 2),
        }

    # ─── Iteration Similarity Detection ──────────────────────────────────

    @staticmethod
    def _extract_finding_keys(findings: list) -> set[str]:
        """Extract normalized keys from findings for similarity comparison.

        Keys are based on provider + description/model (lowercased, stripped).
        Tolerates None values, missing fields, and malformed findings.
        """
        keys = set()
        for f in findings:
            if isinstance(f, dict):
                provider = (f.get("provider") or "").strip().lower()
                desc_raw = (
                    f.get("description")
                    or f.get("model")
                    or f.get("claim")
                    or ""
                )
                desc = str(desc_raw).strip().lower()[:100]
                if provider or desc:
                    keys.add(f"{provider}:{desc}")
            elif isinstance(f, str):
                keys.add(f.lower().strip()[:100])
        return keys

    def compute_iteration_similarity(self, findings_a: list, findings_b: list) -> float:
        """Compute Jaccard similarity between two sets of findings.

        Returns float between 0.0 (completely different) and 1.0 (identical).
        """
        keys_a = self._extract_finding_keys(findings_a)
        keys_b = self._extract_finding_keys(findings_b)

        if not keys_a and not keys_b:
            return 1.0  # Both empty = identical
        if not keys_a or not keys_b:
            return 0.0  # One empty = completely different

        intersection = keys_a & keys_b
        union = keys_a | keys_b
        return len(intersection) / len(union) if union else 0.0

    def detect_repetition(self, current_findings: list) -> dict:
        """Compare current findings with previous iteration to detect repetition.

        Returns dict with:
        - is_repeated: bool
        - similarity: float (0-1)
        - previous_iteration: int or None
        - strategy_shift: suggested action if repetition detected
        """
        history = self.state.get("history", [])
        if len(history) < 2:
            return {
                "is_repeated": False,
                "similarity": 0.0,
                "previous_iteration": None,
                "strategy_shift": None,
            }

        # Compare with the most recent previous iteration's findings
        # We need to load the previous iteration's findings from the findings history
        prev_entry = history[-2] if len(history) >= 2 else None
        if not prev_entry:
            return {
                "is_repeated": False,
                "similarity": 0.0,
                "previous_iteration": None,
                "strategy_shift": None,
            }

        prev_iteration = prev_entry.get("iteration", 0)
        prev_findings_count = prev_entry.get("findings_count", 0)

        # Load previous iteration's findings from disk if available
        prev_findings = self._load_iteration_findings(prev_iteration)
        if not prev_findings:
            return {
                "is_repeated": False,
                "similarity": 0.0,
                "previous_iteration": prev_iteration,
                "strategy_shift": None,
            }

        similarity = self.compute_iteration_similarity(current_findings, prev_findings)
        is_repeated = similarity >= REPETITION_SIMILARITY_THRESHOLD

        strategy_shift = None
        if is_repeated:
            strategy_shift = self._suggest_strategy_shift()
            logger.warning(
                "REPETITION DETECTED: %.1f similarity with iteration %d. Strategy shift: %s",
                similarity, prev_iteration, strategy_shift.get("category", "unknown"),
            )

        return {
            "is_repeated": is_repeated,
            "similarity": round(similarity, 3),
            "previous_iteration": prev_iteration,
            "strategy_shift": strategy_shift,
        }

    def _load_iteration_findings(self, iteration: int) -> list:
        """Load findings from a previous iteration's JSON file."""
        findings_file = self.data_dir / "research" / f"iteration_{iteration}_findings.json"
        if findings_file.exists():
            try:
                with open(findings_file) as f:
                    data = json.load(f)
                return data.get("findings", [])
            except Exception as e:
                logger.warning("Could not load findings for iteration %d: %s", iteration, e)
        return []

    def _suggest_strategy_shift(self) -> dict:
        """Suggest a new research strategy when repetition is detected.

        Analyzes current state and returns a shifted focus.
        """
        iteration = self.state.get("iteration", 1)
        unverified = self.state.get("unverified_claims", [])
        open_q = self.state.get("open_questions", [])
        contradictions = self.state.get("contradictions", [])
        queue = self.state.get("research_queue", [])
        pending = [i for i in queue if i.get("status") == "pending"]

        # Priority: contradictions > unverified claims > open questions > new sources
        if contradictions:
            unresolved = [c for c in contradictions
                         if isinstance(c, dict) and c.get("resolution_status") != "resolved"]
            if unresolved:
                return {
                    "category": "contradiction_resolution",
                    "objectives": [
                        f"Resolve contradiction: {c.get('claim', str(c))[:80]}"
                        for c in unresolved[:3]
                    ],
                    "reason": "Repetition detected — switching to contradiction resolution",
                }

        if unverified:
            return {
                "category": "verification",
                "objectives": [
                    f"Verify claim: {self._get_claim_key(c)}"
                    for c in unverified[:3]
                ],
                "reason": "Repetition detected — switching to claim verification",
            }

        if open_q:
            return {
                "category": "gap_filling",
                "objectives": [f"Answer question: {q}" for q in open_q[:3]],
                "reason": "Repetition detected — switching to gap filling",
            }

        # Fallback: explore different sources
        return {
            "category": "exploration",
            "objectives": [
                "Search alternative sources not yet checked",
                "Investigate niche providers and emerging models",
                "Check community forums and developer discussions",
            ],
            "reason": "Repetition detected — switching to alternative source exploration",
        }

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
