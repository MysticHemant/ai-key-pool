"""Groq provider adapter.

Uses Groq API key for authentication.
Endpoint: https://api.groq.com/openai/v1/chat/completions
Default model: llama-3.3-70b-versatile
"""

from .base_provider import BaseProvider
from .manifest import ProviderManifest


class GroqProvider(BaseProvider):
    """Groq — fast inference with Groq API key."""

    def get_provider_name(self) -> str:
        return "groq"

    def get_endpoint(self) -> str:
        return "https://api.groq.com/openai/v1/chat/completions"

    def get_auth_headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def get_default_model(self) -> str:
        return "llama-3.3-70b-versatile"

    def get_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="groq",
            display_name="Groq",
            adapter="builtin",
            supported_models=[
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
                "mixtral-8x7b-32768",
                "gemma2-9b-it",
            ],
            capabilities=["fast_inference", "reasoning", "coding"],
            priority=1,
            health="unknown",
            endpoint="https://api.groq.com/openai/v1/chat/completions",
            default_model="llama-3.3-70b-versatile",
        )
