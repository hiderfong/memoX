"""配置模块 - 配置加载与管理"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
        key = self.api_key
        if key.startswith("${") and key.endswith("}"):
            env_var = key[2:-1]
            return os.getenv(env_var, "")
        return key


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


@dataclass
class KnowledgeBaseConfig:
    """知识库配置"""
    vector_store: str = "chroma"
    persist_directory: str = "./data/chroma"
    upload_directory: str = "./data/uploads"
    embedding_provider: str = "sentence-transformer"  # 可选: sentence-transformer, openai, dashscope
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 5


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
        "/api/auth/login", "/api/health", "/api/docs", "/api/openapi.json"
    ])
    users: list[AuthUserConfig] = field(default_factory=list)


@dataclass
class Config:
    """全局配置"""
    app: AppConfig
    server: ServerConfig
    coordinator: CoordinatorConfig
    providers: dict[str, ProviderConfig]
    worker_templates: dict[str, WorkerTemplate]
    knowledge_base: KnowledgeBaseConfig
    auth: AuthConfig = field(default_factory=AuthConfig)
    
    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """从 YAML 文件加载配置"""
        with open(path, "r", encoding="utf-8") as f:
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
                "/api/auth/login", "/api/health", "/api/docs", "/api/openapi.json"
            ]),
            users=auth_users,
        )

        return cls(
            app=app,
            server=server,
            coordinator=coordinator,
            providers=providers,
            worker_templates=worker_templates,
            knowledge_base=knowledge_base,
            auth=auth,
        )


_config: Config | None = None


def load_config(config_path: str | Path = "config.yaml") -> Config:
    """加载配置（单例）"""
    global _config
    if _config is None:
        _config = Config.from_yaml(config_path)
    return _config


def get_config() -> Config:
    """获取配置"""
    if _config is None:
        return load_config()
    return _config
