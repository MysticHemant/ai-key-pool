"""API route definitions for AI Key Pool HTTP service."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException

from .auth import verify_master_key
from .models import ChatRequest, ChatResponse, RotateResponse, StatusResponse, ConfigResponse, ProvidersResponse
from ..providers.base_provider import ChatMessage
from ..providers.provider_factory import create_provider, list_providers, get_provider_status
from ..key_pool import KeyManager, KeyRotator, RotationResult
from ..utils.config import Config
from ..utils.config_validator import validate_config
from ..utils.logger import get_logger


logger = get_logger("api")

router = APIRouter()

# These are set by create_app() at startup
_key_manager: KeyManager = None
_key_rotator: KeyRotator = None
_config: Config = None


def init_routes(key_manager: KeyManager, key_rotator: KeyRotator, config: Config) -> None:
    """Initialize route dependencies. Called once at startup."""
    global _key_manager, _key_rotator, _config
    _key_manager = key_manager
    _key_rotator = key_rotator
    _config = config


@router.get("/")
async def root(_: str = Depends(verify_master_key)):
    """Service information."""
    return {
        "service": "ai-key-pool",
        "version": "1.0.0",
        "status": "running",
        "providers": list_providers(),
    }


@router.get("/health")
async def health(_: str = Depends(verify_master_key)):
    """System health check."""
    stats = _key_manager.get_all_stats()
    registry = stats["registry"]
    health = stats["health"]

    active_key = _key_manager.get_active_key(_config.active_provider)

    return {
        "healthy": registry["total_keys"] > 0,
        "active_provider": _config.active_provider,
        "active_key": active_key.key_id if active_key else None,
        "total_keys": registry["total_keys"],
        "healthy_keys": registry["by_status"].get("active", 0),
        "exhausted_keys": registry["by_status"].get("exhausted", 0),
        "disabled_keys": registry["by_status"].get("disabled", 0),
        "health_stats": health,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: str = Depends(verify_master_key)):
    """Send a chat completion request with automatic key rotation.

    The client never sees provider API keys. The service selects
    a healthy key, sends the request, and rotates on failure.
    """
    provider_name = request.provider or _config.active_provider

    # Validate message format
    for i, m in enumerate(request.messages):
        if "role" not in m or "content" not in m:
            raise HTTPException(
                status_code=422,
                detail=f"Message {i} missing required fields 'role' and 'content'",
            )

    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in request.messages]

    try:
        provider = create_provider(provider_name)
    except ValueError as e:
        return ChatResponse(success=False, error=str(e))

    result: RotationResult = _key_rotator.execute_with_rotation(
        provider_name,
        lambda api_key: provider.chat(api_key, request.model, messages),
    )

    if result.success and result.response:
        response = result.response
        return ChatResponse(
            success=True,
            content=response.content,
            model=response.model,
            provider=response.provider,
            key_id=result.key_used,
            rotations=result.rotations,
        )
    else:
        return ChatResponse(
            success=False,
            error=result.error,
            key_id=result.key_used,
            rotations=result.rotations,
        )


@router.post("/rotate", response_model=RotateResponse)
async def rotate(provider: str = None, _: str = Depends(verify_master_key)):
    """Force rotation to the next healthy key."""
    provider_name = provider or _config.active_provider
    active = _key_manager.get_active_key(provider_name)

    if not active:
        return RotateResponse(
            provider=provider_name,
            key_id="",
            status="no healthy keys",
        )

    new_key = _key_rotator.force_rotate(provider_name, active.key_id)
    if new_key:
        return RotateResponse(
            provider=provider_name,
            key_id=new_key.key_id,
            status="rotated",
        )

    return RotateResponse(
        provider=provider_name,
        key_id=active.key_id,
        status="no alternative keys",
    )


@router.post("/status", response_model=StatusResponse)
async def status(_: str = Depends(verify_master_key)):
    """Get current system status."""
    stats = _key_manager.get_all_stats()
    registry = stats["registry"]

    providers = {}
    for provider_name in _key_manager.registry.get_all_providers():
        providers[provider_name] = _key_manager.get_provider_summary(provider_name)

    return StatusResponse(
        active_provider=_config.active_provider,
        total_keys=registry["total_keys"],
        healthy_keys=registry["by_status"].get("active", 0),
        exhausted_keys=registry["by_status"].get("exhausted", 0),
        disabled_keys=registry["by_status"].get("disabled", 0),
        providers=providers,
    )


@router.get("/config")
async def config_health(_: str = Depends(verify_master_key)):
    """Get configuration health report.

    Never exposes secret values. Only shows health status,
    detected providers, and warnings about misconfiguration.
    """
    report = validate_config(_config.data_dir)
    return ConfigResponse(
        is_valid=report.is_valid,
        providers_detected=report.providers_detected,
        providers_configured=report.providers_configured,
        total_secrets_checked=report.total_secrets_checked,
        total_secrets_ok=report.total_secrets_ok,
        warnings=report.warnings,
        errors=report.errors,
        typo_suggestions=report.typo_suggestions,
    )


@router.get("/providers")
async def providers_list(_: str = Depends(verify_master_key)):
    """List all available providers and their adapter status."""
    return ProvidersResponse(
        providers=list_providers(),
        provider_status=get_provider_status(),
    )
