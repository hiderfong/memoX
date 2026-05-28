"""Workers router"""
import os
import re as _re
import tempfile
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agents.base_agent import SUPPORTED_PROVIDER_TYPES, ToolRegistry, create_provider, get_provider_capabilities
from agents.worker_pool import ProviderFallbackConfig, WorkerAgent, WorkerConfig, get_worker_pool
from auth import AuthUser, require_role
from config import Config, WorkerFallbackProviderConfig

router = APIRouter(prefix="/api", tags=["workers"])


class WorkerFallbackProviderRequest(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class WorkerConfigUpdate(BaseModel):
    provider: str
    model: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    fallback_providers: list[WorkerFallbackProviderRequest] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    icon: str = ""
    display_name: str = ""


class WorkerCreateRequest(BaseModel):
    name: str
    provider: str
    model: str
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    fallback_providers: list[WorkerFallbackProviderRequest] = Field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    icon: str = ""
    display_name: str = ""


def _get_config():
    import web.api as _api_module
    return getattr(_api_module, "_config", None)


def _audit_log_from_api(request, user, action, resource, resource_id):
    import web.api as _api_module
    fn = getattr(_api_module, "_audit_log", None)
    if fn:
        fn(request, user, action, resource, resource_id)


class WorkerConfigPersistenceError(RuntimeError):
    """Raised when worker template persistence fails."""


def _config_path() -> Path:
    return Path(os.getenv("MEMOX_CONFIG_PATH", "config.yaml"))


def _worker_template_payload(body: WorkerConfigUpdate | WorkerCreateRequest) -> dict:
    payload = {
        "provider": body.provider,
        "model": body.model,
        "temperature": body.temperature,
        "skills": list(body.skills),
        "tools": list(body.tools),
    }
    if body.icon:
        payload["icon"] = body.icon
    if body.display_name:
        payload["display_name"] = body.display_name
    fallback_providers = _fallback_provider_payloads(body.fallback_providers)
    if fallback_providers:
        payload["fallback_providers"] = fallback_providers
    return payload


def _fallback_provider_payloads(fallbacks: list[WorkerFallbackProviderRequest]) -> list[dict]:
    payloads: list[dict] = []
    for fallback in fallbacks:
        item = {"provider": fallback.provider}
        if fallback.model:
            item["model"] = fallback.model
        if fallback.base_url:
            item["base_url"] = fallback.base_url
        if fallback.headers:
            item["headers"] = dict(fallback.headers)
        payloads.append(item)
    return payloads


def _provider_env_var(provider_config) -> str | None:
    raw_api_key = getattr(provider_config, "api_key", "")
    if not isinstance(raw_api_key, str):
        return None
    match = _re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw_api_key.strip())
    return match.group(1) if match else None


def _provider_has_api_key(provider_config) -> bool:
    resolve = getattr(provider_config, "resolve_api_key", None)
    if callable(resolve):
        return bool(str(resolve()).strip())
    return bool(str(getattr(provider_config, "api_key", "")).strip())


def _provider_usage(config, provider_name: str) -> list[str]:
    usage: list[str] = []

    coordinator = getattr(config, "coordinator", None)
    if coordinator and getattr(coordinator, "provider", None) == provider_name:
        usage.append("coordinator")

    knowledge_base = getattr(config, "knowledge_base", None)
    if knowledge_base:
        if getattr(knowledge_base, "embedding_provider", None) == provider_name:
            usage.append("embedding")
        if (
            getattr(knowledge_base, "enable_graph", False)
            and getattr(knowledge_base, "graph_llm_provider", None) == provider_name
        ):
            usage.append("knowledge_graph")

    for feature_name in ("image_generation", "video_generation", "image_to_video"):
        feature = getattr(config, feature_name, None)
        if (
            feature
            and getattr(feature, "enabled", False)
            and getattr(feature, "provider", None) == provider_name
        ):
            usage.append(feature_name)

    for worker_name, template in getattr(config, "worker_templates", {}).items():
        if getattr(template, "provider", None) == provider_name:
            usage.append(f"worker:{worker_name}")
        for fallback in getattr(template, "fallback_providers", []):
            if getattr(fallback, "provider", None) == provider_name:
                usage.append(f"worker_fallback:{worker_name}")

    return usage


