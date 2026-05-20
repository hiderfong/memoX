"""配置模块 - 配置加载与管理"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """配置无效。"""


def resolve_env_value(value: Any) -> str:
    """解析 ${VAR_NAME} 形式的环境变量引用。"""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1], "")
    return str(value)


def validate_config(config: "Config") -> None:
    """校验启动时必须满足的配置约束。"""
    errors: list[str] = []

    if config.auth.enabled:
        if not config.auth.users:
            errors.append("auth.enabled=true 时至少需要配置一个 auth.users 用户")

        seen_usernames: set[str] = set()
        for user in config.auth.users:
            if user.username in seen_usernames:
                errors.append(f"auth.users 中存在重复用户名: {user.username}")
            seen_usernames.add(user.username)

            if not resolve_env_value(user.password).strip():
                errors.append(
                    f"用户 {user.username!r} 的密码为空；请设置对应环境变量或在 config.yaml 中提供非空密码"
                )

    if config.ops.auto_backup_enabled:
        if config.ops.auto_backup_interval_hours <= 0:
            errors.append("ops.auto_backup_interval_hours 必须大于 0")
        if config.ops.auto_backup_startup_delay_seconds < 0:
            errors.append("ops.auto_backup_startup_delay_seconds 不能为负数")
        if config.ops.max_backups < 1:
            errors.append("ops.max_backups 必须至少为 1")
        if not config.ops.auto_backup_include:
            errors.append("ops.auto_backup_include 不能为空")

    if errors:
        raise ConfigError("MemoX 配置无效:\n- " + "\n- ".join(errors))


@dataclass
class AppConfig:
    """应用配置"""
    name: str = "MemoX"
    debug: bool = False
    log_level: str = "INFO"
    workspace: str = "./workspace"


@dataclass
class ServerConfig:
    """服务器配置"""
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=list)


@dataclass
class ProviderConfig:
    """LLM Provider 配置"""
    api_key: str = ""
    base_url: str = ""
    headers: dict = None  # Custom headers (e.g., User-Agent for Kimi Coding)

    def __post_init__(self):
        if self.headers is None:
            self.headers = {}

    def resolve_api_key(self) -> str:
        """解析环境变量"""
        return resolve_env_value(self.api_key)


@dataclass
class CoordinatorConfig:
    """Coordinator 配置"""
    model: str = "claude-sonnet-4-20250514"
    provider: str = "anthropic"
    temperature: float = 0.7
    max_tokens: int = 4096
    max_workers: int = 5
    task_timeout: int = 300


@dataclass
class WorkerTemplate:
    """Worker Agent 模板"""
    model: str
    provider: str
    temperature: float = 0.7
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    mcp: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    display_name: str = ""


@dataclass
class KnowledgeBaseConfig:
    """知识库配置"""
    vector_store: str = "chroma"
    persist_directory: str = "./data/chroma"
    upload_directory: str = "./data/uploads"
    skills_dir: str = "./data/skills"
    embedding_provider: str = "hash"  # 可选: hash, sentence-transformer, openai, dashscope
    embedding_model: str = "hash"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5
    # 混合搜索配置（BM25 + 向量 RRF 融合）
    hybrid_search: dict = field(default_factory=lambda: {
        "enabled": True,
        "bm25_persist_path": "./data/bm25_index.pkl",
        "rrf_k": 60,
    })
    # 知识图谱配置（实验性）
    enable_graph: bool = False
    graph_persist_path: str = "./data/knowledge_graph.gml"
    manifest_path: str = "./data/documents_manifest.json"
    graph_llm_provider: str = "dashscope"
    graph_llm_api_key: str = ""


@dataclass
class AuthUserConfig:
    """单个用户配置"""
    username: str
    password: str
    role: str = "user"
    display_name: str = ""


@dataclass
class AuthConfig:
    """认证配置"""
    enabled: bool = True
    public_paths: list[str] = field(default_factory=lambda: [
        "/api/auth/login", "/api/health", "/api/docs", "/api/redoc", "/api/openapi.json"
    ])
    users: list[AuthUserConfig] = field(default_factory=list)


@dataclass
class ImageGenerationConfig:
    """文生图配置"""
    enabled: bool = False
    provider: str = "dashscope"
    model: str = "qwen-image-2.0-pro"
    api_key: str = ""
    default_size: str = "1024*1024"

    def resolve_api_key(self) -> str:
        return resolve_env_value(self.api_key)


@dataclass
class VideoGenerationConfig:
    """文生视频配置"""
    enabled: bool = False
    provider: str = "dashscope"
    model: str = "wan2.7-t2v"
    api_key: str = ""
    default_resolution: str = "720P"
    default_ratio: str = "16:9"
    default_duration: int = 5

    def resolve_api_key(self) -> str:
        return resolve_env_value(self.api_key)


@dataclass
class ImageToVideoConfig:
    """图生视频配置"""
    enabled: bool = False
    provider: str = "dashscope"
    model: str = "wan2.7-i2v"
    api_key: str = ""
    default_resolution: str = "720P"
    default_duration: int = 5

    def resolve_api_key(self) -> str:
        return resolve_env_value(self.api_key)


@dataclass
class MemoryConfig:
    """记忆管理配置"""
    enabled: bool = True
    max_turns_before_compress: int = 10  # 超过 N 轮对话时触发摘要压缩
    summary_max_chars: int = 500  # 摘要最大字符数
    recent_messages_to_keep: int = 4  # 摘要后保留最近 N 条消息不归档


@dataclass
class OpsConfig:
    """运维自动化配置"""

    auto_backup_enabled: bool = True
    auto_backup_interval_hours: float = 24.0
    auto_backup_startup_delay_seconds: float = 300.0
    auto_backup_include: list[str] = field(default_factory=lambda: ["config.yaml", "data", "workspace"])
    max_backups: int = 14


@dataclass
class Config:
    """全局配置"""
    app: AppConfig
    server: ServerConfig
    coordinator: CoordinatorConfig
    providers: dict[str, ProviderConfig]
    worker_templates: dict[str, WorkerTemplate]
    knowledge_base: KnowledgeBaseConfig
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ops: OpsConfig = field(default_factory=OpsConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    video_generation: VideoGenerationConfig = field(default_factory=VideoGenerationConfig)
    image_to_video: ImageToVideoConfig = field(default_factory=ImageToVideoConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """从 YAML 文件加载配置"""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "Config":
        """从字典创建配置"""
        app = AppConfig(**data.get("app", {}))
        server = ServerConfig(**data.get("server", {}))
        coordinator = CoordinatorConfig(**data.get("coordinator", {}))

        providers = {
            name: ProviderConfig(**config)
            for name, config in data.get("providers", {}).items()
        }

        worker_templates = {
            name: WorkerTemplate(**config)
            for name, config in data.get("worker_templates", {}).items()
        }

        knowledge_base = KnowledgeBaseConfig(**data.get("knowledge_base", {}))

        auth_data = data.get("auth", {})
        auth_users = [
            AuthUserConfig(**u) for u in auth_data.get("users", [])
        ]
        auth = AuthConfig(
            enabled=auth_data.get("enabled", True),
            public_paths=auth_data.get("public_paths", [
                "/api/auth/login", "/api/health", "/api/docs", "/api/redoc", "/api/openapi.json"
            ]),
            users=auth_users,
        )

        memory = MemoryConfig(**data.get("memory", {}))
        ops = OpsConfig(**data.get("ops", {}))

        image_generation = ImageGenerationConfig(**data.get("image_generation", {}))
        video_generation = VideoGenerationConfig(**data.get("video_generation", {}))
        image_to_video = ImageToVideoConfig(**data.get("image_to_video", {}))

        return cls(
            app=app,
            server=server,
            coordinator=coordinator,
            providers=providers,
            worker_templates=worker_templates,
            knowledge_base=knowledge_base,
            memory=memory,
            ops=ops,
            auth=auth,
            image_generation=image_generation,
            video_generation=video_generation,
            image_to_video=image_to_video,
        )


_config: Config | None = None


def default_config_path() -> Path:
    """返回默认配置文件路径，可通过 MEMOX_CONFIG_PATH 覆盖。"""
    return Path(os.getenv("MEMOX_CONFIG_PATH", "config.yaml"))


def load_config(config_path: str | Path | None = None) -> Config:
    """加载配置（单例）"""
    global _config
    if _config is None:
        _config = Config.from_yaml(config_path or default_config_path())
    return _config


def get_config() -> Config:
    """获取配置"""
    if _config is None:
        return load_config()
    return _config
