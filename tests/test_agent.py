"""Tests for AnalyticalAgent multi-step reasoning, tool dispatch, and SQL sanitization."""

import json
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from paystat.agent.analyst import AnalyticalAgent
from paystat.config import Settings
from paystat.database.connection import ClickHouseManager
from paystat.providers.manager import ProviderManager


def test_agent_parses_clean_json_response() -> None:
    """Agent properly formats JSON response into AnalyzeResponse model."""
    settings = Settings(max_agent_iterations=3, groq_api_key="test")
    mock_db = MagicMock(spec=ClickHouseManager)
    mock_db.settings = settings
    mock_pm = MagicMock(spec=ProviderManager)

    agent = AnalyticalAgent(db_manager=mock_db, provider_manager=mock_pm, settings=settings)

    mock_json = {
        "summary": "Выручка за октябрь составила 35,000 USD.",
        "metrics": [
            {
                "name": "october_revenue",
                "value": 35000,
                "unit": "USD",
                "description": "Сумма завершенных транзакций",
            }
        ],
        "insights": ["Рост на 15% по сравнению с сентябрем."],
        "limitations": ["Учтены только транзакции в USD."],
    }

    mock_pm.invoke_with_fallback.return_value = AIMessage(
        content=f"```json\n{json.dumps(mock_json)}\n```"
    )

    res = agent.analyze("Какова выручка за октябрь?")

    assert res.summary == "Выручка за октябрь составила 35,000 USD."
    assert len(res.metrics) == 1
    assert res.metrics[0].name == "october_revenue"
    assert res.metrics[0].value == 35000
    assert len(res.insights) == 1
    assert len(res.limitations) == 1
    assert res.steps_count == 1


def test_agent_strips_sql_from_response() -> None:
    """Agent must never expose raw SQL in any part of the response (PRD Section 6)."""
    settings = Settings(max_agent_iterations=3, groq_api_key="test")
    mock_db = MagicMock(spec=ClickHouseManager)
    mock_db.settings = settings
    mock_pm = MagicMock(spec=ProviderManager)

    agent = AnalyticalAgent(db_manager=mock_db, provider_manager=mock_pm, settings=settings)

    mock_json = {
        "summary": "Запрос: ```sql\nSELECT sum(amount) FROM transactions\n```. Итого 1000.",
        "metrics": [
            {"name": "metric_1", "value": 1000, "description": "SELECT count(*) FROM transactions"}
        ],
        "insights": ["Использовался SELECT * FROM transactions"],
        "limitations": ["Ограничение: SELECT 1 FROM transactions"],
    }

    mock_pm.invoke_with_fallback.return_value = AIMessage(content=json.dumps(mock_json))

    res = agent.analyze("Покажи выручку")
    assert "SELECT sum(amount)" not in res.summary
    assert "SELECT count(*)" not in (res.metrics[0].description or "")
    assert "SELECT *" not in res.insights[0]
    assert "SELECT 1" not in res.limitations[0]


def test_agent_strict_budget_enforcement() -> None:
    """Agent strictly limits model calls to max_agent_iterations (PRD Section 5: limit 3)."""
    settings = Settings(max_agent_iterations=3, groq_api_key="test")
    mock_db = MagicMock(spec=ClickHouseManager)
    mock_db.settings = settings
    mock_pm = MagicMock(spec=ProviderManager)

    agent = AnalyticalAgent(db_manager=mock_db, provider_manager=mock_pm, settings=settings)

    # First turn: tool call
    step1_response = AIMessage(
        content="",
        tool_calls=[{"name": "inspect_schema", "args": {}, "id": "call_1"}],
    )
    # Second turn: another tool call
    step2_response = AIMessage(
        content="",
        tool_calls=[{"name": "inspect_schema", "args": {}, "id": "call_2"}],
    )
    # Third turn (forced final synthesis call): model returns final JSON
    step3_json = {
        "summary": "Итог после 3 вызовов.",
        "metrics": [],
        "insights": [],
        "limitations": [],
    }
    step3_response = AIMessage(content=json.dumps(step3_json))

    mock_pm.invoke_with_fallback.side_effect = [step1_response, step2_response, step3_response]

    res = agent.analyze("Глубокий анализ")

    assert res.steps_count == 3
    assert mock_pm.invoke_with_fallback.call_count == 3
    assert res.summary == "Итог после 3 вызовов."
