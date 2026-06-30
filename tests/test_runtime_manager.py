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
        manager = RuntimeManager(tmp_path, max_iterations=5)

        # Verify initial state structure
        state = manager.state
        assert state["iteration"] == 1
        assert state["max_iterations"] == 5
        assert state["status"] == "researching"
        assert state["quality_score"] == 0
        assert len(state["cycle_id"]) > 0

        # Modify state and save
        manager.state["quality_score"] = 80
        manager.state["assumptions"] = ["Test assumption"]
        manager.save_state()

        # Reload state in a new manager instance
        manager2 = RuntimeManager(tmp_path, max_iterations=5)
        assert manager2.state["quality_score"] == 80
        assert manager2.state["assumptions"] == ["Test assumption"]
        assert manager2.state["cycle_id"] == state["cycle_id"]

        # Reset state
        manager2.reset_state()
        assert manager2.state["iteration"] == 1
        assert manager2.state["quality_score"] == 0
        assert manager2.state["cycle_id"] != state["cycle_id"]
    print("    PASSED")


def test_runtime_manager_gating():
    """Test the should_send_email condition gating."""
    print("  - Running test_runtime_manager_gating...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        manager = RuntimeManager(tmp_path, max_iterations=4)

        # 1. Quality < 90, Iteration < max_iterations -> No email
        assert not manager.should_send_email()

        # 2. Quality >= 90 -> Email
        manager.state["quality_score"] = 92
        assert manager.should_send_email()

        # 3. Quality < 90, Iteration >= max_iterations -> Email
        manager.state["quality_score"] = 50
        manager.state["iteration"] = 4
        assert manager.should_send_email()
    print("    PASSED")


def test_runtime_manager_archiving():
    """Test state archiving and iteration file cleanup."""
    print("  - Running test_runtime_manager_archiving...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        manager = RuntimeManager(tmp_path, max_iterations=5)
        cycle_id = manager.state["cycle_id"]

        # Create dummy iteration files
        research_dir = tmp_path / "research"
        research_dir.mkdir()
        iter1 = research_dir / "iteration_1.md"
        iter1.write_text("Iteration 1 content", encoding="utf-8")
        iter2 = research_dir / "iteration_2.md"
        iter2.write_text("Iteration 2 content", encoding="utf-8")

        # Update state
        manager.update_state({"quality_score": 95}, [])

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


def test_orchestrator_integration():
    """Test that orchestrator increments iterations and skips email or consolidates and sends."""
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
        }

        # Mock LLM response that fails to hit quality target
        mock_low_quality = {
            "findings": [],
            "summary": "Low quality summary",
            "iteration_report": {"summary": "Low summary", "evidence": "None"},
            "evaluation": {"quality_score": 75, "coverage_score": 60, "confidence_score": 70}
        }

        with patch.dict("os.environ", env):
            # Run 1: Should perform research, save iteration 1, update state, skip email, increment to iteration 2
            with patch("src.maintenance.orchestrator.research_providers", return_value=mock_low_quality):
                with patch("src.maintenance.orchestrator._do_send_email") as mock_send:
                    result = run_daily_maintenance()
                    assert result["steps"]["research"]["status"] == "ok"
                    assert result["steps"]["email"]["status"] == "skipped"
                    mock_send.assert_not_called()

            # Verify iteration file saved & iteration incremented to 2
            assert (tmp_path / "research" / "iteration_1.md").exists()
            with open(tmp_path / "research_runtime.json") as f:
                state = json.load(f)
            assert state["iteration"] == 2
            assert state["quality_score"] == 75

            # Mock LLM response that hits quality target
            mock_high_quality = {
                "findings": [{"provider": "groq", "type": "model", "action": "update"}],
                "summary": "High quality summary",
                "iteration_report": {"summary": "High summary", "evidence": "Got it"},
                "evaluation": {"quality_score": 95, "coverage_score": 90, "confidence_score": 90}
            }

            # Run 2: Should perform research, save iteration 2, meet quality, consolidate report, send email, archive cycle
            with patch("src.maintenance.orchestrator.research_providers", return_value=mock_high_quality):
                with patch("src.maintenance.orchestrator.generate_final_report", return_value=mock_high_quality) as mock_gen_report:
                    with patch("src.maintenance.orchestrator._do_send_email", return_value=True) as mock_send:
                        result = run_daily_maintenance()
                        assert result["steps"]["research"]["status"] == "ok"
                        assert result["steps"]["email"]["status"] == "sent"
                        mock_gen_report.assert_called_once()
                        mock_send.assert_called_once()

            # Verify active iteration files deleted and cycle archived
            assert not (tmp_path / "research" / "iteration_1.md").exists()
            assert not (tmp_path / "research" / "iteration_2.md").exists()
            
            # Verify new cycle restarted (iteration reset to 1)
            with open(tmp_path / "research_runtime.json") as f:
                state = json.load(f)
            assert state["iteration"] == 1
            assert state["quality_score"] == 0

    print("    PASSED")


def main():
    print("Running Runtime Manager Tests...")
    test_runtime_manager_state_load_save()
    test_runtime_manager_gating()
    test_runtime_manager_archiving()
    test_orchestrator_integration()
    print("\nAll Runtime Manager Tests Passed!")


if __name__ == "__main__":
    main()