def _validate_runtime_provider(provider_name: str, provider_config) -> None:
    if provider_name.lower() not in SUPPORTED_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail=f"Provider '{provider_name}' 当前后端未支持")
    if not _provider_has_api_key(provider_config):
        raise HTTPException(status_code=400, detail=f"Provider '{provider_name}' API Key 未配置")


def _resolve_worker_fallback_routes(
    config,
    body: WorkerConfigUpdate | WorkerCreateRequest,
) -> list[ProviderFallbackConfig]:
    routes: list[ProviderFallbackConfig] = []
    for fallback in body.fallback_providers:
        provider_name = fallback.provider.strip()
        if not provider_name:
            raise HTTPException(status_code=400, detail="Fallback provider 不能为空")
        provider_config = config.providers.get(provider_name)
        if not provider_config:
            raise HTTPException(status_code=400, detail=f"Fallback provider '{provider_name}' 未配置")
        _validate_runtime_provider(provider_name, provider_config)
        routes.append(
            ProviderFallbackConfig(
                provider_type=provider_name,
                api_key=provider_config.resolve_api_key(),
                model=fallback.model or body.model,
                base_url=fallback.base_url or provider_config.base_url,
                headers={
                    **dict(getattr(provider_config, "headers", {}) or {}),
                    **dict(fallback.headers or {}),
                },
            )
        )
    return routes


def _read_config_document(config_path: Path) -> dict:
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise WorkerConfigPersistenceError(f"配置文件不存在: {config_path}") from e
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise WorkerConfigPersistenceError(f"配置文件 YAML 无法解析: {e}") from e
    if not isinstance(data, dict):
        raise WorkerConfigPersistenceError("配置文件顶层必须是 YAML mapping")
    return data


