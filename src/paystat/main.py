"""Main FastAPI application entrypoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from paystat.api.routes import router
from paystat.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("paystat")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan event handler for service initialization and cleanup."""
    settings = get_settings()
    logger.info("Запуск сервиса %s в режиме %s", settings.app_name, settings.app_env)
    yield
    logger.info("Остановка сервиса %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="PayStat AI Analyst API",
        description=(
            "Аналитический сервис на основе AI-агента для исследования "
            "платежных данных в ClickHouse и принятия управленческих решений."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # =========================================================================
    # CORS Configuration (PRD Section 6)
    #
    # Front-end preparation comment:
    # A dedicated single-page application (React / Vite dashboard) will consume
    # this API for live financial metrics and interactive agent chats.
    # We restrict allowed origins to trusted developer and production hosts
    # while allowing credentialed HTTP requests and standard REST methods.
    # =========================================================================
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Include API routes
    app.include_router(router)

    # Safe Global Exception Handler (PRD Section 6: Не раскрывать ключи и внутренние ошибки клиенту)
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Необработанное исключение: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": "Внутренняя ошибка сервиса. Подробности записаны в системный журнал.",
            },
        )

    return app


app = create_app()
