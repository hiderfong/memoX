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
