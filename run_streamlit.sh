#!/bin/bash
# 启动 MemoX Streamlit 管理界面
# 默认连接 localhost:8080 的主服务
#
# 用法:
#   ./run_streamlit.sh           # 默认 8501 端口
#   ./run_streamlit.sh 8502     # 指定端口

PORT=${1:-8501}

cd "$(dirname "$0")/.."
uv run streamlit run src/ui/streamlit_app.py --server.port "$PORT" --server.headless true
