# External Agent E2E Runbook

本文档交给具备外网访问能力和安全 secret 注入能力的外部 Agent 执行。当前 Codex 本地环境可以完成代码修复、单元测试、前端构建、提交和推送，但真实 DeepSeek / MiniMax / Qwen / DashScope 调用在本地沙箱中被 DNS/外网限制拦截，因此需要外部 Agent 完成真实模型 E2E 验收。

## 目标

外部 Agent 需要验证以下能力在真实服务下可用：

1. DeepSeek V4 Pro + MiniMax + Qwen3.7 多 Agent 混合编排。
2. MiniMax 多 Agent 协作 E2E。
3. Qwen3.7 OpenAI-compatible provider 基础调用。
4. DashScope Wan2.7 I2V 真实图生视频。
5. MemoX 媒体工作台后台任务接口：入队、轮询、成功/失败记录、作品库查询。

如果发现代码缺陷，外部 Agent 应输出复现步骤、失败日志、最小修复建议；除非被明确授权，不要直接提交代码变更。

## 安全规则

- 不要把任何真实 API key、token、cookie、签名 URL 写入仓库、日志 artifact、issue、PR 描述或聊天回复。
- 命令输出中如果出现 key 或临时资源 URL，提交结果前必须手动打码。
- 不要编辑 `.env`、`config.yaml` 或任何会被提交的文件来保存 secret。
- 使用 shell 环境变量、CI secret、临时 secret manager 或一次性 runtime injection。
- 测试素材必须是非敏感图片/视频。不要使用真实用户数据。
- 所有真实模型调用都会消耗额度。先跑 P0/P1/P2/P3 的最小 smoke，再决定是否跑完整套。

## 必需 Secret

外部 Agent 运行前应由操作者通过安全通道注入：

```bash
export MINIMAX_API_KEY="<redacted>"
export DEEPSEEK_API_KEY="<redacted>"
export QWEN_API_KEY="<redacted>"
export DASHSCOPE_API_KEY="<redacted>"
export MEMOX_FILE_SIGNING_SECRET="<random-long-secret>"
export MEMOX_ADMIN_PASSWORD="<random-long-password>"
export QWEN_MODEL="qwen3.7"
```

说明：

- `MINIMAX_API_KEY` 用于 `https://api.minimaxi.com/anthropic/v1`。
- `DEEPSEEK_API_KEY` 用于 `https://api.deepseek.com`，模型为 `deepseek-v4-pro`。
- `QWEN_API_KEY` 用于 `https://dashscope.aliyuncs.com/compatible-mode/v1`，默认模型为 `qwen3.7`。
- `DASHSCOPE_API_KEY` 用于 DashScope Wan2.7 I2V / video edit。
- 如果实际部署把 Qwen chat 与 DashScope I2V 共用同一个 key，可在安全环境里把同一个 secret 同时注入为 `QWEN_API_KEY` 和 `DASHSCOPE_API_KEY`，不要写入仓库。

## 环境要求

- macOS 或 Linux。
- Python 3.11+。
- Node.js 18+。
- `uv` 可用；如果没有 `uv`，可改用项目 `.venv`，但推荐按仓库现有方式执行。
- HTTPS 出站访问必须可解析并连接：
  - `api.deepseek.com`
  - `api.minimaxi.com`
  - `dashscope.aliyuncs.com`
  - DashScope 上传策略返回的 OSS upload host

## 预检

从最新 `master` 开始：

```bash
git clone https://github.com/hiderfong/memoX.git
cd memoX
git checkout master
git pull --ff-only
git rev-parse --short HEAD
```

安装依赖：

```bash
uv sync --extra dev
cd frontend_wip
npm ci
cd ..
```

确认 secret 已注入，但不要打印具体值：

```bash
uv run --extra dev python - <<'PY'
import os
required = [
    "MINIMAX_API_KEY",
    "DEEPSEEK_API_KEY",
    "QWEN_API_KEY",
    "DASHSCOPE_API_KEY",
    "MEMOX_FILE_SIGNING_SECRET",
    "MEMOX_ADMIN_PASSWORD",
]
missing = [name for name in required if not os.environ.get(name)]
print({"present": [name for name in required if name not in missing], "missing": missing})
raise SystemExit(1 if missing else 0)
PY
```

确认 DNS/网络，不调用模型、不消耗额度：

```bash
uv run --extra dev python - <<'PY'
import socket
hosts = [
    "api.deepseek.com",
    "api.minimaxi.com",
    "dashscope.aliyuncs.com",
]
for host in hosts:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    print(host, "OK", infos[0][4][0])
PY
```

