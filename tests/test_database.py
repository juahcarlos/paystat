"""Tests for ClickHouse manager and schema inspection."""

from unittest.mock import MagicMock

from paystat.config import Settings
from paystat.database.connection import ClickHouseManager
from paystat.database.schema import SchemaInspector


def test_schema_inspector_get_tables() -> None:
    """Schema inspector correctly parses table list from system.tables."""
    mock_db = MagicMock(spec=ClickHouseManager)
    mock_client = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_client
    mock_db.settings = Settings(clickhouse_database="analytics")

    # Mock query result
    mock_result = MagicMock()
    mock_result.result_rows = [["transactions"], ["orders"]]
    mock_client.query.return_value = mock_result

    inspector = SchemaInspector(mock_db)
    tables = inspector.get_tables()

    assert tables == ["transactions", "orders"]
    mock_client.query.assert_called_once()


def test_schema_inspector_describe_table() -> None:
    """Schema inspector extracts column details from system.columns."""
    mock_db = MagicMock(spec=ClickHouseManager)
    mock_client = MagicMock()
    mock_db.session.return_value.__enter__.return_value = mock_client
    mock_db.settings = Settings(clickhouse_database="analytics")

    mock_result = MagicMock()
    mock_result.result_rows = [
        ["id", "UInt64", "Unique identifier"],
        ["amount", "Decimal64(2)", "Transaction amount"],
    ]
    mock_client.query.return_value = mock_result

    inspector = SchemaInspector(mock_db)
    cols = inspector.describe_table("transactions")

    assert len(cols) == 2
    assert cols[0]["column"] == "id"
    assert cols[1]["type"] == "Decimal64(2)"
