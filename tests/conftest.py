"""Pytest fixtures and test configurations."""

from collections.abc import Generator, Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool

from paystat.config import Settings
from paystat.main import app
from paystat.providers.base import BaseLLMProvider


class MockChatModel(BaseChatModel):
    """Mock LangChain chat model that yields predefined responses."""

    responses: list[Any] = []
    _call_count: int = 0

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        from langchain_core.outputs import ChatGeneration, ChatResult

        res = self.responses[self._call_count % len(self.responses)] if self.responses else "OK"
        self._call_count += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(res)))])

    @property
    def _llm_type(self) -> str:
        return "mock_chat"


class MockLLMProvider(BaseLLMProvider):
    """Test provider using mock responses."""

    def __init__(
        self, name: str = "MockProvider", model_name: str = "mock-1", fail: bool = False
    ) -> None:
        self._name = name
        self._model_name = model_name
        self.fail = fail
        self.call_history: list[Any] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._model_name

    def get_chat_model(self) -> BaseChatModel:
        if self.fail:
            raise RuntimeError(f"Simulated failure in provider {self._name}")
        return MockChatModel()

    def bind_tools(self, tools: Sequence[BaseTool]) -> Any:
        mock_runnable = MagicMock()
        if self.fail:
            mock_runnable.invoke.side_effect = RuntimeError(
                f"Simulated tool call failure in {self._name}"
            )
        return mock_runnable


@pytest.fixture
def test_settings() -> Settings:
    """Settings customized for tests."""
    return Settings(
        app_env="test",
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_database="analytics",
        clickhouse_user="readonly_user",
        clickhouse_password="readonly_secret",
        max_agent_iterations=3,
        groq_api_key="test-dummy-key",
    )


@pytest.fixture
def client() -> Generator[TestClient]:
    """Test client for FastAPI app."""
    with TestClient(app) as test_client:
        yield test_client