## 基线测试

先证明代码基线正常：

```bash
uv run --extra dev pytest tests --ignore=tests/e2e -q --tb=short
cd frontend_wip
npm run build
cd ..
git diff --check
```

预期：

- 后端非 E2E 测试通过，允许已有明确 skip。
- 前端 build 通过。
- `git diff --check` 无输出。

## P0: DeepSeek + MiniMax + Qwen 混合编排 E2E

这是最高优先级。它验证 DeepSeek worker、MiniMax worker、Qwen worker、依赖上下文传递、工具写文件和最终产物检查。

```bash
uv run --extra dev pytest \
  tests/e2e/test_deepseek_mixed_orchestration.py \
  -q -s --tb=short
```

预期：

- 测试通过。
- shared output 中有 `deepseek_notes.txt`、`mixed_report.txt` 和 `qwen_review.txt`。
- `deepseek_notes.txt` 包含 `DEEPSEEK_OK=alpha`。
- `mixed_report.txt` 同时包含 `DEEPSEEK_OK=alpha` 和 `MINIMAX_OK=beta`。
- `qwen_review.txt` 同时包含 `DEEPSEEK_OK=alpha`、`MINIMAX_OK=beta` 和 `QWEN_OK=gamma`。

失败判读：

- DNS 或 connect error：外部环境网络未满足要求，不算 MemoX 代码失败。
- 401/403：secret、base URL 或 provider 权限问题。
- 429/quota：额度或限流问题，记录 provider 响应后停止重试。
- 子任务失败后任务却显示 completed：这是严重回归，当前 `master` 已修复，应记录 commit 和完整事件日志。

## P1: MiniMax 多 Agent 协作 E2E

先跑最小场景，确认 MiniMax Anthropic-compatible provider 与工具循环可用：

```bash
uv run --extra dev pytest \
  tests/e2e/test_e2e_collab.py::test_calculator_collaboration \
  -q -s --tb=short
```

预算允许时再跑完整 MiniMax 协作文件：

```bash
uv run --extra dev pytest \
  tests/e2e/test_e2e_collab.py \
  -q -s --tb=short
```

预期：

- 至少一轮迭代成功完成。
- shared 目录中产生 Python 文件。
- 如果有 `test_result.txt`，内容包含 `ok` 或 `passed`。
- 邮件通信日志 `mail_log.txt` 存在。

## P2: Qwen Provider Smoke

外部 Agent 需要补一次真实 Qwen3.7 provider smoke，确认 DashScope OpenAI-compatible chat endpoint、key、模型名和 MemoX provider adapter 可用。

```bash
uv run --extra dev python - <<'PY'
import asyncio
import os

from src.agents.base_agent import create_provider

async def main() -> None:
    provider = create_provider(
        "dashscope",
        os.environ["QWEN_API_KEY"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    response = await provider.chat(
        messages=[
            {"role": "system", "content": "Reply with exactly QWEN_OK."},
            {"role": "user", "content": "Provider smoke test."},
        ],
        model=os.environ.get("QWEN_MODEL", "qwen3.7"),
        temperature=0,
        max_tokens=32,
    )
    content = (response.content or "").strip()
    print("Qwen response:", content)
    assert "QWEN_OK" in content

asyncio.run(main())
PY
```

如果 `qwen3.7` 对当前账号不可用，停止并报告 provider 权限问题；不要自动降级到其他非指定 provider。只有操作者明确要求时，才通过 `QWEN_MODEL` 覆盖为账号实际可用的 Qwen 模型。

## P3: DashScope I2V Direct Client Smoke

此步骤直接调用 `DashScopeImageToVideoClient`，验证 DashScope 上传策略、临时 OSS 上传、Wan2.7 I2V 提交和轮询。会生成真实视频并消耗额度。

```bash
uv run --extra dev python - <<'PY'
import asyncio
import os
import struct
import zlib
from pathlib import Path

from src.imaging.i2v_client import DashScopeImageToVideoClient

def write_png(path: Path, width: int = 512, height: int = 512) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend((
                int(30 + 180 * x / max(1, width - 1)),
                int(80 + 120 * y / max(1, height - 1)),
                180,
            ))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)

async def main() -> None:
    image_path = Path("/tmp/memox-i2v-smoke.png")
    write_png(image_path)
    client = DashScopeImageToVideoClient(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model=os.environ.get("I2V_TEST_MODEL", "wan2.7-i2v"),
        edit_model=os.environ.get("I2V_TEST_EDIT_MODEL", "wan2.7-videoedit"),
        poll_interval=float(os.environ.get("I2V_TEST_POLL_INTERVAL", "5")),
        timeout_s=float(os.environ.get("I2V_TEST_TIMEOUT_SECONDS", "900")),
    )
    video_url = await client.generate_from_file(
        image_path,
        prompt="A slow cinematic push-in over a simple blue and green gradient card, smooth motion, no text.",
        resolution=os.environ.get("I2V_TEST_RESOLUTION", "720P"),
        duration=int(os.environ.get("I2V_TEST_DURATION", "5")),
        prompt_extend=True,
        watermark=False,
        seed=20260530,
    )
    print("I2V video URL:", video_url)
    assert video_url.startswith(("http://", "https://"))

asyncio.run(main())
PY
```

