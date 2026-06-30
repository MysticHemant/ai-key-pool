"""Tests for the Iterative Runtime Manager and workflow gating."""

import sys
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Config
from src.key_pool import KeyManager
from src.maintenance.runtime_manager import RuntimeManager
from src.maintenance.orchestrator import run_daily_maintenance


def test_runtime_manager_state_load_save():
    """Test that RuntimeManager initializes, saves, loads, and resets state properly."""
    print("  - Running test_runtime_manager_state_load_save...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Test config
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Verify initial state structure
        state = manager.state
        assert state["iteration"] == 1
        assert state["max_iterations"] == 5
        assert state["status"] == "researching"
        assert state["quality_score"] == 0
        assert len(state["cycle_id"]) > 0

        # Verify new state variables exist
        assert "verified_claims" in state
        assert "unverified_claims" in state
        assert "long_term_memory" in state
        assert "current_plan" in state

        # Modify state and save
        manager.state["quality_score"] = 80
        manager.state["quality_metrics"]["overall_quality"] = 80
        manager.state["assumptions"] = ["Test assumption"]
        manager.save_state()

        # Reload state in a new manager instance
        manager2 = RuntimeManager(tmp_path, config=config)
        assert manager2.state["quality_score"] == 80
        assert manager2.state["quality_metrics"]["overall_quality"] == 80
        assert manager2.state["assumptions"] == ["Test assumption"]
        assert manager2.state["cycle_id"] == state["cycle_id"]

        # Reset state
        manager2.reset_state()
        assert manager2.state["iteration"] == 1
        assert manager2.state["quality_score"] == 0
        assert manager2.state["quality_metrics"]["overall_quality"] == 0
        assert manager2.state["cycle_id"] != state["cycle_id"]
    print("    PASSED")


def test_runtime_manager_gating():
    """Test the should_send_email condition gating using structured metrics."""
    print("  - Running test_runtime_manager_gating...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(
            research_max_iterations=4,
            research_quality_threshold=90,
            min_verification_score=80,
            min_source_diversity=70,
            min_coverage=80,
        )
        manager = RuntimeManager(tmp_path, config=config)

        # 1. Quality < 90, Iteration < max_iterations -> No email
        assert not manager.should_send_email()

        # 2. Overall Quality >= 90 but verification < min -> No email
        manager.state["quality_metrics"] = {
            "overall_quality": 95,
            "verification": 50,
            "source_diversity": 80,
            "coverage": 85,
        }
        manager.state["verified_claims"] = ["claim1", "claim2", "claim3"]
        assert not manager.should_send_email()

        # 3. Overall Quality >= 90, verification >= min, diversity >= min, coverage >= min, verified >= 3 -> Email
        manager.state["quality_metrics"] = {
            "overall_quality": 95,
            "verification": 85,
            "source_diversity": 75,
            "coverage": 85,
        }
        manager.state["verified_claims"] = ["claim1", "claim2", "claim3"]
        assert manager.should_send_email()

        # 4. Quality met but less than 3 verified claims -> No email
        manager.state["quality_metrics"] = {
            "overall_quality": 95,
            "verification": 85,
            "source_diversity": 75,
            "coverage": 85,
        }
        manager.state["verified_claims"] = ["claim1"]
        assert not manager.should_send_email()

        # 5. Iteration >= max -> Email (guaranteed completion)
        manager.state["quality_metrics"] = {
            "overall_quality": 50,
            "verification": 20,
            "source_diversity": 20,
            "coverage": 20,
        }
        manager.state["verified_claims"] = []
        manager.state["iteration"] = 4
        assert manager.should_send_email()
    print("    PASSED")


def test_runtime_manager_quality_normalization():
    """Test quality score normalization handles 1-10 and out-of-range values."""
    print("  - Running test_runtime_manager_quality_normalization...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Test _normalize_score_to_100 (basic clamping)
        assert manager._normalize_score_to_100(50, "test") == 50
        assert manager._normalize_score_to_100(0, "test") == 0
        assert manager._normalize_score_to_100(100, "test") == 100
        assert manager._normalize_score_to_100(150, "test") == 100
        assert manager._normalize_score_to_100(-5, "test") == 0
        assert manager._normalize_score_to_100(None, "test") == 0
        assert manager._normalize_score_to_100("abc", "test") == 0

        # Test validate_quality_metrics with 1-10 scale (auto-detect)
        metrics_1_10 = {
            "coverage": 7,
            "verification": 8,
            "source_diversity": 6,
            "novel_information": 5,
            "contradictions_resolved": 9,
            "overall_quality": 7,
            "reason": "test 1-10 scale"
        }
        validated = manager._validate_quality_metrics(metrics_1_10)
        assert validated["coverage"] == 70, f"Expected 70, got {validated['coverage']}"
        assert validated["verification"] == 80, f"Expected 80, got {validated['verification']}"
        assert validated["source_diversity"] == 60, f"Expected 60, got {validated['source_diversity']}"
        assert validated["overall_quality"] == 70, f"Expected 70, got {validated['overall_quality']}"

        # Test validate_quality_metrics with 0-100 scale (no normalization)
        metrics_0_100 = {
            "coverage": 85,
            "verification": 72,
            "source_diversity": 68,
            "novel_information": 55,
            "contradictions_resolved": 40,
            "overall_quality": 78,
            "reason": "test 0-100 scale"
        }
        validated2 = manager._validate_quality_metrics(metrics_0_100)
        assert validated2["coverage"] == 85
        assert validated2["verification"] == 72
        assert validated2["overall_quality"] == 78

        # Test validate_quality_metrics clamps out-of-range
        metrics_high = {
            "coverage": 120,
            "verification": -5,
            "source_diversity": 200,
            "novel_information": 0,
            "contradictions_resolved": 50,
            "overall_quality": 110,
            "reason": "out of range"
        }
        validated3 = manager._validate_quality_metrics(metrics_high)
        assert validated3["coverage"] == 100
        assert validated3["verification"] == 0
        assert validated3["source_diversity"] == 100
        assert validated3["overall_quality"] == 100
    print("    PASSED")


def test_runtime_manager_claim_tracking():
    """Test claim tracking properly removes completed items and promotes unresolved."""
    print("  - Running test_runtime_manager_claim_tracking...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Set up initial state
        manager.state["verified_claims"] = ["verified claim 1"]
        manager.state["unverified_claims"] = ["unverified claim 1"]
        manager.state["open_questions"] = ["question 1"]
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []

        # Evaluation with new verified claims and resolved questions
        evaluation = {
            "coverage": 70,
            "verification": 70,
            "source_diversity": 70,
            "novel_information": 70,
            "contradictions_resolved": 70,
            "overall_quality": 70,
            "reason": "test",
            "verified_claims": ["verified claim 1", "new verified claim"],
            "unverified_claims": ["new unverified claim"],
            "resolved_questions": ["question 1"],
            "open_questions": ["new question"],
            "completed_topics": ["completed topic 1"],
            "research_queue": ["queue item 1"],
            "contradictions": [],
            "assumptions": ["assumption 1"],
        }

        manager.update_state(evaluation, [])

        # Verify claim tracking updated correctly
        assert "verified claim 1" in manager.state["verified_claims"]
        assert "new verified claim" in manager.state["verified_claims"]
        assert "question 1" in manager.state["resolved_questions"]
        assert "completed topic 1" in manager.state["completed_topics"]
        assert "new unverified claim" in manager.state["unverified_claims"]
        assert "new question" in manager.state["open_questions"]
        assert "queue item 1" in manager.state["research_queue"]
    print("    PASSED")


def test_runtime_manager_archiving():
    """Test state archiving and iteration file cleanup."""
    print("  - Running test_runtime_manager_archiving...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)
        cycle_id = manager.state["cycle_id"]

        # Create dummy iteration files
        research_dir = tmp_path / "research"
        research_dir.mkdir()
        iter1 = research_dir / "iteration_1.md"
        iter1.write_text("Iteration 1 content", encoding="utf-8")
        iter2 = research_dir / "iteration_2.md"
        iter2.write_text("Iteration 2 content", encoding="utf-8")

        # Update state with high quality metrics to trigger completed status
        manager.update_state({
            "overall_quality": 95,
            "verification": 85,
            "source_diversity": 85,
            "coverage": 85,
            "verified_claims": ["c1", "c2", "c3"],
        }, [])

        # Archive
        manager.archive_cycle()

        # Verify active iteration files are deleted
        assert not iter1.exists()
        assert not iter2.exists()

        # Verify archive contains state and iterations
        archive_dir = tmp_path / "archive" / cycle_id
        assert (archive_dir / "research_runtime.json").exists()
        assert (archive_dir / "research" / "iteration_1.md").exists()
        assert (archive_dir / "research" / "iteration_2.md").exists()

        # Verify new cycle is started
        assert manager.state["iteration"] == 1
        assert manager.state["cycle_id"] != cycle_id
    print("    PASSED")


def test_guaranteed_completion_on_max_iterations():
    """Test that max iterations always triggers email, archive, and reset."""
    print("  - Running test_guaranteed_completion_on_max_iterations...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(
            research_max_iterations=3,
            research_quality_threshold=90,
            min_verification_score=80,
            min_source_diversity=70,
            min_coverage=80,
        )
        manager = RuntimeManager(tmp_path, config=config)

        # Simulate iterations with low quality
        for i in range(1, 4):
            manager.state["iteration"] = i
            manager.state["quality_metrics"] = {
                "overall_quality": 30,
                "verification": 20,
                "source_diversity": 20,
                "novel_information": 10,
                "contradictions_resolved": 0,
                "reason": "Low quality",
            }
            manager.state["verified_claims"] = []

            if manager.should_send_email():
                manager.state["status"] = "completed"
                manager.state["final_report_ready"] = True
                manager.save_state()
                break

            manager.increment_iteration()

        # Should have completed on iteration 3
        assert manager.state["status"] == "completed"
        assert manager.state["final_report_ready"] is True
        assert manager.state["iteration"] == 3
    print("    PASSED")


def test_orchestrator_integration():
    """Test that orchestrator runs continuous research loop and sends email on completion."""
    print("  - Running test_orchestrator_integration...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Mock env & paths
        env = {
            "AIKEYPOOL_ACTIVE_PROVIDER": "groq",
            "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
            "AIKEYPOOL_DATA_DIR": str(tmp_path),
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "u",
            "SMTP_PASSWORD": "p",
            "EMAIL_RECIPIENT": "r@t.com",
            "AIKEYPOOL_RESEARCH_MAX_ITERATIONS": "2",
        }

        # Mock LLM response that fails to hit quality target (low scores)
        mock_low_quality = {
            "findings": [],
            "summary": "Low quality summary",
            "iteration_report": {"summary": "Low summary", "evidence": "None"},
            "evaluation": {
                "overall_quality": 45,
                "coverage": 40,
                "verification": 35,
                "source_diversity": 40,
                "novel_information": 10,
                "contradictions_resolved": 0,
                "verified_claims": [],
                "unverified_claims": ["claim1"],
                "open_questions": ["q1"],
                "research_queue": ["r1"],
                "contradictions": [],
                "completed_topics": [],
            }
        }

        with patch.dict("os.environ", env):
            # Run 1: Continuous loop with low quality — runs 2 iterations, reaches max, sends email
            with patch("src.maintenance.orchestrator.research_providers", return_value=mock_low_quality):
                with patch("src.maintenance.orchestrator.generate_research_plan", return_value={"objectives": ["Test"]}):
                    with patch("src.maintenance.orchestrator.generate_final_report", return_value=mock_low_quality):
                        with patch("src.maintenance.orchestrator._do_send_email", return_value=True) as mock_send:
                            result = run_daily_maintenance()
                            assert result["steps"]["email"]["status"] == "sent"
                            mock_send.assert_called_once()

            # Verify iteration files were created and then archived
            # (files no longer exist in research/ since archive_cycle moves them)
            with open(tmp_path / "research_runtime.json") as f:
                state = json.load(f)
            assert state["iteration"] == 1
            assert state["quality_metrics"]["overall_quality"] == 0

    print("    PASSED")


def test_orchestrator_guaranteed_completion_on_max_iterations():
    """Test orchestrator completes and sends email even with low quality when max iterations reached."""
    print("  - Running test_orchestrator_guaranteed_completion_on_max_iterations...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        env = {
            "AIKEYPOOL_ACTIVE_PROVIDER": "groq",
            "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
            "AIKEYPOOL_DATA_DIR": str(tmp_path),
            "AIKEYPOOL_RESEARCH_MAX_ITERATIONS": "2",
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "u",
            "SMTP_PASSWORD": "p",
            "EMAIL_RECIPIENT": "r@t.com",
        }

        mock_low_quality = {
            "findings": [],
            "summary": "Low quality",
            "iteration_report": {"summary": "Low", "evidence": "None"},
            "evaluation": {
                "overall_quality": 30,
                "coverage": 25,
                "verification": 20,
                "source_diversity": 20,
                "novel_information": 10,
                "contradictions_resolved": 0,
                "verified_claims": [],
                "unverified_claims": [],
                "open_questions": [],
                "research_queue": [],
                "contradictions": [],
                "completed_topics": [],
            }
        }

        with patch.dict("os.environ", env):
            # Run: Continuous loop — runs 2 iterations, reaches max, sends email, archives
            with patch("src.maintenance.orchestrator.research_providers", return_value=mock_low_quality):
                with patch("src.maintenance.orchestrator.generate_research_plan", return_value={"objectives": ["Test"]}):
                    with patch("src.maintenance.orchestrator.generate_final_report", return_value=mock_low_quality):
                        with patch("src.maintenance.orchestrator._do_send_email", return_value=True) as mock_send:
                            result = run_daily_maintenance()
                            assert result["steps"]["email"]["status"] == "sent"
                            mock_send.assert_called_once()

            # Verify cycle was archived and state reset
            with open(tmp_path / "research_runtime.json") as f:
                state = json.load(f)
            assert state["iteration"] == 1
            assert state["quality_metrics"]["overall_quality"] == 0

    print("    PASSED")


def test_completion_diagnostics_logging():
    """Test that completion diagnostics are logged properly."""
    print("  - Running test_completion_diagnostics_logging...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(
            research_max_iterations=8,
            research_quality_threshold=90,
            min_verification_score=80,
            min_coverage=80,
        )
        manager = RuntimeManager(tmp_path, config=config)

        # Set up metrics that don't meet quality threshold
        manager.state["quality_metrics"] = {
            "overall_quality": 75,
            "coverage": 80,
            "verification": 70,
            "source_diversity": 60,
            "novel_information": 50,
            "contradictions_resolved": 30,
            "reason": "Partial coverage",
        }
        manager.state["verified_claims"] = ["claim1"]

        # This should not raise - just tests the logging path
        manager.log_completion_decision()

        # Verify it completes without error
        assert True
    print("    PASSED")


def test_claim_tracking_string_claims():
    """Test claim tracking with legacy string claims."""
    print("  - Running test_claim_tracking_string_claims...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Initialize with string claims
        manager.state["verified_claims"] = ["Claim A", "Claim B"]
        manager.state["unverified_claims"] = ["Claim C"]
        manager.state["open_questions"] = ["Question 1"]
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []
        manager.state["research_queue"] = ["Task 1"]

        # Evaluation with string claims
        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": ["Claim A", "Claim C"],  # Claim C promoted
            "unverified_claims": [],
            "resolved_questions": ["Question 1"],  # Question resolved
            "open_questions": [],
            "completed_topics": ["Task 1"],  # Task completed
            "research_queue": [],
            "contradictions": [],
        }

        manager.update_state(evaluation, [])

        # Verify string claims work without TypeError
        assert "Claim A" in manager.state["verified_claims"]
        assert "Claim C" in manager.state["verified_claims"]
        assert "Question 1" in manager.state["resolved_questions"]
        assert "Task 1" in manager.state["completed_topics"]
        # Claim C removed from unverified since it's now verified
        assert len([c for c in manager.state["unverified_claims"] if c == "Claim C"]) == 0
        print("    PASSED")


def test_claim_tracking_dict_claims():
    """Test claim tracking with structured dictionary claims (the crash case)."""
    print("  - Running test_claim_tracking_dict_claims...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Initialize with dict claims - this used to crash with TypeError
        manager.state["verified_claims"] = [
            {"claim": "Claim A", "confidence": 95, "source": "OpenAI Blog"},
            {"claim": "Claim B", "confidence": 80, "source": "Anthropic"},
        ]
        manager.state["unverified_claims"] = [
            {"claim": "Claim C", "confidence": 50, "source": "Unknown"},
        ]
        manager.state["open_questions"] = ["Question 1"]
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []
        manager.state["research_queue"] = []

        # Evaluation with dict claims
        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                {"claim": "Claim A", "confidence": 98, "source": "OpenAI Blog", "verification_status": "verified"},
                {"claim": "Claim C", "confidence": 75, "source": "Google AI", "verification_status": "verified"},
            ],
            "unverified_claims": [],
            "resolved_questions": [{"question": "Question 1", "answer": "Resolved"}],
            "open_questions": [],
            "completed_topics": ["Topic 1"],
            "research_queue": [],
            "contradictions": [],
        }

        # This must NOT raise TypeError: unhashable type: 'dict'
        manager.update_state(evaluation, [])

        # Verify dict claims work and metadata is preserved
        verified_keys = {RuntimeManager._get_claim_key(c) for c in manager.state["verified_claims"]}
        assert "Claim A" in verified_keys
        assert "Claim C" in verified_keys

        # Verify metadata preserved
        claim_a = [c for c in manager.state["verified_claims"] if RuntimeManager._get_claim_key(c) == "Claim A"][0]
        assert claim_a["confidence"] == 98  # Updated confidence
        assert claim_a["source"] == "OpenAI Blog"

        claim_c = [c for c in manager.state["verified_claims"] if RuntimeManager._get_claim_key(c) == "Claim C"][0]
        assert claim_c["confidence"] == 75  # Promoted from unverified
        print("    PASSED")


def test_claim_tracking_mixed_claims():
    """Test claim tracking with mixed string and dictionary claims."""
    print("  - Running test_claim_tracking_mixed_claims...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Existing state has string claims
        manager.state["verified_claims"] = ["String Claim A"]
        manager.state["unverified_claims"] = ["String Claim B"]
        manager.state["open_questions"] = []
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []
        manager.state["research_queue"] = []

        # Evaluation adds dict claims
        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                {"claim": "Dict Claim X", "confidence": 90},
                "String Claim B",  # Promoted from unverified
            ],
            "unverified_claims": [],
            "resolved_questions": [],
            "open_questions": [],
            "completed_topics": [],
            "research_queue": [],
            "contradictions": [],
        }

        # Must not crash with mixed types
        manager.update_state(evaluation, [])

        verified_keys = {RuntimeManager._get_claim_key(c) for c in manager.state["verified_claims"]}
        assert "String Claim A" in verified_keys
        assert "Dict Claim X" in verified_keys
        assert "String Claim B" in verified_keys
        print("    PASSED")


def test_claim_tracking_duplicate_detection():
    """Test that duplicate claims are properly detected and merged."""
    print("  - Running test_claim_tracking_duplicate_detection...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Existing verified claim
        manager.state["verified_claims"] = [
            {"claim": "Duplicate Claim", "confidence": 80, "source": "Source A"},
        ]
        manager.state["unverified_claims"] = []
        manager.state["open_questions"] = []
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []
        manager.state["research_queue"] = []

        # Evaluation adds same claim with updated metadata
        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                {"claim": "Duplicate Claim", "confidence": 95, "source": "Source B"},
            ],
            "unverified_claims": [],
            "resolved_questions": [],
            "open_questions": [],
            "completed_topics": [],
            "research_queue": [],
            "contradictions": [],
        }

        manager.update_state(evaluation, [])

        # Should only have one claim, with updated metadata
        dup_claims = [c for c in manager.state["verified_claims"]
                      if RuntimeManager._get_claim_key(c) == "Duplicate Claim"]
        assert len(dup_claims) == 1
        assert dup_claims[0]["confidence"] == 95  # Updated
        assert dup_claims[0]["source"] == "Source B"  # Updated
        print("    PASSED")


def test_claim_tracking_promotion_unverified_to_verified():
    """Test that claims are properly promoted from unverified to verified."""
    print("  - Running test_claim_tracking_promotion_unverified_to_verified...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        manager.state["verified_claims"] = []
        manager.state["unverified_claims"] = [
            {"claim": "Promoted Claim", "confidence": 30, "source": "Uncertain"},
            {"claim": "Stays Unverified", "confidence": 20, "source": "Unknown"},
        ]
        manager.state["open_questions"] = []
        manager.state["resolved_questions"] = []
        manager.state["completed_topics"] = []
        manager.state["research_queue"] = []

        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                {"claim": "Promoted Claim", "confidence": 92, "source": "Official Blog", "verification_status": "verified"},
            ],
            "unverified_claims": [
                {"claim": "Stays Unverified", "confidence": 25, "source": "Unknown"},
            ],
            "resolved_questions": [],
            "open_questions": [],
            "completed_topics": [],
            "research_queue": [],
            "contradictions": [],
        }

        manager.update_state(evaluation, [])

        # Promoted Claim should be in verified with updated metadata
        promoted = [c for c in manager.state["verified_claims"]
                    if RuntimeManager._get_claim_key(c) == "Promoted Claim"]
        assert len(promoted) == 1
        assert promoted[0]["confidence"] == 92
        assert promoted[0]["source"] == "Official Blog"

        # Stays Unverified should remain in unverified
        stays = [c for c in manager.state["unverified_claims"]
                 if RuntimeManager._get_claim_key(c) == "Stays Unverified"]
        assert len(stays) == 1
        assert stays[0]["confidence"] == 25
        print("    PASSED")


def test_claim_tracking_no_typeerror_with_dicts():
    """Regression test: verify no TypeError with dict claims (the original crash)."""
    print("  - Running test_claim_tracking_no_typeerror_with_dicts...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Simulate the exact scenario that caused the crash
        manager.state["verified_claims"] = [
            {
                "claim": "OpenAI released GPT-5",
                "confidence": 91,
                "source": "https://openai.com/blog",
                "verification_status": "verified",
                "evidence": "Direct announcement on official blog"
            },
            {
                "claim": "Anthropic released Claude 4",
                "confidence": 85,
                "source": "https://anthropic.com/news",
                "verification_status": "verified"
            }
        ]

        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                {
                    "claim": "OpenAI released GPT-5",
                    "confidence": 95,
                    "source": "https://openai.com/blog",
                    "verification_status": "verified"
                },
                {
                    "claim": "Google released Gemini 3",
                    "confidence": 88,
                    "source": "https://blog.google/technology/ai/",
                    "verification_status": "verified"
                }
            ],
            "unverified_claims": [],
            "resolved_questions": [],
            "open_questions": [],
            "completed_topics": [],
            "research_queue": [],
            "contradictions": [],
        }

        # Must not raise TypeError: unhashable type: 'dict'
        try:
            manager.update_state(evaluation, [])
        except TypeError as e:
            raise AssertionError(f"TypeError raised (the original crash): {e}")

        # Verify state is correct
        verified_keys = {RuntimeManager._get_claim_key(c) for c in manager.state["verified_claims"]}
        assert "OpenAI released GPT-5" in verified_keys
        assert "Anthropic released Claude 4" in verified_keys
        assert "Google released Gemini 3" in verified_keys
        assert len(manager.state["verified_claims"]) == 3
        print("    PASSED")


