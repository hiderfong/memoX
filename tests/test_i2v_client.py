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
    assert body["input"]["media"] == [{"type": "first_frame", "url": "https://x/a.png"}]
    assert body["input"]["prompt"] == "slow zoom"
    assert body["parameters"]["resolution"] == "720P"
    assert body["parameters"]["duration"] == 5
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["headers"]["X-DashScope-Async"] == "enable"
    assert "X-DashScope-OssResourceResolve" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_legacy_model_uses_img_url_shape():
    client = DashScopeImageToVideoClient(api_key="sk-test", model="wan2.6-i2v", poll_interval=0.01)
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
        await client.generate(image_url="https://x/a.png", prompt="p")

    body = instance.post.call_args.kwargs["json"]
    assert body["input"]["img_url"] == "https://x/a.png"
    assert "media" not in body["input"]


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


@pytest.mark.asyncio
async def test_generate_from_file_uploads_to_dashscope_oss(tmp_path):
    source = tmp_path / "a.png"
    source.write_bytes(b"fake-image")
    client = DashScopeImageToVideoClient(api_key="sk-test", poll_interval=0.01)

    policy_resp = MagicMock()
    policy_resp.raise_for_status = MagicMock()
    policy_resp.json.return_value = {
        "data": {
            "upload_dir": "dashscope-instant/123",
            "upload_host": "https://oss.example/upload",
            "oss_access_key_id": "ak",
            "signature": "sig",
            "policy": "policy",
        }
    }
    upload_resp = MagicMock()
    upload_resp.raise_for_status = MagicMock()
    submit_resp = MagicMock()
    submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}
    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn/x.mp4"}}

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=[policy_resp, poll_resp])
        instance.post = AsyncMock(side_effect=[upload_resp, submit_resp])
        url = await client.generate_from_file(source, prompt="slow zoom")

    assert url == "https://cdn/x.mp4"
    submit_call = instance.post.call_args_list[1]
    submit_body = submit_call.kwargs["json"]
    assert submit_body["input"]["media"] == [
        {"type": "first_frame", "url": "oss://dashscope-instant/123/a.png"}
    ]
    assert submit_call.kwargs["headers"]["X-DashScope-OssResourceResolve"] == "enable"


@pytest.mark.asyncio
async def test_video_edit_body_shape():
    client = DashScopeImageToVideoClient(api_key="sk-test", edit_model="wan2.7-videoedit", poll_interval=0.01)
    submit_resp = MagicMock()
    submit_resp.raise_for_status = MagicMock()
    submit_resp.json.return_value = {"output": {"task_id": "t1"}}
    poll_resp = MagicMock()
    poll_resp.raise_for_status = MagicMock()
    poll_resp.json.return_value = {"output": {"task_status": "SUCCEEDED", "video_url": "https://cdn/edited.mp4"}}

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=submit_resp)
        instance.get = AsyncMock(return_value=poll_resp)
        url = await client.edit(
            video_url="https://x/in.mp4",
            prompt="make it brighter",
            reference_image_urls=["https://x/ref.png"],
            resolution="720P",
            ratio="16:9",
            duration=5,
            negative_prompt="blur",
        )

    assert url == "https://cdn/edited.mp4"
    body = instance.post.call_args.kwargs["json"]
    assert body["model"] == "wan2.7-videoedit"
    assert body["input"]["prompt"] == "make it brighter"
    assert body["input"]["negative_prompt"] == "blur"
    assert body["input"]["media"] == [
        {"type": "video", "url": "https://x/in.mp4"},
        {"type": "reference_image", "url": "https://x/ref.png"},
    ]
    assert body["parameters"]["ratio"] == "16:9"
    assert body["parameters"]["duration"] == 5
