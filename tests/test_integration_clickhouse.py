"""Integration tests against real ClickHouse instance (PRD Section 7).

PRD Section 7 Requirement:
"Integration tests check a real ClickHouse.
All checks must pass before each stage is completed."
"""

import clickhouse_connect.driver.exceptions
import pytest

from paystat.agent.tools import AnalyticalToolbox
from paystat.config import Settings
from paystat.database.connection import ClickHouseManager
from paystat.database.schema import SchemaInspector


@pytest.fixture
def real_db_manager() -> ClickHouseManager:
    """Provides a ClickHouseManager configured for the real ClickHouse instance."""
    settings = Settings()
    manager = ClickHouseManager(settings)
    if not manager.check_health():
        pytest.skip("ClickHouse instance is not reachable, skipping integration test.")
    return manager


@pytest.mark.integration
def test_clickhouse_connection_real(real_db_manager: ClickHouseManager) -> None:
    """Verifies that connection to the real ClickHouse database works and responds."""
    assert real_db_manager.check_health() is True


@pytest.mark.integration
def test_schema_inspector_real_clickhouse(real_db_manager: ClickHouseManager) -> None:
    """Inspects real tables and schema structure in ClickHouse."""
    inspector = SchemaInspector(real_db_manager)
    tables = inspector.get_tables()
    assert "transactions" in tables

    columns = inspector.describe_table("transactions")
    col_names = {col["column"] for col in columns}
    expected_cols = {
        "id",
        "tariff_id",
        "amount",
        "email",
        "expires",
        "created",
        "complete",
        "refund",
        "system",
    }
    assert expected_cols.issubset(col_names)


@pytest.mark.integration
def test_analytical_toolbox_query_execution_real(real_db_manager: ClickHouseManager) -> None:
    """Tests AnalyticalToolbox tools against real transactions data."""
    toolbox = AnalyticalToolbox(real_db_manager)
    tools = {t.name: t for t in toolbox.get_tools()}

    # 1. Test inspect_schema tool
    schema_output = tools["inspect_schema"].invoke({})
    assert "transactions" in schema_output
    assert "email" in schema_output

    # 2. Test execute_analytics_query tool with realistic aggregation
    query = """
        SELECT
            complete,
            count(*) AS count,
            sum(amount) AS total_amount
        FROM transactions
        GROUP BY complete
        ORDER BY count DESC
    """
    result = tools["execute_analytics_query"].invoke({"query": query})
    assert "total_amount" in result
    assert "row_count" in result


@pytest.mark.integration
def test_readonly_user_security_enforcement_real(real_db_manager: ClickHouseManager) -> None:
    """Verifies that ClickHouse user and settings enforce read-only at database level."""
    with real_db_manager.session() as client:
        # A simple SELECT should succeed
        res = client.query("SELECT count() FROM transactions")
        assert res.result_rows[0][0] > 0

        # Any attempt to write or mutate must be rejected by ClickHouse server
        with pytest.raises(clickhouse_connect.driver.exceptions.DatabaseError):
            client.query(
                "INSERT INTO transactions (id, amount) VALUES (999999, 100)"
            )
