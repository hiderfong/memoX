# 图生视频 (Image-to-Video) 能力设计

- 日期: 2026-04-14
- 模型: `wan2.7-i2v` (阿里云 DashScope Bailian)
- 参考: https://bailian.console.aliyun.com/cn-beijing/?tab=api#/api/?type=model&url=3025059

## 1. 目标

让用户在 MemoX 中把"对话里的图像"转换成视频。两条触发路径并存：

- **UI 路径**：聊天消息中任一图片旁显示"生成视频"按钮，用户手动选 prompt 和参数。
- **LLM 路径**：coordinator LLM 识别用户自然语言意图（如"把刚才那张图变成视频"），在输出中插入 `[[I2V: <image_url> | <prompt>]]` 标记，后端在流式处理时识别并执行。

图像来源覆盖三类（后端层）：LLM 生成图、用户上传图、知识库文档中的图。前端触发入口本期仅覆盖 chat 中显示的图像（生成 + 上传），其他为 Phase 2。

## 2. 架构

```
┌─────────────────┐   ┌──────────────────┐   ┌─────────────────────────┐
│ 前端 (App.tsx)  │──▶│ /api/videos/i2v  │──▶│ DashScopeI2VClient      │
│ - 图片右上角 🎬 │   │ (FastAPI)        │   │ (异步 submit + 轮询)    │
│ - I2VModal      │◀──│                  │◀──│                         │
└─────────────────┘   └──────────────────┘   └─────────────────────────┘
         ▲                     ▲                        │
         │                     │                        ▼
         │              ┌──────┴───────┐       DashScope video-synthesis
         │              │ /api/chat/   │         (model=wan2.7-i2v)
         │              │  stream SSE  │
         │              │  解析 [[I2V]]│
         └──────────────┤ 事件         │
                        └──────────────┘

辅助:
  GET /api/files/{name}   —— 暴露 data/uploads/ 下图片供 DashScope 公网拉取
```

## 3. 配置

`config.yaml` 新增独立段（与 `video_generation` 并列）：

```yaml
image_to_video:
  enabled: true
  provider: "dashscope"
  model: "wan2.7-i2v"
  api_key: "${DASHSCOPE_API_KEY}"
  default_resolution: "720P"
  default_duration: 5
```

`src/config/__init__.py` 的 Config dataclass 增加对应字段并解析。

## 4. 后端组件

### 4.1 `src/imaging/i2v_client.py`

```python
class DashScopeImageToVideoClient:
    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    TASK_URL   = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    async def generate(
        self,
        image_url: str,
        prompt: str,
        resolution: str | None = None,
        duration: int | None = None,
        negative_prompt: str | None = None,
    ) -> str: ...
```

- Submit body:
  ```json
  {
    "model": "wan2.7-i2v",
    "input": {"img_url": "<image_url>", "prompt": "<prompt>"},
    "parameters": {"resolution": "720P", "duration": 5}
  }
  ```
  `negative_prompt` 若给，放入 `input`。
- Header: `X-DashScope-Async: enable`，`Authorization: Bearer ${api_key}`。
- 轮询 `task_id` 直到 `SUCCEEDED` 取 `output.video_url`；`FAILED/CANCELED/UNKNOWN` 抛 `RuntimeError`；默认 600s 超时。
- 单例模式：`init_i2v_client`、`get_i2v_client`，从 `src/imaging/__init__.py` 导出。启动时在 `src/web/api.py` 生命周期里依据 `image_to_video.enabled` 初始化。

### 4.2 `/api/videos/i2v` 端点

```python
class I2VRequest(BaseModel):
    image_url: str
    prompt: str
    resolution: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None

@app.post("/api/videos/i2v")
async def generate_i2v(req: I2VRequest) -> dict:
    # 503 未启用；500 生成失败；200 返回 {url, prompt, image_url}
```

### 4.3 聊天流式处理器 `[[I2V:]]` 标记

在现有 `[[VIDEO:]]` 抽取逻辑之后加一段：

```python
_I2V_RE = re.compile(r"\[\[I2V:\s*(.+?)\s*\|\s*(.+?)\]\]", flags=re.DOTALL)
i2v_matches = _I2V_RE.findall(raw_text)
```

对每条匹配：
1. 校验 image_url 以 `http://`/`https://` 开头，prompt 非空 → 否则发 `i2v_error`。
2. 发 `i2v_pending` 事件（含 `image_url`、`prompt`）。
3. 调 `i2v_client.generate(image_url, prompt)`（LLM 路径仅用默认参数）。
4. 成功 → 发 `i2v` 事件 `{type, url, prompt, source_image_url}`；失败 → `i2v_error`。

