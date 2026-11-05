"""LLM Provider Manager handling failover, retries, and quota limits."""

import logging
import threading
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool

from paystat.config import Settings, get_settings
from paystat.providers.base import (
    AllProvidersExhaustedError,
    BaseLLMProvider,
)
from paystat.providers.groq_provider import GroqProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Orchestrates free LLM providers with automatic fallback (PRD Section 5)."""

    def __init__(
        self,
        settings: Settings | None = None,
        custom_providers: list[BaseLLMProvider] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._providers: list[BaseLLMProvider] = custom_providers or self._init_providers()
        self._current_index = 0
        self._lock = threading.Lock()

    def _init_providers(self) -> list[BaseLLMProvider]:
        providers: list[BaseLLMProvider] = []
        api_key = self.settings.groq_api_key

        if api_key:
            providers.append(GroqProvider(api_key=api_key, model_name=self.settings.primary_model))

            for fallback_model in self.settings.fallback_models:
                if fallback_model != self.settings.primary_model:
                    providers.append(GroqProvider(api_key=api_key, model_name=fallback_model))

        return providers

    @property
    def current_provider(self) -> BaseLLMProvider:
        with self._lock:
            if not self._providers:
                raise AllProvidersExhaustedError(
                    "Нет настроенных доступных LLM-провайдеров. Проверьте GROQ_API_KEY."
                )
            return self._providers[self._current_index]

    def invoke_with_fallback(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[BaseTool] | None = None,
    ) -> Any:
        """Execute model call, falling back to next provider on rate limit/failure."""
        if not self._providers:
            raise AllProvidersExhaustedError("Список провайдеров пуст.")

        errors: list[str] = []

        with self._lock:
            start_index = self._current_index
            total_providers = len(self._providers)

        for attempt in range(total_providers):
            idx = (start_index + attempt) % total_providers
            provider = self._providers[idx]

            try:
                logger.info(
                    "Вызов LLM через провайдера %s (модель: %s)",
                    provider.name,
                    provider.model_name,
                )
                if tools:
                    runnable = provider.bind_tools(tools)
                    response = runnable.invoke(messages)
                else:
                    chat_model = provider.get_chat_model()
                    response = chat_model.invoke(messages)

                # Update current active provider index in thread-safe manner
                with self._lock:
                    self._current_index = idx
                return response

            except Exception as exc:
                err_msg = f"{provider.name} ({provider.model_name}): {exc}"
                logger.warning(
                    "Ошибка провайдера %s, пробуем следующий. Причина: %s",
                    provider.name,
                    exc,
                )
                errors.append(err_msg)

        all_err_summary = "; ".join(errors)
        raise AllProvidersExhaustedError(
            f"Все настроенные LLM-провайдеры недоступны или исчерпали квоту. Ошибки: {all_err_summary}"
        )
