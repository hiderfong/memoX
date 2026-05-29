"""Imaging router (image/video generation)"""

import asyncio
import mimetypes
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["imaging"])

MAX_I2V_BATCH_ITEMS = 8
MAX_LOCAL_I2V_IMAGE_BYTES = 20 * 1024 * 1024
MAX_LOCAL_VIDEO_EDIT_BYTES = 100 * 1024 * 1024
MAX_CONCURRENT_MEDIA_TASKS = 2
ACTIVE_MEDIA_STATUSES = {"queued", "running"}
I2V_PARAMETER_KEYS = {
    "resolution",
    "duration",
    "negative_prompt",
    "last_frame_url",
    "driving_audio_url",
    "first_clip_url",
    "prompt_extend",
    "watermark",
    "seed",
}

_media_task_semaphore: asyncio.Semaphore | None = None
_media_task_pending_count = 0
_media_task_running_count = 0
_media_task_tasks: set[asyncio.Task] = set()
VIDEO_EDIT_PARAMETER_KEYS = {
    "reference_image_urls",
    "resolution",
    "ratio",
    "duration",
    "negative_prompt",
    "audio_setting",
    "prompt_extend",
    "watermark",
    "seed",
}


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


def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _request_parameters(model: BaseModel, *, exclude: set[str]) -> dict:
    data = _model_to_dict(model)
    return {
        key: value
        for key, value in data.items()
        if key not in exclude and value not in (None, "", [])
    }


def _record_media_asset(
    *,
    operation: str,
    source_url: str,
    prompt: str,
    asset_id: str | None = None,
    url: str = "",
    status: str = "success",
    input_mode: str = "",
    error: str = "",
    parameters: dict | None = None,
) -> dict | None:
    try:
        from storage import get_store

        store = get_store()
        if not store:
            return None
        return store.save_media_asset({
            "id": asset_id,
            "kind": "video",
            "status": status,
            "operation": operation,
            "url": url,
            "source_url": source_url,
            "prompt": prompt,
            "input_mode": input_mode,
            "error": error,
            "parameters": parameters or {},
        })
    except Exception:
        return None


def _media_task_semaphore_for_loop() -> asyncio.Semaphore:
    global _media_task_semaphore
    if _media_task_semaphore is None:
        _media_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_MEDIA_TASKS)
    return _media_task_semaphore


async def _run_scheduled_media_task(coro) -> None:
    global _media_task_pending_count, _media_task_running_count
    acquired = False
    try:
        async with _media_task_semaphore_for_loop():
            acquired = True
            _media_task_pending_count = max(0, _media_task_pending_count - 1)
            _media_task_running_count += 1
            try:
                await coro
            except Exception:
                logger.exception("[MediaQueue] 后台媒体任务执行异常")
            finally:
                _media_task_running_count = max(0, _media_task_running_count - 1)
    finally:
        if not acquired:
            _media_task_pending_count = max(0, _media_task_pending_count - 1)
            close = getattr(coro, "close", None)
            if callable(close):
                close()


def _schedule_media_task(coro) -> None:
    global _media_task_pending_count
    _media_task_pending_count += 1
    task = asyncio.create_task(_run_scheduled_media_task(coro), name="media_asset_job")
    _media_task_tasks.add(task)
    task.add_done_callback(_media_task_tasks.discard)


def media_task_queue_status() -> dict:
    return {
        "max_concurrent": MAX_CONCURRENT_MEDIA_TASKS,
        "runtime_pending": _media_task_pending_count,
        "runtime_running": _media_task_running_count,
        "runtime_tracked": len(_media_task_tasks),
    }


def _reset_media_task_scheduler_for_tests() -> None:
    global _media_task_semaphore, _media_task_pending_count, _media_task_running_count
    for task in list(_media_task_tasks):
        task.cancel()
    _media_task_tasks.clear()
    _media_task_semaphore = None
    _media_task_pending_count = 0
    _media_task_running_count = 0


def _request_from_media_asset(asset: dict) -> I2VRequest | VideoEditRequest:
    operation = asset.get("operation")
    parameters = dict(asset.get("parameters") or {})
    if operation == "i2v":
        payload = {
            key: value
            for key, value in parameters.items()
            if key in I2V_PARAMETER_KEYS
        }
        return I2VRequest(
            image_url=asset.get("source_url") or "",
            prompt=asset.get("prompt") or "",
            **payload,
        )
    if operation == "video_edit":
        payload = {
            key: value
            for key, value in parameters.items()
            if key in VIDEO_EDIT_PARAMETER_KEYS
        }
        return VideoEditRequest(
            video_url=asset.get("source_url") or "",
            prompt=asset.get("prompt") or "",
            **payload,
        )
    raise ValueError(f"Unsupported media operation: {operation}")


