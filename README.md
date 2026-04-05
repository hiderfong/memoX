# MemoX

> 基于 Claude Code 架构 + nanobot 框架 + NotebookLM 理念的多 Agent 智能助手

## 核心特性

| 特性 | 说明 |
|------|------|
| 🎯 **任务执行 + 优化建议** | Agent 不仅完成任务，还主动分析并提供优化方案 |
| 🔀 **多 Agent 并行** | 复杂任务拆分为多个子任务，多个独立 Agent 并行执行 |
| ⚙️ **独立 Agent 配置** | 每个 Agent 可配置不同的大模型、API Key、技能、MCP |
| 📂 **知识库 RAG** | 文档上传 → 解析 → 向量存储 → 智能问答 |
| 🌐 **跨平台 Web UI** | 响应式设计，支持桌面和移动端 |

## 项目结构

```
notebook-lm-pro/
├── src/
│   ├── main.py                 # 入口文件
│   ├── config/                 # 配置模块
│   ├── coordinator/             # 任务规划器
│   ├── agents/                 # Agent 模块
│   │   ├── base_agent.py       # 基础 Agent
│   │   └── worker_pool.py      # Worker 池
│   ├── knowledge/              # 知识库模块
│   │   ├── document_parser.py  # 文档解析
│   │   ├── vector_store.py     # 向量存储
│   │   └── rag_engine.py       # RAG 引擎
│   ├── tools/                  # 工具模块
│   └── web/                    # Web 服务
│       └── api.py              # FastAPI 服务
├── frontend/                   # React 前端
├── config.yaml                # 配置文件
└── requirements.txt           # Python 依赖
```

## 快速开始

### 1. 安装依赖

```bash
# Python 依赖
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install
```

### 2. 配置

编辑 `config.yaml`：

```yaml
providers:
  anthropic:
    api_key: "${ANTHROPIC_API_KEY}"  # 或直接填入 API Key
  openai:
    api_key: "${OPENAI_API_KEY}"

coordinator:
  model: "claude-sonnet-4-20250514"
  max_workers: 5
```

### 3. 启动

```bash
# 启动后端 (终端 1)
cd notebook-lm-pro
python -m src.main

# 启动前端 (终端 2)
cd frontend
npm run dev
```

访问 http://localhost:3000

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/documents` | 列出文档 |
| `POST` | `/api/documents` | 上传文档 |
| `DELETE` | `/api/documents/{id}` | 删除文档 |
| `POST` | `/api/chat` | 聊天问答 |
| `POST` | `/api/chat/stream` | 流式聊天 |
| `POST` | `/api/tasks` | 创建并执行任务 |
| `GET` | `/api/tasks` | 列出任务 |
| `GET` | `/api/workers` | Worker 状态 |
| `WS` | `/ws` | WebSocket |

## 架构说明

### 多 Agent 并行

```
用户任务 → Coordinator 分析 → 任务分解 → Worker Pool 分配 → 并行执行 → 结果聚合
                                      ↓
                    ┌─────────────────┼─────────────────┐
                    ↓                 ↓                 ↓
                Worker-1          Worker-2          Worker-N
              (Claude-3)         (GPT-4o)          (Gemini)
```

### 独立 Agent 配置

```yaml
worker_templates:
  code_worker:
    model: "claude-sonnet-4-20250514"
    provider: "anthropic"
    skills: ["code-review", "frontend-design-3"]
    tools: ["filesystem", "shell", "git"]
  
  research_worker:
    model: "gpt-4o"
    provider: "openai"
    skills: ["data-analysis"]
    tools: ["web_search", "web_fetch"]
```

## License

MIT
