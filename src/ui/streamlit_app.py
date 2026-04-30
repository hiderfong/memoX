"""
MemoX Streamlit 管理界面
========================
快速验证用轻量管理界面（开发/测试阶段使用）。

运行方式：
    streamlit run src/ui/streamlit_app.py
    # 或
    uv run streamlit run src/ui/streamlit_app.py

注意：Streamlit 和主 FastAPI 服务使用不同端口，
默认 Streamlit 8501，主服务 8080。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

# ==================== 配置 ====================

API_BASE = "http://localhost:8080/api"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin123"

st.set_page_config(
    page_title="MemoX Admin",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ==================== 工具函数 ====================

@st.cache_data(ttl=30)
def fetch_json(url: str, params: dict | None = None) -> dict | list | None:
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"请求失败: {e}")
        return None


def post_json(url: str, data: dict | None = None, files: dict | None = None) -> dict | None:
    try:
        if files:
            r = requests.post(url, data=data, files=files, timeout=60)
        else:
            r = requests.post(url, json=data, timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"请求失败: {e}")
        return None


def delete_json(url: str) -> bool:
    try:
        r = requests.delete(url, timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        st.error(f"删除失败: {e}")
        return False


def format_size(bytes_num: int) -> str:
    if bytes_num < 1024:
        return f"{bytes_num} B"
    if bytes_num < 1024 * 1024:
        return f"{bytes_num / 1024:.1f} KB"
    return f"{bytes_num / (1024 * 1024):.1f} MB"


def ensure_auth():
    """确保已登录，若无 token 则自动用默认凭据登录。"""
    if "token" not in st.session_state:
        st.session_state.token = None
    if st.session_state.token is None:
        try:
            r = requests.post(
                f"{API_BASE}/auth/login",
                json={"username": DEFAULT_USER, "password": DEFAULT_PASSWORD},
                timeout=5,
            )
            r.raise_for_status()
            st.session_state.token = r.json()["access_token"]
        except Exception:
            st.warning("未连接 MemoX 后端服务（8080），部分功能不可用")
            st.session_state.token = None


def api_headers() -> dict:
    h = {}
    if st.session_state.get("token"):
        h["Authorization"] = f"Bearer {st.session_state.token}"
    return h


def api_get(url: str, params: dict | None = None) -> dict | list | None:
    return fetch_json(url, params)


def api_post(url: str, data: dict | None = None) -> dict | None:
    try:
        r = requests.post(url, json=data, headers=api_headers(), timeout=60)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"请求失败: {e}")
        return None


def api_delete(url: str) -> bool:
    return delete_json(url)


# ==================== 页面定义 ====================

def page_knowledge_base():
    """知识库管理：文档列表 + 上传 + 搜索测试"""
    st.title("📚 知识库管理")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("文档列表")
        docs = api_get(f"{API_BASE}/documents")
        if docs is None:
            st.info("无法连接到后端服务")
            return

        if not docs:
            st.info("暂无文档，请上传文件")
        else:
            for doc in docs:
                with st.container():
                    c1, c2, c3 = st.columns([4, 1, 1])
                    c1.markdown(f"**{doc.get('filename', '未知')}**")
                    c1.caption(
                        f"ID: `{doc.get('id', '-')[:8]}...` "
                        f"| 大小: {format_size(doc.get('size', 0))} "
                        f"| chunk: {doc.get('chunk_count', '?')}"
                    )
                    if c2.button("查看 Chunk", key=f"chunks_{doc['id']}"):
                        st.session_state[f"view_chunks_{doc['id']}"] = True
                    if c3.button("🗑️", key=f"del_{doc['id']}"):
                        if api_delete(f"{API_BASE}/documents/{doc['id']}"):
                            st.rerun()
                    st.divider()

    with col2:
        st.subheader("上传文档")
        uploaded = st.file_uploader(
            "选择文件", type=None, accept_multiple_files=False, key="doc_uploader"
        )
        if uploaded and st.button("上传", type="primary"):
            with st.spinner("上传并解析中..."):
                files = {"file": (uploaded.name, uploaded.getvalue())}
                data = {}
                result = post_json(f"{API_BASE}/documents", data=data, files=files)
                if result:
                    st.success(f"上传成功: {uploaded.name}")
                    time.sleep(1)
                    st.rerun()

        st.subheader("导入 URL")
        url_input = st.text_input("网页 URL", placeholder="https://...")
        if url_input and st.button("抓取网页"):
            with st.spinner("抓取中..."):
                result = api_post(f"{API_BASE}/documents/url", {"url": url_input})
                if result:
                    st.success("网页导入成功")
                    st.rerun()


def page_rag_search():
    """RAG 检索测试：输入 query，看检索结果和来源"""
    st.title("🔍 RAG 检索测试")

    query = st.text_input("输入搜索 query", placeholder="例如：深度学习的核心技术是什么？", label_visibility="collapsed")
    run_search = st.button("搜索", type="primary")

    if run_search and query:
        with st.spinner("检索中..."):
            result = api_post(f"{API_BASE}/documents/search", {"query": query, "top_k": 5})
            if result:
                results = result.get("results", [])
                if not results:
                    st.info("未找到相关结果")
                else:
                    st.success(f"找到 {len(results)} 个相关 chunk")
                    for i, item in enumerate(results):
                        with st.container():
                            st.markdown(f"**结果 {i + 1}**")
                            col_a, col_b = st.columns([3, 1])
                            col_a.markdown(item.get("content", "")[:500] + ("..." if len(item.get("content", "")) > 500 else ""))
                            meta = item.get("metadata", {})
                            col_b.caption(f"doc: `{meta.get('doc_id', '-')[:8]}...`")
                            col_b.caption(f"chunk: {meta.get('chunk_index', '-')}")
                            score = item.get("score", 0)
                            col_b.progress(min(score, 1.0) if score else 0, text=f"score: {score:.4f}" if score else "N/A")
                            st.divider()


def page_workers():
    """Worker 状态监控面板"""
    st.title("🤖 Agent 监控")

    with st.spinner("加载中..."):
        workers = api_get(f"{API_BASE}/workers")
        providers = api_get(f"{API_BASE}/providers")
        tasks_running = api_get(f"{API_BASE}/tasks/running")

    if workers is None:
        st.error("无法连接到后端服务")
        return

    # 状态概览
    if workers:
        online = sum(1 for w in workers if w.get("status") == "online")
        st.metric("在线 Worker", online, total := len(workers))
    if tasks_running is not None:
        st.metric("运行中任务", len(tasks_running) if isinstance(tasks_running, list) else 0)

    # Provider 状态
    if providers:
        st.subheader("Provider 状态")
        for p in providers:
            status_icon = "🟢" if p.get("available") else "🔴"
            st.markdown(f"{status_icon} **{p.get('name', p.get('provider', '?'))}** — {p.get('status', 'unknown')}")
        st.divider()

    # Worker 详情
    if workers:
        st.subheader("Worker 详情")
        for w in workers:
            status = w.get("status", "unknown")
            icon = "🟢" if status == "online" else "🔴" if status == "offline" else "🟡"
            with st.expander(f"{icon} {w.get('name', w.get('id', '?'))} — {status}"):
                col1, col2 = st.columns(2)
                col1.markdown(f"**ID**: `{w.get('id', '-')}`")
                col1.markdown(f"**模型**: {w.get('model', '-')}")
                col1.markdown(f"**Provider**: {w.get('provider', '-')}")
                col2.markdown(f"**任务数**: {w.get('task_count', 0)}")
                col2.markdown(f"**Skills**: {', '.join(w.get('skills', [])) or '无'}")
                # Token 消耗（若有）
                token_info = w.get("token_usage_today") or w.get("total_tokens")
                if token_info:
                    col2.metric("Token 消耗（今日）", f"{token_info:,}")
    else:
        st.info("暂无 Worker 信息")


def page_memory():
    """跨会话记忆管理：查看 / 添加 / 删除记忆"""
    st.title("🧠 记忆管理")

    tab_view, tab_add, tab_search = st.tabs(["记忆列表", "添加记忆", "搜索记忆"])

    # Tab: 列表
    with tab_view:
        user_id = st.text_input("用户 ID（留空表示所有）", value="", key="mem_user_filter")
        category = st.selectbox("类别", ["all", "preference", "fact", "todo", "context"], key="mem_cat_filter")
        params = {}
        if user_id:
            params["user_id"] = user_id
        if category != "all":
            params["category"] = category

        memories = api_get(f"{API_BASE}/memories", params)
        if memories is None:
            st.info("暂无记忆，或无法连接后端")
        elif not memories:
            st.info("没有找到记忆")
        else:
            st.success(f"共 {len(memories)} 条记忆")
            for m in memories:
                mem_id = m.get("id", "-")
                imp = m.get("importance", 0)
                pinned = "📌" if m.get("is_pinned") else "  "
                imp_bar = "⭐" * int(imp * 5) if imp else "—"
                with st.container():
                    c1, c2, c3 = st.columns([5, 1, 1])
                    c1.markdown(f"{pinned} **{m.get('content', '')[:80]}**")
                    c1.caption(
                        f"分类: `{m.get('category', '-')}` | "
                        f"重要度: {imp_bar} ({imp:.1f}) | "
                        f"访问: {m.get('access_count', 0)}次 | "
                        f"{m.get('created_at', '')[:10]}"
                    )
                    if c2.button("详情", key=f"mem_view_{mem_id}"):
                        st.info(m.get("content", ""))
                    if c3.button("🗑️", key=f"mem_del_{mem_id}"):
                        if api_delete(f"{API_BASE}/memories/{mem_id}"):
                            st.rerun()
                    st.divider()

    # Tab: 添加
    with tab_add:
        with st.form("add_memory_form"):
            content = st.text_area("记忆内容", placeholder="输入要记住的信息...", height=100)
            new_user_id = st.text_input("用户 ID", value="default")
            new_category = st.selectbox("类别", ["fact", "preference", "todo", "context"])
            new_importance = st.slider("重要程度", 0.0, 1.0, 0.5, 0.1)
            submitted = st.form_submit_button("添加记忆", type="primary")
            if submitted and content:
                result = api_post(f"{API_BASE}/memories", {
                    "content": content,
                    "user_id": new_user_id,
                    "category": new_category,
                    "importance": new_importance,
                })
                if result:
                    st.success("记忆已添加")
                    st.rerun()

    # Tab: 搜索
    with tab_search:
        q = st.text_input("搜索关键词", placeholder="输入关键词...")
        if q and st.button("搜索", type="primary"):
            results = api_get(f"{API_BASE}/memories/search", {"q": q})
            if results is not None:
                st.success(f"找到 {len(results)} 条相关记忆")
                for m in results:
                    with st.expander(m.get("content", "")[:80]):
                        st.markdown(m.get("content", ""))
                        st.caption(f"分类: `{m.get('category')}` | 重要度: {m.get('importance', 0):.1f}")


def page_chat():
    """简易对话界面（测试 RAG + Agent）"""
    st.title("💬 快速问答")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # 展示历史
    for msg in st.session_state.chat_history:
        if msg["role"] == "user":
            st.chat_message("user").markdown(msg["content"])
        else:
            st.chat_message("assistant").markdown(msg["content"])

    # 输入
    user_input = st.chat_input("输入问题...")
    if user_input:
        st.chat_message("user").markdown(user_input)
        st.session_state.chat_history.append({"role": "user", "content": user_input})

        with st.spinner("思考中..."):
            result = api_post(f"{API_BASE}/chat", {
                "message": user_input,
                "stream": False,
            })

        if result:
            answer = result.get("text", result.get("message", "（无回复）"))
            sources = result.get("sources", [])
            citations = result.get("citations", [])

            st.chat_message("assistant").markdown(answer)
            if sources:
                with st.expander("📄 引用来源"):
                    for s in sources:
                        st.markdown(f"- `{s.get('doc_id','')}` — {s.get('filename', '未知文件')}")
            if citations:
                with st.expander("🔗 引用列表"):
                    for c in citations:
                        st.markdown(f"- {c.get('content_preview', '')[:100]}")
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
        else:
            st.error("请求失败，请检查后端服务是否运行")

    if st.button("清空对话"):
        st.session_state.chat_history = []
        st.rerun()


# ==================== 主程序 ====================

def main():
    ensure_auth()

    st.sidebar.title("📚 MemoX")
    st.sidebar.markdown(f"连接: `{API_BASE}`")
    if st.session_state.get("token"):
        st.sidebar.success("已登录")
    else:
        st.sidebar.warning("未连接后端")

    page = st.sidebar.radio(
        "功能",
        [
            "📚 知识库",
            "🔍 RAG 检索",
            "🤖 Agent 监控",
            "🧠 记忆管理",
            "💬 快速问答",
        ],
    )

    {
        "📚 知识库": page_knowledge_base,
        "🔍 RAG 检索": page_rag_search,
        "🤖 Agent 监控": page_workers,
        "🧠 记忆管理": page_memory,
        "💬 快速问答": page_chat,
    }[page]()


if __name__ == "__main__":
    main()
