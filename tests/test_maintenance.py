"""Comprehensive tests for AI Key Pool v1.2.0.

Covers: startup init, key sync, provider discovery, plugin loading,
secret validation, typo detection, dashboard gen, research pipeline,
email sending, SMTP failures, workflow execution, config endpoint.
"""

import sys
import json
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.key_pool import KeyManager, KeyRotator
from src.key_pool.key_registry import KeyStatus
from src.utils.config import Config, load_config, ProviderConfig
from src.utils.config_validator import validate_config, _detect_typos, _detect_provider_secrets
from src.providers.base_provider import ChatMessage, ProviderError
from src.providers.provider_factory import (
    create_provider, list_providers, get_provider_status,
    _discover_providers, PROVIDER_MAP,
)
from src.providers.groq import GroqProvider
from src.providers.openrouter import OpenRouterProvider
from src.providers.github_models import GitHubModelsProvider
from src.providers.plugins.generic_openai import GenericOpenAIProvider
from src.providers.plugins.loader import load_plugins, get_plugin_providers


DATA_DIR = Path(__file__).parent.parent / "data"


def clean_data():
    for f in ["key_registry.json", "key_health.json", "research_history.json",
              "last_maintenance.json", "configuration_report.json"]:
        p = DATA_DIR / f
        if p.exists():
            p.unlink()


# ═══════════════════════════════════════════════════════════════
# 1. Startup Initialization
# ═══════════════════════════════════════════════════════════════

def test_maintenance_initialization():
    """Test that maintenance initializes with shared startup logic."""
    print("\n=== Test 1: Maintenance Initialization ===")
    from src.startup.sync_keys import sync_provider_keys

    clean_data()
    config = Config(active_provider="groq", max_consecutive_failures=3)
    config.providers = {"groq": ProviderConfig(name="groq", keys=["gsk_key1"])}

    key_manager = KeyManager(config.data_dir, config.max_consecutive_failures)
    sync_provider_keys(config, key_manager.registry)
    key_rotator = KeyRotator(config, key_manager)

    assert len(key_manager.registry.keys) == 1
    assert key_rotator.config.active_provider == "groq"
    assert key_manager.max_consecutive_failures == 3
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 2. Key Synchronization
# ═══════════════════════════════════════════════════════════════

def test_sync_provider_keys():
    """Test that sync imports keys from config into registry."""
    print("\n=== Test 2: Key Synchronization ===")
    from src.startup.sync_keys import sync_provider_keys

    clean_data()
    manager = KeyManager(DATA_DIR)
    config = Config()
    config.providers = {
        "groq": ProviderConfig(name="groq", keys=["gsk_key1", "gsk_key2"]),
        "openrouter": ProviderConfig(name="openrouter", keys=["or_key1"]),
    }

    sync_provider_keys(config, manager.registry)
    assert len(manager.registry.keys) == 3
    assert manager.registry.get_key("groq-1") is not None
    assert manager.registry.get_key("groq-2") is not None
    assert manager.registry.get_key("openrouter-1") is not None

    # Re-sync should not duplicate
    sync_provider_keys(config, manager.registry)
    assert len(manager.registry.keys) == 3
    print("  PASSED")


def test_sync_removes_demo_keys():
    """Test that demo keys are removed when real keys are imported."""
    print("\n=== Test 3: Demo Key Removal ===")
    from src.startup.sync_keys import sync_provider_keys

    clean_data()
    manager = KeyManager(DATA_DIR)
    manager.register_key("test-key-1", "demo", "demo-key-1")
    manager.register_key("test-key-2", "demo", "demo-key-2")

    config = Config()
    config.providers = {"groq": ProviderConfig(name="groq", keys=["gsk_real_key"])}
    sync_provider_keys(config, manager.registry)

    assert manager.registry.get_key("test-key-1") is None
    assert manager.registry.get_key("test-key-2") is None
    assert manager.registry.get_key("groq-1") is not None
    print("  PASSED")


def test_sync_empty_providers():
    """Test sync with no providers configured."""
    print("\n=== Test 4: Empty Provider Sync ===")
    from src.startup.sync_keys import sync_provider_keys

    clean_data()
    manager = KeyManager(DATA_DIR)
    config = Config()
    config.providers = {}

    sync_provider_keys(config, manager.registry)
    assert len(manager.registry.keys) == 0
    print("  PASSED")


