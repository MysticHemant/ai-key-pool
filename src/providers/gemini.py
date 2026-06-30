"""Gemini provider adapter.

Uses Google AI API key for authentication.
Endpoint: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
Default model: gemini-2.0-flash

Note: Gemini uses a non-OpenAI-compatible API format.
This adapter translates OpenAI-style requests to Gemini format.
"""

import json
import httpx
from .base_provider import BaseProvider, ChatResponse, ProviderError
from .manifest import ProviderManifest


class GeminiProvider(BaseProvider):
    """Google Gemini — reasoning, coding, long context, vision."""

    def get_provider_name(self) -> str:
        return "gemini"

    def get_endpoint(self) -> str:
        return "https://generativelanguage.googleapis.com/v1beta"

    def get_auth_headers(self, api_key: str) -> dict:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

    def get_default_model(self) -> str:
        return "gemini-2.0-flash"

    def chat(self, api_key: str, model: str, messages: list, **kwargs) -> ChatResponse:
        """Send chat request to Gemini API.

        Translates OpenAI-style messages to Gemini format.
        """
        model = model or self.get_default_model()
        endpoint = f"{self.get_endpoint()}/models/{model}:generateContent"

        # Convert OpenAI messages to Gemini format
        gemini_contents = []
        system_instruction = None

        for msg in messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")

            if role == "system":
                system_instruction = content
            elif role == "assistant":
                gemini_contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                gemini_contents.append({"role": "user", "parts": [{"text": content}]})

        payload = {"contents": gemini_contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        headers = self.get_auth_headers(api_key)

        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            error_type = self._classify_http_error(e.response.status_code, e.response.text)
            raise ProviderError(
                f"Gemini API error: {e.response.status_code}",
                error_type=error_type,
                provider=self.get_provider_name(),
            )
        except Exception as e:
            raise ProviderError(
                f"Gemini request failed: {e}",
                error_type="unknown_error",
                provider=self.get_provider_name(),
            )

        # Extract response text
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise ProviderError("No candidates in Gemini response", error_type="unknown_error")
            text = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise ProviderError(
                f"Invalid Gemini response format: {e}",
                error_type="unknown_error",
            )

        return ChatResponse(
            success=True,
            content=text,
            model=model,
            provider=self.get_provider_name(),
        )

    def get_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="gemini",
            display_name="Google Gemini",
            adapter="builtin",
            supported_models=[
                "gemini-2.0-flash",
                "gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ],
            capabilities=["reasoning", "coding", "long_context", "vision"],
            priority=2,
            health="unknown",
            endpoint=self.get_endpoint(),
            default_model="gemini-2.0-flash",
        )
