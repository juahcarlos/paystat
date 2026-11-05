"""FastAPI routes definition."""

import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from paystat.agent.analyst import AnalyticalAgent
from paystat.config import Settings, get_settings
from paystat.database.connection import ClickHouseManager
from paystat.providers.base import AllProvidersExhaustedError
from paystat.providers.manager import ProviderManager
from paystat.schemas.api import AnalyzeRequest, AnalyzeResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_provider_manager: ProviderManager | None = None
_manager_lock = threading.Lock()


def get_shared_provider_manager() -> ProviderManager:
    """Return thread-safe singleton ProviderManager to preserve active provider index."""
    global _provider_manager
    if _provider_manager is None:
        with _manager_lock:
            if _provider_manager is None:
                _provider_manager = ProviderManager(get_settings())
    return _provider_manager


def get_agent(settings: Annotated[Settings, Depends(get_settings)]) -> AnalyticalAgent:
    """Dependency provider for AnalyticalAgent."""
    db_mgr = ClickHouseManager(settings)
    provider_mgr = get_shared_provider_manager()
    return AnalyticalAgent(db_manager=db_mgr, provider_manager=provider_mgr, settings=settings)


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Анализ платёжных данных через AI-агента",
    description="Принимает вопрос на естественном языке, автономно исследует схему ClickHouse и возвращает структурированный аналитический ответ.",
)
def analyze_payments(
    request: AnalyzeRequest,
    agent: Annotated[AnalyticalAgent, Depends(get_agent)],
) -> AnalyzeResponse:
    """Execute analytical investigation synchronously in threadpool to prevent blocking the event loop."""
    try:
        response = agent.analyze(request.question)
        return response
    except AllProvidersExhaustedError as exc:
        logger.error("Все LLM провайдеры исчерпали квоту или недоступны: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервис временно недоступен: внешние AI-провайдеры исчерпали лимит запросов.",
        ) from exc
    except Exception as exc:
        logger.error("Внутренняя ошибка при анализе: %s", exc, exc_info=True)
        # PRD Section 6: Не раскрывать ключи и внутренние ошибки клиенту
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Произошла непредвиденная ошибка при выполнении анализа.",
        ) from exc


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка работоспособности сервиса",
)
def healthcheck(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Healthcheck verifying database connectivity."""
    db_mgr = ClickHouseManager(settings)
    is_connected = db_mgr.check_health()
    return HealthResponse(
        status="ok" if is_connected else "degraded",
        database_connected=is_connected,
        version="0.1.0",
    )
