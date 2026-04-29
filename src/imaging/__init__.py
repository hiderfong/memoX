"""文生图 / 文生视频 / 图生视频 服务"""

from .dashscope_client import DashScopeImageClient, get_image_client, init_image_client
from .i2v_client import DashScopeImageToVideoClient, get_i2v_client, init_i2v_client
from .video_client import DashScopeVideoClient, get_video_client, init_video_client

__all__ = [
    "DashScopeImageClient", "init_image_client", "get_image_client",
    "DashScopeVideoClient", "init_video_client", "get_video_client",
    "DashScopeImageToVideoClient", "init_i2v_client", "get_i2v_client",
]
