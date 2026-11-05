"""Database schema introspection module.

Allows the AI agent to explore actual available schema and prevents
assumptions about non-existent columns (PRD Section 2).
"""

from typing import Any

from paystat.database.connection import ClickHouseManager


class SchemaInspector:
    """Inspects tables and columns in ClickHouse database."""

    def __init__(self, db_manager: ClickHouseManager) -> None:
        self.db_manager = db_manager

    def get_tables(self, database: str | None = None) -> list[str]:
        """Fetch list of user tables in the specified database."""
        db_name = database or self.db_manager.settings.clickhouse_database
        query = f"SELECT name FROM system.tables WHERE database = '{db_name}' AND is_temporary = 0 ORDER BY name"
        with self.db_manager.session() as client:
            res = client.query(query)
            return [str(row[0]) for row in res.result_rows]

    def describe_table(self, table_name: str, database: str | None = None) -> list[dict[str, Any]]:
        """Fetch column names, types, and comments for a given table."""
        db_name = database or self.db_manager.settings.clickhouse_database
        clean_name = table_name.split(".")[-1].replace("'", "").replace(";", "")

        query = f"""
            SELECT name, type, comment
            FROM system.columns
            WHERE database = '{db_name}' AND table = '{clean_name}'
            ORDER BY position
        """
        with self.db_manager.session() as client:
            res = client.query(query)
            columns: list[dict[str, Any]] = []
            for row in res.result_rows:
                columns.append(
                    {
                        "column": str(row[0]),
                        "type": str(row[1]),
                        "comment": str(row[2]) if row[2] else "",
                    }
                )
            return columns

    def get_full_schema_summary(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch all tables with their columns in a single summary map."""
        tables = self.get_tables()
        schema_map: dict[str, list[dict[str, Any]]] = {}
        for tbl in tables:
            schema_map[tbl] = self.describe_table(tbl)
        return schema_map
