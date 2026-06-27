"""Base provider interface for AI Key Pool.

All provider adapters must implement this interface.
The key manager never depends on provider-specific code.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ChatMessage:
    """A single message in a chat conversation."""
    role: str        # "system", "user", or "assistant"
    content: str


@dataclass
class ChatResponse:
    """Response from a chat completion."""
    content: str
    model: str
    provider: str
    usage: dict      # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}


class ProviderError(Exception):
    """Error from a provider request.

    Attributes:
        error_type: Normalized error category.
        status_code: HTTP status code if available.
    """
    def __init__(self, message: str, error_type: str = "unknown", status_code: Optional[int] = None):
        self.error_type = error_type
        self.status_code = status_code
        super().__init__(message)


class BaseProvider(ABC):
    """Abstract base for all AI provider adapters.

    Subclasses must implement get_provider_name(), get_endpoint(),
    and get_auth_headers(). The chat() and health_check() methods
    are concrete and use these abstract methods.
    """

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider identifier (e.g., 'github_models', 'groq')."""

    @abstractmethod
    def get_endpoint(self) -> str:
        """Return the chat completions endpoint URL."""

    @abstractmethod
    def get_auth_headers(self, api_key: str) -> dict:
        """Return HTTP headers for authentication.

        Args:
            api_key: The provider API key
        """

    def chat(self, api_key: str, model: str, messages: list[ChatMessage]) -> ChatResponse:
        """Send a chat completion request.

        Args:
            api_key: Provider API key
            model: Model identifier
            messages: List of chat messages

        Returns:
            ChatResponse with the completion

        Raises:
            ProviderError: On HTTP errors or invalid responses
        """
        import httpx

        headers = self.get_auth_headers(api_key)
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(self.get_endpoint(), json=payload, headers=headers)
        except httpx.RequestError as e:
            raise ProviderError(
                f"Request failed: {e}",
                error_type="provider_unavailable",
            ) from e

        if response.status_code != 200:
            error_type = self._classify_http_error(response.status_code, response.text)
            raise ProviderError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                error_type=error_type,
                status_code=response.status_code,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            return ChatResponse(
                content=choice["message"]["content"],
                model=data.get("model", model),
                provider=self.get_provider_name(),
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            )
        except (KeyError, IndexError) as e:
            raise ProviderError(
                f"Invalid response format: {e}",
                error_type="unknown_error",
            ) from e

    def health_check(self, api_key: str, model: Optional[str] = None) -> bool:
        """Check if the API key is valid by sending a minimal request.

        Args:
            api_key: Provider API key to test
            model: Model to use for the check (provider-specific default if None)

        Returns:
            True if the key is valid, False otherwise.
        """
        check_model = model or "gpt-4o-mini"
        try:
            self.chat(api_key, check_model, [ChatMessage(role="user", content="hi")])
            return True
        except ProviderError:
            return False

    def _classify_http_error(self, status_code: int, body: str) -> str:
        """Map HTTP status codes to normalized error types.

        Returns:
            One of: quota_exhausted, rate_limit, auth_error,
            provider_unavailable, invalid_request, unknown_error
        """
        body_lower = body.lower()

        if status_code == 429:
            return "rate_limit"
        if status_code in (402, 403) and ("quota" in body_lower or "exceeded" in body_lower):
            return "quota_exhausted"
        if status_code in (401, 403):
            return "auth_error"
        if status_code >= 500:
            return "provider_unavailable"
        if status_code == 400:
            return "invalid_request"
        return "unknown_error"
