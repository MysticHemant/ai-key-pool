"""FastAPI application factory for AI Key Pool."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .auth import set_master_key
from .routes import init_routes, router
from ..key_pool import KeyManager, KeyRotator
from ..utils.config import load_config
from ..utils.logger import get_logger

logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    config = load_config()

    # Fail fast if the service is not configured correctly.
    if not config.master_key:
        logger.critical(
            "AIKEYPOOL_MASTER_KEY is not configured. "
            "Refusing to start the API."
        )
        raise RuntimeError(
            "Missing required environment variable: AIKEYPOOL_MASTER_KEY"
        )

    set_master_key(config.master_key)

    key_manager = KeyManager(
        config.data_dir,
        config.max_consecutive_failures,
    )
    key_rotator = KeyRotator(config, key_manager)

    init_routes(key_manager, key_rotator, config)

    logger.info(
        "AI Key Pool started — provider=%s, keys=%d",
        config.active_provider,
        len(key_manager.registry.keys),
    )

    yield

    logger.info("AI Key Pool shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AI Key Pool",
        description="Lightweight API key pool with automatic rotation",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(router)

    return app