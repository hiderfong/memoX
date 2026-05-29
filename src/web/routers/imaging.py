"""Imaging router (image/video generation)"""

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["imaging"])

MAX_I2V_BATCH_ITEMS = 8
MAX_LOCAL_I2V_IMAGE_BYTES = 20 * 1024 * 1024
MAX_LOCAL_VIDEO_EDIT_BYTES = 100 * 1024 * 1024


class ImageGenRequest(BaseModel):
    prompt: str
    size: str | None = None
    n: int = 1


class VideoGenRequest(BaseModel):
    prompt: str
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None


class I2VRequest(BaseModel):
    image_url: str
    prompt: str
    resolution: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None
    last_frame_url: str | None = None
    driving_audio_url: str | None = None
    first_clip_url: str | None = None
    prompt_extend: bool | None = None
    watermark: bool | None = None
    seed: int | None = None


class I2VBatchRequest(BaseModel):
    items: list[I2VRequest] = Field(default_factory=list)


class VideoEditRequest(BaseModel):
    video_url: str
    prompt: str
    reference_image_urls: list[str] = Field(default_factory=list)
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None
    audio_setting: str | None = None
    prompt_extend: bool | None = None
    watermark: bool | None = None
    seed: int | None = None


def _local_upload_name_from_url(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        path = parsed.path
    elif not parsed.scheme:
        path = value.split("?", 1)[0]
    else:
        return None

    prefix = "/api/files/"
    if path.startswith(prefix):
        return unquote(path.removeprefix(prefix))
    if "/" not in path and "\\" not in path and path:
        return unquote(path)
    return None


def _resolve_upload_reference(raw: str, *, expected: str) -> Path | None:
    name = _local_upload_name_from_url(raw)
    if not name:
        return None

    from web.api import _upload_file_path, _validate_upload_file_name

    safe_name = _validate_upload_file_name(name, raw_name=name)
    path = _upload_file_path(safe_name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="本地素材文件不存在")

    mime_type = mimetypes.guess_type(path.name)[0] or ""
    if expected == "image":
        if path.stat().st_size > MAX_LOCAL_I2V_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="图生视频本地图片超过 20 MB")
        if not mime_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="图生视频本地素材必须是图片")
    elif expected == "video":
        if path.stat().st_size > MAX_LOCAL_VIDEO_EDIT_BYTES:
            raise HTTPException(status_code=413, detail="视频编辑本地视频超过 100 MB")
        if not (mime_type.startswith("video/") or path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}):
            raise HTTPException(status_code=400, detail="视频编辑本地素材必须是视频")
    return path


async def _generate_i2v_item(client, item: I2VRequest) -> dict:
    image_path = _resolve_upload_reference(item.image_url, expected="image")
    input_mode = "dashscope_upload" if image_path else "url"
    if image_path:
        url = await client.generate_from_file(
            image_path,
            prompt=item.prompt,
            resolution=item.resolution,
            duration=item.duration,
            negative_prompt=item.negative_prompt,
            last_frame_url=item.last_frame_url,
            driving_audio_url=item.driving_audio_url,
            first_clip_url=item.first_clip_url,
            prompt_extend=item.prompt_extend,
            watermark=item.watermark,
            seed=item.seed,
        )
    else:
        url = await client.generate(
            image_url=item.image_url,
            prompt=item.prompt,
            resolution=item.resolution,
            duration=item.duration,
            negative_prompt=item.negative_prompt,
            last_frame_url=item.last_frame_url,
            driving_audio_url=item.driving_audio_url,
            first_clip_url=item.first_clip_url,
            prompt_extend=item.prompt_extend,
            watermark=item.watermark,
            seed=item.seed,
        )
    return {
        "url": url,
        "prompt": item.prompt,
        "image_url": item.image_url,
        "input_mode": input_mode,
    }


@router.post("/images/generate")
async def generate_image(request: ImageGenRequest) -> dict:
    """文生图（同步等待，返回图像 URL 列表）"""
    from imaging import get_image_client

    client = get_image_client()
    if not client:
        raise HTTPException(status_code=503, detail="图像生成未启用")
    try:
        urls = await client.generate(request.prompt, size=request.size, n=request.n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图像生成失败: {e}") from e
    return {"urls": urls, "prompt": request.prompt}


@router.post("/videos/generate")
async def generate_video(request: VideoGenRequest) -> dict:
    """文生视频（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_video_client

    client = get_video_client()
    if not client:
        raise HTTPException(status_code=503, detail="视频生成未启用")
    try:
        url = await client.generate(
            request.prompt,
            resolution=request.resolution,
            ratio=request.ratio,
            duration=request.duration,
            negative_prompt=request.negative_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"视频生成失败: {e}") from e
    return {"url": url, "prompt": request.prompt}


@router.post("/videos/i2v")
async def generate_i2v(request: I2VRequest) -> dict:
    """图生视频（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_i2v_client

    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="图生视频未启用")
    try:
        result = await _generate_i2v_item(client, request)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"图生视频失败: {e}") from e
    return result


@router.post("/videos/i2v/batch")
async def generate_i2v_batch(request: I2VBatchRequest) -> dict:
    """批量图生视频。单项失败不会中断整批，返回逐项结果。"""
    from imaging import get_i2v_client

    if not request.items:
        raise HTTPException(status_code=400, detail="批量任务不能为空")
    if len(request.items) > MAX_I2V_BATCH_ITEMS:
        raise HTTPException(status_code=400, detail=f"批量任务最多支持 {MAX_I2V_BATCH_ITEMS} 个素材")

    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="图生视频未启用")

    results: list[dict] = []
    for index, item in enumerate(request.items):
        try:
            item_result = await _generate_i2v_item(client, item)
            results.append({"index": index, "ok": True, **item_result})
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 500
            detail = e.detail if isinstance(e, HTTPException) else str(e)
            results.append({
                "index": index,
                "ok": False,
                "image_url": item.image_url,
                "prompt": item.prompt,
                "status_code": status_code,
                "error": str(detail),
            })

    return {
        "ok": all(item["ok"] for item in results),
        "count": len(results),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


@router.post("/videos/edit")
async def edit_video(request: VideoEditRequest) -> dict:
    """视频编辑（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_i2v_client

    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="视频编辑未启用")

    try:
        video_path = _resolve_upload_reference(request.video_url, expected="video")
        input_mode = "dashscope_upload" if video_path else "url"
        if video_path:
            url = await client.edit_from_file(
                video_path,
                prompt=request.prompt,
                reference_image_urls=request.reference_image_urls,
                resolution=request.resolution,
                ratio=request.ratio,
                duration=request.duration,
                negative_prompt=request.negative_prompt,
                audio_setting=request.audio_setting,
                prompt_extend=request.prompt_extend,
                watermark=request.watermark,
                seed=request.seed,
            )
        else:
            url = await client.edit(
                video_url=request.video_url,
                prompt=request.prompt,
                reference_image_urls=request.reference_image_urls,
                resolution=request.resolution,
                ratio=request.ratio,
                duration=request.duration,
                negative_prompt=request.negative_prompt,
                audio_setting=request.audio_setting,
                prompt_extend=request.prompt_extend,
                watermark=request.watermark,
                seed=request.seed,
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"视频编辑失败: {e}") from e

    return {
        "url": url,
        "prompt": request.prompt,
        "video_url": request.video_url,
        "input_mode": input_mode,
    }
