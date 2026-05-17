"""Imaging router (image/video generation)"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["imaging"])


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
        url = await client.generate(
            image_url=request.image_url,
            prompt=request.prompt,
            resolution=request.resolution,
            duration=request.duration,
            negative_prompt=request.negative_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图生视频失败: {e}") from e
    return {"url": url, "prompt": request.prompt, "image_url": request.image_url}
