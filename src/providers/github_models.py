"""GitHub Models provider adapter.

Uses GitHub PAT for authentication.
Endpoint: https://models.github.ai/inference/chat/completions
Default model: gpt-4.1-mini
"""

from .base_provider import BaseProvider
from .manifest import ProviderManifest


class GitHubModelsProvider(BaseProvider):
    """GitHub Models — AI inference via GitHub PAT."""

    def get_provider_name(self) -> str:
        return "github_models"

    def get_endpoint(self) -> str:
        return "https://models.github.ai/inference/chat/completions"

    def get_auth_headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
        }

    def get_default_model(self) -> str:
        return "gpt-4.1-mini"

    def get_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="github_models",
            display_name="GitHub Models",
            adapter="builtin",
            supported_models=[
                "gpt-4.1-mini",
                "gpt-4.1",
                "gpt-4o",
                "gpt-4o-mini",
                "claude-3.5-sonnet",
                "phi-3-medium-128k-instruct",
            ],
            capabilities=["reasoning", "coding"],
            priority=3,
            health="unknown",
            endpoint="https://models.github.ai/inference/chat/completions",
            default_model="gpt-4.1-mini",
        )