预期：

- 命令最终输出一个 HTTP(S) 视频 URL。
- 没有 `提交 i2v 任务失败`、`i2v 任务失败` 或 `i2v 任务超时`。

失败判读：

- `/uploads?action=getPolicy` 失败：DashScope key、模型权限或上传策略问题。
- OSS upload host 连接失败：外网策略未允许 DashScope 返回的 OSS 域名。
- task 失败：记录 DashScope `task_id`、状态和错误，但打码任何敏感字段。

## P4: Backend Media Job Smoke

此步骤验证 MemoX 后端媒体工作台链路：本地素材引用、后台任务入队、作品库持久化、状态轮询。会再次调用真实 I2V，消耗额度。

创建临时配置：

```bash
cat > /tmp/memox-external-e2e.yaml <<'YAML'
app:
  name: "MemoX External E2E"
  debug: false
  log_level: "INFO"
  workspace: "/tmp/memox-external-e2e-workspace"

server:
  host: "127.0.0.1"
  port: 18080

coordinator:
  model: "MiniMax-M2.7-highspeed"
  provider: "minimax"
  temperature: 0.1
  max_tokens: 2048
  max_workers: 2
  task_timeout: 300

providers:
  minimax:
    api_key: "${MINIMAX_API_KEY}"
    base_url: "https://api.minimaxi.com/anthropic/v1"
  deepseek:
    api_key: "${DEEPSEEK_API_KEY}"
    base_url: "https://api.deepseek.com"
  dashscope:
    api_key: "${QWEN_API_KEY}"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"

worker_templates: {}

knowledge_base:
  persist_directory: "/tmp/memox-external-e2e-data/chroma"
  upload_directory: "/tmp/memox-external-e2e-data/uploads"
  skills_dir: "/tmp/memox-external-e2e-data/skills"
  embedding_provider: "hash"
  embedding_model: "hash-e2e"
  chunk_size: 200
  chunk_overlap: 20
  top_k: 3
  hybrid_search:
    enabled: true
    bm25_persist_path: "/tmp/memox-external-e2e-data/bm25_index.pkl"
  enable_graph: false
  manifest_path: "/tmp/memox-external-e2e-data/documents_manifest.json"

auth:
  enabled: false

file_access:
  signing_secret: "${MEMOX_FILE_SIGNING_SECRET}"
  signed_url_ttl_seconds: 300

image_to_video:
  enabled: true
  provider: "dashscope"
  model: "wan2.7-i2v"
  edit_model: "wan2.7-videoedit"
  api_key: "${DASHSCOPE_API_KEY}"
  default_resolution: "720P"
  default_duration: 5
YAML
```

启动后端：

```bash
MEMOX_CONFIG_PATH=/tmp/memox-external-e2e.yaml \
uv run --extra dev uvicorn src.web.api:app --host 127.0.0.1 --port 18080 \
  > /tmp/memox-external-e2e-server.log 2>&1 &
export MEMOX_E2E_SERVER_PID=$!

for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:18080/api/health >/tmp/memox-health.json; then
    cat /tmp/memox-health.json
    break
  fi
  sleep 1
done
```

提交后台 I2V 任务并轮询：