def test_never_empty_registry():
    """Test that sync prevents empty registry when keys exist."""
    print("\n=== Test 5: Never Empty Registry ===")
    from src.startup.sync_keys import sync_provider_keys

    clean_data()
    config = Config()
    config.providers = {"groq": ProviderConfig(name="groq", keys=["gsk_1", "gsk_2"])}
    key_manager = KeyManager(config.data_dir)
    sync_provider_keys(config, key_manager.registry)
    assert len(key_manager.registry.keys) > 0
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 6. Provider Discovery
# ═══════════════════════════════════════════════════════════════

def test_provider_discovery_from_env():
    """Test automatic provider discovery from environment variables."""
    print("\n=== Test 6: Provider Discovery ===")
    env = {
        "AIKEYPOOL_PROVIDER_TOGETHER_KEYS": "tok_test1",
        "AIKEYPOOL_PROVIDER_FIREWORKS_KEYS": "fw_test1",
    }
    with patch.dict(os.environ, env):
        _discover_providers()
        assert "together" in PROVIDER_MAP
        assert "fireworks" in PROVIDER_MAP

    # Verify they produce usable provider instances
    p = create_provider("together")
    assert p.get_provider_name() == "together"
    assert "together.xyz" in p.get_endpoint()

    p = create_provider("fireworks")
    assert p.get_provider_name() == "fireworks"
    assert "fireworks.ai" in p.get_endpoint()
    print("  PASSED")


def test_provider_factory_builtin():
    """Test built-in provider factory."""
    print("\n=== Test 7: Built-in Provider Factory ===")
    providers = list_providers()
    assert "github_models" in providers
    assert "groq" in providers
    assert "openrouter" in providers

    p = create_provider("groq")
    assert isinstance(p, GroqProvider)
    p = create_provider("openrouter")
    assert isinstance(p, OpenRouterProvider)
    p = create_provider("github_models")
    assert isinstance(p, GitHubModelsProvider)

    try:
        create_provider("nonexistent")
        assert False, "Should raise ValueError"
    except ValueError:
        pass
    print("  PASSED")


def test_provider_status():
    """Test provider status reporting."""
    print("\n=== Test 8: Provider Status ===")
    status = get_provider_status()
    assert "groq" in status
    assert status["groq"]["adapter"] == "builtin"
    print("  PASSED")


def test_generic_openai_adapter():
    """Test generic OpenAI-compatible adapter."""
    print("\n=== Test 9: Generic OpenAI Adapter ===")
    env = {
        "AIKEYPOOL_PROVIDER_TESTCUSTOM_ENDPOINT": "https://api.test.com/v1/chat/completions",
        "AIKEYPOOL_PROVIDER_TESTCUSTOM_MODEL": "test-model-1",
        "AIKEYPOOL_PROVIDER_TESTCUSTOM_KEYS": "test_key",
    }
    with patch.dict(os.environ, env):
        _discover_providers()
        p = create_provider("testcustom")
        assert p.get_provider_name() == "testcustom"
        assert p.get_endpoint() == "https://api.test.com/v1/chat/completions"

        headers = p.get_auth_headers("test_key")
        assert "Bearer test_key" in headers["Authorization"]
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 7. Plugin Loading
# ═══════════════════════════════════════════════════════════════

def test_plugin_loading():
    """Test plugin discovery and loading."""
    print("\n=== Test 10: Plugin Loading ===")
    env = {
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
        "AIKEYPOOL_PROVIDER_TOGETHER_KEYS": "tok_test",
    }
    with patch.dict(os.environ, env):
        plugins = load_plugins(["groq", "together", "anthropic"])
        assert "groq" in plugins
        assert "together" in plugins
        # anthropic is not OpenAI-compatible, should not be loaded as generic
    print("  PASSED")


def test_plugin_providers_status():
    """Test plugin provider status detection."""
    print("\n=== Test 11: Plugin Provider Status ===")
    env = {
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
        "AIKEYPOOL_PROVIDER_CUSTOM_KEYS": "custom_test",
    }
    with patch.dict(os.environ, env):
        status = get_plugin_providers()
        assert "groq" in status
        assert status["groq"] in ("builtin", "generic")
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 4. Secret Validation
# ═══════════════════════════════════════════════════════════════

