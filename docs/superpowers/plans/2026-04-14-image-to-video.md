# 图生视频 (Image-to-Video) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户通过 UI 按钮或 coordinator LLM 的 `[[I2V:...]]` 标记，把对话中的图片转换为视频（DashScope `wan2.7-i2v`）。

**Architecture:** 新增异步 `DashScopeImageToVideoClient`（参考现有 t2v client）、`/api/videos/i2v` 端点、`GET /api/files/{name}` 静态路由、聊天 SSE 流的 `[[I2V:]]` 解析、前端 `<I2VModal>` 组件。UI 路径与 LLM 路径共享同一 client 与结果渲染（复用 `msg.videos`）。

**Tech Stack:** Python 3.11 / FastAPI / httpx / pytest / React 18 / Ant Design 5 / Vite

**Spec:** `docs/superpowers/specs/2026-04-14-image-to-video-design.md`

---

## File Structure

**Create:**
- `src/imaging/i2v_client.py` — DashScope i2v 异步客户端
- `tests/test_i2v_client.py` — client 单测
- `tests/test_i2v_api.py` — `/api/videos/i2v` 端点测试
- `tests/test_files_route.py` — `/api/files/{name}` 测试
- `tests/test_chat_i2v_marker.py` — `[[I2V:]]` 流解析测试
- `frontend/src/components/I2VModal.tsx` — i2v 弹窗组件

**Modify:**
- `config.yaml` — 新增 `image_to_video:` 段
- `src/config/__init__.py` — 新增 `ImageToVideoConfig` dataclass 与解析
- `src/imaging/__init__.py` — 导出新 client
- `src/web/api.py` — 生命周期初始化 / 端点 / 静态路由 / SSE 解析
- `src/auth.py`（若需要调 public_paths） — 或直接改 config
- `frontend/src/App.tsx` — 图片渲染处加按钮、导入 Modal、处理结果

---

## Task 1: 配置 dataclass 与 YAML

**Files:**
- Modify: `src/config/__init__.py`
- Modify: `config.yaml`

- [ ] **Step 1: 新增 `ImageToVideoConfig` dataclass**

在 `src/config/__init__.py` 的 `VideoGenerationConfig` 之后加入：

```python
@dataclass
class ImageToVideoConfig:
    """图生视频配置"""
    enabled: bool = False
    provider: str = "dashscope"
    model: str = "wan2.7-i2v"
    api_key: str = ""
    default_resolution: str = "720P"
    default_duration: int = 5

    def resolve_api_key(self) -> str:
        key = self.api_key
        if key.startswith("${") and key.endswith("}"):
            return os.getenv(key[2:-1], "")
        return key
```

- [ ] **Step 2: 挂到 `Config` dataclass**

在 `Config` 里加字段：

```python
image_to_video: ImageToVideoConfig = field(default_factory=ImageToVideoConfig)
```

在 `_from_dict` 中加载：

```python
image_to_video = ImageToVideoConfig(**data.get("image_to_video", {}))
```

并传入 `return cls(...)` 调用。

- [ ] **Step 3: `config.yaml` 新增段**

在 `video_generation:` 段之后插入：

```yaml
# 图生视频配置
image_to_video:
  enabled: true
  provider: "dashscope"
  model: "wan2.7-i2v"
  api_key: "${DASHSCOPE_API_KEY}"
  default_resolution: "720P"
  default_duration: 5
```

- [ ] **Step 4: 验证配置解析**

Run: `python -c "from src.config import load_config; c = load_config('config.yaml'); print(c.image_to_video)"`
Expected: 打印 `ImageToVideoConfig(enabled=True, ..., model='wan2.7-i2v', ...)`

- [ ] **Step 5: Commit**

```bash
git add src/config/__init__.py config.yaml
git commit -m "feat(config): add image_to_video config section"
```

---

## Task 2: `DashScopeImageToVideoClient` — 成功路径 TDD