def test_claim_tracking_backward_compatibility():
    """Test backward compatibility with older runtime_state.json formats."""
    print("  - Running test_claim_tracking_backward_compatibility...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config = Config(research_max_iterations=5)
        manager = RuntimeManager(tmp_path, config=config)

        # Simulate loading an old state with all-string lists
        manager.state["verified_claims"] = ["Old Claim 1", "Old Claim 2"]
        manager.state["unverified_claims"] = ["Old Unverified"]
        manager.state["open_questions"] = ["Old Question"]
        manager.state["resolved_questions"] = ["Old Resolved"]
        manager.state["completed_topics"] = ["Old Topic"]
        manager.state["research_queue"] = ["Old Task"]

        # New evaluation with new-format claims
        evaluation = {
            "coverage": 70, "verification": 70, "source_diversity": 70,
            "novel_information": 70, "contradictions_resolved": 70, "overall_quality": 70,
            "verified_claims": [
                "Old Claim 1",  # String (legacy)
                {"claim": "New Claim", "confidence": 90},  # Dict (new)
            ],
            "unverified_claims": ["Old Unverified"],
            "resolved_questions": ["Old Resolved"],
            "open_questions": ["New Question"],
            "completed_topics": ["Old Topic"],
            "research_queue": ["New Task"],
            "contradictions": [],
        }

        # Must handle both formats without error
        manager.update_state(evaluation, [])

        # Verify mixed formats coexist
        verified_keys = {RuntimeManager._get_claim_key(c) for c in manager.state["verified_claims"]}
        assert "Old Claim 1" in verified_keys
        assert "Old Claim 2" in verified_keys
        assert "New Claim" in verified_keys
        print("    PASSED")


def test_helper_methods():
    """Test _get_claim_key and _get_claim_map helper methods."""
    print("  - Running test_helper_methods...")
    # Test _get_claim_key
    assert RuntimeManager._get_claim_key("string claim") == "string claim"
    assert RuntimeManager._get_claim_key({"claim": "dict claim"}) == "dict claim"
    assert RuntimeManager._get_claim_key({"id": "id-based"}) == "id-based"
    assert RuntimeManager._get_claim_key({"text": "text-based"}) == "text-based"
    assert RuntimeManager._get_claim_key({}) == ""  # Empty dict fallback
    assert RuntimeManager._get_claim_key(42) == "42"  # Non-string fallback

    # Test _get_claim_map
    claims = [
        "String Claim",
        {"claim": "Dict Claim A", "confidence": 90},
        {"claim": "Dict Claim B", "confidence": 80},
    ]
    claim_map = RuntimeManager._get_claim_map(claims)
    assert claim_map["String Claim"] == "String Claim"
    assert claim_map["Dict Claim A"]["confidence"] == 90
    assert claim_map["Dict Claim B"]["confidence"] == 80
    assert len(claim_map) == 3

    # Test _get_claim_map with duplicates (last wins)
    dup_claims = [
        {"claim": "Dup", "confidence": 50},
        {"claim": "Dup", "confidence": 90},
    ]
    dup_map = RuntimeManager._get_claim_map(dup_claims)
    assert len(dup_map) == 1
    assert dup_map["Dup"]["confidence"] == 90
    print("    PASSED")


def main():
    print("Running Runtime Manager Tests...")
    test_runtime_manager_state_load_save()
    test_runtime_manager_gating()
    test_runtime_manager_quality_normalization()
    test_runtime_manager_claim_tracking()
    test_runtime_manager_archiving()
    test_guaranteed_completion_on_max_iterations()
    test_orchestrator_integration()
    test_orchestrator_guaranteed_completion_on_max_iterations()
    test_completion_diagnostics_logging()
    test_claim_tracking_string_claims()
    test_claim_tracking_dict_claims()
    test_claim_tracking_mixed_claims()
    test_claim_tracking_duplicate_detection()
    test_claim_tracking_promotion_unverified_to_verified()
    test_claim_tracking_no_typeerror_with_dicts()
    test_claim_tracking_backward_compatibility()
    test_helper_methods()
    print("\nAll Runtime Manager Tests Passed!")


if __name__ == "__main__":
    main()
