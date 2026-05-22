import json
from unittest.mock import patch, MagicMock

import pytest

from src.tools.database import DatabaseQueryTool


@pytest.fixture
def db_tool():
    return DatabaseQueryTool()


def test_db_tool_properties(db_tool):
    assert db_tool.name == "database_query"
    assert "执行 SQL 查询" in db_tool.description
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
    mock_result.fetchall.return_value = [(1, "Alice"), (2, "Bob")]
    
    result_json = await db_tool.execute({
        "connection_string": "sqlite:///:memory:",
        "query": "SELECT * FROM users"
    })
    
    result = json.loads(result_json)
    assert result["columns"] == ["id", "name"]
    assert len(result["rows"]) == 2
    assert result["row_count"] == 2


@pytest.mark.asyncio
@patch("src.tools.database.create_engine")
async def test_db_execute_update(mock_create_engine, db_tool):
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
        "query": "UPDATE users SET name='Alice' WHERE id=1"
    })
    
    result = json.loads(result_json)
    assert result["status"] == "success"
    assert result["row_count"] == 1
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