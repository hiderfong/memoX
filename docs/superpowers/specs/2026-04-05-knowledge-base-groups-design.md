# 知识库分组功能设计

**日期**: 2026-04-05  
**状态**: 已确认

---

## 背景

MemoX 当前知识库为扁平结构，所有文档混合存储在同一个 ChromaDB collection 中。用户在执行任务或智能问答时无法按需选择部分文档集合，导致不相关文档干扰检索结果。

本功能为知识库引入分组机制，让用户在对话和任务执行时可选择激活哪些分组。

---

## 设计约束

- 每篇文档属于且仅属于一个分组
- 历史文档自动归入内置"未分组"分组
- 删除分组时，其下文档自动回到"未分组"
- 默认激活全部分组（行为与现在一致）

---

## 数据模型

### 分组定义（`data/groups.json`）

```json
[
  {
    "id": "ungrouped",
    "name": "未分组",
    "color": "#999999",
    "created_at": "2026-04-05T00:00:00"
  },
  {
    "id": "abc123",
    "name": "技术文档",
    "color": "#1890ff",
    "created_at": "2026-04-05T12:00:00"
  }
]
```

- `"ungrouped"` 是内置保留分组，不可删除、不可改名
- `id` 使用 8 位 UUID hex
- `color` 用于前端分组标签显示

### 文档分组归属

在 ChromaDB chunk metadata 中新增 `group_id` 字段（字符串）。

- 新上传文档：`group_id` 默认为 `"ungrouped"`，可在上传时指定
- 历史文档（无 `group_id` 字段）：读取时在应用层视为 `"ungrouped"`
- 移动文档分组：批量更新该文档所有 chunk 的 `group_id` metadata

---

## 后端实现

### 新增：`src/knowledge/group_store.py`

负责管理 `data/groups.json` 的读写：

```python
class GroupStore:
    def list_groups() -> list[KnowledgeGroup]
    def get_group(id) -> KnowledgeGroup | None
    def create_group(name, color) -> KnowledgeGroup
    def update_group(id, name, color) -> KnowledgeGroup
    def delete_group(id) -> None  # 不可删 "ungrouped"
```

### 修改：`src/knowledge/rag_engine.py`

- `add_document()` 新增 `group_id: str = "ungrouped"` 参数
- `list_documents()` 返回的 `DocumentInfo` 新增 `group_id` 字段
- `search()` 新增 `group_ids: list[str] | None = None` 参数；非 None 时追加 ChromaDB filter: `{"group_id": {"$in": group_ids}}`
- 新增 `move_document_group(doc_id, new_group_id)` 方法，批量更新所有 chunk 的 metadata

### 修改：`src/knowledge/vector_store.py`

新增 `update_metadata_by_doc_id(doc_id, metadata_patch)` 方法，用于批量更新 chunk metadata。

### 新增 API 端点（`src/web/api.py`）

```
GET    /api/groups
POST   /api/groups          body: {name, color}
PUT    /api/groups/{id}     body: {name?, color?}
DELETE /api/groups/{id}     → 将该组文档 group_id 改为 "ungrouped"

PUT    /api/documents/{id}/group   body: {group_id}
```

### 修改现有端点

| 端点 | 变更 |
|------|------|
| `POST /api/documents` | form-data 新增可选 `group_id` 字段 |
| `GET /api/documents` | 响应增加 `group_id` 字段 |
| `POST /api/chat` | 请求体新增 `active_group_ids: list[str] \| null`（null=全部） |
| `POST /api/tasks` | 请求体新增 `active_group_ids: list[str] \| null` |

---

## 前端实现

### 知识库页面（DocumentsPage）

- 顶部新增分组标签栏（"全部" + 各分组），点击过滤文档列表
- 文档卡片显示所属分组的彩色 Tag
- 文档 Actions 增加"移动到分组"下拉菜单
- 右上角增加"管理分组"按钮，打开 Drawer：
  - 列出所有分组（含文档数）
  - 支持新建、重命名（含改色）、删除（非"未分组"）

### 智能问答页面（ChatPage）

- 输入区上方增加分组多选器（Checkbox.Group），默认全选
- 发送请求时携带 `active_group_ids`（全选时传 null）

### 任务执行页面（TasksPage）

- 任务输入框上方增加相同的分组多选器
- 执行任务时携带 `active_group_ids`

---

## 兼容性

- 现有历史文档的 chunk metadata 不需要迁移，应用层将无 `group_id` 字段的文档视为 `"ungrouped"`
- 搜索时若历史文档 chunk 无 `group_id` 字段，ChromaDB `$in` filter 不会匹配它们——需特殊处理：当 `active_group_ids` 包含 `"ungrouped"` 时，同时对无 `group_id` 的 chunk 放开过滤（可通过不传 filter 而改用 doc_ids 白名单实现）

**解决方案**：移动文档分组时顺带为无 `group_id` 的历史文档写入 `"ungrouped"`；启动时执行一次迁移，为所有无 `group_id` chunk 补写该字段。

---

## 文件变更清单

**新建**：
- `src/knowledge/group_store.py`

**修改**：
- `src/knowledge/rag_engine.py` — DocumentInfo、search、add_document、新增 move_document_group
- `src/knowledge/vector_store.py` — 新增 update_metadata_by_doc_id
- `src/web/api.py` — 新增分组 API、修改文档/聊天/任务端点
- `frontend/src/App.tsx` — DocumentsPage、ChatPage、TasksPage

**数据**：
- `data/groups.json` — 启动时若不存在则自动创建（含默认"未分组"分组）
