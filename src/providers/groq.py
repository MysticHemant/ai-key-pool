"""Groq provider adapter.

Uses Groq API key for authentication.
Endpoint: https://api.groq.com/openai/v1/chat/completions
"""

from .base_provider import BaseProvider


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
