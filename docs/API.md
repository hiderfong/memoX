# MemoX API

MemoX 后端基于 FastAPI，默认服务地址为 `http://localhost:8080`。交互式文档可在后端启动后访问：

- Swagger UI: `http://localhost:8080/api/docs`
- ReDoc: `http://localhost:8080/api/redoc`
- OpenAPI JSON: `http://localhost:8080/api/openapi.json`

除公开路径外，接口默认需要携带 Bearer Token：

```http
Authorization: Bearer <token>
```

公开路径由 `config.yaml` 的 `auth.public_paths` 控制，默认包含 `/api/auth/login`、`/api/health`、`/api/docs`、`/api/redoc`、`/api/openapi.json` 和 `/api/files/`。

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
| `PUT` | `/api/documents/{doc_id}/group` | 移动文档到指定分组，仅管理员 |
| `GET` | `/api/documents/search` | 搜索文档，支持 `q` 和 `group_ids` |
| `GET` | `/api/groups` | 列出文档分组 |
| `POST` | `/api/groups` | 创建文档分组 |
| `PUT` | `/api/groups/{group_id}` | 更新分组，仅管理员 |
| `DELETE` | `/api/groups/{group_id}` | 删除分组，仅管理员 |

## 任务与 Worker

Worker 创建、更新和删除接口会持久化修改 `config.yaml` 中的 `worker_templates`。生产环境建议限制管理员权限，并在变更前备份配置文件。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks` | 列出任务 |
| `POST` | `/api/tasks` | 创建并执行任务 |
| `GET` | `/api/tasks/running` | 列出运行中任务 |
| `GET` | `/api/tasks/{task_id}` | 获取任务详情 |
| `GET` | `/api/tasks/{task_id}/files` | 获取任务 shared 目录文件 |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消运行中任务 |
| `POST` | `/api/tasks/{task_id}/feedback` | 提交 Human-in-the-Loop 反馈 |
| `GET` | `/api/workers` | 列出 Worker |
| `POST` | `/api/workers` | 创建 Worker，仅管理员 |
| `GET` | `/api/workers/{worker_id}/logs` | 获取 Worker 日志 |
| `DELETE` | `/api/workers/{worker_id}/logs` | 清空 Worker 日志，仅管理员 |
| `PUT` | `/api/workers/{worker_id}/config` | 更新 Worker 配置，仅管理员 |
| `DELETE` | `/api/workers/{worker_id}` | 删除 Worker，仅管理员 |
| `GET` | `/api/providers` | 列出 Provider 配置摘要、模型、服务端密钥状态与引用位置 |

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
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/files/{name}` | 暴露上传目录中的单个文件 |
| `WS` | `/ws` | WebSocket 实时通信，支持聊天和任务进度消息 |
