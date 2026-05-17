"""Skills router — skill registry, search, install, uninstall"""
import asyncio
import contextlib
import json as _json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth import AuthUser, require_role

router = APIRouter(prefix="/api/skills", tags=["skills"])

# ── Model classes ──────────────────────────────────────────────────────────

class SkillInstallRequest(BaseModel):
    source_url: str
    name: str | None = None
    force: bool = False


class SkillLintRequest(BaseModel):
    action: Literal["upsert", "resolve"]  # noqa: N815
    name: str
    contradictions: list[str] = []
    contested: bool = False


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_globals():
    import web.api as _api_module
    return (
        getattr(_api_module, "_config"),
        getattr(_api_module, "_rag_engine"),
    )


def _append_log(data: dict, action: str, name: str, note: str = "") -> None:
    """Append to the in-memory log list within data (used by lint endpoint)."""
    from datetime import datetime
    log: list[dict] = data.setdefault("log", [])
    entry = {
        "action": action,
        "name": name,
        "at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    if len(log) >= 500:
        log[:] = log[-499:]
    log.append(entry)


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_installed_skills() -> dict:
    """列出已安装 skill。"""
    from skills.loader import list_skills as _list_skills
    _config, _ = _get_globals()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")
    skills_dir = Path(_config.knowledge_base.skills_dir)
    out = []
    for s in _list_skills(skills_dir):
        source_url = ""
        meta_path = s.path / ".install.json"
        if meta_path.is_file():
            with contextlib.suppress(OSError, _json.JSONDecodeError):
                source_url = _json.loads(meta_path.read_text(encoding="utf-8")).get("source_url", "")
        out.append({
            "name": s.name,
            "description": s.description,
            "source_url": source_url,
        })
    return {"skills": out}


@router.get("/search")
async def search_skills(q: str = "", limit: int = 10) -> dict:
    """在 registry 里按关键字+向量混合搜索 skill。已安装的会被排除。"""
    from skills.github_meta import enrich_with_repo_meta
    from skills.loader import list_skills as _list_skills
    from skills.registry import load_registry, search_registry

    _config, _rag_engine = _get_globals()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")

    skills_dir = Path(_config.knowledge_base.skills_dir)
    registry_path = Path("data/skills_registry.json")
    meta_cache_path = Path("data/github_meta_cache.json")

    installed = {s.name for s in _list_skills(skills_dir)}
    entries = load_registry(registry_path)

    # Embedding-based search when query is not empty and rag_engine is available
    query_embedding: list[float] | None = None
    if q and _rag_engine is not None and _rag_engine.vector_store is not None:
        try:
            emb_fn = _rag_engine.vector_store.embedding_function
            if emb_fn is not None:
                results_emb = await emb_fn.embed([q])
                query_embedding = results_emb[0]
        except Exception:
            pass  # Fall back to keyword-only search

    results = search_registry(entries, q, installed, limit=limit, query_embedding=query_embedding)
    meta = await enrich_with_repo_meta([r.source_url for r in results], meta_cache_path)

    return {
        "query": q,
        "results": [
            {
                "name": r.name,
                "description": r.description,
                "source_url": r.source_url,
                "score": r.score,
                "stars": meta.get(r.source_url, {}).get("stars"),
                "pushed_at": meta.get(r.source_url, {}).get("pushed_at"),
                "created": r.created,
                "updated": r.updated,
                "tags": r.tags,
                "sources": r.sources,
                "contradictions": r.contradictions,
                "contested": r.contested,
            }
            for r in results
        ],
    }


@router.post("/install")
async def install_skill(body: SkillInstallRequest):
    """从 GitHub 安装 skill，以 SSE 流推送阶段进度。"""
    from skills.installer import install_from_github

    _config, _ = _get_globals()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")

    skills_dir = Path(_config.knowledge_base.skills_dir)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_progress(stage: str, msg: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"stage": stage, "message": msg},
        )

    async def worker() -> None:
        try:
            skill = await asyncio.to_thread(
                install_from_github,
                body.source_url,
                skills_dir,
                body.name,
                body.force,
                on_progress,
            )
            # 回写 registry
            try:
                from skills.registry import upsert_registry_entry
                changed, action = upsert_registry_entry(
                    Path("data/skills_registry.json"),
                    name=skill.name,
                    description=skill.description,
                    source_url=body.source_url,
                )
                if changed:
                    await queue.put({"stage": "registry", "message": f"已登记到 registry ({action})"})
                else:
                    await queue.put({"stage": "registry", "message": "registry 内容无变化"})
            except Exception as e:
                await queue.put({"stage": "registry", "message": f"registry 写入失败: {e}"})
            await queue.put({
                "stage": "success",
                "name": skill.name,
                "description": skill.description,
            })
        except FileExistsError as e:
            await queue.put({"stage": "error", "code": "exists", "message": str(e)})
        except FileNotFoundError as e:
            await queue.put({"stage": "error", "code": "not_found", "message": str(e)})
        except (ValueError, RuntimeError) as e:
            await queue.put({"stage": "error", "code": "invalid", "message": str(e)})
        except Exception as e:
            await queue.put({"stage": "error", "code": "unknown", "message": str(e)})
        finally:
            await queue.put(None)

    async def event_stream():
        asyncio.create_task(worker())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {_json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/rebuild-embeddings")
async def rebuild_skill_embeddings(
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """预计算所有 skill 的描述向量（仅管理员）。"""
    _config, _rag_engine = _get_globals()
    if not _rag_engine or not _rag_engine.vector_store:
        raise HTTPException(status_code=503, detail="RAG engine 未初始化")

    emb_fn = _rag_engine.vector_store.embedding_function
    if emb_fn is None:
        raise HTTPException(status_code=503, detail="embedding function 不可用")

    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")

    from skills.registry import rebuild_embeddings
    count = await rebuild_embeddings(registry_path, emb_fn)
    return {"computed": count, "message": f"已为 {count} 个 skill 计算向量"}


@router.get("/log")
async def get_skill_log(limit: int = 20) -> dict:
    """返回 registry 变更日志（append-only），最新在前。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_change_log
    log = get_change_log(registry_path, limit=limit)
    return {"log": log, "count": len(log)}


@router.get("/contested")
async def get_contested_skills() -> dict:
    """返回所有标记了 contested 的 entry（存在未解决冲突）。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_contested
    entries = get_contested(registry_path)
    return {
        "contested": [
            {
                "name": e.name,
                "description": e.description,
                "contradictions": e.contradictions,
                "updated": e.updated,
            }
            for e in entries
        ],
        "count": len(entries),
    }


@router.get("/tags")
async def get_all_skill_tags() -> dict:
    """返回 registry 中所有已使用的标签（去重、排序）。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_all_tags
    tags = get_all_tags(registry_path)
    return {"tags": tags, "count": len(tags)}


@router.post("/lint")
async def lint_skill_registry(
    body: SkillLintRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """手动更新 skill 的冲突标记（仅管理员）。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")

    data = _json.loads(registry_path.read_text(encoding="utf-8"))
    entries: list[dict] = data.get("skills", [])

    for e in entries:
        if e["name"] == body.name:
            if body.action == "upsert":
                e["contradictions"] = body.contradictions
                e["contested"] = body.contested
                _append_log(data, "conflict_mark", body.name, f"contested={body.contested}")
            elif body.action == "resolve":
                e["contested"] = False
                _append_log(data, "resolve", body.name, "")
            registry_path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return {"ok": True, "name": body.name, "action": body.action}
    raise HTTPException(status_code=404, detail=f"skill '{body.name}' not found in registry")


@router.delete("/{name}")
async def uninstall_skill(
    name: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """卸载已安装 skill（仅管理员）。"""
    from skills.installer import remove_skill as _remove
    _config, _ = _get_globals()
    if not _config:
        raise HTTPException(status_code=500, detail="Config not available")
    skills_dir = Path(_config.knowledge_base.skills_dir)
    try:
        _remove(skills_dir, name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Audit log
    import web.api as _api_module
    _audit_log = getattr(_api_module, "_audit_log", lambda *a, **kw: None)
    _audit_log(request, user, "uninstall", "skill", name)
    return {"success": True, "name": name}
