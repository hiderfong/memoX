import asyncio
import json
import re
from typing import Any

from sqlalchemy import create_engine, text

from src.agents.base_agent import BaseTool
from src.config import DatabaseToolPolicyConfig, get_config, resolve_env_value


class DatabaseSafetyError(ValueError):
    """Raised when a SQL query is rejected by tool policy."""


READ_ONLY_VERBS = {"select", "with", "show", "describe", "desc", "explain"}
READ_ONLY_PRAGMAS = {
    "table_info",
    "table_xinfo",
    "index_info",
    "index_list",
    "foreign_key_list",
    "database_list",
    "function_list",
    "module_list",
    "schema_version",
    "user_version",
    "quick_check",
    "integrity_check",
}
DML_VERBS = {"insert", "update", "delete", "merge", "replace", "upsert"}
DDL_VERBS = {
    "alter",
    "create",
    "drop",
    "truncate",
    "grant",
    "revoke",
    "reindex",
    "vacuum",
    "analyze",
    "refresh",
    "comment",
    "lock",
}
UNSUPPORTED_CONTROL_VERBS = {
    "attach",
    "detach",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release",
    "set",
    "reset",
    "copy",
    "call",
    "exec",
    "execute",
    "load_extension",
}
MUTATING_TOKENS = DML_VERBS | DDL_VERBS | UNSUPPORTED_CONTROL_VERBS


def _database_policy() -> DatabaseToolPolicyConfig:
    try:
        return get_config().tool_policy.database
    except Exception:
        return DatabaseToolPolicyConfig()


def _mask_sql_literals_and_comments(sql: str) -> str:
    masked: list[str] = []
    i = 0
    state: str | None = None
    quote_char = ""

    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if state == "line_comment":
            if ch == "\n":
                state = None
                masked.append(ch)
            else:
                masked.append(" ")
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                masked.extend("  ")
                state = None
                i += 2
            else:
                masked.append("\n" if ch == "\n" else " ")
                i += 1
            continue

        if state == "quoted":
            if ch == quote_char:
                if quote_char == "'" and nxt == "'":
                    masked.extend("  ")
                    i += 2
                    continue
                state = None
            masked.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        if ch == "-" and nxt == "-":
            masked.extend("  ")
            state = "line_comment"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            masked.extend("  ")
            state = "block_comment"
            i += 2
            continue
        if ch in {"'", '"', "`"}:
            quote_char = ch
            state = "quoted"
            masked.append(" ")
            i += 1
            continue

        masked.append(ch)
        i += 1

    return "".join(masked)


def _split_sql_statements(sql: str) -> list[str]:
    masked = _mask_sql_literals_and_comments(sql)
    statements: list[str] = []
    start = 0

    for index, ch in enumerate(masked):
        if ch == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1

    tail = sql[start:].strip()
    if tail:
        statements.append(tail)

    return statements


def _sql_tokens(sql: str) -> list[str]:
    return re.findall(r"[a-z_][a-z0-9_]*", _mask_sql_literals_and_comments(sql).lower())


def _statement_access(statement: str) -> str:
    tokens = _sql_tokens(statement)
    if not tokens:
        raise DatabaseSafetyError("SQL query is empty")

    first = tokens[0]
    if first == "pragma":
        pragma_name = tokens[1] if len(tokens) > 1 else ""
        if "=" in _mask_sql_literals_and_comments(statement) or pragma_name not in READ_ONLY_PRAGMAS:
            raise DatabaseSafetyError(f"PRAGMA {pragma_name or '<unknown>'} is not allowed")
        return "read_only"

    if first in READ_ONLY_VERBS:
        mutating = MUTATING_TOKENS & set(tokens)
        if mutating:
            raise DatabaseSafetyError(
                "Read-only SQL cannot contain mutating/control keywords: "
                + ", ".join(sorted(mutating))
            )
        return "read_only"

    if first in DML_VERBS:
        return "write"

    if first in DDL_VERBS:
        return "admin"

    if first in UNSUPPORTED_CONTROL_VERBS:
        raise DatabaseSafetyError(f"SQL command {first.upper()} is not supported by this tool")

    raise DatabaseSafetyError(f"SQL command {first.upper()} is not allowed")