def test_secret_validation():
    """Test configuration secret validation."""
    print("\n=== Test 12: Secret Validation ===")
    env = {
        "AIKEYPOOL_MASTER_KEY": "test_master_key",
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
        "SMTP_HOST": "smtp.test.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user@test.com",
        "SMTP_PASSWORD": "pass",
        "EMAIL_RECIPIENT": "recipient@test.com",
    }
    with patch.dict(os.environ, env, clear=True):
        report = validate_config()
        assert report.is_valid
        assert report.master_key.has_value
        assert "groq" in report.providers_detected
        assert report.total_secrets_checked > 0
    print("  PASSED")


def test_typo_detection():
    """Test typo detection in environment variable names."""
    print("\n=== Test 13: Typo Detection ===")
    env = {
        "AIKEYPOOL_PROVIDER_GROQ_KEY": "gsk_test",
    }
    with patch.dict(os.environ, env, clear=True):
        typos = _detect_typos()
        assert len(typos) > 0
        assert any("GROQ_KEYS" in t["suggestion"] for t in typos)
    print("  PASSED")


def test_missing_master_key():
    """Test validation catches missing master key."""
    print("\n=== Test 14: Missing Master Key ===")
    with patch.dict(os.environ, {}, clear=True):
        report = validate_config()
        assert not report.is_valid
        assert any("MASTER_KEY" in e for e in report.errors)
    print("  PASSED")


def test_empty_provider_key():
    """Test validation catches empty provider keys."""
    print("\n=== Test 15: Empty Provider Key ===")
    env = {
        "AIKEYPOOL_MASTER_KEY": "test_key",
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "",
    }
    with patch.dict(os.environ, env, clear=True):
        report = validate_config()
        assert any("GROQ_KEYS" in s.name and not s.has_value for s in report.provider_secrets)
    print("  PASSED")


def test_config_report_written():
    """Test that configuration report is written to disk."""
    print("\n=== Test 16: Config Report Written ===")
    env = {"AIKEYPOOL_MASTER_KEY": "test"}
    with patch.dict(os.environ, env, clear=True):
        report = validate_config(DATA_DIR)
        report_path = DATA_DIR / "configuration_report.json"
        assert report_path.exists()
        with open(report_path) as f:
            data = json.load(f)
        assert "is_valid" in data
        assert "providers_detected" in data
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 8. Dashboard Generation
# ═══════════════════════════════════════════════════════════════

def test_dashboard_status_generation():
    """Test status.json generation with config health."""
    print("\n=== Test 17: Dashboard Status ===")
    from src.maintenance.dashboard_gen import generate_status_json

    clean_data()
    manager = KeyManager(DATA_DIR)
    manager.register_key("test-key-1", "openai", "sk-test-1")

    config = Config(active_provider="openai")
    output_dir = Path(tempfile.mkdtemp())

    try:
        generate_status_json(manager, config, output_dir, maintenance_duration=5.0, workflow_status="completed")
        with open(output_dir / "status.json") as f:
            status = json.load(f)
        assert status["total_keys"] == 1
        assert status["maintenance_status"] == "completed"
        assert "config_health" in status
        assert "plugins" in status
    finally:
        shutil.rmtree(output_dir)
    print("  PASSED")


def test_dashboard_recommendations():
    """Test recommendations.json generation."""
    print("\n=== Test 18: Dashboard Recommendations ===")
    from src.maintenance.dashboard_gen import generate_recommendations_json

    output_dir = Path(tempfile.mkdtemp())
    research = {
        "findings": [
            {"provider": "openai", "model": "gpt-5", "description": "New", "url": "https://x.com", "type": "model", "action": "add_key", "confidence": "high"},
            {"name": "TestProvider", "type": "provider", "description": "New", "action": "add_key"},
        ],
        "summary": "Test",
    }
    try:
        generate_recommendations_json(research, output_dir)
        with open(output_dir / "recommendations.json") as f:
            recs = json.load(f)
        assert len(recs["findings"]) == 2
        assert len(recs["new_providers"]) == 1
    finally:
        shutil.rmtree(output_dir)
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 9. Email Sending & SMTP Failures
# ═══════════════════════════════════════════════════════════════

