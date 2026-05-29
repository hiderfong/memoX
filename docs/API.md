# MemoX API

MemoX 后端基于 FastAPI，默认服务地址为 `http://localhost:8080`。交互式文档可在后端启动后访问：

- Swagger UI: `http://localhost:8080/api/docs`
- ReDoc: `http://localhost:8080/api/redoc`
- OpenAPI JSON: `http://localhost:8080/api/openapi.json`

除公开路径外，接口默认需要携带 Bearer Token：

```http
Authorization: Bearer <token>
```

公开路径由 `config.yaml` 的 `auth.public_paths` 控制，默认包含 `/api/auth/login`、`/api/health`、`/api/docs`、`/api/redoc` 和 `/api/openapi.json`。上传文件路径不应加入公开路径；外部服务需要拉取文件时，使用短期签名 URL。

## Auth

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 用户登录，返回 Bearer Token |
| `POST` | `/api/auth/logout` | 注销当前 Token |
| `GET` | `/api/auth/me` | 获取当前用户信息 |

## Chat 与会话记忆

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 非流式聊天，支持 RAG 和指定 Worker |
| `POST` | `/api/chat/stream` | SSE 流式聊天 |
| `GET` | `/api/chat/sessions` | 列出会话 |
| `PATCH` | `/api/chat/sessions/{session_id}` | 更新会话标题或归档状态 |
| `DELETE` | `/api/chat/sessions/{session_id}` | 删除会话 |
| `GET` | `/api/chat/sessions/{session_id}/messages` | 获取会话消息 |
| `POST` | `/api/chat/sessions/{session_id}/compress` | 压缩会话上下文 |
| `GET` | `/api/chat/sessions/{session_id}/memory` | 获取会话摘要记忆 |
| `POST` | `/api/chat/sessions/{session_id}/memory` | 更新会话摘要记忆 |
| `DELETE` | `/api/chat/sessions/{session_id}/memory` | 清空会话记忆 |
| `POST` | `/api/chat/sessions/{session_id}/extract-memories` | 从会话抽取跨会话记忆 |
| `POST` | `/api/chat/sessions/{session_id}/summarize-task` | 将会话总结成任务描述 |
| `GET` | `/api/memory/config` | 获取记忆配置 |
| `PATCH` | `/api/memory/config` | 更新记忆配置 |

## 跨会话记忆

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/memories` | 列出跨会话记忆 |
| `POST` | `/api/memories` | 创建跨会话记忆 |
| `GET` | `/api/memories/search` | 搜索跨会话记忆 |
| `GET` | `/api/memories/{memory_id}` | 获取单条记忆 |
| `PATCH` | `/api/memories/{memory_id}` | 更新记忆 |
| `DELETE` | `/api/memories/{memory_id}` | 删除记忆 |

## 文档与分组

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/documents` | 列出文档 |
| `POST` | `/api/documents` | 上传文档，支持 `group_id` 表单字段 |
| `POST` | `/api/documents/url` | 抓取网页并导入知识库 |
| `DELETE` | `/api/documents/{doc_id}` | 删除文档，仅管理员 |
| `GET` | `/api/documents/{doc_id}/chunks` | 获取文档分块 |
| `GET` | `/api/documents/{doc_id}/media-assets` | 获取文档预览中可用的图片资产，不泄露本地文件路径 |
| `PUT` | `/api/documents/{doc_id}/group` | 移动文档到指定分组，仅管理员 |
| `GET` | `/api/documents/search` | 搜索文档，支持 `q` 和 `group_ids` |
| `GET` | `/api/knowledge/graph` | 获取知识图谱探索 payload，支持 `entity`、`q`、`depth`、`limit`、`min_confidence`、`predicate` 筛选，返回节点、关系、统计、核心实体、关系 facets 与来源 chunk |
| `GET` | `/api/knowledge/graph/quality` | 获取知识图谱质量审核候选，支持 `confidence_threshold`、`limit` 和 `status`；返回候选内容指纹、过期旧决策标记、身份冲突与来源簇分歧拆分建议，以及 `quality_metrics` 抽取质量指标、`quality_gate` 门禁结果、导入触发追踪和阈值告警，并将告警状态去重写入 `knowledge_graph_quality_alert` 运维事件；门禁失败或健康分明显下降时同步生成 `knowledge_graph_governance_task` 治理事件，治理操作恢复后写入 resolved 事件 |
| `GET` | `/api/knowledge/graph/quality/history` | 获取最近知识图谱质量指标快照，支持 `limit`，用于趋势监控 |
| `POST` | `/api/knowledge/graph/quality/decisions` | 保存知识图谱审核候选决策，仅管理员；建议在 `details.candidate_fingerprint` 写入候选指纹，避免旧决策压住内容已变化的候选 |
| `POST` | `/api/knowledge/graph/quality/decisions/batch` | 批量保存知识图谱审核候选决策，仅管理员，单次最多 100 个候选 |
| `POST` | `/api/knowledge/graph/entities/merge` | 合并重复实体，仅管理员 |
| `POST` | `/api/knowledge/graph/entities/split` | 按选定关系将实体的一部分证据拆到新实体，仅管理员，单次最多 100 条关系 |
| `PUT` | `/api/knowledge/graph/triples` | 修正单条知识图谱关系，仅管理员 |
| `POST` | `/api/knowledge/graph/triples/delete` | 删除单条知识图谱关系，仅管理员 |
| `GET` | `/api/groups` | 列出文档分组 |
| `POST` | `/api/groups` | 创建文档分组 |
| `PUT` | `/api/groups/{group_id}` | 更新分组，仅管理员 |
| `DELETE` | `/api/groups/{group_id}` | 删除分组，仅管理员 |

