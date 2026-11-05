"""Agent analytical tools for ClickHouse inspection and query execution."""

import json
from decimal import Decimal
from typing import Any

from langchain_core.tools import tool

from paystat.database.connection import ClickHouseManager
from paystat.database.schema import SchemaInspector
from paystat.security.validator import SQLSecurityError, SQLSecurityValidator


class AnalyticalToolbox:
    """Encapsulates LangChain tools bound to a specific ClickHouse session."""

    def __init__(
        self,
        db_manager: ClickHouseManager,
        validator: SQLSecurityValidator | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.schema_inspector = SchemaInspector(db_manager)
        # Fix: explicitly pass max_rows from db_manager settings
        self.validator = validator or SQLSecurityValidator(
            max_rows=self.db_manager.settings.max_query_rows
        )

    def get_tools(self) -> list[Any]:
        """Return configured tools for LangChain agent."""

        @tool
        def inspect_schema() -> str:
            """Explore the available tables and fields in the ClickHouse database.

            Returns the actual table names, column types and comments.
            Always call this tool before building SQL queries,
            so as not to make false assumptions about the schema.
            """
            try:
                schema = self.schema_inspector.get_full_schema_summary()
                return json.dumps(schema, ensure_ascii=False, indent=2, default=str)
            except Exception as exc:
                return f"Error while exploring the data schema: {exc}"

        @tool
        def execute_analytics_query(query: str) -> str:
            """Execute a read-only analytical SQL query (SELECT) in ClickHouse.

            The query is checked by the security validator:
            - Only SELECT statements are allowed.
            - Any DDL/DML modifications are forbidden (DROP, INSERT, UPDATE, ALTER).
            - Access is allowed only to the target analytics tables.
            - A LIMIT restriction is applied automatically.
            Returns the query result in JSON format.
            """
            try:
                # 1. Security validation and limit enforcement
                sanitized_query = self.validator.validate_and_prepare_query(query)

                # 2. Execution against ClickHouse
                with self.db_manager.session() as client:
                    result = client.query(sanitized_query)
                    cols = result.column_names
                    rows = result.result_rows

                    formatted_rows: list[dict[str, Any]] = []
                    for row in rows:
                        row_dict = {}
                        for idx, col in enumerate(cols):
                            val = row[idx]
                            # Only cast Decimal to float, preserving int as int
                            if isinstance(val, Decimal):
                                val = float(val)
                            elif hasattr(val, "isoformat"):
                                val = val.isoformat()
                            row_dict[col] = val
                        formatted_rows.append(row_dict)

                    response_data = {
                        "row_count": len(formatted_rows),
                        "columns": cols,
                        "data": formatted_rows,
                    }
                    # Fix: default=str prevents UUID TypeError
                    return json.dumps(response_data, ensure_ascii=False, default=str)

            except SQLSecurityError as sec_err:
                return f"SQL security error: {sec_err}"
            except Exception as exc:
                return f"Query execution error: {exc}"

        return [inspect_schema, execute_analytics_query]