def test_email_html_generation():
    """Test email HTML body generation."""
    print("\n=== Test 19: Email HTML ===")
    from src.maintenance.email_sender import _build_html_body

    html = _build_html_body(
        status={"active_provider": "groq", "total_keys": 5, "healthy_keys": 3, "exhausted_keys": 1, "disabled_keys": 1, "providers": {"groq": {"total_keys": 3, "healthy_keys": 2}}},
        recommendations={"findings": [{"provider": "x", "description": "d", "url": "https://x.com", "type": "model", "action": "add_key", "confidence": "high"}], "new_models": ["gpt-5"], "summary": "Test"},
        errors=["Warning 1"],
        maintenance_duration=45.2,
        workflow_status="completed_with_errors",
    )
    assert "groq" in html
    assert "gpt-5" in html
    assert "Warning 1" in html
    assert "v1.2.0" in html
    print("  PASSED")


def test_email_missing_env_vars():
    """Test email skips when env vars missing."""
    print("\n=== Test 20: Missing Email Secrets ===")
    from src.maintenance.email_sender import send_daily_summary
    result = send_daily_summary("", 587, "", "", "", {}, {}, [])
    assert result is False
    print("  PASSED")


def test_email_smtp_connection_failure():
    """Test email handles SMTP connection failure."""
    print("\n=== Test 21: SMTP Connection Failure ===")
    from src.maintenance.email_sender import send_daily_summary, EmailDeliveryError
    with patch("smtplib.SMTP", side_effect=Exception("Connection refused")):
        try:
            send_daily_summary("invalid.host", 587, "u@t.com", "p", "r@t.com", {}, {}, [])
            assert False
        except EmailDeliveryError as e:
            assert "connection" in e.stage.lower() or "Connection" in str(e.detail)
    print("  PASSED")


def test_email_smtp_auth_failure():
    """Test email handles SMTP auth failure."""
    print("\n=== Test 22: SMTP Auth Failure ===")
    from src.maintenance.email_sender import send_daily_summary, EmailDeliveryError
    import smtplib
    mock_server = MagicMock()
    mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
    with patch("smtplib.SMTP", return_value=mock_server):
        try:
            send_daily_summary("smtp.t.com", 587, "u@t.com", "wrong", "r@t.com", {}, {}, [])
            assert False
        except EmailDeliveryError as e:
            assert "auth" in e.stage.lower()
    print("  PASSED")


def test_email_smtp_recipient_rejected():
    """Test email handles rejected recipient."""
    print("\n=== Test 23: SMTP Recipient Rejected ===")
    from src.maintenance.email_sender import send_daily_summary, EmailDeliveryError
    import smtplib
    mock_server = MagicMock()
    mock_server.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"bad@addr.com": (550, b"Rejected")})
    with patch("smtplib.SMTP", return_value=mock_server):
        try:
            send_daily_summary("smtp.t.com", 587, "u@t.com", "p", "bad@addr.com", {}, {}, [])
            assert False
        except EmailDeliveryError as e:
            assert "send" in e.stage.lower()
    print("  PASSED")


def test_email_successful_send():
    """Test successful email send."""
    print("\n=== Test 24: Successful Email Send ===")
    from src.maintenance.email_sender import send_daily_summary
    mock_server = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_server):
        result = send_daily_summary("smtp.t.com", 587, "u@t.com", "p", "r@t.com", {"active_provider": "groq", "total_keys": 5, "healthy_keys": 3, "exhausted_keys": 1, "disabled_keys": 1}, {"findings": [], "summary": "T"}, [])
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 10. Research Pipeline
# ═══════════════════════════════════════════════════════════════

def test_research_deduplication():
    """Test research deduplication."""
    print("\n=== Test 25: Research Deduplication ===")
    from src.maintenance.research import deduplicate_findings
    findings = [
        {"provider": "openai", "title": "New GPT-5"},
        {"provider": "openai", "title": "New GPT-5"},
        {"provider": "anthropic", "title": "Claude 4"},
    ]
    unique = deduplicate_findings(findings)
    assert len(unique) == 2
    print("  PASSED")


