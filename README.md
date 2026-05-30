# MemoX

> 一个多智能体协作知识管理平台，支持会话管理、文档解析、RAG 检索、定时任务调度和多 Worker 并行协作。

## 核心特性

| 特性 | 说明 |
|------|------|
| 🎯 **任务执行 + 优化建议** | Agent 不仅完成任务，还主动分析并提供优化方案 |
| 🔀 **多 Agent 并行** | 复杂任务拆分为多个子任务，多个独立 Agent 并行执行 |
| ⚙️ **独立 Agent 配置** | 每个 Agent 可配置不同的大模型、API Key、技能、MCP |
| 📂 **知识库 RAG** | 文档上传 → 解析 → 向量存储 → 智能问答 |
| 🎬 **媒体创作工作台** | 聊天图片/知识库图片 → I2V 生成，支持本地素材上传兜底、批量后台任务、视频编辑、作品库、重试和队列治理 |
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
│   ├── ui/                     # Streamlit 旧版/诊断界面
│   └── web/                    # Web 服务
│       └── api.py              # FastAPI 服务
├── frontend_wip/               # React 主 Web UI（目录名待重命名）
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

# 前端依赖（React 主界面开发/构建）
cd frontend_wip && npm ci
```

### 2. 配置

仓库提供了 `config.example.yaml` 和 `.env.example` 作为可公开模板。首次启动前至少设置管理员密码；如果使用默认 DashScope 配置，还需要设置 `DASHSCOPE_API_KEY`。如需让外部服务短时拉取本地上传文件，还需要设置 `MEMOX_FILE_SIGNING_SECRET`。

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

file_access:
  signing_secret: "${MEMOX_FILE_SIGNING_SECRET:-}"
  signed_url_ttl_seconds: 300

knowledge_base:
  enable_graph: false
  graph_llm_provider: "dashscope"
  graph_llm_model: "qwen-turbo"
  graph_llm_api_key: "${DASHSCOPE_API_KEY}"

image_to_video:
  enabled: false
  model: "wan2.7-i2v"
  edit_model: "wan2.7-videoedit"
  api_key: "${DASHSCOPE_API_KEY}"
```

`auth.enabled=true` 时，启动会拒绝空密码；如果 `MEMOX_ADMIN_PASSWORD` 未设置，后端会直接报出配置错误。

### 3. 启动

```bash
# 启动后端 (终端 1)
uv run memox

# 启动 React 主 UI (终端 2)
cd frontend_wip
npm run dev
```

旧版 Streamlit 诊断/兼容界面仍保留用于轻量排障，不作为主 UI：

```bash
uv run --extra ui streamlit run src/ui/streamlit_app.py
```

### 4. 冒烟验证

```bash
# 仅验证临时后端
uv run --extra dev python scripts/smoke_test.py

# 验证临时后端 + Vite 前端代理
uv run --extra dev python scripts/smoke_test.py --frontend

# 可选：运行真实浏览器管理后台 E2E（需要 frontend_wip/node_modules 和 Playwright Chromium）
MEMOX_BROWSER_E2E=1 uv run --extra dev pytest tests/e2e/test_admin_ui_browser_flow.py
```

冒烟脚本会使用临时数据目录和确定性的本地 embedding 替身，不需要真实模型 API Key；它会覆盖登录、文档检索、系统健康、备份清单、索引修复、诊断包导出、运维事件、备份校验、恢复预检、真实恢复拒绝闸门和临时恢复演练。`--frontend` 模式要求已执行过 `cd frontend_wip && npm ci`。
浏览器 E2E 会启动临时后端和 Vite 前端，覆盖管理后台登录、设置页 Web 工具策略保存、策略持久化校验、系统状态页工具调用审计和移动宽度回归检查。

### 5. 常用检查

```bash
uv run --extra dev ruff check src tests scripts
uv run --extra dev python -m compileall -q src tests scripts
uv run --extra dev pytest tests --ignore=tests/e2e
cd frontend_wip && npm run build
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
uv run --extra dev python scripts/backup_restore.py prune --keep 14 --dry-run
```

