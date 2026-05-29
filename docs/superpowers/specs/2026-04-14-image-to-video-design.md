# 图生视频 (Image-to-Video) 能力设计

- 日期: 2026-04-14
- 状态: Phase 2 已完成，设计作为历史与运维参考保留 (2026-05-29)
- 模型: `wan2.7-i2v` (阿里云 DashScope Bailian)
- 参考:
  - https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference
  - https://www.alibabacloud.com/help/en/model-studio/get-temporary-file-url
  - https://www.alibabacloud.com/help/en/model-studio/wan-video-editing-api-reference

## 1. 目标

让用户在 MemoX 中把"对话里的图像"转换成视频。两条触发路径并存：

- **UI 路径**：聊天消息中任一图片旁显示"生成视频"按钮，用户手动选 prompt 和参数。
- **LLM 路径**：coordinator LLM 识别用户自然语言意图（如"把刚才那张图变成视频"），在输出中插入 `[[I2V: <image_url> | <prompt>]]` 标记，后端在流式处理时识别并执行。

图像来源覆盖三类（后端层）：LLM 生成图、用户上传图、知识库文档中的图。聊天图片入口和知识库文档预览入口已接入；批量 I2V 与视频编辑先以后端 API 形式提供，后续可再做专门的创作工作台 UI。

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
  GET /api/files/{name}   —— Bearer/HMAC 短链访问 data/uploads/ 下图片
  DashScope uploads API  —— 私网/本地文件自动转临时 oss:// URL
```

## 3. 配置

`config.yaml` 新增独立段（与 `video_generation` 并列）：

```yaml
image_to_video:
  enabled: true
  provider: "dashscope"
  model: "wan2.7-i2v"
  edit_model: "wan2.7-videoedit"
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
    "input": {
      "prompt": "<prompt>",
      "media": [{"type": "first_frame", "url": "<image_url>"}]
    },
    "parameters": {"resolution": "720P", "duration": 5}
  }
  ```
  `negative_prompt` 若给，放入 `input`。老模型仍兼容旧 `input.img_url` 形态。
- 本地上传文件通过 `upload_file()` 先调用 DashScope 临时文件上传接口获得 `oss://...`，提交生成/编辑任务时带 `X-DashScope-OssResourceResolve: enable`。
- 支持扩展媒体字段：`last_frame_url`、`driving_audio_url`、`first_clip_url`，以及 `prompt_extend`、`watermark`、`seed`。
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
    last_frame_url: str | None = None
    driving_audio_url: str | None = None
    first_clip_url: str | None = None

@app.post("/api/videos/i2v")
async def generate_i2v(req: I2VRequest) -> dict: ...

@app.post("/api/videos/i2v/batch")
async def generate_i2v_batch(req: I2VBatchRequest) -> dict: ...

@app.post("/api/videos/edit")
async def edit_video(req: VideoEditRequest) -> dict: ...
```

单条 I2V 返回 `{url, prompt, image_url, input_mode}`。其中 `input_mode=url` 表示直接 URL 提交，`input_mode=dashscope_upload` 表示后端已把本地上传文件转为 DashScope 临时 OSS URL。

批量 I2V 最多 8 个素材，逐项返回 `ok/url/error`，单项失败不会中断整批，便于前端呈现部分成功结果。

视频编辑复用同一个 client，支持公网视频 URL、本地上传视频兜底、最多 3 张参考图、分辨率/比例/时长/负面提示词等参数。

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

- 白名单目录 `data/uploads/`；拒绝 `..`、绝对路径和路径穿越。
- 需要 Bearer Token 或短期 HMAC 签名 URL；文件访问不再加入公开路径。
- 返回 `FileResponse`。

### 4.5 私网部署兜底

若 MemoX 部署在私网、DashScope 无法拉取 `/api/files/...`，后端在 `/api/videos/i2v` 中识别 `/api/files/{name}` 或上传文件名，读取 `data/uploads/` 下的文件并上传到 DashScope 临时 OSS，再提交 i2v 任务。

### 4.6 非 chat 图像入口

`GET /api/documents/{doc_id}/media-assets` 从知识库 chunk metadata 和正文中提取图片素材：

- 本地图片文档：返回 `/api/files/{name}` 的短期签名 URL（未配置签名密钥时返回 Bearer 访问 URL）。
- 远程图片：提取 Markdown 图片和图片扩展名 URL。

前端文档预览页会在可用图片下展示“生成视频”按钮，复用 `<I2VModal>`。

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

知识库文档预览中展示可生成视频的图片缩略图，点击“生成视频”打开 `<I2VModal>`，生成结果回填到当前文档预览区。

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
  - mock httpx 验证 submit body 中 `model=wan2.7-i2v`、`input.media`、`parameters.duration/resolution` 正确
  - 验证老模型仍使用 `input.img_url`
  - 验证本地文件上传到 DashScope 临时 OSS 后带 `X-DashScope-OssResourceResolve`
  - 验证 `wan2.7-videoedit` 视频编辑 body
  - 模拟轮询：PENDING → RUNNING → SUCCEEDED，断言返回 `video_url`
  - 模拟 FAILED 抛 RuntimeError
- `tests/test_i2v_api.py`
  - 打桩 `get_i2v_client`，验证 `/api/videos/i2v` 参数透传与错误码映射
  - 503 分支（未初始化）
  - 本地 `/api/files/{name}` 自动走 `generate_from_file`
  - `/api/videos/i2v/batch` 部分失败返回
  - `/api/videos/edit` 参数透传
  - `/api/documents/{doc_id}/media-assets` 本地/远程素材提取
- `tests/test_chat_i2v_marker.py`
  - 输入含 `[[I2V: http://x/a.png | 缓慢推进]]` 的 LLM 输出，验证事件顺序 `i2v_pending` → `i2v`
  - 非法 URL 仅发 `i2v_error`

## 8. Phase 2 状态

已完成：HMAC/Bearer 文件访问、本地文件 DashScope 上传兜底、知识库非 chat 图片入口、批量 I2V、视频编辑、Wan2.7 `input.media` 协议升级。

后续可继续增强：批量任务后台化与进度推送、前端批量选择 UI、视频编辑专用前端面板、生成资产持久化归档。

## 9. 实施顺序建议

1. 配置 + dataclass 扩展
2. `DashScopeImageToVideoClient` + 测试
3. 生命周期初始化 + `/api/videos/i2v` + 测试
4. 静态文件路由 `/api/files/{name}`
5. SSE 流 `[[I2V:]]` 解析 + 事件 + 测试
6. 前端：按钮 + Modal + 结果回填
7. 端到端手动验证（用一张生成图测试 UI 路径，用 coordinator 对话测试 LLM 路径）