前端可继续把 `i2v` 当 video 渲染（复用 `msg.videos` 列表），仅多存一个 `source_image_url` 字段。

### 4.4 静态图片路由 `GET /api/files/{name}`

- 白名单目录 `data/uploads/`；拒绝 `..` 和绝对路径；按扩展名给 `Content-Type`。
- 加入 `auth.public_paths` 以便 DashScope 拉取（本期明确为开发/公网部署假设，生产应替换为短时 HMAC 签名 token，Phase 2）。
- 返回 `FileResponse`。

### 4.5 私网部署兜底（Phase 2，不在本期实现）

若 MemoX 部署在私网、DashScope 无法拉取 `/api/files/...`，UI 路径改为：先上传图片到 DashScope 文件服务拿临时 URL，再提交 i2v 任务。设计文档记录但本期不实现。

## 5. 前端交互

### 5.1 图片旁按钮

`App.tsx` 中渲染 `msg.images` 的地方：每张图包裹一层带 hover 的容器，右上角显示 🎬 按钮（Ant Design `Tooltip + Button`）。点击调用 `openI2VModal(imageUrl)`。

### 5.2 `<I2VModal>`

Ant Design `Modal`，字段：

- 缩略图预览（只读）
- `Input.TextArea` **prompt**（必填，placeholder "描述画面中的运动/变化…"）
- `Select` **duration**: 3 / 5 / 8 秒（默认 5）
- `Select` **resolution**: 480P / 720P / 1080P（默认 720P）
- `Collapse`"高级" → `Input.TextArea` **negative_prompt**

提交：

```ts
await fetch('/api/videos/i2v', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
  body: JSON.stringify({image_url, prompt, duration, resolution, negative_prompt})
})
```

- loading 状态显示在 Modal 内（预期 30–120s）
- 成功：关闭 Modal，把 `{url, prompt}` 追加到当前会话最后一条 assistant 消息的 `videos` 数组（或新建一条系统消息，设计选后者以避免追加到历史消息语义混乱）
- 失败：`message.error(...)`

### 5.3 非 chat 图像入口

Phase 2。知识库文档预览、上传管理页等暂不加入口。

## 6. 错误处理

| 场景                         | 行为                                  |
|------------------------------|---------------------------------------|
| `image_to_video.enabled=false` 或无 API Key | API 返回 503                          |
| DashScope submit 4xx/5xx      | 冒泡成 500，中文错误消息              |
| 轮询得 `FAILED/CANCELED`      | RuntimeError → 500                    |
| 轮询超过 600s                 | TimeoutError → 504                    |
| LLM 标记 image_url 非 http(s) / prompt 空 | 跳过并发 `i2v_error` 事件         |
| 静态路由请求文件不存在        | 404                                   |

## 7. 测试

- `tests/test_i2v_client.py`
  - mock httpx 验证 submit body 中 `model=wan2.7-i2v`、`input.img_url`、`parameters.duration/resolution` 正确
  - 模拟轮询：PENDING → RUNNING → SUCCEEDED，断言返回 `video_url`
  - 模拟 FAILED 抛 RuntimeError
- `tests/test_i2v_api.py`
  - 打桩 `get_i2v_client`，验证 `/api/videos/i2v` 参数透传与错误码映射
  - 503 分支（未初始化）
- `tests/test_chat_i2v_marker.py`
  - 输入含 `[[I2V: http://x/a.png | 缓慢推进]]` 的 LLM 输出，验证事件顺序 `i2v_pending` → `i2v`
  - 非法 URL 仅发 `i2v_error`

## 8. 不在范围内（明确排除）

- Phase 2 / 未来：HMAC 签名静态路由、DashScope 文件上传兜底、非 chat 图片入口、批量 i2v、视频编辑。
- 不改动 `video_generation` (t2v) 现有行为。

## 9. 实施顺序建议

1. 配置 + dataclass 扩展
2. `DashScopeImageToVideoClient` + 测试
3. 生命周期初始化 + `/api/videos/i2v` + 测试
4. 静态文件路由 `/api/files/{name}`
5. SSE 流 `[[I2V:]]` 解析 + 事件 + 测试
6. 前端：按钮 + Modal + 结果回填
7. 端到端手动验证（用一张生成图测试 UI 路径，用 coordinator 对话测试 LLM 路径）
