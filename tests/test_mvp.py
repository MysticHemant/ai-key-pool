"""Comprehensive tests for AI Key Pool MVP.

Tests the full stack: providers, API routes, maintenance, and dashboard generation.
Uses mocks for HTTP requests — no real API calls.
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.key_pool import KeyManager, KeyRotator
from src.key_pool.key_registry import KeyStatus
from src.utils.config import Config
from src.providers.base_provider import BaseProvider, ChatMessage, ChatResponse, ProviderError
from src.providers.provider_factory import create_provider, list_providers
from src.providers.github_models import GitHubModelsProvider
from src.providers.groq import GroqProvider
from src.providers.openrouter import OpenRouterProvider


DATA_DIR = Path(__file__).parent.parent / "data"


def clean_data():
    """Remove stale test data."""
    for f in ["key_registry.json", "key_health.json"]:
        p = DATA_DIR / f
        if p.exists():
            p.unlink()


# ─── Provider Tests ────────────────────────────────────────

def test_provider_factory():
    """Test provider instantiation by name."""
    print("\n=== Test 1: Provider Factory ===")
    providers = list_providers()
    assert "github_models" in providers
    assert "groq" in providers
    assert "openrouter" in providers

    p = create_provider("groq")
    assert isinstance(p, GroqProvider)
    assert p.get_provider_name() == "groq"

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


def test_provider_endpoint_urls():
    """Test that each provider has correct endpoint."""
    print("\n=== Test 2: Provider Endpoints ===")
    assert "github.ai" in GitHubModelsProvider().get_endpoint()
    assert "groq.com" in GroqProvider().get_endpoint()
    assert "openrouter.ai" in OpenRouterProvider().get_endpoint()
    print("  PASSED")


def test_provider_auth_headers():
    """Test that auth headers are correct."""
    print("\n=== Test 3: Provider Auth Headers ===")
    h = GitHubModelsProvider().get_auth_headers("ghp_test123")
    assert "Bearer ghp_test123" in h["Authorization"]
    assert "X-GitHub-Api-Version" in h

    h = GroqProvider().get_auth_headers("gsk_test")
    assert "Bearer gsk_test" in h["Authorization"]

    h = OpenRouterProvider().get_auth_headers("or_test")
    assert "Bearer or_test" in h["Authorization"]

    h = OpenRouterProvider(site_url="https://example.com", site_name="Test").get_auth_headers("key")
    assert h["HTTP-Referer"] == "https://example.com"
    assert h["X-OpenRouter-Title"] == "Test"

    print("  PASSED")


def test_provider_error_classification():
    """Test HTTP error classification."""
    print("\n=== Test 4: Error Classification ===")
    p = GroqProvider()
    assert p._classify_http_error(429, "rate limited") == "rate_limit"
    assert p._classify_http_error(402, "quota exceeded") == "quota_exhausted"
    assert p._classify_http_error(401, "unauthorized") == "auth_error"
    assert p._classify_http_error(403, "forbidden") == "auth_error"
    assert p._classify_http_error(500, "server error") == "provider_unavailable"
    assert p._classify_http_error(400, "bad request") == "invalid_request"
    assert p._classify_http_error(418, "teapot") == "unknown_error"
    print("  PASSED")


def test_provider_chat_mock():
    """Test chat method with mocked HTTP response."""
    print("\n=== Test 5: Provider Chat (Mocked) ===")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "model": "llama-3.3-70b-versatile",
        "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_http.post.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        provider = GroqProvider()
        response = provider.chat("gsk_test", "llama-3.3-70b-versatile", [ChatMessage(role="user", content="Hi")])

        assert response.content == "Hello!"
        assert response.provider == "groq"
        assert response.usage["total_tokens"] == 15

    print("  PASSED")


def test_provider_chat_error():
    """Test chat method with error response."""
    print("\n=== Test 6: Provider Chat Error ===")
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limit exceeded"

    with patch("httpx.Client") as mock_client_cls:
        mock_http = MagicMock()
        mock_http.post.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        provider = GroqProvider()
        try:
            provider.chat("gsk_test", "llama-3.3-70b-versatile", [ChatMessage(role="user", content="Hi")])
            assert False, "Should raise ProviderError"
        except ProviderError as e:
            assert e.error_type == "rate_limit"
            assert e.status_code == 429

    print("  PASSED")


# ─── Key Rotation Integration Tests ────────────────────────

def test_rotation_with_provider():
    """Test key rotation with a mocked provider."""
    print("\n=== Test 7: Rotation with Provider ===")
    clean_data()
    manager = KeyManager(DATA_DIR)
    manager.register_key("groq-key-1", "groq", "gsk_key1")
    manager.register_key("groq-key-2", "groq", "gsk_key2")

    config = Config(retry_count=2)
    rotator = KeyRotator(config, manager)

    call_count = 0

    def mock_request(api_key):
        nonlocal call_count
        call_count += 1
        if api_key == "gsk_key1":
            raise ProviderError("429 Rate limit", error_type="rate_limited", status_code=429)
        return "success"

    result = rotator.execute_with_rotation("groq", mock_request)
    assert result.success is True
    assert result.key_used == "groq-key-2"
    assert result.rotations >= 1

    print("  PASSED")


# ─── Dashboard Generation Tests ────────────────────────────

def test_dashboard_generation():
    """Test status.json and recommendations.json generation."""
    print("\n=== Test 8: Dashboard Generation ===")
    from src.maintenance.dashboard_gen import generate_status_json, generate_recommendations_json

    clean_data()
    manager = KeyManager(DATA_DIR)
    manager.register_key("test-key-1", "openai", "sk-test-1")
    manager.register_key("test-key-2", "groq", "gsk-test-1")

    config = Config(active_provider="openai")
    output_dir = Path(__file__).parent.parent / "dashboard" / "data"

    generate_status_json(manager, config, output_dir)
    assert (output_dir / "status.json").exists()

    with open(output_dir / "status.json") as f:
        status = json.load(f)
    assert status["total_keys"] == 2
    assert status["active_provider"] == "openai"

    research = {
        "findings": [
            {"name": "TestProvider", "type": "provider", "description": "New provider", "action": "add_key"},
            {"name": "gpt-5", "type": "model", "description": "New model", "action": "monitor"},
        ],
        "summary": "Test research",
    }
    generate_recommendations_json(research, output_dir)
    assert (output_dir / "recommendations.json").exists()

    with open(output_dir / "recommendations.json") as f:
        recs = json.load(f)
    assert len(recs["new_providers"]) == 1
    assert len(recs["new_models"]) == 1

    print("  PASSED")


# ─── Email Generation Tests ────────────────────────────────

def test_email_generation():
    """Test email HTML generation."""
    print("\n=== Test 9: Email Generation ===")
    from src.maintenance.email_sender import _build_html_body

    html = _build_html_body(
        status={"active_provider": "groq", "total_keys": 5, "healthy_keys": 3, "exhausted_keys": 1, "disabled_keys": 1},
        recommendations={"findings": [{"name": "Test", "description": "Desc", "action": "add_key"}], "summary": "Test summary"},
        errors=["Test error"],
    )
    assert "groq" in html
    assert "Test" in html
    assert "Test error" in html
    assert "<html>" in html

    print("  PASSED")


# ─── Auth Tests ────────────────────────────────────────────

def test_master_key_auth():
    """Test Master Key authentication logic."""
    print("\n=== Test 10: Master Key Auth ===")
    from src.api.auth import set_master_key, get_master_key

    set_master_key("test-secret-key")
    assert get_master_key() == "test-secret-key"

    set_master_key("")
    assert get_master_key() == ""

    print("  PASSED")


def main():
    """Run all tests."""
    print("AI Key Pool — MVP Tests\n")

    test_provider_factory()
    test_provider_endpoint_urls()
    test_provider_auth_headers()
    test_provider_error_classification()
    test_provider_chat_mock()
    test_provider_chat_error()
    test_rotation_with_provider()
    test_dashboard_generation()
    test_email_generation()
    test_master_key_auth()

    print("\n=== All Tests Complete ===")
    print("All tests passed!")


if __name__ == "__main__":
    main()
