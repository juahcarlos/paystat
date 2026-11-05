"""GroqCloud LLM Provider implementation using langchain-groq."""

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from pydantic import SecretStr

from paystat.providers.base import BaseLLMProvider


class GroqProvider(BaseLLMProvider):
    """GroqCloud provider using official free tier API."""

    def __init__(self, api_key: str, model_name: str = "openai/gpt-oss-120b") -> None:
        self._api_key = api_key
        self._model_name = model_name
        # Modern ChatGroq parameter names: api_key and model
        self._chat_model = ChatGroq(
            api_key=SecretStr(self._api_key),
            model=self._model_name,
            temperature=0.0,
            max_retries=2,
        )

    @property
    def name(self) -> str:
        return "GroqCloud"

    @property
    def model_name(self) -> str:
        return self._model_name

    def get_chat_model(self) -> BaseChatModel:
        return self._chat_model

    def bind_tools(self, tools: Sequence[BaseTool]) -> Any:
        return self._chat_model.bind_tools(list(tools))