也可以执行一次完整恢复演练，确认备份恢复后服务仍能启动并检索已恢复文档：

```bash
uv run --extra dev python scripts/restore_drill.py
```

排查检索异常时可以巡检 Chroma、BM25 和 manifest 的一致性：

```bash
uv run --extra dev python scripts/index_consistency.py
```

日常运维可以跑一键巡检，默认会读取配置、检查持久化路径、审计索引一致性、SQLite、磁盘空间并校验最近一次备份；备份新鲜度和本地归档数量阈值默认跟随 `ops.auto_backup_interval_hours` 与 `ops.max_backups`，也可以用命令行参数覆盖：

```bash
uv run --extra dev python scripts/ops_check.py
```

服务启动后还会按 `ops.auto_backup_*` 配置执行后台备份维护：默认每 24 小时备份 `config.yaml`、`data/`、`workspace/`，并保留最近 14 个本地归档；最近一次自动维护结果会显示在管理员系统状态页，管理员也可以在该页面手动触发一次即时备份。设置 `ops.archive_mirror_dir` 后，自动/手动备份和诊断包会额外镜像到该目录下的 `backups/` 与 `diagnostics/`，适合指向挂载盘或宿主机外部同步目录。后台备份不包含主机上的 `.env`；升级、迁移或外部归档前仍建议使用上面的 CLI 备份命令做一次完整校验。

真实用户长期运行前，建议先按 [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) 完成发布前清单，再按 [docs/RECOVERY_RUNBOOK.md](docs/RECOVERY_RUNBOOK.md) 做一次恢复演练；它把诊断导出、备份选择、预检、API 恢复、离线恢复、索引修复和恢复后验收串成了维护窗口操作顺序。

Docker 镜像默认不安装 `sentence-transformers`/Streamlit 这类重依赖。需要本地 embedding 时使用 `uv sync --extra local-embeddings`，需要旧的 Streamlit 诊断/兼容界面时使用 `uv run --extra ui streamlit run src/ui/streamlit_app.py`。

## API 接口

完整接口清单见 [docs/API.md](docs/API.md)，后端启动后也可以访问 `http://localhost:8080/api/docs` 查看 Swagger UI。

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 登录并获取 Bearer Token |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/system/health` | 管理员系统巡检 |
| `GET` | `/api/system/backups` | 管理员查看本地备份归档 |
| `GET` | `/api/system/events` | 管理员分页查看、筛选运维事件与操作详情 |
| `GET` | `/api/system/tool-audit` | 管理员分页查看、筛选工具调用审计 |
| `GET/PUT` | `/api/system/tool-policy` | 管理员查看、保存网络/数据库工具权限策略 |
| `GET` | `/api/system/diagnostics/export` | 管理员导出 zip 诊断包 |
| `POST` | `/api/system/indexes/repair` | 管理员修复 Chroma / BM25 / manifest 索引一致性 |
| `POST` | `/api/system/backups/{name}/verify` | 管理员校验单个备份归档 |
| `POST` | `/api/system/backups/{name}/restore-preflight` | 管理员预检恢复覆盖风险 |
| `POST` | `/api/system/backups/{name}/restore` | 管理员强确认后执行真实恢复，恢复前自动创建安全备份 |
| `POST` | `/api/system/backups/{name}/restore-drill` | 管理员执行临时目录恢复演练 |
| `POST` | `/api/system/maintenance/backup` | 管理员手动触发备份维护 |
| `POST` | `/api/system/maintenance/lifecycle` | 管理员预检或执行保守生命周期清理 |
| `GET` | `/api/documents` | 列出文档 |
| `POST` | `/api/documents` | 上传文档 |
| `GET` | `/api/documents/search` | 搜索文档 |
| `POST` | `/api/chat` | 聊天问答 |
| `POST` | `/api/chat/stream` | 流式聊天 |
| `POST` | `/api/tasks` | 创建并执行任务 |
| `GET` | `/api/tasks` | 列出任务 |
| `GET` | `/api/workers` | Worker 状态 |
| `POST` | `/api/workflows/validate` | 校验 workflow YAML |
| `WS` | `/ws` | 任务事件通知 |

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
