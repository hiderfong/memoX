from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.imaging.i2v_client import DashScopeImageToVideoClient


@pytest.mark.asyncio
async def test_submit_body_shape():
    client = DashScopeImageToVideoClient(api_key="sk-test", model="wan2.7-i2v", poll_interval=0.01)

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
    submit_resp = MagicMock()
    submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}
    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn/x.mp4"}}

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=submit_resp)
        instance.get = AsyncMock(return_value=poll_resp)
        await client.generate(image_url="https://x/a.png", prompt="p", negative_prompt="blur")

    body = instance.post.call_args.kwargs["json"]
    assert body["input"]["negative_prompt"] == "blur"
