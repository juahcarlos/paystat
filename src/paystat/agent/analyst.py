"""Multi-step analytical agent implementation using LangChain and ProviderManager."""

import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from paystat.agent.prompts import SYSTEM_PROMPT
from paystat.agent.tools import AnalyticalToolbox
from paystat.config import Settings, get_settings
from paystat.database.connection import ClickHouseManager
from paystat.providers.manager import ProviderManager
from paystat.schemas.api import AnalyzeResponse, MetricItem

logger = logging.getLogger(__name__)


class AnalysisLimitExceededError(Exception):
    """Raised when analysis steps exceed the permitted iteration budget."""


class AnalyticalAgent:
    """Orchestrates multi-step decision analysis over ClickHouse data."""

    def __init__(
        self,
        db_manager: ClickHouseManager | None = None,
        provider_manager: ProviderManager | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_manager = db_manager or ClickHouseManager(self.settings)
        self.provider_manager = provider_manager or ProviderManager(self.settings)
        self.toolbox = AnalyticalToolbox(self.db_manager)
        self.tools = self.toolbox.get_tools()
        self.tools_by_name = {tool.name: tool for tool in self.tools}

    def analyze(self, question: str) -> AnalyzeResponse:
        """Run iterative analytical reasoning loop over user question.

        Enforces:
        - Strict budget of max 3 model calls (PRD Section 5).
        - Sequential step execution directed by intermediate results (PRD Section 2).
        - SQL stripping from all fields in final response (PRD Section 6).
        """
        messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Вопрос для анализа: {question}"),
        ]

        total_llm_calls = 0
        max_calls = self.settings.max_agent_iterations
        final_ai_message: AIMessage | None = None

        while total_llm_calls < max_calls:
            total_llm_calls += 1
            is_final_call = total_llm_calls == max_calls

            logger.info(
                "Вызов LLM %d/%d (финальный: %s)", total_llm_calls, max_calls, is_final_call
            )

            if is_final_call:
                # Force final synthesis: do not pass tools and prompt for JSON answer
                messages.append(
                    HumanMessage(
                        content="Лимит запросов исчерпан. Сформируй итоговый JSON-ответ на основе собранных данных без вызова инструментов."
                    )
                )
                response = self.provider_manager.invoke_with_fallback(messages=messages, tools=None)
            else:
                response = self.provider_manager.invoke_with_fallback(
                    messages=messages, tools=self.tools
                )

            if not isinstance(response, AIMessage):
                response = AIMessage(content=str(response))

            messages.append(response)

            # Check if tools were requested
            if response.tool_calls and not is_final_call:
                for tool_call in response.tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args", {})
                    call_id = tool_call.get("id", f"call_{total_llm_calls}")

                    logger.info("Агент вызывает инструмент %s", tool_name)
                    target_tool = self.tools_by_name.get(tool_name)

                    if target_tool:
                        try:
                            tool_result = target_tool.invoke(tool_args)
                        except Exception as exc:
                            tool_result = f"Ошибка выполнения инструмента {tool_name}: {exc}"
                    else:
                        tool_result = f"Инструмент '{tool_name}' не найден."

                    messages.append(ToolMessage(content=str(tool_result), tool_call_id=call_id))
            else:
                # Agent completed answer or final call finished
                final_ai_message = response
                break

        if final_ai_message is None:
            final_ai_message = AIMessage(content="{}")

        # Parse structured output and ensure no SQL leaks
        return self._format_response(
            question, final_ai_message.content, steps_count=total_llm_calls
        )

    def _format_response(
        self, question: str, raw_content: Any, steps_count: int
    ) -> AnalyzeResponse:
        """Parse agent output into structured AnalyzeResponse, stripping SQL from all fields."""
        text = str(raw_content).strip()

        # 1. Try parsing directly as JSON first
        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass

        # 2. If direct JSON parsing fails, look for ```json fence
        if data is None:
            json_fence_match = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
            if json_fence_match:
                try:
                    parsed = json.loads(json_fence_match.group(1).strip())
                    if isinstance(parsed, dict):
                        data = parsed
                except Exception:
                    pass

        # 3. If still not parsed, look for outer curly braces { ... }
        if data is None:
            brace_match = re.search(r"(\{[\s\S]*\})", text)
            if brace_match:
                try:
                    parsed = json.loads(brace_match.group(1).strip())
                    if isinstance(parsed, dict):
                        data = parsed
                except Exception:
                    pass

        # 4. Fallback if model returned unstructured text
        if data is None:
            cleaned_text = self._strip_sql(text)
            return AnalyzeResponse(
                question=question,
                summary=cleaned_text,
                metrics=[],
                insights=[],
                limitations=["Модель вернула неструктурированный ответ."],
                steps_count=steps_count,
            )

        # Parse and sanitize metrics
        metrics_list: list[MetricItem] = []
        for m in data.get("metrics", []):
            if isinstance(m, dict) and "name" in m and "value" in m:
                metrics_list.append(
                    MetricItem(
                        name=self._strip_sql(str(m["name"])),
                        value=m["value"],
                        unit=self._strip_sql(str(m["unit"])) if m.get("unit") else None,
                        description=self._strip_sql(str(m["description"]))
                        if m.get("description")
                        else None,
                    )
                )

        # Sanitize insights and limitations
        insights = [
            self._strip_sql(str(item))
            for item in data.get("insights", [])
            if isinstance(item, (str, int, float))
        ]
        limitations = [
            self._strip_sql(str(lim))
            for lim in data.get("limitations", [])
            if isinstance(lim, (str, int, float))
        ]

        summary = self._strip_sql(str(data.get("summary", "Анализ завершен.")))

        return AnalyzeResponse(
            question=question,
            summary=summary,
            metrics=metrics_list,
            insights=insights,
            limitations=limitations,
            steps_count=steps_count,
        )

    def _strip_sql(self, text: str) -> str:
        """Sanitize text to guarantee no raw SQL queries leak to user (PRD Section 6)."""
        # Remove SQL codeblocks with DOTALL
        text = re.sub(r"```(?:sql)?[\s\S]*?```", "[SQL скрыт]", text, flags=re.IGNORECASE)
        # Remove inline and multiline SELECT expressions with DOTALL
        text = re.sub(
            r"\bSELECT\s+.+?\s+FROM\s+\S+",
            "[запрос к БД]",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return text