def _schedule_media_asset_job(asset: dict) -> None:
    request = _request_from_media_asset(asset)
    if asset["operation"] == "i2v":
        _schedule_media_task(_run_i2v_asset_job(asset["id"], request))
        return
    if asset["operation"] == "video_edit":
        _schedule_media_task(_run_video_edit_asset_job(asset["id"], request))
        return
    raise ValueError(f"Unsupported media operation: {asset['operation']}")


def mark_interrupted_media_assets() -> int:
    """Mark active media tasks from a previous process as failed and retryable."""
    from storage import get_store

    store = get_store()
    if not store:
        return 0
    interrupted: list[dict] = []
    for status in ACTIVE_MEDIA_STATUSES:
        interrupted.extend(store.list_media_assets(kind="video", status=status, limit=200))

    now = datetime.now().isoformat()
    for asset in interrupted:
        parameters = dict(asset.get("parameters") or {})
        parameters["interrupted_status"] = asset["status"]
        parameters["interrupted_at"] = now
        store.save_media_asset({
            **asset,
            "status": "failed",
            "url": "",
            "input_mode": "",
            "error": "服务重启前媒体任务未完成，已标记为失败，可点击重试。",
            "parameters": parameters,
        })
    return len(interrupted)


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


async def _edit_video_item(client, item: VideoEditRequest) -> dict:
    video_path = _resolve_upload_reference(item.video_url, expected="video")
    input_mode = "dashscope_upload" if video_path else "url"
    if video_path:
        url = await client.edit_from_file(
            video_path,
            prompt=item.prompt,
            reference_image_urls=item.reference_image_urls,
            resolution=item.resolution,
            ratio=item.ratio,
            duration=item.duration,
            negative_prompt=item.negative_prompt,
            audio_setting=item.audio_setting,
            prompt_extend=item.prompt_extend,
            watermark=item.watermark,
            seed=item.seed,
        )
    else:
        url = await client.edit(
            video_url=item.video_url,
            prompt=item.prompt,
            reference_image_urls=item.reference_image_urls,
            resolution=item.resolution,
            ratio=item.ratio,
            duration=item.duration,
            negative_prompt=item.negative_prompt,
            audio_setting=item.audio_setting,
            prompt_extend=item.prompt_extend,
            watermark=item.watermark,
            seed=item.seed,
        )
    return {
        "url": url,
        "prompt": item.prompt,
        "video_url": item.video_url,
        "input_mode": input_mode,
    }


async def _run_i2v_asset_job(asset_id: str, request: I2VRequest) -> None:
    _record_media_asset(
        asset_id=asset_id,
        operation="i2v",
        status="running",
        source_url=request.image_url,
        prompt=request.prompt,
        parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
    )
    from imaging import get_i2v_client

    client = get_i2v_client()
    if not client:
        _record_media_asset(
            asset_id=asset_id,
            operation="i2v",
            status="failed",
            source_url=request.image_url,
            prompt=request.prompt,
            error="图生视频未启用",
            parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
        )
        return

    try:
        result = await _generate_i2v_item(client, request)
        _record_media_asset(
            asset_id=asset_id,
            operation="i2v",
            source_url=request.image_url,
            prompt=request.prompt,
            url=result["url"],
            input_mode=result.get("input_mode", ""),
            parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
        )
    except Exception as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        _record_media_asset(
            asset_id=asset_id,
            operation="i2v",
            status="failed",
            source_url=request.image_url,
            prompt=request.prompt,
            error=str(detail),
            parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
        )


async def _run_video_edit_asset_job(asset_id: str, request: VideoEditRequest) -> None:
    _record_media_asset(
        asset_id=asset_id,
        operation="video_edit",
        status="running",
        source_url=request.video_url,
        prompt=request.prompt,
        parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
    )
    from imaging import get_i2v_client

    client = get_i2v_client()
    if not client:
        _record_media_asset(
            asset_id=asset_id,
            operation="video_edit",
            status="failed",
            source_url=request.video_url,
            prompt=request.prompt,
            error="视频编辑未启用",
            parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
        )
        return

    try:
        result = await _edit_video_item(client, request)
        _record_media_asset(
            asset_id=asset_id,
            operation="video_edit",
            source_url=request.video_url,
            prompt=request.prompt,
            url=result["url"],
            input_mode=result.get("input_mode", ""),
            parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
        )
    except Exception as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        _record_media_asset(
            asset_id=asset_id,
            operation="video_edit",
            status="failed",
            source_url=request.video_url,
            prompt=request.prompt,
            error=str(detail),
            parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
        )


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
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        _record_media_asset(
            operation="i2v",
            status="failed",
            source_url=request.image_url,
            prompt=request.prompt,
            error=str(detail),
            parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"图生视频失败: {e}") from e
    asset = _record_media_asset(
        operation="i2v",
        source_url=request.image_url,
        prompt=request.prompt,
        url=result["url"],
        input_mode=result.get("input_mode", ""),
        parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
    )
    if asset:
        result["asset_id"] = asset["id"]
    return result


