"""MemoX - 主入口"""

import sys
from pathlib import Path

# 添加 src 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from loguru import logger

from .config import load_config


def main():
    """主函数"""
    # 加载配置
    config_path = Path(__file__).parent.parent / "config.yaml"
    config = load_config(config_path)

    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        level=config.app.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 启动服务器
    logger.info(f"🚀 启动 {config.app.name}...")
    logger.info(f"📂 工作目录: {config.app.workspace}")
    logger.info(f"🔧 最大 Worker 数: {config.coordinator.max_workers}")

    # SSL 证书路径（相对于项目根目录）
    ssl_cert = Path(__file__).parent.parent / "ssl" / "cert.pem"
    ssl_key  = Path(__file__).parent.parent / "ssl" / "key.pem"
    use_ssl  = ssl_cert.exists() and ssl_key.exists()

    if use_ssl:
        logger.info(f"🔒 SSL 已启用: {ssl_cert}")
    else:
        logger.warning("⚠️  未找到 SSL 证书，以 HTTP 模式启动")

    uvicorn.run(
        "src.web.api:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.app.debug,
        log_level=config.app.log_level.lower(),
        ssl_certfile=str(ssl_cert) if use_ssl else None,
        ssl_keyfile=str(ssl_key)  if use_ssl else None,
    )


if __name__ == "__main__":
    main()