def _validate_sql_policy(query: str, access_mode: str, policy: DatabaseToolPolicyConfig) -> str:
    if access_mode not in {"read_only", "write", "admin"}:
        raise DatabaseSafetyError("access_mode must be read_only, write, or admin")

    statements = _split_sql_statements(query)
    if not statements:
        raise DatabaseSafetyError("SQL query is empty")
    if len(statements) > 1 and not policy.allow_multiple_statements:
        raise DatabaseSafetyError("Multiple SQL statements are disabled")

    required_modes = [_statement_access(statement) for statement in statements]
    required = "read_only"
    if "admin" in required_modes:
        required = "admin"
    elif "write" in required_modes:
        required = "write"

    if required == "read_only":
        return required
    if required == "write":
        if access_mode not in {"write", "admin"}:
            raise DatabaseSafetyError("Write SQL requires access_mode='write'")
        if not policy.allow_write:
            raise DatabaseSafetyError("Write SQL is disabled by tool policy")
        return required

    if access_mode != "admin":
        raise DatabaseSafetyError("DDL/admin SQL requires access_mode='admin'")
    if not policy.allow_ddl:
        raise DatabaseSafetyError("DDL/admin SQL is disabled by tool policy")
    return required


def _resolve_connection_string(arguments: dict, policy: DatabaseToolPolicyConfig) -> str:
    data_source = str(arguments.get("data_source") or "").strip()
    if data_source:
        connection_string = policy.data_sources.get(data_source)
        if not connection_string:
            raise DatabaseSafetyError(f"Unknown data_source: {data_source}")
        resolved = resolve_env_value(connection_string).strip()
        if not resolved:
            raise DatabaseSafetyError(f"data_source {data_source} resolved to an empty connection string")
        return resolved

    connection_string = str(arguments.get("connection_string") or "").strip()
    if not connection_string:
        raise DatabaseSafetyError("connection_string or data_source is required")
    if not policy.allow_raw_connection_strings:
        raise DatabaseSafetyError("Raw connection strings are disabled; use a configured data_source")
    resolved = resolve_env_value(connection_string).strip()
    if not resolved:
        raise DatabaseSafetyError("connection_string resolved to an empty value")
    return resolved


def _bounded_max_rows(arguments: dict, policy: DatabaseToolPolicyConfig) -> int:
    requested = arguments.get("max_rows")
    if requested is None:
        return policy.max_result_rows
    try:
        return max(1, min(int(requested), policy.max_result_rows))
    except (TypeError, ValueError) as exc:
        raise DatabaseSafetyError("max_rows must be a positive integer") from exc


class DatabaseQueryTool(BaseTool):
    """通用数据库查询工具。"""

    @property
    def name(self) -> str:
        return "database_query"

    @property
    def description(self) -> str:
        return (
            "执行数据库查询并返回结果。默认只允许只读 SQL；写入需显式 access_mode='write'，"
            "DDL 需配置允许并使用 access_mode='admin'。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "connection_string": {
                    "type": "string",
                    "description": "SQLAlchemy 连接字符串 (例如 'sqlite:///test.db', 'postgresql://user:pass@localhost/db')"
                },
                "data_source": {
                    "type": "string",
                    "description": "配置中的数据源名称；提供后优先于 connection_string"
                },
                "query": {
                    "type": "string",
                    "description": "要执行的 SQL 查询语句"
                },
                "access_mode": {
                    "type": "string",
                    "enum": ["read_only", "write", "admin"],
                    "description": "执行权限模式，默认取配置 tool_policy.database.default_access_mode"
                },
                "parameters": {
                    "type": "object",
                    "description": "查询参数 (可选)"
                },
                "max_rows": {
                    "type": "integer",
                    "description": "最多返回的行数，上限由 tool_policy.database.max_result_rows 限制"
                }
            },
            "required": ["query"]
        }

    async def execute(self, arguments: dict) -> Any:
        policy = _database_policy()
        query = str(arguments["query"])
        parameters = arguments.get("parameters") or {}
        access_mode = str(arguments.get("access_mode") or policy.default_access_mode)

        def _execute():
            connection_string = _resolve_connection_string(arguments, policy)
            statement_access = _validate_sql_policy(query, access_mode, policy)
            max_rows = _bounded_max_rows(arguments, policy)

            engine = create_engine(connection_string)
            with engine.connect() as conn:
                result = conn.execute(text(query), parameters)
                if result.returns_rows:
                    rows = result.fetchmany(max_rows + 1)
                    truncated = len(rows) > max_rows
                    rows = rows[:max_rows]
                    keys = list(result.keys())
                    return {
                        "columns": keys,
                        "rows": [list(row) for row in rows],
                        "row_count": len(rows),
                        "truncated": truncated,
                        "access": statement_access,
                    }

                conn.commit()
                return {"row_count": result.rowcount, "status": "success", "access": statement_access}

        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, _execute)
            return json.dumps(res, ensure_ascii=False, indent=2)
        except DatabaseSafetyError as e:
            return f"Database query rejected: {e}"
        except Exception as e:
            return f"Database query failed: {e}"
