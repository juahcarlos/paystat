"""ClickHouse client management and connection pooling."""

from collections.abc import Generator
from contextlib import contextmanager

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from paystat.config import Settings, get_settings


class DatabaseConnectionError(Exception):
    """Raised when connecting or communicating with ClickHouse fails."""


class ClickHouseManager:
    """Manages ClickHouse client instances with server-side safety constraints."""

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def get_client(self) -> Client:
        """Create and return a configured ClickHouse client with server-side read-only settings."""
        try:
            # PRD Section 6: Server-side safety enforcement
            server_settings = {
                "readonly": 1,
                "max_execution_time": self.settings.query_timeout_seconds,
                "max_result_rows": self.settings.max_query_rows,
                "result_overflow_mode": "break",
            }

            client = clickhouse_connect.get_client(
                host=self.settings.clickhouse_host,
                port=self.settings.clickhouse_port,
                username=self.settings.clickhouse_user,
                password=self.settings.clickhouse_password,
                database=self.settings.clickhouse_database,
                connect_timeout=self.settings.query_timeout_seconds,
                send_receive_timeout=self.settings.query_timeout_seconds,
                settings=server_settings,
            )
            return client
        except Exception as exc:
            raise DatabaseConnectionError(
                f"Не удалось подключиться к ClickHouse ({self.settings.clickhouse_host}:{self.settings.clickhouse_port}): {exc}"
            ) from exc

    def check_health(self) -> bool:
        """Check if database connection is alive."""
        try:
            client = self.get_client()
            res = client.command("SELECT 1")
            return res == 1
        except Exception:
            return False

    @contextmanager
    def session(self) -> Generator[Client]:
        """Context manager for obtaining a client session."""
        client = self.get_client()
        try:
            yield client
        finally:
            client.close()
