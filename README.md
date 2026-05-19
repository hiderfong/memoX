# MemoX

> 一个多智能体协作知识管理平台，支持会话管理、文档解析、RAG 检索、定时任务调度和多 Worker 并行协作。

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
memoX/
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
├── scripts/                    # 开发、验证脚本
├── tests/                      # 后端测试
├── config.yaml                # 配置文件
├── config.example.yaml        # 可公开的配置模板
├── .env.example               # 环境变量模板
├── pyproject.toml             # Python 依赖与工具配置
└── uv.lock                    # Python 锁文件
```

## 快速开始

### 1. 安装依赖

```bash
# Python 依赖
uv sync --extra dev

# 前端依赖
cd frontend && npm ci
```

### 2. 配置

仓库提供了 `config.example.yaml` 和 `.env.example` 作为可公开模板。首次启动前至少设置管理员密码；如果使用默认 DashScope 配置，还需要设置 `DASHSCOPE_API_KEY`。

```bash
# 如需从模板重建本地配置
cp config.example.yaml config.yaml

# 填写后加载环境变量；也可以改用 direnv、shell profile 或部署平台环境变量
cp .env.example .env
set -a
source .env
set +a
```

关键配置项：

```yaml
providers:
  dashscope:
    api_key: "${DASHSCOPE_API_KEY}"

coordinator:
  provider: "dashscope"
  model: "qwen3.6-plus"
  max_workers: 5

auth:
  users:
    - username: "admin"
      password: "${MEMOX_ADMIN_PASSWORD}"
```

`auth.enabled=true` 时，启动会拒绝空密码；如果 `MEMOX_ADMIN_PASSWORD` 未设置，后端会直接报出配置错误。

### 3. 启动

```bash
# 启动后端 (终端 1)
uv run memox

# 启动前端 (终端 2)
cd frontend
npm run dev
```

访问 http://localhost:3000

### 4. 冒烟验证

```bash
# 仅验证临时后端
uv run --extra dev python scripts/smoke_test.py

# 验证临时后端 + Vite 前端代理
uv run --extra dev python scripts/smoke_test.py --frontend
```

冒烟脚本会使用临时数据目录和确定性的本地 embedding 替身，不需要真实模型 API Key；`--frontend` 模式要求已执行过 `cd frontend && npm ci`。

### 5. 常用检查

```bash
uv run --extra dev ruff check src tests scripts
uv run --extra dev python -m compileall -q src tests scripts
uv run --extra dev pytest tests --ignore=tests/e2e
cd frontend && npm run build
```

## 部署

当前提供单节点 Docker Compose 部署入口，适合长期试用和小团队内网部署：

```bash
cp .env.example .env
cp config.example.yaml config.yaml
docker compose up -d --build
```

部署会把 `config.yaml`、`data/` 和 `workspace/` 挂载为持久化数据。更多说明见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

部署前可以先跑离线容器冒烟检查：

```bash
uv run --extra dev python scripts/docker_smoke_test.py
```

升级或迁移前创建并校验备份：

```bash
uv run --extra dev python scripts/backup_restore.py create
uv run --extra dev python scripts/backup_restore.py verify backups/<backup-file>.tar.gz
```

也可以执行一次完整恢复演练，确认备份恢复后服务仍能启动并检索已恢复文档：

```bash
uv run --extra dev python scripts/restore_drill.py
```

排查检索异常时可以巡检 Chroma、BM25 和 manifest 的一致性：

```bash
uv run --extra dev python scripts/index_consistency.py
```

日常运维可以跑一键巡检，默认会读取配置、检查持久化路径、审计索引一致性并校验最近一次备份：

```bash
uv run --extra dev python scripts/ops_check.py
```

Docker 镜像默认不安装 `sentence-transformers`/Streamlit 这类重依赖。需要本地 embedding 时使用 `uv sync --extra local-embeddings`，需要旧的 Streamlit 管理界面时使用 `uv run --extra ui streamlit run src/ui/streamlit_app.py`。

## API 接口

完整接口清单见 [docs/API.md](docs/API.md)，后端启动后也可以访问 `http://localhost:8080/api/docs` 查看 Swagger UI。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 登录并获取 Bearer Token |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/documents` | 列出文档 |
| `POST` | `/api/documents` | 上传文档 |
| `GET` | `/api/documents/search` | 搜索文档 |
| `POST` | `/api/chat` | 聊天问答 |
| `POST` | `/api/chat/stream` | 流式聊天 |
| `POST` | `/api/tasks` | 创建并执行任务 |
| `GET` | `/api/tasks` | 列出任务 |
| `GET` | `/api/workers` | Worker 状态 |
| `POST` | `/api/workflows/validate` | 校验 workflow YAML |
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
