"""Tests for the History Tracker module."""

import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.maintenance.history_tracker import HistoryTracker


def test_history_tracker_init_creates_empty():
    """Test HistoryTracker initializes with empty history."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        h = tracker.history
        assert "known_providers" in h
        assert "model_history" in h
        assert "rate_limit_changes" in h
        assert "free_tier_changes" in h
        assert "outages" in h
        assert "discoveries" in h
        assert len(h["known_providers"]) == 0
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_loads_existing():
    """Test HistoryTracker loads existing history from disk."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Write a history file
        existing = {
            "last_report_date": "2026-01-01",
            "known_providers": {"groq": {"first_seen": "2026-01-01", "status": "healthy"}},
            "model_history": {},
            "rate_limit_changes": [],
            "free_tier_changes": [],
            "outages": [],
            "discoveries": [],
        }
        with open(tmpdir / "intelligence_history.json", "w") as f:
            json.dump(existing, f)
        tracker = HistoryTracker(tmpdir)
        assert tracker.history["last_report_date"] == "2026-01-01"
        assert "groq" in tracker.history["known_providers"]
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_save_and_reload():
    """Test HistoryTracker saves and reloads from disk."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_provider("test_provider", status="healthy", models=["model-1"])
        tracker.save_history()
        # Reload
        tracker2 = HistoryTracker(tmpdir)
        assert "test_provider" in tracker2.history["known_providers"]
        assert tracker2.history["known_providers"]["test_provider"]["status"] == "healthy"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_update_provider():
    """Test update_provider creates and updates provider entries."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_provider("groq", status="healthy", models=["llama-3.3-70b"])
        p = tracker.history["known_providers"]["groq"]
        assert p["status"] == "healthy"
        assert "llama-3.3-70b" in p["models"]
        assert p["first_seen"] != ""
        assert p["last_active"] != ""
        # Update again
        tracker.update_provider("groq", status="degraded")
        assert tracker.history["known_providers"]["groq"]["status"] == "degraded"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_update_model():
    """Test update_model tracks model history."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_model("llama-3.3-70b", provider="groq", status="active")
        m = tracker.history["model_history"]["llama-3.3-70b"]
        assert m["provider"] == "groq"
        assert m["status"] == "active"
        assert m["first_seen"] != ""
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_record_rate_limit_change():
    """Test record_rate_limit_change tracks changes."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.record_rate_limit_change("groq", "increased", "100 rpm", "200 rpm")
        assert len(tracker.history["rate_limit_changes"]) == 1
        change = tracker.history["rate_limit_changes"][0]
        assert change["provider"] == "groq"
        assert change["old_value"] == "100 rpm"
        assert change["new_value"] == "200 rpm"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_record_free_tier_change():
    """Test record_free_tier_change tracks changes."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.record_free_tier_change("together", "new free tier added")
        assert len(tracker.history["free_tier_changes"]) == 1
        assert tracker.history["free_tier_changes"][0]["provider"] == "together"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_record_outage():
    """Test record_outage tracks outages."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.record_outage("groq", reason="API downtime", start="2026-01-01T00:00:00Z")
        assert len(tracker.history["outages"]) == 1
        outage = tracker.history["outages"][0]
        assert outage["provider"] == "groq"
        assert outage["reason"] == "API downtime"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_record_discovery():
    """Test record_discovery tracks discoveries."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.record_discovery("new_provider", source="cool-ai-stuff", details={"url": "https://example.com"})
        assert len(tracker.history["discoveries"]) == 1
        disc = tracker.history["discoveries"][0]
        assert disc["provider"] == "new_provider"
        assert disc["source"] == "cool-ai-stuff"
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_get_changes_since():
    """Test get_changes_since returns changes after a date."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_provider("groq", status="healthy")
        tracker.update_model("model-1", provider="groq")
        # Get changes since before now - should include everything
        changes = tracker.get_changes_since("2000-01-01")
        assert len(changes["new_providers"]) > 0
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_get_changes_since_last_report():
    """Test get_changes_since_last_report."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        # First report
        changes = tracker.get_changes_since_last_report()
        assert changes.get("is_first_report") is True
        # Mark report generated
        tracker.mark_report_generated()
        # Make some changes
        tracker.update_provider("new_provider", status="healthy")
        # Get changes since last report - returns dict without is_first_report
        changes2 = tracker.get_changes_since_last_report()
        # When not first report, it returns changes since last_report_date
        assert "new_providers" in changes2 or "is_first_report" not in changes2
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_mark_report_generated():
    """Test mark_report_generated updates last_report_date."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        assert tracker.history["last_report_date"] == ""
        tracker.mark_report_generated()
        assert tracker.history["last_report_date"] != ""
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_format_changes():
    """Test format_changes_since_last_report produces readable text."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_provider("groq", status="healthy")
        tracker.record_rate_limit_change("groq", "increased", "100 rpm", "200 rpm")
        text = tracker.format_changes_since_last_report()
        assert isinstance(text, str)
        assert len(text) > 0
    finally:
        shutil.rmtree(tmpdir)


def test_history_tracker_get_provider_summary():
    """Test get_provider_summary returns provider info."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        tracker = HistoryTracker(tmpdir)
        tracker.update_provider("groq", status="healthy", models=["llama-3.3-70b"])
        tracker.update_provider("openrouter", status="degraded", models=["gpt-4o"])
        summary = tracker.get_provider_summary()
        assert summary["total_known"] == 2
        assert "groq" in summary["providers"]
        assert summary["providers"]["groq"]["status"] == "healthy"
    finally:
        shutil.rmtree(tmpdir)


def run_all():
    """Run all history tracker tests."""
    tests = [
        test_history_tracker_init_creates_empty,
        test_history_tracker_loads_existing,
        test_history_tracker_save_and_reload,
        test_history_tracker_update_provider,
        test_history_tracker_update_model,
        test_history_tracker_record_rate_limit_change,
        test_history_tracker_record_free_tier_change,
        test_history_tracker_record_outage,
        test_history_tracker_record_discovery,
        test_history_tracker_get_changes_since,
        test_history_tracker_get_changes_since_last_report,
        test_history_tracker_mark_report_generated,
        test_history_tracker_format_changes,
        test_history_tracker_get_provider_summary,
    ]
    for test in tests:
        test()
    return len(tests)


if __name__ == "__main__":
    n = run_all()
    print(f"All {n} history tracker tests passed!")
