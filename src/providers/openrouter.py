"""OpenRouter provider adapter.

Uses OpenRouter API key for authentication.
Endpoint: https://openrouter.ai/api/v1/chat/completions
Default model: configurable via AIKEYPOOL_PROVIDER_OPENROUTER_MODEL env var
"""

import os
from typing import Optional
from .base_provider import BaseProvider


class OpenRouterProvider(BaseProvider):
    """OpenRouter — multi-model routing."""

    def __init__(self, site_url: Optional[str] = None, site_name: Optional[str] = None):
        self.site_url = site_url
        self.site_name = site_name

    def get_provider_name(self) -> str:
        return "openrouter"

    def get_endpoint(self) -> str:
        return "https://openrouter.ai/api/v1/chat/completions"

    def get_auth_headers(self, api_key: str) -> dict:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-OpenRouter-Title"] = self.site_name
        return headers

    def get_default_model(self) -> str:
        return os.environ.get("AIKEYPOOL_PROVIDER_OPENROUTER_MODEL", "openrouter/horizon-beta")
