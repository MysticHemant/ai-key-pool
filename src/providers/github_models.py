"""GitHub Models provider adapter.

Uses GitHub PAT for authentication.
Endpoint: https://models.github.ai/inference/chat/completions
"""

from .base_provider import BaseProvider


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
