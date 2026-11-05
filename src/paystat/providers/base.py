"""Base interface and exceptions for LLM providers."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool


class ProviderError(Exception):
    """General provider failure (rate limit, connection, auth)."""


class ProviderQuotaExceededError(ProviderError):
    """Provider rate limit or free quota reached."""


class AllProvidersExhaustedError(Exception):
    """Raised when all configured free providers fail (PRD Section 5)."""


class BaseLLMProvider(ABC):
    """Abstract wrapper around chat model implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the provider."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        """Return configured LangChain BaseChatModel."""

    @abstractmethod
    def bind_tools(self, tools: Sequence[BaseTool]) -> Any:
        """Bind tools to the model."""
