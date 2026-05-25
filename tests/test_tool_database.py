import json
from unittest.mock import MagicMock, patch

import pytest

from src.config import DatabaseToolPolicyConfig
from src.tools.database import DatabaseQueryTool


@pytest.fixture
def db_tool():
    return DatabaseQueryTool()


def test_db_tool_properties(db_tool):
    assert db_tool.name == "database_query"
    assert "执行数据库查询" in db_tool.description
    assert "connection_string" in db_tool.input_schema["properties"]


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_execute_select(mock_create_engine, db_tool):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_result = MagicMock()

    mock_create_engine.return_value = mock_engine
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = mock_result

    mock_result.returns_rows = True
    mock_result.keys.return_value = ["id", "name"]
    mock_result.fetchmany.return_value = [(1, "Alice"), (2, "Bob")]

    result_json = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "SELECT * FROM users"
    })

    result = json.loads(result_json)
    assert result["columns"] == ["id", "name"]
    assert len(result["rows"]) == 2
    assert result["row_count"] == 2
    assert result["access"] == "read_only"
    assert result["truncated"] is False
    mock_result.fetchmany.assert_called_once_with(201)


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_execute_update_requires_write_mode(mock_create_engine, db_tool):
    result = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "UPDATE users SET name='Alice' WHERE id=1"
    })

    assert "Database query rejected" in result
    assert "access_mode='write'" in result
    mock_create_engine.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_execute_update_with_write_mode(mock_create_engine, db_tool):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_result = MagicMock()

    mock_create_engine.return_value = mock_engine
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = mock_result

    mock_result.returns_rows = False
    mock_result.rowcount = 1

    result_json = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "UPDATE users SET name='Alice' WHERE id=1",
        "access_mode": "write",
    })

    result = json.loads(result_json)
    assert result["status"] == "success"
    assert result["row_count"] == 1
    assert result["access"] == "write"
    mock_conn.commit.assert_called_once()


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_execute_error(mock_create_engine, db_tool):
    mock_create_engine.side_effect = Exception("Connection failed")

    result = await db_tool.execute({
        "connection_string": "invalid_conn",
        "query": "SELECT * FROM users"
    })

    assert "Database query failed: Connection failed" in result


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_blocks_multi_statement_query(mock_create_engine, db_tool):
    result = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "SELECT * FROM users; DELETE FROM users",
        "access_mode": "write",
    })

    assert "Database query rejected" in result
    assert "Multiple SQL statements" in result
    mock_create_engine.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_rejects_invalid_access_mode(mock_create_engine, db_tool):
    result = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "SELECT 1",
        "access_mode": "owner",
    })

    assert "Database query rejected" in result
    assert "access_mode must be" in result
    mock_create_engine.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_blocks_mutating_keyword_inside_read_only_cte(mock_create_engine, db_tool):
    result = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "WITH changed AS (UPDATE users SET name='x' RETURNING id) SELECT * FROM changed",
    })

    assert "Database query rejected" in result
    assert "mutating/control keywords" in result
    mock_create_engine.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_uses_named_data_source_when_raw_strings_disabled(mock_create_engine, db_tool, monkeypatch):
    policy = DatabaseToolPolicyConfig(
        allow_raw_connection_strings=False,
        data_sources={"analytics": "sqlite:///:memory:"},
    )
    monkeypatch.setattr("src.tools.database._database_policy", lambda: policy)

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_result = MagicMock()

    mock_create_engine.return_value = mock_engine
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = mock_result

    mock_result.returns_rows = True
    mock_result.keys.return_value = ["count"]
    mock_result.fetchmany.return_value = [(3,)]

    result_json = await db_tool.execute({
        "data_source": "analytics",
        "query": "SELECT COUNT(*) FROM users",
    })

    result = json.loads(result_json)
    assert result["rows"] == [[3]]
    mock_create_engine.assert_called_once_with("sqlite:///:memory:")


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_rejects_raw_string_when_disabled(mock_create_engine, db_tool, monkeypatch):
    policy = DatabaseToolPolicyConfig(allow_raw_connection_strings=False)
    monkeypatch.setattr("src.tools.database._database_policy", lambda: policy)

    result = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "SELECT 1",
    })

    assert "Database query rejected" in result
    assert "Raw connection strings are disabled" in result
    mock_create_engine.assert_not_called()
