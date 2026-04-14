"""DashScope 文生视频客户端（wan2.x 异步任务模式）

Submit: POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable, Authorization: Bearer ...
Body:
    {"model": "wan2.7-t2v",
     "input": {"prompt": "..."},
     "parameters": {"resolution": "720P", "ratio": "16:9", "duration": 5, ...}}

Poll: GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
Success: output.video_url (24h 有效)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger


class DashScopeVideoClient:
    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    def __init__(
        self,
        api_key: str,
        model: str = "wan2.7-t2v",
        default_resolution: str = "720P",
        default_ratio: str = "16:9",
        default_duration: int = 5,
        poll_interval: float = 5.0,
        timeout_s: float = 600.0,
    ):
        self._api_key = api_key
        self._model = model
        self._default_resolution = default_resolution
        self._default_ratio = default_ratio
        self._default_duration = default_duration
        self._poll_interval = poll_interval
        self._timeout_s = timeout_s

    async def generate(
        self,
        prompt: str,
        resolution: str | None = None,
        ratio: str | None = None,
        duration: int | None = None,
        negative_prompt: str | None = None,
    ) -> str:
        """生成视频，返回视频 URL"""
        if not self._api_key:
            raise RuntimeError("视频生成未配置 API Key")

        submit_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        parameters: dict[str, Any] = {
            "resolution": resolution or self._default_resolution,
            "ratio": ratio or self._default_ratio,
            "duration": int(duration or self._default_duration),
        }
        input_body: dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            input_body["negative_prompt"] = negative_prompt

        body = {
            "model": self._model,
            "input": input_body,
            "parameters": parameters,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.SUBMIT_URL, headers=submit_headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            task_id = (data.get("output") or {}).get("task_id")
            if not task_id:
                raise RuntimeError(f"提交视频任务失败: {data}")

            logger.info(f"[Video] 任务已提交 task_id={task_id} model={self._model}")

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
                        raise RuntimeError(f"响应中未找到 video_url: {d}")
                    logger.info(f"[Video] 任务完成 task_id={task_id}")
                    return video_url
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    raise RuntimeError(f"视频任务失败: {d}")

            raise TimeoutError(f"视频任务超时 task_id={task_id}")


_client: DashScopeVideoClient | None = None


def init_video_client(api_key: str, model: str = "wan2.7-t2v", **kwargs: Any) -> DashScopeVideoClient:
    global _client
    _client = DashScopeVideoClient(api_key=api_key, model=model, **kwargs)
    logger.info(f"[Video] 客户端已初始化: model={model}")
    return _client


def get_video_client() -> DashScopeVideoClient | None:
    return _client
