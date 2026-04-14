"""DashScope 文生图客户端（qwen-image 同步调用模式）

API: POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Body:
    {
      "model": "qwen-image-2.0-pro",
      "input": {"messages": [{"role": "user", "content": [{"text": "..."}]}]},
      "parameters": {"size": "1024*1024", "watermark": false, "prompt_extend": true,
                     "negative_prompt": "..."}
    }
Resp:
    output.choices[0].message.content[0].image  # 生成图像 URL（24h 有效）
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger


class DashScopeImageClient:
    URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-image-2.0-pro",
        default_size: str = "1024*1024",
        watermark: bool = False,
        prompt_extend: bool = True,
        timeout_s: float = 120.0,
    ):
        self._api_key = api_key
        self._model = model
        self._default_size = default_size
        self._watermark = watermark
        self._prompt_extend = prompt_extend
        self._timeout_s = timeout_s

    async def generate(
        self,
        prompt: str,
        size: str | None = None,
        n: int = 1,  # 保留参数；qwen-image 单次返回一张
        negative_prompt: str | None = None,
    ) -> list[str]:
        """生成图像，返回图像 URL 列表"""
        if not self._api_key:
            raise RuntimeError("图像生成未配置 API Key")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        parameters: dict[str, Any] = {
            "size": size or self._default_size,
            "watermark": self._watermark,
            "prompt_extend": self._prompt_extend,
        }
        if negative_prompt:
            parameters["negative_prompt"] = negative_prompt

        body = {
            "model": self._model,
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": prompt}]},
                ],
            },
            "parameters": parameters,
        }

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(self.URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        urls = self._extract_urls(data)
        if not urls:
            raise RuntimeError(f"响应中未找到图像 URL: {data}")
        logger.info(f"[Imaging] 生成完成 model={self._model} 图像数={len(urls)}")
        return urls

    @staticmethod
    def _extract_urls(data: dict) -> list[str]:
        urls: list[str] = []
        output = data.get("output") or {}
        for choice in output.get("choices") or []:
            message = choice.get("message") or {}
            content = message.get("content") or []
            for item in content:
                if isinstance(item, dict):
                    u = item.get("image") or item.get("url")
                    if u:
                        urls.append(u)
        return urls


_client: DashScopeImageClient | None = None


def init_image_client(api_key: str, model: str = "qwen-image-2.0-pro", **kwargs: Any) -> DashScopeImageClient:
    global _client
    _client = DashScopeImageClient(api_key=api_key, model=model, **kwargs)
    logger.info(f"[Imaging] 客户端已初始化: model={model}")
    return _client


def get_image_client() -> DashScopeImageClient | None:
    return _client
