"""Master Key authentication for AI Key Pool API.

Validates Bearer token against the configured master key.
"""

from fastapi import Security, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

_master_key: str = ""


def set_master_key(key: str) -> None:
    """Set the master key for authentication."""
    global _master_key
    _master_key = key


def get_master_key() -> str:
    """Get the current master key."""
    return _master_key


async def verify_master_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """FastAPI dependency that verifies the Bearer token.

    Returns:
        The validated master key

    Raises:
        HTTPException: 401 if key is missing or invalid
    """
    if not _master_key:
        raise HTTPException(
            status_code=503,
            detail="Master key not configured",
        )

    if credentials.credentials != _master_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid master key",
        )

    return credentials.credentials