```bash
uv run --extra dev python - <<'PY'
import asyncio
import json
import struct
import zlib
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:18080/api"
UPLOAD_DIR = Path("/tmp/memox-external-e2e-data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def write_png(path: Path, width: int = 512, height: int = 512) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend((180, int(40 + 180 * x / max(1, width - 1)), int(60 + 160 * y / max(1, height - 1))))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )

async def main() -> None:
    image_name = "memox-media-job-smoke.png"
    write_png(UPLOAD_DIR / image_name)
    async with httpx.AsyncClient(timeout=30) as client:
        enqueue = await client.post(
            f"{BASE}/videos/i2v/jobs",
            json={
                "image_url": image_name,
                "prompt": "A gentle animated camera move over a colorful abstract gradient card, no text.",
                "resolution": "720P",
                "duration": 5,
                "prompt_extend": True,
                "watermark": False,
                "seed": 20260530,
            },
        )
        print("enqueue:", enqueue.status_code, enqueue.text)
        enqueue.raise_for_status()
        asset = enqueue.json()["asset"]
        asset_id = asset["id"]
        assert asset["status"] == "queued"

        for attempt in range(180):
            status = (await client.get(f"{BASE}/videos/jobs/status")).json()
            item = (await client.get(f"{BASE}/videos/assets/{asset_id}")).json()["asset"]
            print("poll", attempt, json.dumps({"queue": status, "asset": item}, ensure_ascii=False))
            if item["status"] == "success":
                assert item["url"].startswith(("http://", "https://"))
                return
            if item["status"] == "failed":
                raise AssertionError(item["error"])
            await asyncio.sleep(5)
        raise TimeoutError(f"media asset {asset_id} did not finish")

asyncio.run(main())
PY
```

停止后端：

```bash
kill "$MEMOX_E2E_SERVER_PID"
```

预期：

- `/api/videos/i2v/jobs` 返回 `queued` asset。
- `/api/videos/jobs/status` 可返回运行槽位和持久化 queued/running 统计。
- `/api/videos/assets/{asset_id}` 最终为 `success`，且 `url` 是 HTTP(S) 视频链接。
- 如果任务失败，`/api/videos/assets?status=failed` 能看到错误，且失败任务可通过 `/api/videos/assets/{asset_id}/retry` 重试。

## P5: Optional Full E2E Sweep

如果 P0-P4 都通过，并且预算允许，可以跑所有 E2E：

```bash
uv run --extra dev pytest tests/e2e -m e2e -q -s --tb=short
```

注意：

- 浏览器 E2E 可能需要 Playwright Chromium 和前端依赖。
- Admin 浏览器 E2E 不需要真实 provider key，但需要本地浏览器运行能力。
- 如果 CI 环境不支持浏览器，可只报告非浏览器 E2E 结果。

## 失败处理原则

外部 Agent 不应只给“失败了”。请按以下层级定位：

1. 环境失败：DNS、TLS、代理、依赖安装、Node/Python 版本。
2. Secret/provider 失败：401、403、模型不存在、账户无权限、quota、rate limit。
3. MemoX 集成失败：请求体结构、provider adapter、工具循环、任务状态持久化。
4. 产品行为失败：状态误报、后台任务卡住、不能重试、作品库丢记录。

对于 1/2 类，停止并回报，不要改代码。

对于 3/4 类，提供：

- 失败命令。
- 最小复现步骤。
- 相关日志片段，打码 secret。
- 预期行为和实际行为。
- 涉及文件与可疑函数。
- 是否建议修复，以及建议修复范围。

## 回传报告模板

请外部 Agent 最终返回以下内容：

```text
Repo:
Commit:
Runner OS:
Python:
Node:
Started at:
Finished at:

Secret handling:
- Confirmed no secrets written to repo: yes/no
- Logs redacted: yes/no

Network preflight:
- api.deepseek.com:
- api.minimaxi.com:
- dashscope.aliyuncs.com:

Baseline:
- backend non-e2e:
- frontend build:
- git diff --check:

P0 DeepSeek + MiniMax + Qwen mixed orchestration:
- result:
- duration:
- notes:

P1 MiniMax collaboration:
- result:
- scenario(s):
- notes:

P2 Qwen smoke:
- result:
- model:
- notes:

P3 DashScope I2V direct:
- result:
- task/video evidence, redacted if needed:
- notes:

P4 Backend media job:
- result:
- asset id:
- final status:
- notes:

P5 full sweep, if run:
- result:
- skipped tests:
- notes:

Failures:
- category:
- command:
- error summary:
- recommended next action:

Decision:
- GO / NO-GO for real-user long-running deployment:
- Blocking issues:
- Non-blocking follow-ups:
```

## 当前已知上下文

- 最新本地验证已通过：`pytest tests --ignore=tests/e2e`、目标编排测试、`git diff --check`。
- 本地 Codex 沙箱曾尝试 P0，失败原因为 DNS/外网限制：`ConnectError: [Errno 8] nodename nor servname provided, or not known`。
- 因该尝试暴露的“子任务失败但主任务可能被评分兜底为 completed”问题，已在 `master` 修复：失败/耗尽子任务会让主任务进入 `failed`，依赖失败的下游子任务不会继续执行。