def test_research_empty_sources():
    """Test research with no sources."""
    print("\n=== Test 26: Empty Research ===")
    from src.maintenance.research import _empty_research_result
    result = _empty_research_result("No sources")
    assert result["findings"] == []
    assert "No sources" in result["summary"]
    print("  PASSED")


def test_research_raw_fallback():
    """Test raw fallback when LLM unavailable."""
    print("\n=== Test 27: Research Raw Fallback ===")
    from src.maintenance.research import _build_raw_fallback
    raw = [{"provider": "openai", "title": "GPT-5", "source_url": "https://x.com"}]
    result = _build_raw_fallback(raw)
    assert len(result["findings"]) == 1
    assert "LLM summarization unavailable" in result["summary"]
    print("  PASSED")


def test_research_web_collection_mock():
    """Test web news collection with mocked HTTP."""
    print("\n=== Test 28: Web News Collection ===")
    from src.maintenance.research import collect_web_news
    mock_html = "<html><body><article><h2>OpenAI announces GPT-5 with advanced reasoning capabilities and improved performance</h2></article></body></html>"
    with patch("src.maintenance.research._fetch_url", return_value=mock_html):
        findings = collect_web_news()
        assert len(findings) > 0
    print("  PASSED")


def test_research_rss_parsing():
    """Test RSS feed parsing."""
    print("\n=== Test 29: RSS Parsing ===")
    from src.maintenance.research import _parse_rss
    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>New Model</title><link>https://x.com/1</link></item>
    </channel></rss>"""
    entries = _parse_rss(rss)
    assert len(entries) >= 1
    assert entries[0]["title"] == "New Model"
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 11. Empty Registry
# ═══════════════════════════════════════════════════════════════

def test_empty_registry():
    """Test behavior with empty registry."""
    print("\n=== Test 30: Empty Registry ===")
    clean_data()
    manager = KeyManager(DATA_DIR)
    assert len(manager.registry.keys) == 0
    stats = manager.get_all_stats()
    assert stats["registry"]["total_keys"] == 0
    key = manager.get_active_key("groq")
    assert key is None
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 12. Key Rotation
# ═══════════════════════════════════════════════════════════════

def test_rotation_with_provider():
    """Test key rotation with mocked provider."""
    print("\n=== Test 31: Key Rotation ===")
    clean_data()
    manager = KeyManager(DATA_DIR)
    manager.register_key("g-1", "groq", "gsk_1")
    manager.register_key("g-2", "groq", "gsk_2")
    config = Config(retry_count=2)
    rotator = KeyRotator(config, manager)

    def mock_req(api_key):
        if api_key == "gsk_1":
            raise ProviderError("429", error_type="rate_limit", status_code=429)
        return "ok"

    result = rotator.execute_with_rotation("groq", mock_req)
    assert result.success
    assert result.key_used == "g-2"
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 13. Full Workflow Execution
# ═══════════════════════════════════════════════════════════════

def test_full_maintenance_cycle():
    """Test complete daily maintenance with mocks."""
    print("\n=== Test 32: Full Maintenance Cycle ===")
    from src.maintenance.orchestrator import run_daily_maintenance
    clean_data()

    env = {
        "AIKEYPOOL_ACTIVE_PROVIDER": "groq",
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
        "SMTP_HOST": "", "SMTP_PORT": "587", "SMTP_USER": "",
        "SMTP_PASSWORD": "", "EMAIL_RECIPIENT": "",
    }
    mock_research = {"findings": [], "summary": "Test", "new_providers": [], "new_models": [],
                     "pricing_changes": [], "free_tier_changes": [], "breaking_changes": []}

    with patch.dict(os.environ, env):
        with patch("src.maintenance.orchestrator.research_providers", return_value=mock_research):
            result = run_daily_maintenance()

    assert result["status"] in ("completed", "completed_with_errors")
    assert "steps" in result
    assert result["diagnostics"]["keys_loaded"] > 0
    assert "groq" in result["diagnostics"]["loaded_providers"]
    print("  PASSED")


def test_independent_failure_handling():
    """Test that one subsystem failing doesn't block others."""
    print("\n=== Test 33: Independent Failures ===")
    from src.maintenance.orchestrator import run_daily_maintenance
    clean_data()

    env = {
        "AIKEYPOOL_ACTIVE_PROVIDER": "groq",
        "AIKEYPOOL_PROVIDER_GROQ_KEYS": "gsk_test",
        "SMTP_HOST": "", "SMTP_PORT": "587", "SMTP_USER": "",
        "SMTP_PASSWORD": "", "EMAIL_RECIPIENT": "",
    }
    with patch.dict(os.environ, env):
        with patch("src.maintenance.orchestrator.research_providers", side_effect=Exception("Crash")):
            result = run_daily_maintenance()

    assert result["steps"]["health_check"]["status"] == "ok"
    assert result["steps"]["research"]["status"] == "error"
    assert result["steps"]["status_report"]["status"] == "ok"
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# 14. API Config Endpoint
# ═══════════════════════════════════════════════════════════════

