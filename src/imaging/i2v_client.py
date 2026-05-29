"""DashScope 图生视频客户端 (wan2.7-i2v, 异步任务模式).

Submit: POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable
Body:  {"model": "wan2.7-i2v",
        "input":  {"prompt": "...", "media": [{"type": "first_frame", "url": "..."}]},
        "parameters": {"resolution": "720P", "duration": 5}}
Poll:  GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any

import httpx
from loguru import logger


class DashScopeImageToVideoClient:
    SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
    TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    UPLOAD_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"

    def __init__(
        self,
        api_key: str,
        model: str = "wan2.7-i2v",
        edit_model: str = "wan2.7-videoedit",
        default_resolution: str = "720P",
        default_duration: int = 5,
        poll_interval: float = 5.0,
        timeout_s: float = 600.0,
    ):
        self._api_key = api_key
        self._model = model
        self._edit_model = edit_model
        self._default_resolution = default_resolution
        self._default_duration = default_duration
        self._poll_interval = poll_interval
        self._timeout_s = timeout_s

    def _submit_headers(self, *, resolve_oss: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        if resolve_oss:
            headers["X-DashScope-OssResourceResolve"] = "enable"
        return headers

    @staticmethod
    def _uses_media_protocol(model: str) -> bool:
        return model.startswith("wan2.7")

    @staticmethod
    def _contains_oss_url(value: Any) -> bool:
        if isinstance(value, str):
            return value.startswith("oss://")
        if isinstance(value, list):
            return any(DashScopeImageToVideoClient._contains_oss_url(v) for v in value)
        if isinstance(value, dict):
            return any(DashScopeImageToVideoClient._contains_oss_url(v) for v in value.values())
        return False

    async def _submit_and_poll(
        self,
        body: dict[str, Any],
        *,
        operation: str,
        resolve_oss: bool = False,
    ) -> str:
        if not self._api_key:
            raise RuntimeError("图生视频未配置 API Key")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.SUBMIT_URL, headers=self._submit_headers(resolve_oss=resolve_oss), json=body)
            resp.raise_for_status()
            data = resp.json()
            task_id = (data.get("output") or {}).get("task_id")
            if not task_id:
                raise RuntimeError(f"提交 {operation} 任务失败: {data}")

            logger.info(f"[I2V] {operation}任务已提交 task_id={task_id} model={body.get('model')}")

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
                    logger.info(f"[I2V] {operation}任务完成 task_id={task_id}")
                    return video_url
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    raise RuntimeError(f"{operation} 任务失败: {d}")

            raise TimeoutError(f"{operation} 任务超时 task_id={task_id}")

    async def upload_file(self, file_path: str | Path, *, model: str | None = None) -> str:
        """上传本地文件到 DashScope 临时 OSS，返回 oss:// URL。"""
        if not self._api_key:
            raise RuntimeError("图生视频未配置 API Key")

        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")

        target_model = model or self._model
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            policy_resp = await client.get(
                self.UPLOAD_URL,
                headers=headers,
                params={"action": "getPolicy", "model": target_model},
            )
            policy_resp.raise_for_status()
            policy_data = (policy_resp.json().get("data") or {})
            upload_dir = policy_data.get("upload_dir")
            upload_host = policy_data.get("upload_host")
            if not upload_dir or not upload_host:
                raise RuntimeError(f"DashScope 上传凭证响应异常: {policy_resp.json()}")

            key = f"{upload_dir}/{path.name}"
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as fh:
                files = {
                    "OSSAccessKeyId": (None, policy_data.get("oss_access_key_id", "")),
                    "Signature": (None, policy_data.get("signature", "")),
                    "policy": (None, policy_data.get("policy", "")),
                    "x-oss-object-acl": (None, policy_data.get("x_oss_object_acl", "private")),
                    "x-oss-forbid-overwrite": (None, policy_data.get("x_oss_forbid_overwrite", "true")),
                    "x-oss-content-type": (None, content_type),
                    "key": (None, key),
                    "success_action_status": (None, "200"),
                }
                security_token = policy_data.get("x_oss_security_token") or policy_data.get("security_token")
                if security_token:
                    files["x-oss-security-token"] = (None, security_token)
                files["file"] = (path.name, fh, content_type)
                upload_resp = await client.post(upload_host, files=files)
                upload_resp.raise_for_status()

        return f"oss://{key}"

    async def generate(
        self,
        image_url: str,
        prompt: str,
        resolution: str | None = None,
        duration: int | None = None,
        negative_prompt: str | None = None,
        last_frame_url: str | None = None,
        driving_audio_url: str | None = None,
        first_clip_url: str | None = None,
        prompt_extend: bool | None = None,
        watermark: bool | None = None,
        seed: int | None = None,
    ) -> str:
        input_body: dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            input_body["negative_prompt"] = negative_prompt

        if self._uses_media_protocol(self._model):
            media: list[dict[str, str]] = []
            if first_clip_url:
                media.append({"type": "first_clip", "url": first_clip_url})
            else:
                media.append({"type": "first_frame", "url": image_url})
            if last_frame_url:
                media.append({"type": "last_frame", "url": last_frame_url})
            if driving_audio_url:
                media.append({"type": "driving_audio", "url": driving_audio_url})
            input_body["media"] = media
        else:
            input_body["img_url"] = image_url

        parameters: dict[str, Any] = {
            "resolution": resolution or self._default_resolution,
            "duration": int(duration or self._default_duration),
        }
        if prompt_extend is not None:
            parameters["prompt_extend"] = prompt_extend
        if watermark is not None:
            parameters["watermark"] = watermark
        if seed is not None:
            parameters["seed"] = seed

        body = {"model": self._model, "input": input_body, "parameters": parameters}
        return await self._submit_and_poll(body, operation="i2v", resolve_oss=self._contains_oss_url(body))

    async def generate_from_file(self, file_path: str | Path, prompt: str, **kwargs: Any) -> str:
        oss_url = await self.upload_file(file_path, model=self._model)
        return await self.generate(image_url=oss_url, prompt=prompt, **kwargs)

    async def edit(
        self,
        video_url: str,
        prompt: str,
        reference_image_urls: list[str] | None = None,
        resolution: str | None = None,
        ratio: str | None = None,
        duration: int | None = None,
        negative_prompt: str | None = None,
        audio_setting: str | None = None,
        prompt_extend: bool | None = None,
        watermark: bool | None = None,
        seed: int | None = None,
    ) -> str:
        input_body: dict[str, Any] = {
            "prompt": prompt,
            "media": [{"type": "video", "url": video_url}],
        }
        for ref_url in (reference_image_urls or [])[:3]:
            input_body["media"].append({"type": "reference_image", "url": ref_url})
        if negative_prompt:
            input_body["negative_prompt"] = negative_prompt

        parameters: dict[str, Any] = {"resolution": resolution or self._default_resolution}
        if ratio:
            parameters["ratio"] = ratio
        if duration is not None:
            parameters["duration"] = int(duration)
        if audio_setting:
            parameters["audio_setting"] = audio_setting
        if prompt_extend is not None:
            parameters["prompt_extend"] = prompt_extend
        if watermark is not None:
            parameters["watermark"] = watermark
        if seed is not None:
            parameters["seed"] = seed

        body = {"model": self._edit_model, "input": input_body, "parameters": parameters}
        return await self._submit_and_poll(body, operation="videoedit", resolve_oss=self._contains_oss_url(body))

    async def edit_from_file(self, file_path: str | Path, prompt: str, **kwargs: Any) -> str:
        oss_url = await self.upload_file(file_path, model=self._edit_model)
        return await self.edit(video_url=oss_url, prompt=prompt, **kwargs)


_client: DashScopeImageToVideoClient | None = None


def init_i2v_client(api_key: str, model: str = "wan2.7-i2v", **kwargs: Any) -> DashScopeImageToVideoClient:
    global _client
    _client = DashScopeImageToVideoClient(api_key=api_key, model=model, **kwargs)
    logger.info(f"[I2V] 客户端已初始化: model={model}")
    return _client


def get_i2v_client() -> DashScopeImageToVideoClient | None:
    return _client