**Files:**
- Create: `src/imaging/i2v_client.py`
- Create: `tests/test_i2v_client.py`

- [ ] **Step 1: 写第一个失败测试（submit body 正确）**

创建 `tests/test_i2v_client.py`：

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.imaging.i2v_client import DashScopeImageToVideoClient


@pytest.mark.asyncio
async def test_submit_body_shape():
    client = DashScopeImageToVideoClient(api_key="sk-test", model="wan2.7-i2v")

    submit_resp = MagicMock()
    submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {
        "output": {"task_status": "SUCCEEDED", "video_url": "https://cdn/x.mp4"}
    }

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=submit_resp)
        instance.get = AsyncMock(return_value=poll_resp)

        url = await client.generate(
            image_url="https://x/a.png",
            prompt="slow zoom",
            resolution="720P",
            duration=5,
        )

    assert url == "https://cdn/x.mp4"
    args, kwargs = instance.post.call_args
    body = kwargs["json"]
    assert body["model"] == "wan2.7-i2v"
    assert body["input"]["img_url"] == "https://x/a.png"
    assert body["input"]["prompt"] == "slow zoom"
    assert body["parameters"]["resolution"] == "720P"
    assert body["parameters"]["duration"] == 5
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["headers"]["X-DashScope-Async"] == "enable"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_i2v_client.py::test_submit_body_shape -v`
Expected: `ModuleNotFoundError: No module named 'src.imaging.i2v_client'`

- [ ] **Step 3: 最小实现**

创建 `src/imaging/i2v_client.py`：

```python
"""DashScope 图生视频客户端 (wan2.7-i2v, 异步任务模式).

Submit: POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable
Body:  {"model": "wan2.7-i2v",
        "input":  {"img_url": "...", "prompt": "..."},
        "parameters": {"resolution": "720P", "duration": 5}}
Poll:  GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger


class DashScopeImageToVideoClient:
    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(
        self,
        api_key: str,
        model: str = "wan2.7-i2v",
        default_resolution: str = "720P",
        default_duration: int = 5,
        poll_interval: float = 5.0,
        timeout_s: float = 600.0,
    ):
        self._api_key = api_key
        self._model = model
        self._default_resolution = default_resolution
        self._default_duration = default_duration
        self._poll_interval = poll_interval
        self._timeout_s = timeout_s

    async def generate(
        self,
        image_url: str,
        prompt: str,
        resolution: str | None = None,
        duration: int | None = None,
        negative_prompt: str | None = None,
    ) -> str:
        if not self._api_key:
            raise RuntimeError("图生视频未配置 API Key")

        submit_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        input_body: dict[str, Any] = {"img_url": image_url, "prompt": prompt}
        if negative_prompt:
            input_body["negative_prompt"] = negative_prompt
        parameters: dict[str, Any] = {
            "resolution": resolution or self._default_resolution,
            "duration": int(duration or self._default_duration),
        }
        body = {"model": self._model, "input": input_body, "parameters": parameters}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.SUBMIT_URL, headers=submit_headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            task_id = (data.get("output") or {}).get("task_id")
            if not task_id:
                raise RuntimeError(f"提交 i2v 任务失败: {data}")

            logger.info(f"[I2V] 任务已提交 task_id={task_id} model={self._model}")

            elapsed = 0.0
            poll_headers = {"Authorization": f"Bearer {self._api_key}"}
            while elapsed < self._timeout_s:
                await asyncio.sleep(self._poll_interval)
                elapsed += self._poll_interval
                r = await client.get(self.TASK_URL.format(task_id=task_id), headers=poll_headers)
                r.raise_for_status()
                d = r.json()
                output = d.get("output") or {}
                status = output.get("task_status")
                if status == "SUCCEEDED":
                    video_url = output.get("video_url")
                    if not video_url:
                        raise RuntimeError(f"响应缺少 video_url: {d}")
                    logger.info(f"[I2V] 任务完成 task_id={task_id}")
                    return video_url
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    raise RuntimeError(f"i2v 任务失败: {d}")

            raise TimeoutError(f"i2v 任务超时 task_id={task_id}")


_client: DashScopeImageToVideoClient | None = None


def init_i2v_client(api_key: str, model: str = "wan2.7-i2v", **kwargs: Any) -> DashScopeImageToVideoClient:
    global _client
    _client = DashScopeImageToVideoClient(api_key=api_key, model=model, **kwargs)
    logger.info(f"[I2V] 客户端已初始化: model={model}")
    return _client


def get_i2v_client() -> DashScopeImageToVideoClient | None:
    return _client
```

测试中的 `asyncio.sleep` 会卡住 5 秒——在测试代码头部加一行把 `poll_interval` 调小：

把 Step 1 中 `DashScopeImageToVideoClient(api_key="sk-test", model="wan2.7-i2v")` 改为：

```python
client = DashScopeImageToVideoClient(api_key="sk-test", model="wan2.7-i2v", poll_interval=0.01)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_i2v_client.py::test_submit_body_shape -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/imaging/i2v_client.py tests/test_i2v_client.py
git commit -m "feat(imaging): add DashScopeImageToVideoClient (wan2.7-i2v)"
```

---

## Task 3: Client 失败与超时路径

**Files:**
- Modify: `tests/test_i2v_client.py`

- [ ] **Step 1: 写失败路径测试**

追加：

```python
@pytest.mark.asyncio
async def test_failed_status_raises():
    client = DashScopeImageToVideoClient(api_key="sk-test", poll_interval=0.01)

    submit_resp = MagicMock()
    submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}

    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {"output": {"task_status": "FAILED", "message": "bad"}}

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=submit_resp)
        instance.get = AsyncMock(return_value=poll_resp)

        with pytest.raises(RuntimeError, match="i2v 任务失败"):
            await client.generate(image_url="https://x/a.png", prompt="p")


@pytest.mark.asyncio
async def test_missing_api_key_raises():
    client = DashScopeImageToVideoClient(api_key="")
    with pytest.raises(RuntimeError, match="未配置 API Key"):
        await client.generate(image_url="https://x/a.png", prompt="p")


@pytest.mark.asyncio
async def test_negative_prompt_passed_in_input():
    client = DashScopeImageToVideoClient(api_key="sk-test", poll_interval=0.01)
    submit_resp = MagicMock(); submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}
    poll_resp = MagicMock(); poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn/x.mp4"}}

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=submit_resp)
        instance.get = AsyncMock(return_value=poll_resp)
        await client.generate(image_url="https://x/a.png", prompt="p", negative_prompt="blur")

    body = instance.post.call_args.kwargs["json"]
    assert body["input"]["negative_prompt"] == "blur"
```

- [ ] **Step 2: 运行 3 个新测试**

Run: `pytest tests/test_i2v_client.py -v`
Expected: 全部 PASS（实现已覆盖）

- [ ] **Step 3: Commit**

```bash
git add tests/test_i2v_client.py
git commit -m "test(i2v): cover failed/missing-key/negative-prompt paths"
```

---

## Task 4: 在 `imaging` 包导出并挂入生命周期

**Files:**
- Modify: `src/imaging/__init__.py`
- Modify: `src/web/api.py`（lifecycle 段）

- [ ] **Step 1: 更新 `src/imaging/__init__.py`**

```python
"""文生图 / 文生视频 / 图生视频 服务"""

from .dashscope_client import DashScopeImageClient, init_image_client, get_image_client
from .video_client import DashScopeVideoClient, init_video_client, get_video_client
from .i2v_client import DashScopeImageToVideoClient, init_i2v_client, get_i2v_client

__all__ = [
    "DashScopeImageClient", "init_image_client", "get_image_client",
    "DashScopeVideoClient", "init_video_client", "get_video_client",
    "DashScopeImageToVideoClient", "init_i2v_client", "get_i2v_client",
]
```

- [ ] **Step 2: 在 `src/web/api.py` lifecycle 中初始化**

定位到 `# 初始化文生视频客户端` 块（约 line 355-365）之后插入：

```python
    # 初始化图生视频客户端
    i2v_cfg = _config.image_to_video if _config else None
    if i2v_cfg and i2v_cfg.enabled:
        from imaging import init_i2v_client
        init_i2v_client(
            api_key=i2v_cfg.resolve_api_key(),
            model=i2v_cfg.model,
            default_resolution=i2v_cfg.default_resolution,
            default_duration=i2v_cfg.default_duration,
        )
```

- [ ] **Step 3: 冒烟验证**

Run: `python -c "from src.imaging import get_i2v_client, init_i2v_client; init_i2v_client('sk-x'); print(get_i2v_client())"`
Expected: 打印 `DashScopeImageToVideoClient` 实例

- [ ] **Step 4: Commit**

```bash
git add src/imaging/__init__.py src/web/api.py
git commit -m "feat(api): init i2v client during lifespan startup"
```

---

## Task 5: `/api/videos/i2v` 端点 TDD

**Files:**
- Modify: `src/web/api.py`
- Create: `tests/test_i2v_api.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_i2v_api.py`：

```python
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from src.web.api import app


@pytest.fixture
def anon_client(monkeypatch):
    # 跳过认证
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "_config", None)
    return TestClient(app)


def test_i2v_endpoint_503_when_not_initialized(anon_client):
    with patch("src.imaging.get_i2v_client", return_value=None):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png", "prompt": "p"
        })
    assert r.status_code == 503


def test_i2v_endpoint_success(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(return_value="https://cdn/vid.mp4")
    with patch("src.imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png",
            "prompt": "slow zoom",
            "duration": 5,
            "resolution": "720P",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["url"] == "https://cdn/vid.mp4"
    assert data["image_url"] == "https://x/a.png"
    fake.generate.assert_awaited_once_with(
        image_url="https://x/a.png",
        prompt="slow zoom",
        resolution="720P",
        duration=5,
        negative_prompt=None,
    )


def test_i2v_endpoint_500_on_error(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("src.imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png", "prompt": "p"
        })
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_i2v_api.py -v`
Expected: 404（端点未定义）

- [ ] **Step 3: 加端点到 `src/web/api.py`**

在 `generate_video` 函数之后插入：

```python
class I2VRequest(BaseModel):
    image_url: str
    prompt: str
    resolution: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None


@app.post("/api/videos/i2v")
async def generate_i2v(request: I2VRequest) -> dict:
    """图生视频（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_i2v_client
    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="图生视频未启用")
    try:
        url = await client.generate(
            image_url=request.image_url,
            prompt=request.prompt,
            resolution=request.resolution,
            duration=request.duration,
            negative_prompt=request.negative_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图生视频失败: {e}")
    return {"url": url, "prompt": request.prompt, "image_url": request.image_url}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_i2v_api.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/web/api.py tests/test_i2v_api.py
git commit -m "feat(api): POST /api/videos/i2v endpoint with tests"
```

---

## Task 6: `GET /api/files/{name}` 静态文件路由

**Files:**
- Modify: `src/web/api.py`
- Modify: `config.yaml`（public_paths）
- Create: `tests/test_files_route.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_files_route.py`：

```python
from pathlib import Path
from fastapi.testclient import TestClient

from src.web.api import app


def test_files_route_serves_existing_file(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    f = uploads / "abc_test.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)

    client = TestClient(app)
    r = client.get("/api/files/abc_test.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert r.headers["content-type"].startswith("image/")


def test_files_route_rejects_traversal(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    client = TestClient(app)
    r = client.get("/api/files/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)


def test_files_route_404_on_missing(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    client = TestClient(app)
    r = client.get("/api/files/nope.png")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_files_route.py -v`
Expected: 404 或 AttributeError

- [ ] **Step 3: 实现路由**

在 `src/web/api.py` 顶部（与其他全局变量一起）添加：

```python
from fastapi.responses import FileResponse
UPLOADS_DIR = Path("data/uploads")
```

（如 `Path` 未导入，`from pathlib import Path`）

在现有端点区（建议紧跟 `/api/videos/i2v` 之后）加：

```python
@app.get("/api/files/{name}")
async def serve_upload(name: str):
    """暴露 data/uploads/ 下的文件（供 DashScope 拉取图片等场景）"""
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = (UPLOADS_DIR / name).resolve()
    try:
        path.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path))
```

- [ ] **Step 4: 加入 auth public_paths**

修改 `config.yaml` 的 `auth.public_paths`：

```yaml
auth:
  enabled: true
  public_paths:
    - "/api/auth/login"
    - "/api/health"
    - "/api/docs"
    - "/api/openapi.json"
    - "/api/files/"
```

（认证中间件按前缀匹配——若不是前缀匹配，见 Step 5）

- [ ] **Step 5: 验证认证放行**

Run: `pytest tests/test_files_route.py -v`
Expected: 3 PASS

若某个测试 401，打开 `src/auth.py` 查看 `is_public_path` 逻辑；若是等值比较，改为：

```python
if any(path == p or path.startswith(p) for p in public_paths if p.endswith("/")):
    return True
if path in public_paths:
    return True
```

- [ ] **Step 6: Commit**

```bash
git add src/web/api.py tests/test_files_route.py config.yaml
git commit -m "feat(api): GET /api/files/{name} static route for uploads"
```

---

## Task 7: SSE 流 `[[I2V:]]` 标记解析

**Files:**
- Modify: `src/web/api.py`（streaming generator）
- Create: `tests/test_chat_i2v_marker.py`

- [ ] **Step 1: 写单元测试（纯解析函数）**

先把解析抽成纯函数便于测试。创建 `tests/test_chat_i2v_marker.py`：

```python
from src.web.api import parse_i2v_markers


def test_parses_single_marker():
    text = "here: [[I2V: https://x/a.png | slow zoom]] done"
    matches = parse_i2v_markers(text)
    assert matches == [("https://x/a.png", "slow zoom")]


def test_parses_multiple_markers():
    text = "[[I2V: https://a | p1]]\n[[I2V: https://b | p2]]"
    assert parse_i2v_markers(text) == [("https://a", "p1"), ("https://b", "p2")]


def test_ignores_invalid_url():
    text = "[[I2V: not-a-url | p]]"
    assert parse_i2v_markers(text) == []


def test_ignores_empty_prompt():
    text = "[[I2V: https://x |    ]]"
    assert parse_i2v_markers(text) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_chat_i2v_marker.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `parse_i2v_markers`**

在 `src/web/api.py` 顶部（imports 下方）添加：

```python
import re as _re_module

_I2V_RE = _re_module.compile(r"\[\[I2V:\s*(.+?)\s*\|\s*(.+?)\]\]", flags=_re_module.DOTALL)


def parse_i2v_markers(text: str) -> list[tuple[str, str]]:
    """从 LLM 输出中抽取 [[I2V: <image_url> | <prompt>]] 对。

    过滤非 http(s) URL 和空 prompt。
    """
    out: list[tuple[str, str]] = []
    for url, prompt in _I2V_RE.findall(text):
        url = url.strip()
        prompt = prompt.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if not prompt:
            continue
        out.append((url, prompt))
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_chat_i2v_marker.py -v`
Expected: 4 PASS

- [ ] **Step 5: 在流式生成器中调用**

定位到 `src/web/api.py` 大约 line 1141（`video_prompts = _re.findall(r"\[\[VIDEO:...` 之后）。修改 `display_text` 的正则以同时剥离 I2V：

```python
display_text = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", raw_text, flags=_re.DOTALL).strip()
i2v_pairs = parse_i2v_markers(raw_text)
```

在 "生成视频并逐段推送" 块之后插入：

```python
            # 图生视频（LLM 路径）
            i2v_results: list[tuple[str, str, str]] = []  # (url, prompt, source_image_url)
            if i2v_pairs:
                from imaging import get_i2v_client
                i2v_client = get_i2v_client()
                for image_url, prompt_text in i2v_pairs:
                    if not i2v_client:
                        yield f"data: {json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': '图生视频未启用'})}\n\n"
                        continue
                    yield f"data: {json.dumps({'type': 'i2v_pending', 'prompt': prompt_text, 'image_url': image_url})}\n\n"
                    try:
                        url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                        i2v_results.append((url, prompt_text, image_url))
                        yield f"data: {json.dumps({'type': 'i2v', 'url': url, 'prompt': prompt_text, 'source_image_url': image_url})}\n\n"
                    except Exception as ie:
                        yield f"data: {json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': str(ie)})}\n\n"
```

并在 `md_tail` 段合并 i2v 结果：

```python
            if i2v_results:
                md_tail += [f"[video:{pt}]({u})" for u, pt, _ in i2v_results]
```

- [ ] **Step 6: 运行整个测试套件**

Run: `pytest tests/ -x -k "i2v or files_route"`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add src/web/api.py tests/test_chat_i2v_marker.py
git commit -m "feat(chat): parse and execute [[I2V:]] markers in SSE stream"
```

---

## Task 8: 非流式路径同步补齐（可选但推荐）

如果 `/api/chat` 非流式接口（line ~744-860）同样需支持 `[[I2V:]]`，照 Task 7 在该处镜像同样逻辑。若本期仅流式支持，跳过。

**Files:**
- Modify: `src/web/api.py`（非流式 `/api/chat` 处理器）

- [ ] **Step 1: 在非流式处理器中追加 I2V 解析**

定位到约 line 799 `video_prompts = _re.findall(r"\[\[VIDEO:..` 之后，追加：

```python
            i2v_pairs = parse_i2v_markers(raw_answer)
```

并在 `video_results` 处理之后：

```python
            i2v_results: list[dict] = []
            if i2v_pairs:
                from imaging import get_i2v_client
                i2v_client = get_i2v_client()
                if i2v_client:
                    for image_url, prompt_text in i2v_pairs:
                        try:
                            url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                            i2v_results.append({"url": url, "prompt": prompt_text, "source_image_url": image_url})
                        except Exception as ve:
                            i2v_results.append({"error": str(ve), "prompt": prompt_text, "image_url": image_url})
```

把 `i2v_results` 加入响应 `{... "i2v": i2v_results}`。

- [ ] **Step 2: 冒烟跑现有测试**

Run: `pytest tests/ -x`
Expected: 无回归

- [ ] **Step 3: Commit**

```bash
git add src/web/api.py
git commit -m "feat(chat): parse [[I2V:]] in non-streaming /api/chat"
```

---

## Task 9: 前端 `<I2VModal>` 组件

**Files:**
- Create: `frontend/src/components/I2VModal.tsx`

- [ ] **Step 1: 创建组件**

```tsx
import React, { useState } from 'react';
import { Modal, Input, Select, Button, Collapse, message } from 'antd';

const { TextArea } = Input;

interface I2VModalProps {
  open: boolean;
  imageUrl: string;
  authToken: string;
  onClose: () => void;
  onSuccess: (videoUrl: string, prompt: string, sourceImageUrl: string) => void;
}

export const I2VModal: React.FC<I2VModalProps> = ({ open, imageUrl, authToken, onClose, onSuccess }) => {
  const [prompt, setPrompt] = useState('');
  const [duration, setDuration] = useState(5);
  const [resolution, setResolution] = useState('720P');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!prompt.trim()) {
      message.warning('请填写 prompt');
      return;
    }
    setLoading(true);
    try {
      const resp = await fetch('/api/videos/i2v', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          image_url: imageUrl,
          prompt: prompt.trim(),
          duration,
          resolution,
          negative_prompt: negativePrompt.trim() || undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      onSuccess(data.url, prompt.trim(), imageUrl);
      onClose();
      setPrompt('');
      setNegativePrompt('');
    } catch (e: any) {
      message.error(`生成失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="图生视频"
      open={open}
      onCancel={loading ? undefined : onClose}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={loading}>取消</Button>,
        <Button key="ok" type="primary" loading={loading} onClick={handleSubmit}>生成</Button>,
      ]}
      width={520}
    >
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <img src={imageUrl} alt="source"
             style={{ width: 120, height: 120, objectFit: 'cover', borderRadius: 4 }} />
        <div style={{ flex: 1 }}>
          <TextArea
            rows={3}
            placeholder="描述画面中的运动/变化…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4 }}>时长</div>
          <Select value={duration} onChange={setDuration} style={{ width: '100%' }}
                  disabled={loading}
                  options={[{value:3,label:'3 秒'},{value:5,label:'5 秒'},{value:8,label:'8 秒'}]} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4 }}>分辨率</div>
          <Select value={resolution} onChange={setResolution} style={{ width: '100%' }}
                  disabled={loading}
                  options={[{value:'480P',label:'480P'},{value:'720P',label:'720P'},{value:'1080P',label:'1080P'}]} />
        </div>
      </div>
      <Collapse
        items={[{
          key: 'adv', label: '高级',
          children: (
            <TextArea rows={2} placeholder="negative prompt (可选)"
                      value={negativePrompt}
                      onChange={(e) => setNegativePrompt(e.target.value)}
                      disabled={loading} />
          )
        }]}
      />
      {loading && (
        <div style={{ marginTop: 12, color: '#888', fontSize: 12 }}>
          正在生成，可能需要 30–120 秒…
        </div>
      )}
    </Modal>
  );
};
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/I2VModal.tsx
git commit -m "feat(frontend): add I2VModal component"
```

---

## Task 10: App.tsx 接入图片按钮与结果回填

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 导入组件与状态**

在 App.tsx 顶部 import 区域加：

```tsx
import { I2VModal } from './components/I2VModal';
```

在 Chat 页面组件的 state 区加：

```tsx
const [i2vModalOpen, setI2vModalOpen] = useState(false);
const [i2vSourceUrl, setI2vSourceUrl] = useState<string>('');
```

- [ ] **Step 2: 图片渲染处加按钮**

定位 `msg.images.map((img, i) => (` 附近（约 line 1394）。把现有的 `<a>` 改为带浮层按钮的容器：

```tsx
{msg.images.map((img, i) =>
  img.url ? (
    <div key={i} style={{ position: 'relative', display: 'inline-block', marginRight: 8 }}>
      <a href={img.url} target="_blank" rel="noreferrer" title={img.prompt}>
        <img src={img.url} alt={img.prompt || 'generated'}
             style={{ maxWidth: 200, borderRadius: 4 }} />
      </a>
      <Tooltip title="生成视频">
        <Button
          size="small"
          shape="circle"
          icon={<span>🎬</span>}
          style={{ position: 'absolute', top: 4, right: 4 }}
          onClick={() => { setI2vSourceUrl(img.url!); setI2vModalOpen(true); }}
        />
      </Tooltip>
    </div>
  ) : (
    <Tag key={i} color="error">图像生成失败: {img.error}</Tag>
  )
)}
```

（确保 `Tooltip` 和 `Button` 已从 antd 导入。）

- [ ] **Step 3: 渲染 Modal 并处理成功回调**

在 Chat 页面组件 return 的顶层（Layout 内）加：

```tsx
<I2VModal
  open={i2vModalOpen}
  imageUrl={i2vSourceUrl}
  authToken={token /* 当前会话 token 变量名以代码为准 */}
  onClose={() => setI2vModalOpen(false)}
  onSuccess={(videoUrl, prompt, sourceImageUrl) => {
    const newMsg: Message = {
      role: 'assistant',
      content: `图生视频完成（源图: ${sourceImageUrl}）\n\n[video:${prompt}](${videoUrl})`,
      videos: [{ url: videoUrl, prompt }],
    };
    setMessages((prev) => [...prev, newMsg]);
  }}
/>
```

（`Message` 类型和 `setMessages` 变量名以现有代码为准；若 token 不在 scope 内，从 `localStorage.getItem('token')` 读取）

- [ ] **Step 4: 处理 LLM 路径的 i2v SSE 事件**

定位 streaming 处理 switch 块（`case 'video':` 附近）。新增：

```tsx
case 'i2v_pending':
  // 可选：加 loading 提示
  break;
case 'i2v':
  updateLastAssistant((m) => ({
    ...m,
    videos: [...(m.videos || []), { url: evt.url, prompt: evt.prompt }],
  }));
  break;
case 'i2v_error':
  updateLastAssistant((m) => ({
    ...m,
    videos: [...(m.videos || []), { error: evt.message, prompt: evt.prompt }],
  }));
  break;
```

（`updateLastAssistant` 以代码中实际辅助函数为准；若直接 `setMessages((ms) => ms.map(...))` 模式也可。）

- [ ] **Step 5: 本地开发验证**

Run（在 2 个终端）:

```bash
# 终端 1
python -m src.main
# 终端 2
cd frontend && npm run dev
```

浏览器打开 `http://localhost:3000`，登录后：
1. 让 AI 画一张图（触发 `[[IMAGE:]]`）
2. 图片右上角点 🎬，填 prompt，生成
3. 等待 30-120s，视频以新消息形式出现

Expected: 视频 URL 可播放

- [ ] **Step 6: 构建确认无 TS 错误**

Run: `cd frontend && npm run build`
Expected: build 成功

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): wire I2V button on chat images + SSE event handling"
```

---

## Task 11: 端到端手动验证

- [ ] **Step 1: UI 路径**

流程同 Task 10 Step 5。确认：
- 🎬 按钮出现在每张聊天图片上
- Modal 提交后显示 loading
- 成功后视频以新消息出现并可播放
- 失败 toast 正常

- [ ] **Step 2: LLM 路径**

在 chat 里输入：

```
把这张图变成一个缓慢推进镜头的视频：https://example-cdn/my.png
（请在回答里使用 [[I2V: <url> | <描述>]] 标记）
```

观察 SSE 日志：应看到 `i2v_pending` → `i2v` 事件；前端消息末尾追加视频。

- [ ] **Step 3: 私网部署说明（文档）**

在 README 或 CLAUDE.md 的"部署注意"一节补一句：

> 若 MemoX 部署在私网，DashScope 无法拉取 `/api/files/...`；请使用 LLM 生成图（自带公网 URL）或等待 Phase 2 的文件上传兜底。

- [ ] **Step 4: Final Commit**

```bash
git add README.md   # 或 CLAUDE.md
git commit -m "docs: note i2v private-network caveat"
```

---

## 验证清单

- [ ] `pytest tests/test_i2v_client.py tests/test_i2v_api.py tests/test_files_route.py tests/test_chat_i2v_marker.py -v` 全绿
- [ ] `pytest tests/` 无回归
- [ ] `cd frontend && npm run build` 成功
- [ ] UI 路径端到端跑通
- [ ] LLM 路径 `[[I2V:]]` 标记跑通
- [ ] 503（未启用）/ 500（生成失败）分支已验证
