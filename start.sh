#!/bin/bash
# MemoX 启动脚本

cd "$(dirname "$0")"

# 添加 src 到 PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# 启动服务
echo "🚀 启动 MemoX..."
echo "   API: http://localhost:8080"
echo "   前端: http://localhost:3000"

uvicorn src.web.api:app --host 0.0.0.0 --port 8080 --reload
