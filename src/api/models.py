"""API request/response models for AI Key Pool."""

from typing import Optional
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request body for POST /chat."""
    model: str
    messages: list[dict]  # [{"role": "user", "content": "..."}]
    provider: Optional[str] = None  # Override provider (uses active if omitted)


class ChatResponse(BaseModel):
    """Response body for POST /chat."""
    success: bool
    content: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    key_id: Optional[str] = None
    rotations: int = 0
    error: Optional[str] = None


class RotateResponse(BaseModel):
    """Response body for POST /rotate."""
    provider: str
    key_id: str
    status: str


class StatusResponse(BaseModel):
    """Response body for POST /status."""
    active_provider: str
    total_keys: int
    healthy_keys: int
    exhausted_keys: int
    disabled_keys: int
    providers: dict


class ConfigResponse(BaseModel):
    """Response body for GET /config."""
    is_valid: bool
    providers_detected: list[str]
    providers_configured: list[str]
    total_secrets_checked: int
    total_secrets_ok: int
    warnings: list[str]
    errors: list[str]
    typo_suggestions: list[dict]


class ProvidersResponse(BaseModel):
    """Response body for GET /providers."""
    providers: list[str]
    provider_status: dict
