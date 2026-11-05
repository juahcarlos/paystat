"""SQL Security and Validation Module.

Enforces strict read-only restrictions, query structure checks,
table allowlisting, forbidden keyword/function rejection, and row limits.
(PRD Section 6: Безопасность)

The validator parses the query with its own lexer (strings, quoted identifiers,
comments), so keywords inside string literals and comments
do not affect the checks, and the query that is executed always has its comments stripped.
"""

import re
from typing import NamedTuple


class SQLSecurityError(ValueError):
    """Raised when a query violates security policies."""


class _Token(NamedTuple):
    """Lexical token of a SQL query (kind: ws, comment, string, quoted, word, num, punct)."""

    kind: str
    text: str
    start: int
    end: int


_WORD_RE = re.compile(r"[^\W\d]\w*")
_NUM_RE = re.compile(r"\d\w*")
_WHITESPACE = " \t\n\r\f\v"


class SQLSecurityValidator:
    """Validates and sanitizes SQL queries to ensure read-only execution."""

    # Disallowed DDL/DML keywords
    FORBIDDEN_KEYWORDS: set[str] = {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "RENAME",
        "OPTIMIZE",
        "GRANT",
        "REVOKE",
        "ATTACH",
        "DETACH",
        "KILL",
        "SET",
        "BACKUP",
        "RESTORE",
        "EXCHANGE",
    }

    # Forbidden ClickHouse table functions that access network, files, or external systems
    FORBIDDEN_FUNCTIONS: set[str] = {
        "url",
        "file",
        "remote",
        "remotesecure",
        "s3",
        "hdfs",
        "executable",
        "cluster",
        "jdbc",
        "odbc",
        "mysql",
        "postgresql",
        "mongo",
        "executablepool",
    }

    ALLOWED_STATEMENTS: set[str] = {"SELECT", "WITH", "DESCRIBE", "EXPLAIN"}

    # Words that end a table reference (alias is skipped until one of them or a comma)
    _TABLE_TERMINATORS: frozenset[str] = frozenset(
        {
            "WHERE", "PREWHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "FETCH",
            "UNION", "INTERSECT", "EXCEPT", "SETTINGS", "FORMAT", "INTO", "WINDOW", "QUALIFY",
            "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER", "NATURAL", "GLOBAL",
            "LOCAL", "ANY", "ALL", "ASOF", "SEMI", "ANTI", "PASTE", "ARRAY", "ON", "USING",
            "FINAL", "SAMPLE", "WITH",
        }
    )  # fmt: skip

    _SET_OPERATORS: frozenset[str] = frozenset({"UNION", "INTERSECT", "EXCEPT"})
    _LIMIT_FOLLOWERS: frozenset[str] = frozenset(
        {"BY", "WITH", "SETTINGS", "FORMAT", "INTO", "UNION", "INTERSECT", "EXCEPT"}
    )

    def __init__(
        self,
        allowed_tables: set[str] | None = None,
        max_rows: int = 1000,
    ) -> None:
        self.allowed_tables = allowed_tables or {"transactions", "analytics.transactions"}
        self.max_rows = max_rows

    # ------------------------------------------------------------------ public API

    def validate_and_prepare_query(self, query: str) -> str:
        """Validate query against security rules and safely apply LIMIT.

        Returns:
            Sanitized query string (without comments) with enforced LIMIT.

        Raises:
            SQLSecurityError: If query fails any security assertion.
        """
        raw_query = query.strip()
        if not raw_query:
            raise SQLSecurityError("Пустой SQL-запрос недопустим.")

        # 1. Lex, strip comments and re-lex the cleaned text so offsets stay consistent
        stripped = "".join(
            " " if tok.kind == "comment" else tok.text for tok in self._tokenize(raw_query)
        ).strip()
        sig = [tok for tok in self._tokenize(stripped) if tok.kind != "ws"]

        while sig and sig[-1].text == ";":
            sig.pop()
        if not sig:
            raise SQLSecurityError("Пустой SQL-запрос недопустим.")

        # Disallow stacked statements
        if any(tok.text == ";" for tok in sig):
            raise SQLSecurityError("Запрещено выполнять несколько SQL-запросов за один вызов.")
        cleaned_sql = stripped[: sig[-1].end]

        # 2. Enforce statement type: must be SELECT, WITH, DESCRIBE, EXPLAIN
        first = sig[0]
        first_up = first.text.upper()
        if first.kind != "word" or first_up not in self.ALLOWED_STATEMENTS:
            raise SQLSecurityError(
                "Разрешены только запросы на чтение (SELECT, WITH, DESCRIBE, EXPLAIN). "
                f"Обнаружен: {first.text}"
            )

        # 3. Scan for forbidden DDL/DML keywords
        for tok in sig:
            if tok.kind == "word" and tok.text.upper() in self.FORBIDDEN_KEYWORDS:
                raise SQLSecurityError(f"Запрещенная операция в запросе: {tok.text.upper()}")

        # 4. Check for forbidden external/network table functions
        for idx, tok in enumerate(sig[:-1]):
            if (
                tok.kind in ("word", "quoted")
                and self._ident(tok) in self.FORBIDDEN_FUNCTIONS
                and sig[idx + 1].text == "("
            ):
                raise SQLSecurityError(
                    "Запрос содержит недопустимые функции обращения к внешним ресурсам или файлам."
                )

        # 5. Verify allowed tables and table functions, including CTEs and subqueries
        allowed = {tbl.lower() for tbl in self.allowed_tables}
        for name, is_function in self._extract_tables(sig):
            if is_function or name not in allowed:
                raise SQLSecurityError(
                    f"Доступ к таблице или функции '{name}' запрещен. "
                    f"Разрешенные таблицы: {', '.join(sorted(self.allowed_tables))}"
                )

        # 6. Enforce safe LIMIT (not applicable to DESCRIBE / EXPLAIN)
        if first_up in {"DESCRIBE", "EXPLAIN"}:
            return cleaned_sql
        return self._enforce_limit(cleaned_sql)

    # ------------------------------------------------------------------ lexer

    @classmethod
    def _tokenize(cls, sql: str) -> list[_Token]:
        """Split SQL into tokens, honouring strings, quoted identifiers and comments."""
        tokens: list[_Token] = []
        pos, size = 0, len(sql)
        while pos < size:
            start = pos
            ch = sql[pos]
            if ch in _WHITESPACE:
                while pos < size and sql[pos] in _WHITESPACE:
                    pos += 1
                kind = "ws"
            elif sql.startswith("--", pos) or ch == "#":
                newline = sql.find("\n", pos)
                pos = size if newline == -1 else newline
                kind = "comment"
            elif sql.startswith("/*", pos):
                close = sql.find("*/", pos + 2)
                if close == -1:
                    raise SQLSecurityError("Незакрытый блочный комментарий в SQL-запросе.")
                pos = close + 2
                kind = "comment"
            elif ch in ("'", "`", '"'):
                pos = cls._skip_quoted(sql, pos)
                kind = "string" if ch == "'" else "quoted"
            elif ch == "$":
                raise SQLSecurityError("Символ '$' (heredoc-строки) в запросе недопустим.")
            elif word := _WORD_RE.match(sql, pos):
                pos = word.end()
                kind = "word"
            elif number := _NUM_RE.match(sql, pos):
                pos = number.end()
                kind = "num"
            else:
                pos += 1
                kind = "punct"
            tokens.append(_Token(kind, sql[start:pos], start, pos))
        return tokens

    @staticmethod
    def _skip_quoted(sql: str, pos: int) -> int:
        """Return index right after the quoted literal that starts at ``pos``."""
        quote = sql[pos]
        idx = pos + 1
        while idx < len(sql):
            char = sql[idx]
            if char == "\\":
                idx += 2
            elif char == quote:
                if sql[idx + 1 : idx + 2] == quote:
                    idx += 2
                else:
                    return idx + 1
            else:
                idx += 1
        raise SQLSecurityError("Незакрытая кавычка в SQL-запросе.")

    @staticmethod
    def _ident(tok: _Token) -> str:
        """Lower-cased identifier text without surrounding quotes."""
        text = tok.text[1:-1] if tok.kind == "quoted" else tok.text
        return text.lower()

    @staticmethod
    def _match_parens(sig: list[_Token]) -> dict[int, int]:
        """Map index of every opening parenthesis to the index of its closing one."""
        stack: list[int] = []
        close_of: dict[int, int] = {}
        for idx, tok in enumerate(sig):
            if tok.kind != "punct":
                continue
            if tok.text == "(":
                stack.append(idx)
            elif tok.text == ")":
                if not stack:
                    raise SQLSecurityError("Несбалансированные скобки в SQL-запросе.")
                close_of[stack.pop()] = idx
        if stack:
            raise SQLSecurityError("Несбалансированные скобки в SQL-запросе.")
        return close_of

    # ------------------------------------------------------------------ tables

    def _extract_tables(self, sig: list[_Token]) -> list[tuple[str, bool]]:
        """Collect (name, is_function) for every table reference in FROM/JOIN clauses.

        Walks the whole token stream, so subqueries and CTE bodies are covered.
        Names of CTEs visible at the reference point are not reported.
        """
        size = len(sig)
        close_of = self._match_parens(sig)
        ups = [tok.text.upper() if tok.kind == "word" else "" for tok in sig]
        refs: list[tuple[list[str], bool, int]] = []

        # DESCRIBE [TABLE] <ref>
        if ups[0] == "DESCRIBE":
            pos = 2 if size > 1 and ups[1] == "TABLE" else 1
            _, ref = self._read_table_ref(sig, pos, close_of)
            if ref is not None:
                refs.append(ref)

        # CTE names with the token range (enclosing parentheses) where they are visible
        ctes: list[tuple[str, int, int]] = []
        stack: list[int] = []
        for idx, tok in enumerate(sig):
            if tok.kind == "punct" and tok.text == "(":
                stack.append(idx)
            elif tok.kind == "punct" and tok.text == ")":
                stack.pop()
            elif (
                tok.kind in ("word", "quoted")
                and idx > 0
                and idx + 3 < size
                and ups[idx + 1] == "AS"
                and sig[idx + 2].text == "("
                and ups[idx + 3] in ("SELECT", "WITH")
                and (ups[idx - 1] in ("WITH", "RECURSIVE") or sig[idx - 1].text == ",")
            ):
                low = stack[-1] if stack else -1
                high = close_of[stack[-1]] if stack else size
                ctes.append((self._ident(tok), low, high))

        # FROM / JOIN references
        stack = []
        for idx, tok in enumerate(sig):
            if tok.kind == "punct" and tok.text == "(":
                stack.append(idx)
                continue
            if tok.kind == "punct" and tok.text == ")":
                stack.pop()
                continue
            if ups[idx] not in ("FROM", "JOIN"):
                continue
            # FROM inside function call, e.g. EXTRACT(MONTH FROM col), is not a table reference
            if stack and not self._is_subquery_paren(sig, ups, stack[-1]):
                continue
            if ups[idx] == "JOIN" and idx > 0 and ups[idx - 1] == "ARRAY":
                continue
            pos = idx + 1
            while pos < size:
                pos, ref = self._read_table_ref(sig, pos, close_of)
                if ref is not None:
                    refs.append(ref)
                pos = self._skip_alias(sig, ups, pos, close_of)
                if pos < size and sig[pos].text == ",":
                    pos += 1
                    continue
                break

        result: list[tuple[str, bool]] = []
        for parts, is_function, first_idx in refs:
            name = ".".join(parts)
            if (
                not is_function
                and len(parts) == 1
                and any(cte == name and low < first_idx < high for cte, low, high in ctes)
            ):
                continue
            result.append((name, is_function))
        return result

    @staticmethod
    def _is_subquery_paren(sig: list[_Token], ups: list[str], open_idx: int) -> bool:
        nxt = open_idx + 1
        return nxt < len(sig) and (ups[nxt] in ("SELECT", "WITH") or sig[nxt].text == "(")

    def _read_table_ref(
        self, sig: list[_Token], pos: int, close_of: dict[int, int]
    ) -> tuple[int, tuple[list[str], bool, int] | None]:
        """Read table reference at ``pos``: returns (next position, (parts, is_function, idx))."""
        size = len(sig)
        if pos >= size:
            return pos, None
        tok = sig[pos]
        if tok.kind == "punct" and tok.text == "(":
            # subquery: its own FROM/JOIN are found by the main scan
            return close_of[pos] + 1, None
        if tok.kind not in ("word", "quoted"):
            return pos, None
        parts = [self._ident(tok)]
        nxt = pos + 1
        while (
            nxt + 1 < size
            and sig[nxt].kind == "punct"
            and sig[nxt].text == "."
            and sig[nxt + 1].kind in ("word", "quoted")
        ):
            parts.append(self._ident(sig[nxt + 1]))
            nxt += 2
        if nxt < size and sig[nxt].kind == "punct" and sig[nxt].text == "(":
            return close_of[nxt] + 1, (parts, True, pos)
        return nxt, (parts, False, pos)

    def _skip_alias(
        self, sig: list[_Token], ups: list[str], pos: int, close_of: dict[int, int]
    ) -> int:
        """Skip alias / modifiers after a table reference up to a comma or clause keyword."""
        size = len(sig)
        while pos < size:
            tok = sig[pos]
            if tok.kind == "punct" and tok.text in (",", ")"):
                break
            if tok.kind == "punct" and tok.text == "(":
                pos = close_of[pos] + 1
                continue
            if ups[pos] in self._TABLE_TERMINATORS:
                break
            pos += 1
        return pos

    # ------------------------------------------------------------------ limit

    def _enforce_limit(self, query: str) -> str:
        """Enforce max_rows limit on the outer query, before SETTINGS/FORMAT clauses."""
        sql = query.strip()
        sig = [tok for tok in self._tokenize(sql) if tok.kind not in ("ws", "comment")]
        size = len(sig)

        depth = 0
        tail_at: int | None = None
        count_tok: _Token | None = None
        needs_wrap = False

        for idx, tok in enumerate(sig):
            if tok.kind == "punct" and tok.text == "(":
                depth += 1
                continue
            if tok.kind == "punct" and tok.text == ")":
                depth -= 1
                continue
            if depth or tok.kind != "word":
                continue
            up = tok.text.upper()
            nxt = sig[idx + 1] if idx + 1 < size else None
            nxt2 = sig[idx + 2] if idx + 2 < size else None
            # ClickHouse requires LIMIT to precede these trailing clauses
            if up == "SETTINGS" and nxt2 is not None and nxt2.text == "=":
                tail_at = idx
                break
            if (
                up == "FORMAT"
                and nxt is not None
                and nxt.kind == "word"
                and (nxt2 is None or nxt2.text.upper() == "SETTINGS")
            ):
                tail_at = idx
                break
            if up == "INTO" and nxt is not None and nxt.text.upper() == "OUTFILE":
                tail_at = idx
                break
            if up in self._SET_OPERATORS:
                needs_wrap = True
            elif up == "LIMIT":
                count, is_limit_by = self._parse_limit(sig, idx)
                if count is None:
                    needs_wrap = True
                elif not is_limit_by:
                    count_tok = count

        if tail_at is not None:
            main = sql[: sig[tail_at].start].rstrip()
            tail = sql[sig[tail_at].start :]
        else:
            main, tail = sql, ""

        if needs_wrap:
            # UNION/INTERSECT/EXCEPT or non-literal LIMIT: cap the whole result
            main = f"SELECT * FROM ({main}) LIMIT {self.max_rows}"
        elif count_tok is None:
            main = f"{main} LIMIT {self.max_rows}"
        elif int(count_tok.text) > self.max_rows:
            main = main[: count_tok.start] + str(self.max_rows) + main[count_tok.end :]

        return f"{main} {tail}" if tail else main

    def _parse_limit(self, sig: list[_Token], idx: int) -> tuple[_Token | None, bool]:
        """Parse LIMIT arguments at ``idx``.

        Returns (token with row count or None if not a plain integer literal, is LIMIT ... BY).
        """
        size = len(sig)
        first = sig[idx + 1] if idx + 1 < size else None
        if first is None or first.kind != "num" or not first.text.isdigit():
            return None, False
        count = first
        pos = idx + 2
        if pos + 1 < size and sig[pos].text == ",":
            second = sig[pos + 1]
            if second.kind != "num" or not second.text.isdigit():
                return None, False
            count = second
            pos += 2
        elif pos + 1 < size and sig[pos].text.upper() == "OFFSET":
            offset = sig[pos + 1]
            if offset.kind != "num" or not offset.text.isdigit():
                return None, False
            pos += 2
        after = sig[pos] if pos < size else None
        if after is not None and after.text.upper() not in self._LIMIT_FOLLOWERS:
            return None, False
        return count, after is not None and after.text.upper() == "BY"