## 任务与 Worker

Worker 创建、更新和删除接口会持久化修改 `config.yaml` 中的 `worker_templates`。生产环境建议限制管理员权限，并在变更前备份配置文件。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 列出任务 |
| `POST` | `/api/tasks` | 提交后台执行任务，立即返回可轮询的任务记录 |
| `GET` | `/api/tasks/running` | 列出运行中任务 |
| `GET` | `/api/tasks/{task_id}` | 获取任务详情 |
| `GET` | `/api/tasks/{task_id}/files` | 获取任务 shared 目录文件 |
| `GET` | `/api/tasks/{task_id}/events` | 获取任务生命周期事件、子任务进度、失败原因和租约状态 |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消运行中任务 |
| `POST` | `/api/tasks/{task_id}/retry` | 将可重试失败任务重新入队，或手动恢复无活跃租约的未完成任务 |
| `POST` | `/api/tasks/{task_id}/feedback` | 提交 Human-in-the-Loop 反馈 |
| `GET` | `/api/workers` | 列出 Worker |
| `POST` | `/api/workers` | 创建 Worker，仅管理员 |
| `GET` | `/api/workers/{worker_id}/logs` | 获取 Worker 日志 |
| `DELETE` | `/api/workers/{worker_id}/logs` | 清空 Worker 日志，仅管理员 |
| `PUT` | `/api/workers/{worker_id}/config` | 更新 Worker 配置，仅管理员 |
| `DELETE` | `/api/workers/{worker_id}` | 删除 Worker，仅管理员 |
| `GET` | `/api/providers` | 列出 Provider 配置摘要、模型、服务端密钥状态与引用位置 |

后台任务失败事件会在 `details.failure_type` 中标记原因：`user_cancelled` 表示用户取消，`timeout` 和 `retryable_exception` 可自动重试，`non_retryable_exception` 需要人工排查，`lease_lost` 表示当前执行器丢失租约并已停止本地执行以避免重复写入。自动重试会记录 `auto_retry_scheduled`、`auto_retry_queued`、`auto_retry_exhausted` 事件；任务详情的 `job.auto_retry_count` 和 `job.next_retry_at` 可用于展示重试次数和下一次重试时间。

## Skills、工作流与定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/skills` | 列出已安装技能 |
| `GET` | `/api/skills/search` | 搜索技能 |
| `POST` | `/api/skills/install` | 安装技能，仅管理员 |
| `DELETE` | `/api/skills/{name}` | 卸载技能，仅管理员 |
| `POST` | `/api/skills/rebuild-embeddings` | 重建技能 embedding |
| `GET` | `/api/skills/log` | 获取技能操作日志 |
| `GET` | `/api/skills/contested` | 查看冲突或候选技能 |
| `GET` | `/api/skills/tags` | 获取技能标签 |
| `POST` | `/api/skills/lint` | 校验技能注册表 |
| `POST` | `/api/workflows/validate` | 校验 workflow YAML |
| `POST` | `/api/workflows/run` | 运行 workflow |
| `GET` | `/api/workflows/runs` | 列出 workflow 运行记录 |
| `GET` | `/api/workflows/runs/{run_id}` | 获取 workflow 运行详情 |
| `POST` | `/api/workflows/runs/{run_id}/pause` | 暂停 workflow |
| `POST` | `/api/workflows/runs/{run_id}/resume` | 恢复 workflow |
| `DELETE` | `/api/workflows/runs/{run_id}` | 删除 workflow 运行记录 |
| `GET` | `/api/scheduled-tasks` | 列出定时任务 |
| `POST` | `/api/scheduled-tasks` | 创建定时任务，仅管理员 |
| `PATCH` | `/api/scheduled-tasks/{task_id}` | 更新定时任务，仅管理员 |
| `DELETE` | `/api/scheduled-tasks/{task_id}` | 删除定时任务，仅管理员 |

