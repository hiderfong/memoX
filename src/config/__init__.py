"""配置模块 - 配置加载与管理"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """配置无效。"""


def resolve_env_value(value: Any) -> str:
    """解析 ${VAR_NAME} 或 ${VAR_NAME:-default} 形式的环境变量引用。"""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        inner = value[2:-1]
        if ":-" in inner:
            var_name, default_val = inner.split(":-", 1)
            return os.getenv(var_name, default_val)
        return os.getenv(inner, "")
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
    if config.ops.ops_event_retention_days < 0:
        errors.append("ops.ops_event_retention_days 不能为负数")
    if config.ops.audit_log_retention_days < 0:
        errors.append("ops.audit_log_retention_days 不能为负数")
    if config.ops.task_job_retention_days < 0:
        errors.append("ops.task_job_retention_days 不能为负数")
    if config.ops.diagnostic_retention_days < 0:
        errors.append("ops.diagnostic_retention_days 不能为负数")
    if config.ops.max_diagnostic_bundles < 1:
        errors.append("ops.max_diagnostic_bundles 必须至少为 1")
    if config.coordinator.task_auto_retry_max_attempts < 0:
        errors.append("coordinator.task_auto_retry_max_attempts 不能为负数")
    if config.coordinator.task_auto_retry_initial_delay_seconds < 0:
        errors.append("coordinator.task_auto_retry_initial_delay_seconds 不能为负数")
    if config.coordinator.task_auto_retry_max_delay_seconds < 0:
        errors.append("coordinator.task_auto_retry_max_delay_seconds 不能为负数")
    if config.coordinator.task_auto_retry_backoff_multiplier < 1:
        errors.append("coordinator.task_auto_retry_backoff_multiplier 必须至少为 1")
    if config.file_access.signed_url_ttl_seconds < 1:
        errors.append("file_access.signed_url_ttl_seconds 必须至少为 1")

    database_policy = config.tool_policy.database
    if database_policy.default_access_mode not in {"read_only", "write", "admin"}:
        errors.append("tool_policy.database.default_access_mode 必须是 read_only、write 或 admin")
    if database_policy.max_result_rows < 1:
        errors.append("tool_policy.database.max_result_rows 必须至少为 1")
    for name, connection_string in database_policy.data_sources.items():
        if not str(name).strip():
            errors.append("tool_policy.database.data_sources 不能包含空数据源名称")
        if not resolve_env_value(connection_string).strip():
            errors.append(f"tool_policy.database.data_sources.{name} 连接字符串不能为空")

    crawler_policy = config.tool_policy.playwright_crawler
    if crawler_policy.max_concurrency < 1:
        errors.append("tool_policy.playwright_crawler.max_concurrency 必须至少为 1")
    if crawler_policy.queue_timeout_seconds < 0:
        errors.append("tool_policy.playwright_crawler.queue_timeout_seconds 不能为负数")
    if crawler_policy.total_timeout_seconds < 1:
        errors.append("tool_policy.playwright_crawler.total_timeout_seconds 必须至少为 1")
    if crawler_policy.navigation_timeout_ms < 1000:
        errors.append("tool_policy.playwright_crawler.navigation_timeout_ms 必须至少为 1000")
    if crawler_policy.selector_timeout_ms < 0:
        errors.append("tool_policy.playwright_crawler.selector_timeout_ms 不能为负数")
    if crawler_policy.idle_wait_ms < 0:
        errors.append("tool_policy.playwright_crawler.idle_wait_ms 不能为负数")
    if crawler_policy.max_pages < 1:
        errors.append("tool_policy.playwright_crawler.max_pages 必须至少为 1")
    if crawler_policy.max_response_bytes < 1024:
        errors.append("tool_policy.playwright_crawler.max_response_bytes 必须至少为 1024")
    if crawler_policy.max_output_chars < 100:
        errors.append("tool_policy.playwright_crawler.max_output_chars 必须至少为 100")

    web_policy = config.tool_policy.web
    if web_policy.request_timeout_seconds < 1:
        errors.append("tool_policy.web.request_timeout_seconds 必须至少为 1")
    if web_policy.max_response_bytes < 1024:
        errors.append("tool_policy.web.max_response_bytes 必须至少为 1024")
    if web_policy.max_fetch_chars < 100:
        errors.append("tool_policy.web.max_fetch_chars 必须至少为 100")
    if web_policy.max_search_results < 1:
        errors.append("tool_policy.web.max_search_results 必须至少为 1")

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
    task_auto_retry_enabled: bool = True
    task_auto_retry_max_attempts: int = 2
    task_auto_retry_initial_delay_seconds: float = 30.0
    task_auto_retry_max_delay_seconds: float = 300.0
    task_auto_retry_backoff_multiplier: float = 2.0


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
class NetworkToolPolicyConfig:
    """Network tool policy."""
    allow_internal_hosts: list[str] = field(default_factory=list)


@dataclass
class PlaywrightCrawlerPolicyConfig:
    """Playwright crawler resource policy."""
    max_concurrency: int = 2
    queue_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 45.0
    navigation_timeout_ms: int = 30000
    selector_timeout_ms: int = 10000
    idle_wait_ms: int = 2000
    max_pages: int = 1
    max_response_bytes: int = 5_000_000
    max_output_chars: int = 8000


@dataclass
class DatabaseToolPolicyConfig:
    """Database tool policy."""
    default_access_mode: str = "read_only"
    allow_raw_connection_strings: bool = True
    allow_write: bool = True
    allow_ddl: bool = False
    allow_multiple_statements: bool = False
    max_result_rows: int = 200
    data_sources: dict[str, str] = field(default_factory=dict)


@dataclass
class WebToolPolicyConfig:
    """Web search/fetch resource policy."""
    request_timeout_seconds: float = 15.0
    max_response_bytes: int = 2_000_000
    max_fetch_chars: int = 20_000
    max_search_results: int = 10


@dataclass
class ToolPolicyConfig:
    """High-permission tool safety policy."""
    network: NetworkToolPolicyConfig = field(default_factory=NetworkToolPolicyConfig)
    web: WebToolPolicyConfig = field(default_factory=WebToolPolicyConfig)
    playwright_crawler: PlaywrightCrawlerPolicyConfig = field(default_factory=PlaywrightCrawlerPolicyConfig)
    database: DatabaseToolPolicyConfig = field(default_factory=DatabaseToolPolicyConfig)


@dataclass
class RerankerConfig:
    """重排序配置"""
    enabled: bool = False
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

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
    # 重排序配置
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    # 知识图谱配置
    enable_graph: bool = False
    graph_type: str = "networkx" # 可选: networkx, neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    graph_persist_path: str = "./data/knowledge_graph.gml"
    manifest_path: str = "./data/documents_manifest.json"
    graph_llm_provider: str = "dashscope"
    graph_llm_api_key: str = ""

    def __post_init__(self):
        self.neo4j_uri = resolve_env_value(self.neo4j_uri)
        self.neo4j_user = resolve_env_value(self.neo4j_user)
        self.neo4j_password = resolve_env_value(self.neo4j_password)


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
class FileAccessConfig:
    """上传文件访问配置"""
    signing_secret: str = ""
    signed_url_ttl_seconds: int = 300

    def resolve_signing_secret(self) -> str:
        return resolve_env_value(self.signing_secret)


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
    archive_mirror_dir: str = ""
    ops_event_retention_days: int = 90
    audit_log_retention_days: int = 180
    task_job_retention_days: int = 30
    diagnostic_retention_days: int = 30
    max_diagnostic_bundles: int = 20


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
    tool_policy: ToolPolicyConfig = field(default_factory=ToolPolicyConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    file_access: FileAccessConfig = field(default_factory=FileAccessConfig)
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

        kb_data = data.get("knowledge_base", {})
        if "reranker" in kb_data:
            kb_data["reranker"] = RerankerConfig(**kb_data["reranker"])
        knowledge_base = KnowledgeBaseConfig(**kb_data)

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
        file_access = FileAccessConfig(**data.get("file_access", {}))
        tool_policy_data = data.get("tool_policy", {})
        network_policy = NetworkToolPolicyConfig(**tool_policy_data.get("network", {}))
        web_policy = WebToolPolicyConfig(**tool_policy_data.get("web", {}))
        playwright_crawler_policy = PlaywrightCrawlerPolicyConfig(
            **tool_policy_data.get("playwright_crawler", {})
        )
        database_policy = DatabaseToolPolicyConfig(**tool_policy_data.get("database", {}))
        tool_policy = ToolPolicyConfig(
            network=network_policy,
            web=web_policy,
            playwright_crawler=playwright_crawler_policy,
            database=database_policy,
        )

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
            tool_policy=tool_policy,
            auth=auth,
            file_access=file_access,
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
