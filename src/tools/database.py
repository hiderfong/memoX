import asyncio
from typing import Any

from sqlalchemy import create_engine, text

from src.agents.base_agent import BaseTool


class DatabaseQueryTool(BaseTool):
    """通用数据库查询工具。"""

    @property
    def name(self) -> str:
        return "database_query"

    @property
    def description(self) -> str:
        return "执行 SQL 查询并返回结果。支持 PostgreSQL, SQLite 等主流关系型数据库。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "connection_string": {
                    "type": "string",
                    "description": "SQLAlchemy 连接字符串 (例如 'sqlite:///test.db', 'postgresql://user:pass@localhost/db')"
                },
                "query": {
                    "type": "string",
                    "description": "要执行的 SQL 查询语句"
                },
                "parameters": {
                    "type": "object",
                    "description": "查询参数 (可选)"
                }
            },
            "required": ["connection_string", "query"]
        }

    async def execute(self, arguments: dict) -> Any:
        connection_string = arguments["connection_string"]
        query = arguments["query"]
        parameters = arguments.get("parameters") or {}

        def _execute():
            engine = create_engine(connection_string)
            with engine.connect() as conn:
                result = conn.execute(text(query), parameters)
                # 判断是否有返回结果（比如 SELECT 语句）
                if result.returns_rows:
                    rows = result.fetchall()
                    keys = list(result.keys())
                    return {
                        "columns": keys,
                        "rows": [list(row) for row in rows],
                        "row_count": len(rows)
                    }
                else:
                    conn.commit()
                    return {"row_count": result.rowcount, "status": "success"}

        try:
            import json
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, _execute)
            return json.dumps(res, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Database query failed: {e}"