## 媒体、系统与实时通信

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/images/generate` | 文生图 |
| `POST` | `/api/videos/generate` | 文生视频 |
| `POST` | `/api/videos/i2v` | 图生视频 |
| `POST` | `/api/videos/i2v/jobs` | 提交单条图生视频后台任务，立即返回媒体资产记录 |
| `POST` | `/api/videos/i2v/batch` | 批量图生视频，逐项返回成功或错误 |
| `POST` | `/api/videos/i2v/batch/jobs` | 批量提交图生视频后台任务，返回每条 queued 媒体资产 |
| `POST` | `/api/videos/edit` | 视频编辑，支持视频 URL、提示词和参考图片 |
| `POST` | `/api/videos/edit/jobs` | 提交视频编辑后台任务，立即返回媒体资产记录 |
| `GET` | `/api/videos/assets` | 查询媒体作品库，支持 `kind`、`status`、`operation`、`limit` |
| `GET` | `/api/videos/assets/{asset_id}` | 获取单条媒体资产状态，用于轮询后台任务结果 |
| `POST` | `/api/videos/assets/{asset_id}/retry` | 复用原素材、Prompt 和参数重试失败的媒体任务 |
| `DELETE` | `/api/videos/assets/{asset_id}` | 删除媒体资产记录，不删除远端生成文件 |
| `GET` | `/api/videos/jobs/status` | 查看媒体后台队列运行槽位、等待数和持久化 queued/running 数 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/system/health` | 管理员系统巡检：配置、索引、SQLite、磁盘，并返回最近备份、后台任务、知识图谱质量告警、质量门禁与最近导入触发追踪等运维状态 |
| `GET` | `/api/system/backups` | 管理员查看本地备份归档 |
| `GET` | `/api/system/events` | 管理员查看运维事件，支持 `event_type`、`status`、`limit`、`offset` 过滤与分页，返回 `total` 与事件详情；图谱质量告警事件类型为 `knowledge_graph_quality_alert`，图谱治理任务事件类型为 `knowledge_graph_governance_task` |
| `GET` | `/api/system/tool-audit` | 管理员查看工具调用审计，支持 `tool_name`、`status`、`worker_id`、`task_id`、`limit`、`offset` 过滤与分页，返回脱敏参数摘要、结果预览和状态汇总 |
| `GET/PUT` | `/api/system/tool-policy` | 管理员查看、保存网络、Web 抓取、浏览器爬虫和数据库工具权限策略；数据源连接串会脱敏显示，保存脱敏占位时保留原始值 |
| `GET` | `/api/system/diagnostics/export` | 管理员导出 zip 诊断包，包含健康报告、备份清单、运维事件、索引一致性、脱敏配置与日志尾部；设置 `ops.archive_mirror_dir` 后会同步镜像一份 |
| `POST` | `/api/system/indexes/repair` | 管理员修复 Chroma / BM25 / manifest 索引一致性，可用 `collection` 查询参数指定集合 |
| `POST` | `/api/system/backups/{name}/verify` | 管理员校验单个备份归档 |
| `POST` | `/api/system/backups/{name}/restore-preflight` | 管理员预检恢复覆盖风险 |
| `POST` | `/api/system/backups/{name}/restore` | 管理员强确认后执行真实恢复；请求体需提供 `confirm_archive_name`、`acknowledge_overwrite`、`acknowledge_maintenance_mode` |
| `POST` | `/api/system/backups/{name}/restore-drill` | 管理员执行临时目录恢复演练 |
| `POST` | `/api/system/maintenance/backup` | 管理员手动触发备份维护；设置 `ops.archive_mirror_dir` 后会将归档镜像到外部目录 |
| `POST` | `/api/system/maintenance/lifecycle` | 管理员执行保守生命周期清理；默认 `dry_run=true` 只预检，`dry_run=false` 清理过期运维事件、审计日志和诊断包，不删除聊天、记忆、上传或工作区文件 |
| `POST` | `/api/files/sign` | 已登录用户为上传目录中的单个文件生成短期签名 URL |
| `GET` | `/api/files/{name}` | 访问上传目录中的单个文件，需要 Bearer Token 或短期签名参数 |
| `WS` | `/ws` | WebSocket 实时通信，支持聊天和任务进度消息 |

`POST /api/files/sign` 请求体：

```json
{
  "name": "example.png",
  "ttl_seconds": 300
}
```

响应包含绝对 URL 和过期时间戳：

```json
{
  "url": "https://memox.example.com/api/files/example.png?expires=...&signature=...",
  "expires": 1760000000
}
```

签名功能依赖 `file_access.signing_secret`，推荐在环境变量 `MEMOX_FILE_SIGNING_SECRET` 中配置长随机值。