@router.post("/videos/i2v/jobs")
async def enqueue_i2v_job(request: I2VRequest) -> dict:
    """提交图生视频后台任务，立即返回可轮询的媒体资产记录。"""
    from imaging import get_i2v_client

    if not get_i2v_client():
        raise HTTPException(status_code=503, detail="图生视频未启用")
    asset = _record_media_asset(
        operation="i2v",
        status="queued",
        source_url=request.image_url,
        prompt=request.prompt,
        parameters=_request_parameters(request, exclude={"image_url", "prompt"}),
    )
    if not asset:
        raise HTTPException(status_code=503, detail="媒体资产库未初始化")
    _schedule_media_task(_run_i2v_asset_job(asset["id"], request))
    return {"asset": asset}


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
            asset = _record_media_asset(
                operation="i2v",
                source_url=item.image_url,
                prompt=item.prompt,
                url=item_result["url"],
                input_mode=item_result.get("input_mode", ""),
                parameters=_request_parameters(item, exclude={"image_url", "prompt"}),
            )
            if asset:
                item_result["asset_id"] = asset["id"]
            results.append({"index": index, "ok": True, **item_result})
        except Exception as e:
            status_code = e.status_code if isinstance(e, HTTPException) else 500
            detail = e.detail if isinstance(e, HTTPException) else str(e)
            item_result = {
                "index": index,
                "ok": False,
                "image_url": item.image_url,
                "prompt": item.prompt,
                "status_code": status_code,
                "error": str(detail),
            }
            asset = _record_media_asset(
                operation="i2v",
                status="failed",
                source_url=item.image_url,
                prompt=item.prompt,
                error=str(detail),
                parameters=_request_parameters(item, exclude={"image_url", "prompt"}),
            )
            if asset:
                item_result["asset_id"] = asset["id"]
            results.append(item_result)

    return {
        "ok": all(item["ok"] for item in results),
        "count": len(results),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


@router.post("/videos/i2v/batch/jobs")
async def enqueue_i2v_batch_jobs(request: I2VBatchRequest) -> dict:
    """批量提交图生视频后台任务，逐项返回可轮询的媒体资产记录。"""
    from imaging import get_i2v_client

    if not request.items:
        raise HTTPException(status_code=400, detail="批量任务不能为空")
    if len(request.items) > MAX_I2V_BATCH_ITEMS:
        raise HTTPException(status_code=400, detail=f"批量任务最多支持 {MAX_I2V_BATCH_ITEMS} 个素材")
    if not get_i2v_client():
        raise HTTPException(status_code=503, detail="图生视频未启用")

    assets: list[dict] = []
    for index, item in enumerate(request.items):
        asset = _record_media_asset(
            operation="i2v",
            status="queued",
            source_url=item.image_url,
            prompt=item.prompt,
            parameters={
                **_request_parameters(item, exclude={"image_url", "prompt"}),
                "batch_index": index,
            },
        )
        if not asset:
            raise HTTPException(status_code=503, detail="媒体资产库未初始化")
        assets.append({"index": index, **asset})
        _schedule_media_task(_run_i2v_asset_job(asset["id"], item))

    return {"count": len(assets), "assets": assets}


@router.post("/videos/edit")
async def edit_video(request: VideoEditRequest) -> dict:
    """视频编辑（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_i2v_client

    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="视频编辑未启用")

    try:
        result = await _edit_video_item(client, request)
    except Exception as e:
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        _record_media_asset(
            operation="video_edit",
            status="failed",
            source_url=request.video_url,
            prompt=request.prompt,
            error=str(detail),
            parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"视频编辑失败: {e}") from e

    asset = _record_media_asset(
        operation="video_edit",
        source_url=request.video_url,
        prompt=request.prompt,
        url=result["url"],
        input_mode=result.get("input_mode", ""),
        parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
    )
    if asset:
        result["asset_id"] = asset["id"]
    return result


@router.post("/videos/edit/jobs")
async def enqueue_video_edit_job(request: VideoEditRequest) -> dict:
    """提交视频编辑后台任务，立即返回可轮询的媒体资产记录。"""
    from imaging import get_i2v_client

    if not get_i2v_client():
        raise HTTPException(status_code=503, detail="视频编辑未启用")
    asset = _record_media_asset(
        operation="video_edit",
        status="queued",
        source_url=request.video_url,
        prompt=request.prompt,
        parameters=_request_parameters(request, exclude={"video_url", "prompt"}),
    )
    if not asset:
        raise HTTPException(status_code=503, detail="媒体资产库未初始化")
    _schedule_media_task(_run_video_edit_asset_job(asset["id"], request))
    return {"asset": asset}


@router.get("/videos/assets")
async def list_video_assets(
    kind: str | None = None,
    status: str | None = None,
    operation: str | None = None,
    limit: int = 50,
) -> dict:
    """列出生成/编辑过的媒体资产，用于前端作品库。"""
    from storage import get_store

    store = get_store()
    if not store:
        return {"assets": [], "count": 0, "stats": {"queued": 0, "running": 0, "success": 0, "failed": 0}}
    assets = store.list_media_assets(
        kind=kind,
        status=status,
        operation=operation,
        limit=limit,
    )
    return {
        "assets": assets,
        "count": len(assets),
        "stats": {
            "queued": sum(1 for item in assets if item["status"] == "queued"),
            "running": sum(1 for item in assets if item["status"] == "running"),
            "success": sum(1 for item in assets if item["status"] == "success"),
            "failed": sum(1 for item in assets if item["status"] == "failed"),
        },
    }


@router.get("/videos/jobs/status")
async def get_video_jobs_status() -> dict:
    """Return runtime and persisted queue pressure for media background jobs."""
    from storage import get_store

    queued_assets = 0
    running_assets = 0
    store = get_store()
    if store:
        queued_assets = len(store.list_media_assets(kind="video", status="queued", limit=200))
        running_assets = len(store.list_media_assets(kind="video", status="running", limit=200))
    return {
        **media_task_queue_status(),
        "persisted_queued": queued_assets,
        "persisted_running": running_assets,
    }


@router.get("/videos/assets/{asset_id}")
async def get_video_asset(asset_id: str) -> dict:
    """获取单条媒体资产记录，用于轮询后台任务结果。"""
    from storage import get_store

    store = get_store()
    if not store:
        raise HTTPException(status_code=503, detail="媒体资产库未初始化")
    asset = store.get_media_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="媒体资产不存在")
    return asset


@router.post("/videos/assets/{asset_id}/retry")
async def retry_video_asset(asset_id: str) -> dict:
    """重新提交一条媒体资产任务，保留原素材和参数。"""
    from imaging import get_i2v_client
    from storage import get_store

    store = get_store()
    if not store:
        raise HTTPException(status_code=503, detail="媒体资产库未初始化")
    asset = store.get_media_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="媒体资产不存在")
    if asset["status"] in ACTIVE_MEDIA_STATUSES:
        raise HTTPException(status_code=409, detail="媒体任务仍在执行中")
    if asset["operation"] not in {"i2v", "video_edit"}:
        raise HTTPException(status_code=400, detail="不支持重试该媒体任务")
    if not get_i2v_client():
        raise HTTPException(status_code=503, detail="媒体生成未启用")

    parameters = dict(asset.get("parameters") or {})
    parameters["retry_count"] = int(parameters.get("retry_count") or 0) + 1
    parameters["last_retry_at"] = datetime.now().isoformat()
    queued = store.save_media_asset({
        **asset,
        "status": "queued",
        "url": "",
        "input_mode": "",
        "error": "",
        "parameters": parameters,
    })
    try:
        _schedule_media_asset_job(queued)
    except ValueError as e:
        store.save_media_asset({
            **queued,
            "status": "failed",
            "error": str(e),
        })
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"asset": queued}


@router.delete("/videos/assets/{asset_id}")
async def delete_video_asset(asset_id: str) -> dict:
    """从作品库删除一条媒体资产记录，不删除远端生成文件。"""
    from storage import get_store

    store = get_store()
    if not store:
        raise HTTPException(status_code=503, detail="媒体资产库未初始化")
    deleted = store.delete_media_asset(asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="媒体资产不存在")
    return {"deleted": True}
