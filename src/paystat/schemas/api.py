"""API Request and Response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """Input payload for POST /analyze endpoint."""

    question: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Natural language question regarding payment data.",
        examples=["Какова общая выручка и объем завершенных платежей за сентябрь 2026?"],
    )


class MetricItem(BaseModel):
    """Single numerical metric item supporting decisions."""

    name: str = Field(..., description="Metric identifier or name")
    value: Any = Field(..., description="Calculated value (number, string, ratio)")
    unit: str | None = Field(None, description="Currency or measurement unit (e.g. USD, count, %)")
    description: str | None = Field(None, description="Contextual note for the metric")


class AnalyzeResponse(BaseModel):
    """Structured analytical response for decision support (PRD Section 3).

    Note (PRD Section 2 & 6):
    SQL queries are intentionally excluded from the standard response
    to prevent exposure of database internals.
    """

    question: str = Field(..., description="Original user question")
    summary: str = Field(..., description="High-level analytical summary for decision makers")
    metrics: list[MetricItem] = Field(
        default_factory=list,
        description="Key numerical indicators derived from actual ClickHouse records",
    )
    insights: list[str] = Field(
        default_factory=list,
        description="Key factual observations and trends derived from data",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Constraints or missing data caveats (PRD Section 2)",
    )
    steps_count: int = Field(
        ...,
        description="Number of analytical investigative steps taken by the agent",
    )


class ErrorDetail(BaseModel):
    """Safe, structured error details without sensitive tracebacks."""

    error: str = Field(..., description="Error category code")
    message: str = Field(..., description="Human-readable safe explanation")
    details: dict[str, Any] | None = Field(None, description="Non-sensitive error attributes")


class HealthResponse(BaseModel):
    """Healthcheck endpoint response."""

    status: str = "ok"
    database_connected: bool
    version: str