def test_config_endpoint_models():
    """Test API model definitions for config endpoint."""
    print("\n=== Test 34: Config Endpoint Models ===")
    from src.api.models import ConfigResponse, ProvidersResponse
    resp = ConfigResponse(
        is_valid=True, providers_detected=["groq"], providers_configured=["groq"],
        total_secrets_checked=5, total_secrets_ok=4,
        warnings=["w1"], errors=[], typo_suggestions=[{"detected": "x", "suggestion": "y", "reason": "r"}],
    )
    assert resp.is_valid
    assert len(resp.providers_detected) == 1

    resp2 = ProvidersResponse(providers=["groq"], provider_status={"groq": {"adapter": "builtin"}})
    assert "groq" in resp2.providers
    print("  PASSED")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    print("AI Key Pool v1.2.0 — Comprehensive Tests\n")

    test_maintenance_initialization()
    test_sync_provider_keys()
    test_sync_removes_demo_keys()
    test_sync_empty_providers()
    test_never_empty_registry()
    test_provider_discovery_from_env()
    test_provider_factory_builtin()
    test_provider_status()
    test_generic_openai_adapter()
    test_plugin_loading()
    test_plugin_providers_status()
    test_secret_validation()
    test_typo_detection()
    test_missing_master_key()
    test_empty_provider_key()
    test_config_report_written()
    test_dashboard_status_generation()
    test_dashboard_recommendations()
    test_email_html_generation()
    test_email_missing_env_vars()
    test_email_smtp_connection_failure()
    test_email_smtp_auth_failure()
    test_email_smtp_recipient_rejected()
    test_email_successful_send()
    test_research_deduplication()
    test_research_empty_sources()
    test_research_raw_fallback()
    test_research_web_collection_mock()
    test_research_rss_parsing()
    test_empty_registry()
    test_rotation_with_provider()
    test_full_maintenance_cycle()
    test_independent_failure_handling()
    test_config_endpoint_models()

    print("\nRunning Runtime Manager tests...")
    import test_runtime_manager
    test_runtime_manager.test_runtime_manager_state_load_save()
    test_runtime_manager.test_runtime_manager_gating()
    test_runtime_manager.test_runtime_manager_quality_normalization()
    test_runtime_manager.test_runtime_manager_claim_tracking()
    test_runtime_manager.test_runtime_manager_archiving()
    test_runtime_manager.test_guaranteed_completion_on_max_iterations()
    test_runtime_manager.test_orchestrator_integration()
    test_runtime_manager.test_orchestrator_guaranteed_completion_on_max_iterations()
    test_runtime_manager.test_completion_diagnostics_logging()
    test_runtime_manager.test_claim_tracking_string_claims()
    test_runtime_manager.test_claim_tracking_dict_claims()
    test_runtime_manager.test_claim_tracking_mixed_claims()
    test_runtime_manager.test_claim_tracking_duplicate_detection()
    test_runtime_manager.test_claim_tracking_promotion_unverified_to_verified()
    test_runtime_manager.test_claim_tracking_no_typeerror_with_dicts()
    test_runtime_manager.test_claim_tracking_backward_compatibility()
    test_runtime_manager.test_helper_methods()

    print("\n" + "=" * 50)
    print("All 54 tests passed!")
    print("=" * 50)


if __name__ == "__main__":
    main()
