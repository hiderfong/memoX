import pytest
from unittest.mock import AsyncMock, patch, MagicMock

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
