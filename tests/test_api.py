"""Tests for API endpoints, payload validation, and CORS."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from paystat.api.routes import get_agent
from paystat.main import app
from paystat.providers.base import AllProvidersExhaustedError
from paystat.schemas.api import AnalyzeResponse, MetricItem


def test_health_endpoint(client: TestClient) -> None:
    """Check health endpoint returns proper structure."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database_connected" in data
    assert "version" in data


def test_analyze_input_validation(client: TestClient) -> None:
    """Too short question must return 422 Unprocessable Entity."""
    response = client.post("/analyze", json={"question": "a"})
    assert response.status_code == 422


def test_analyze_successful_flow(client: TestClient) -> None:
    """Check POST /analyze returns structured response without leaking SQL."""
    mock_agent = MagicMock()
    mock_agent.analyze.return_value = AnalyzeResponse(
        question="What is the total amount of completed payments?",
        summary="The total amount of completed payments is 45,000 USD.",
        metrics=[
            MetricItem(
                name="total_completed_amount", value=45000, unit="USD", description="Revenue"
            ),
        ],
        insights=["Most of the revenue was received in August."],
        limitations=[],
        steps_count=2,
    )

    app.dependency_overrides[get_agent] = lambda: mock_agent

    try:
        response = client.post(
            "/analyze",
            json={"question": "What is the total amount of completed payments?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "What is the total amount of completed payments?"
        assert data["summary"] == "The total amount of completed payments is 45,000 USD."
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["name"] == "total_completed_amount"
        assert data["steps_count"] == 2

        # Verify SQL is not leaked in the payload
        raw_text = response.text.upper()
        assert "SELECT " not in raw_text
        assert "FROM TRANSACTIONS" not in raw_text
    finally:
        app.dependency_overrides.clear()


def test_analyze_all_providers_exhausted(client: TestClient) -> None:
    """When all providers fail or exhaust quota, service must return 503."""
    mock_agent = MagicMock()
    mock_agent.analyze.side_effect = AllProvidersExhaustedError("All free providers exhausted")

    app.dependency_overrides[get_agent] = lambda: mock_agent

    try:
        response = client.post(
            "/analyze",
            json={"question": "Show the payment volume by day"},
        )
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert "exhausted the limit" in data["detail"]
    finally:
        app.dependency_overrides.clear()


def test_cors_headers(client: TestClient) -> None:
    """Verify CORS headers respond to preflight for allowed origin."""
    response = client.options(
        "/analyze",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"