def _section_span(text: str, key: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    start_line: int | None = None
    for index, line in enumerate(lines):
        if _re.match(rf"^{_re.escape(key)}:\s*(?:#.*)?$", line.rstrip("\n")):
            start_line = index
            break
    if start_line is None:
        return None

    end_line = len(lines)
    for index in range(start_line + 1, len(lines)):
        if _re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$", lines[index].rstrip("\n")):
            end_line = index
            break

    while end_line > start_line + 1:
        previous = lines[end_line - 1].strip()
        if previous and not previous.startswith("#"):
            break
        end_line -= 1

    start = sum(len(line) for line in lines[:start_line])
    end = sum(len(line) for line in lines[:end_line])
    return start, end


def _dump_worker_templates_section(worker_templates: dict) -> str:
    return yaml.safe_dump(
        {"worker_templates": worker_templates},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()


def _replace_worker_templates_section(text: str, worker_templates: dict) -> str:
    new_section = _dump_worker_templates_section(worker_templates)
    span = _section_span(text, "worker_templates")
    if span is None:
        suffix = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{suffix}\n{new_section}\n"
    start, end = span
    return f"{text[:start]}{new_section}\n\n{text[end:].lstrip()}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _persist_worker_template(config_path: Path, worker_id: str, payload: dict) -> None:
    data = _read_config_document(config_path)
    templates = data.setdefault("worker_templates", {})
    if not isinstance(templates, dict):
        raise WorkerConfigPersistenceError("配置项 worker_templates 必须是 YAML mapping")
    if worker_id not in templates:
        raise WorkerConfigPersistenceError(f"配置文件中不存在 Worker 模板: {worker_id}")
    templates[worker_id] = payload
    Config._from_dict(data)
    _atomic_write_text(config_path, _replace_worker_templates_section(config_path.read_text(encoding="utf-8"), templates))


def _persist_new_worker_template(config_path: Path, worker_id: str, payload: dict) -> None:
    data = _read_config_document(config_path)
    templates = data.setdefault("worker_templates", {})
    if not isinstance(templates, dict):
        raise WorkerConfigPersistenceError("配置项 worker_templates 必须是 YAML mapping")
    if worker_id in templates:
        raise WorkerConfigPersistenceError(f"配置文件中已存在 Worker 模板: {worker_id}")
    templates[worker_id] = payload
    Config._from_dict(data)
    _atomic_write_text(config_path, _replace_worker_templates_section(config_path.read_text(encoding="utf-8"), templates))


def _delete_worker_template(config_path: Path, worker_id: str) -> None:
    data = _read_config_document(config_path)
    templates = data.get("worker_templates", {})
    if not isinstance(templates, dict):
        raise WorkerConfigPersistenceError("配置项 worker_templates 必须是 YAML mapping")
    if worker_id in templates:
        templates.pop(worker_id)
        Config._from_dict(data)
        _atomic_write_text(
            config_path,
            _replace_worker_templates_section(config_path.read_text(encoding="utf-8"), templates),
        )


@router.get("/workers")
async def list_workers() -> list[dict]:
    """列出所有 Worker"""
    worker_pool = get_worker_pool()
    if not worker_pool:
        return []
    return worker_pool.list_workers()


@router.get("/workers/{worker_id}/logs")
async def get_worker_logs(worker_id: str, limit: int = 50) -> dict:
    """获取指定 Worker 的最近日志"""
    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    worker = worker_pool._workers[worker_id]
    return {
        "worker_id": worker_id,
        "logs": worker.get_logs(limit),
    }


@router.delete("/workers/{worker_id}/logs")
async def clear_worker_logs(
    worker_id: str,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """清空指定 Worker 的日志（仅管理员）"""
    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    worker_pool._workers[worker_id].clear_logs()
    return {"success": True, "message": "日志已清空"}


@router.get("/providers")
async def list_providers() -> list[dict]:
    """列出可用的 Provider 及其模型"""
    _config = _get_config()
    if _config is None:
        return []

    provider_models: dict[str, set[str]] = {}
    for template in _config.worker_templates.values():
        provider_models.setdefault(template.provider, set()).add(template.model)
        for fallback in getattr(template, "fallback_providers", []):
            fallback_provider = getattr(fallback, "provider", "")
            if fallback_provider:
                provider_models.setdefault(fallback_provider, set()).add(
                    getattr(fallback, "model", "") or template.model
                )
    provider_models.setdefault(_config.coordinator.provider, set()).add(_config.coordinator.model)

    result = []
    for name, provider_config in _config.providers.items():
        capabilities = get_provider_capabilities(name)
        models = provider_models.get(name, set())
        if capabilities:
            models.update(capabilities.well_known_models)
        configured = _provider_has_api_key(provider_config)
        supported = name.lower() in SUPPORTED_PROVIDER_TYPES
        used_by = _provider_usage(_config, name)
        warnings: list[str] = []
        if not supported:
            warnings.append("当前后端未支持该 Provider 类型")
        if used_by and not configured:
            warnings.append("该 Provider 正被功能引用，但 API Key 未配置")
        result.append({
            "name": name,
            "models": sorted(models),
            "configured": configured,
            "supported": supported,
            "env_var": _provider_env_var(provider_config),
            "base_url": getattr(provider_config, "base_url", ""),
            "used_by": used_by,
            "capabilities": capabilities.to_dict() if capabilities else None,
            "warnings": warnings,
        })
    return result


@router.put("/workers/{worker_id}/config")
async def update_worker_config(
    worker_id: str,
    body: WorkerConfigUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """更新 Worker 配置并持久化到 config.yaml（仅管理员）"""
    _config = _get_config()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")

    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker = worker_pool._workers[worker_id]
    if worker.is_busy:
        raise HTTPException(status_code=409, detail="Worker 正在执行任务，无法修改配置")

    provider_config = _config.providers.get(body.provider)
    if not provider_config:
        raise HTTPException(status_code=400, detail=f"Provider '{body.provider}' 未配置")
    _validate_runtime_provider(body.provider, provider_config)
    fallback_routes = _resolve_worker_fallback_routes(_config, body)

    try:
        _persist_worker_template(_config_path(), worker_id, _worker_template_payload(body))
    except WorkerConfigPersistenceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    worker_provider = create_provider(
        body.provider,
        provider_config.resolve_api_key(),
        base_url=provider_config.base_url,
        headers=provider_config.headers,
    )

    worker.config.provider_type = body.provider
    worker.config.model = body.model
    worker.config.skills = body.skills
    worker.config.tools = body.tools
    worker.config.fallback_providers = fallback_routes

    from skills.tool import LoadSkillTool
    if body.skills:
        skills_dir = Path(_config.knowledge_base.skills_dir)
        worker.tools.register(LoadSkillTool(skills_dir, set(body.skills)))
    else:
        worker.tools.unregister("load_skill")
    worker.config.temperature = body.temperature
    worker.config.max_tokens = body.max_tokens
    worker.config.icon = body.icon
    worker.config.display_name = body.display_name
    worker.refresh_provider_routes(worker_provider)

    if worker_id in _config.worker_templates:
        tpl = _config.worker_templates[worker_id]
        tpl.provider = body.provider
        tpl.model = body.model
        tpl.skills = body.skills
        tpl.tools = body.tools
        tpl.temperature = body.temperature
        tpl.fallback_providers = [
            WorkerFallbackProviderConfig(
                provider=item.provider,
                model=item.model,
                base_url=item.base_url,
                headers=dict(item.headers),
            )
            for item in body.fallback_providers
        ]
        tpl.icon = body.icon
        tpl.display_name = body.display_name

    return {"success": True, "message": f"Worker '{worker_id}' 配置已更新"}


@router.post("/workers")
async def create_worker(
    body: WorkerCreateRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """新增 Worker 并持久化到 config.yaml（仅管理员）"""
    _config = _get_config()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")

    worker_pool = get_worker_pool()
    if not worker_pool:
        raise HTTPException(status_code=500, detail="Worker pool not initialized")

    name = body.name.strip()
    if not name or not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise HTTPException(status_code=400, detail="名称只能包含字母、数字和下划线，且以字母或下划线开头")
    if name in worker_pool._workers:
        raise HTTPException(status_code=409, detail=f"Worker '{name}' 已存在")

    provider_config = _config.providers.get(body.provider)
    if not provider_config:
        raise HTTPException(status_code=400, detail=f"Provider '{body.provider}' 未配置")
    _validate_runtime_provider(body.provider, provider_config)
    fallback_routes = _resolve_worker_fallback_routes(_config, body)

    worker_config = WorkerConfig(
        name=name,
        provider_type=body.provider,
        api_key=provider_config.resolve_api_key(),
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        tools=body.tools,
        skills=body.skills,
        fallback_providers=fallback_routes,
        icon=body.icon,
        display_name=body.display_name,
    )
    worker_provider = create_provider(
        body.provider,
        provider_config.resolve_api_key(),
        base_url=provider_config.base_url,
        headers=provider_config.headers,
    )

    try:
        _persist_new_worker_template(_config_path(), name, _worker_template_payload(body))
    except WorkerConfigPersistenceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    from config import WorkerTemplate
    _config.worker_templates[name] = WorkerTemplate(
        model=body.model,
        provider=body.provider,
        temperature=body.temperature,
        skills=body.skills,
        tools=body.tools,
        fallback_providers=[
            WorkerFallbackProviderConfig(
                provider=item.provider,
                model=item.model,
                base_url=item.base_url,
                headers=dict(item.headers),
            )
            for item in body.fallback_providers
        ],
        icon=body.icon,
        display_name=body.display_name,
    )
    worker_pool.register_worker(WorkerAgent(worker_config, ToolRegistry(), worker_provider))

    return {"success": True, "message": f"Worker '{name}' 已创建"}


@router.delete("/workers/{worker_id}")
async def delete_worker(
    worker_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除 Worker 并从 config.yaml 移除（仅管理员）"""
    _config = _get_config()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")

    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker = worker_pool._workers[worker_id]
    if worker.is_busy:
        raise HTTPException(status_code=409, detail="Worker 正在执行任务，无法删除")

    if len(worker_pool._workers) <= 1:
        raise HTTPException(status_code=400, detail="至少需要保留一个 Worker")

    try:
        _delete_worker_template(_config_path(), worker_id)
    except WorkerConfigPersistenceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    worker_pool.unregister_worker(worker_id)
    _config.worker_templates.pop(worker_id, None)

    _audit_log_from_api(request, user, "delete", "worker", worker_id)
    return {"success": True, "message": f"Worker '{worker_id}' 已删除"}
