"""Tests for LLM provider failover and rate limit handling."""

import pytest
from langchain_core.messages import HumanMessage

from paystat.config import Settings
from paystat.providers.base import AllProvidersExhaustedError
from paystat.providers.manager import ProviderManager
from tests.conftest import MockLLMProvider


def test_primary_provider_success() -> None:
    """Primary provider is called and succeeds."""
    p1 = MockLLMProvider(name="GroqPrimary", model_name="gpt-oss-120b")
    p2 = MockLLMProvider(name="GroqFallback", model_name="llama-3.3-70b")

    manager = ProviderManager(
        settings=Settings(groq_api_key="test"),
        custom_providers=[p1, p2],
    )

    msg = [HumanMessage(content="Hello")]
    res = manager.invoke_with_fallback(msg)
    assert res is not None
    assert manager.current_provider.name == "GroqPrimary"


def test_fallback_on_primary_failure() -> None:
    """When primary provider fails, fallback provider is automatically invoked."""
    p1 = MockLLMProvider(name="GroqPrimary", model_name="gpt-oss-120b", fail=True)
    p2 = MockLLMProvider(name="GroqFallback", model_name="llama-3.3-70b", fail=False)

    manager = ProviderManager(
        settings=Settings(groq_api_key="test"),
        custom_providers=[p1, p2],
    )

    msg = [HumanMessage(content="Hello")]
    res = manager.invoke_with_fallback(msg)
    assert res is not None
    assert manager.current_provider.name == "GroqFallback"


def test_all_providers_exhausted_raises_exception() -> None:
    """When all providers fail, controlled AllProvidersExhaustedError must be raised."""
    p1 = MockLLMProvider(name="GroqPrimary", fail=True)
    p2 = MockLLMProvider(name="GroqFallback", fail=True)

    manager = ProviderManager(
        settings=Settings(groq_api_key="test"),
        custom_providers=[p1, p2],
    )

    msg = [HumanMessage(content="Hello")]
    with pytest.raises(
        AllProvidersExhaustedError, match="Все настроенные LLM-провайдеры недоступны"
    ):
        manager.invoke_with_fallback(msg)
