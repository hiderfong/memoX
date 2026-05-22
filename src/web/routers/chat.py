"""Chat router — sessions, memory, streaming and non-streaming chat"""
import asyncio
import json as _json
import re as _re
import uuid
from typing import Annotated

import httpx as _httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.base_agent import create_provider
from agents.worker_pool import get_worker_pool
from auth import AuthUser, get_current_user, require_role
from imaging import get_i2v_client, get_image_client, get_video_client
from knowledge import SearchResult
from web.state import get_store as _gs

router = APIRouter(prefix="/api", tags=["chat"])

# ── Model classes ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    use_rag: bool = True
    stream: bool = True
    active_group_ids: list[str] | None = None
    worker_id: str | None = None


class SessionUpdateRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


class MemorySummaryRequest(BaseModel):
    session_id: str
    summary: str
    force: bool = False


class MemoryConfigRequest(BaseModel):
    enabled: bool | None = None
    max_turns_before_compress: int | None = None
    summary_max_chars: int | None = None


class SummarizeTaskRequest(BaseModel):
    task_type: str | None = None


_TASK_TYPE_OPTIONS = [
    "撰写文档", "撰写PPT", "开发应用",
    "配置定时任务", "生成参考图片视频",
]

# ── Helper functions ────────────────────────────────────────────────────────

_I2V_RE = _re.compile(r"\[\[I2V:\s*(.+?)\s*\|\s*(.+?)\]\]", flags=_re.DOTALL)


def parse_i2v_markers(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for url, prompt in _I2V_RE.findall(text):
        url = url.strip()
        prompt = prompt.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if not prompt:
            continue
        out.append((url, prompt))
    return out


def _get_globals():
    import web.api as _api_module
    return (
        _api_module._rag_engine,
        _api_module._orchestrator,
        _api_module._task_planner,
        _api_module._memory_manager,
        _api_module._preference_learner,
        _api_module._memory_recall,
        _api_module._config,
        _api_module._task_results,
    )


def _load_chat_history(session_id: str) -> list[dict]:
    _HISTORY_TURN_LIMIT = 20
    store = _gs()
    if not store:
        return []
    try:
        rows = store.get_session_messages(session_id)
    except Exception:
        return []
    if not rows:
        return []
    rows = rows[-_HISTORY_TURN_LIMIT:]
    cleaned: list[dict] = []
    for r in rows:
        role = r.get("role")
        if role not in ("user", "assistant"):
            continue
        content = r.get("content") or ""
        content = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", content, flags=_re.DOTALL)
        content = _re.sub(r"!\[[^\]]*\]\(https?://[^\s)]+\)", "", content)
        content = _re.sub(r"\[video:[^\]]*\]\(https?://[^\s)]+\)", "", content)
        content = _re.sub(r"\n{3,}", "\n\n", content).strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _inject_history(messages: list[dict], session_id: str) -> list[dict]:
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()

    if _memory_manager:
        summary, history = _memory_manager.get_context(session_id)
        if history or summary:
            if summary:
                summary_msg = {
                    "role": "system",
                    "content": f"【会话记忆摘要】{summary}\n（以上为历史对话摘要，以下为最近对话）",
                }
                inject_idx = 1
                for i, m in enumerate(messages[:-1]):
                    if m.get("role") == "system":
                        inject_idx = i + 1
                messages = messages[:inject_idx] + [summary_msg] + messages[inject_idx:]
            if not messages or messages[-1].get("role") != "user":
                messages = messages + history
            else:
                messages = messages[:-1] + history + messages[-1:]
            if _preference_learner:
                pref_text = _preference_learner.get_and_format(limit=8)
                if pref_text:
                    messages = messages + [{"role": "system", "content": pref_text}]
            return messages

    history = _load_chat_history(session_id)
    if not history:
        return messages
    if not messages or messages[-1].get("role") != "user":
        messages = messages + history
    else:
        messages = messages[:-1] + history + messages[-1:]
    if _preference_learner:
        pref_text = _preference_learner.get_and_format(limit=8)
        if pref_text:
            messages = messages + [{"role": "system", "content": pref_text}]
    return messages


def _resolve_chat_llm(worker_id: str | None):
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()

    if worker_id:
        worker_pool = get_worker_pool()
        if worker_pool and worker_id in worker_pool._workers:
            w = worker_pool._workers[worker_id]
            pcfg = _config.providers.get(w.config.provider_type)
            if not pcfg:
                raise HTTPException(status_code=400, detail=f"Worker '{worker_id}' 的 provider '{w.config.provider_type}' 未配置")
            provider = create_provider(
                w.config.provider_type,
                pcfg.resolve_api_key(),
                base_url=pcfg.base_url,
                headers=pcfg.headers,
            )
            return provider, w.config.model, w.config.temperature, w.config.max_tokens, worker_id
        else:
            raise HTTPException(status_code=404, detail=f"Worker '{worker_id}' 不存在")

    pcfg = _config.providers.get(_config.coordinator.provider)
    if not pcfg:
        raise HTTPException(status_code=500, detail="LLM provider not configured")
    api_key = pcfg.resolve_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail=f"LLM API Key 未配置（provider: {_config.coordinator.provider}）")
    provider = create_provider(
        _config.coordinator.provider,
        api_key,
        base_url=pcfg.base_url,
        headers=pcfg.headers,
    )
    return provider, _config.coordinator.model, _config.coordinator.temperature, _config.coordinator.max_tokens, None


# ── Non-streaming chat ─────────────────────────────────────────────────────

@router.post("/chat")
async def chat(request: Request, chat_req: ChatRequest) -> dict:
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()

    session_id = chat_req.session_id or str(uuid.uuid4())[:8]
    session = _rag_engine.get_session(session_id)
    if not session:
        session = _rag_engine.create_session()

    _rag_engine.add_message(session_id, "user", chat_req.message)

    search_results: list[SearchResult] = []
    if chat_req.use_rag:
        search_results = await _rag_engine.search(chat_req.message, group_ids=chat_req.active_group_ids)

    from imaging import get_image_client, get_video_client
    image_client = get_image_client()
    video_client = get_video_client()
    media_instruction = ""
    if image_client:
        media_instruction += (
            "\n\n【图像生成能力】当用户明确要求生成/绘制/画一张图片、插图、海报、参考图、示意图等视觉内容时，"
            "你必须在回答的合适位置输出形如 [[IMAGE: 英文或中文的详细画面描述]] 的标记（每张图片一个标记）。"
            "系统会自动将标记替换为真实图像展示给用户。描述要具体、包含风格、主体、场景、光线等要素。"
            "不要在标记外再贴链接，不要解释这是占位符。若用户未要求图片，则不要输出此标记。"
        )
    if video_client:
        media_instruction += (
            "\n\n【视频生成能力】当用户明确要求生成/制作一段视频、短片、动画、演示视频等动态视觉内容时，"
            "你必须在回答的合适位置输出形如 [[VIDEO: 详细画面与动作描述]] 的标记（每段视频一个标记）。"
            "描述要包含主体、动作、场景、时长氛围等。视频生成耗时较长（30s~数分钟），请如实告知用户需要等待。"
            "不要在标记外贴链接，不要解释这是占位符。若用户未要求视频，则不要输出此标记。"
        )
    if search_results:
        messages = _rag_engine.build_rag_prompt(chat_req.message, search_results)
        if media_instruction:
            messages[0]["content"] = (messages[0]["content"] or "") + media_instruction
    else:
        messages = [
            {"role": "system", "content": "你是一个智能助手。" + media_instruction},
            {"role": "user", "content": chat_req.message},
        ]

    messages = _inject_history(messages, session_id)
    provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(chat_req.worker_id)

    try:
        response = await provider.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        if isinstance(e, _httpx.HTTPStatusError):
            status = e.response.status_code
            if status == 401:
                raise HTTPException(status_code=502, detail="LLM API Key 无效或已过期") from e
            elif status == 429:
                raise HTTPException(status_code=429, detail="LLM API 请求频率超限，请稍后重试") from e
            else:
                raise HTTPException(status_code=502, detail=f"LLM 服务返回错误 {status}") from e
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}") from e

    raw_answer = response.content or "抱歉，我无法回答这个问题。"

    image_prompts = _re.findall(r"\[\[IMAGE:\s*(.+?)\]\]", raw_answer, flags=_re.DOTALL)
    video_prompts = _re.findall(r"\[\[VIDEO:\s*(.+?)\]\]", raw_answer, flags=_re.DOTALL)
    display_text = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", raw_answer, flags=_re.DOTALL).strip()
    i2v_pairs = parse_i2v_markers(raw_answer)

    image_results: list[dict] = []
    if image_prompts and image_client:
        for p in image_prompts:
            ptext = p.strip()
            try:
                urls = await image_client.generate(ptext)
                for u in urls:
                    image_results.append({"url": u, "prompt": ptext})
            except Exception as ie:
                image_results.append({"error": str(ie), "prompt": ptext})

    video_results: list[dict] = []
    if video_prompts and video_client:
        for p in video_prompts:
            ptext = p.strip()
            try:
                url = await video_client.generate(ptext)
                video_results.append({"url": url, "prompt": ptext})
            except Exception as ve:
                video_results.append({"error": str(ve), "prompt": ptext})

    i2v_results: list[dict] = []
    if i2v_pairs:
        from imaging import get_i2v_client
        i2v_client = get_i2v_client()
        if i2v_client:
            for image_url, prompt_text in i2v_pairs:
                try:
                    url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                    i2v_results.append({"url": url, "prompt": prompt_text, "source_image_url": image_url})
                except Exception as ve:
                    i2v_results.append({"error": str(ve), "prompt": prompt_text, "image_url": image_url})

    answer = display_text
    md_parts: list[str] = []
    for r in image_results:
        if r.get("url"):
            md_parts.append(f"![{r.get('prompt','')}]({r['url']})")
        elif r.get("error"):
            md_parts.append(f"_图像生成失败: {r['error']}_")
    for r in video_results:
        if r.get("url"):
            md_parts.append(f"[video:{r.get('prompt','')}]({r['url']})")
        elif r.get("error"):
            md_parts.append(f"_视频生成失败: {r['error']}_")
    for r in i2v_results:
        if r.get("url"):
            md_parts.append(f"[video:{r.get('prompt','')}]({r['url']})")
    if md_parts:
        answer = (display_text + "\n\n" + "\n\n".join(md_parts)).strip()

    _rag_engine.add_message(session_id, "assistant", answer)
    store = _gs()
    if store:
        store.save_message(session_id, "user", chat_req.message)
        store.save_message(session_id, "assistant", answer)
        existing = store.get_session_messages(session_id)
        if len(existing) <= 2:
            title = chat_req.message[:30].strip()
            store.update_session_title(session_id, title)
        if _memory_manager and _orchestrator:
            await _memory_manager.compress_if_needed_async(session_id, _orchestrator._provider)

    cited_ref_ids = _rag_engine.extract_citations_from_text(answer)
    citations: list[dict] = []
    for ref_id in cited_ref_ids:
        try:
            idx = int(ref_id.split("-")[1]) - 1
            if 0 <= idx < len(search_results):
                r = search_results[idx]
                citation = r.citation
                if citation:
                    citations.append(citation.to_dict())
        except (ValueError, IndexError):
            pass

    return {
        "session_id": session_id,
        "answer": answer,
        "worker_id": resolved_worker_id,
        "images": image_results,
        "videos": video_results,
        "i2v": i2v_results,
        "citations": citations,
        "sources": [
            {
                "content": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "score": r.score,
                "filename": r.metadata.get("filename", "unknown"),
                "doc_id": r.metadata.get("doc_id", ""),
                "chunk_index": r.metadata.get("chunk_index", 0),
            }
            for r in search_results
        ],
    }


# ── Chat sessions ───────────────────────────────────────────────────────────

@router.get("/chat/sessions")
async def list_chat_sessions(archived: str | None = None) -> list[dict]:
    store = _gs()
    if not store:
        return []
    if archived is None:
        flag: bool | None = False
    else:
        v = archived.lower()
        if v == "all":
            flag = None
        elif v in ("1", "true", "yes"):
            flag = True
        else:
            flag = False
    return store.list_sessions(archived=flag)


@router.patch("/chat/sessions/{session_id}")
async def update_chat_session(session_id: str, request: SessionUpdateRequest) -> dict:
    from fastapi import HTTPException
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    if request.title is None and request.archived is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    touched = False
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        if len(title) > 100:
            raise HTTPException(status_code=400, detail="Title too long (max 100)")
        if not store.rename_session(session_id, title):
            raise HTTPException(status_code=404, detail="Session not found")
        touched = True
    if request.archived is not None:
        if not store.set_session_archived(session_id, request.archived):
            raise HTTPException(status_code=404, detail="Session not found")
        touched = True
    return {"success": touched}


@router.get("/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> list[dict]:
    from fastapi import HTTPException
    store = _gs()
    if store:
        messages = store.get_session_messages(session_id)
        if messages:
            return messages
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/chat/sessions/{session_id}/compress")
async def compress_session(session_id: str, force: bool = False) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    provider = _orchestrator._provider if _orchestrator else None
    if not provider:
        raise HTTPException(status_code=503, detail="LLM provider 未就绪")
    try:
        new_summary, archived_count = await _memory_manager.compress_if_needed_async(session_id, provider, force=force)
        return {
            "success": True,
            "session_id": session_id,
            "summary": new_summary,
            "archived_messages": archived_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"压缩失败: {e}") from e


@router.get("/chat/sessions/{session_id}/memory")
async def get_session_memory(session_id: str) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    summary, history = _memory_manager.get_context(session_id)
    return {
        "session_id": session_id,
        "summary": summary,
        "uncompressed_count": len(history),
        "is_compressed": summary is not None,
    }


@router.post("/chat/sessions/{session_id}/memory")
async def update_session_memory_summary(session_id: str, req: MemorySummaryRequest) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    store.save_session_summary(session_id, req.summary)
    return {"success": True, "session_id": session_id}


@router.get("/memory/config")
async def get_memory_config() -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    mc = _config.memory
    return {
        "enabled": mc.enabled,
        "max_turns_before_compress": mc.max_turns_before_compress,
        "summary_max_chars": mc.summary_max_chars,
        "recent_messages_to_keep": mc.recent_messages_to_keep,
    }


@router.patch("/memory/config")
async def update_memory_config(
    req: MemoryConfigRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_manager or not _config:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    if req.enabled is not None:
        _config.memory.enabled = req.enabled
    if req.max_turns_before_compress is not None:
        _config.memory.max_turns_before_compress = req.max_turns_before_compress
        _memory_manager._max_turns = req.max_turns_before_compress
    if req.summary_max_chars is not None:
        _config.memory.summary_max_chars = req.summary_max_chars
        _memory_manager._summary_max_chars = req.summary_max_chars
    return {"success": True}


@router.delete("/chat/sessions/{session_id}/memory")
async def clear_session_memory(
    session_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    store.save_session_summary(session_id, "")
    store.clear_archived_messages(session_id)
    return {"success": True, "session_id": session_id}


@router.post("/chat/sessions/{session_id}/extract-memories")
async def extract_memories_from_session(session_id: str) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    messages = store.get_session_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话不存在或无消息")
    provider = _orchestrator._provider if _orchestrator else None
    count = await _memory_recall.save_from_conversation_async(
        messages=messages,
        session_id=session_id,
        llm_provider=provider,
    )
    return {"success": True, "session_id": session_id, "extracted_count": count}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if _rag_engine:
        _rag_engine.delete_session(session_id)
    return {"success": True}


@router.post("/chat/sessions/{session_id}/summarize-task")
async def summarize_session_as_task(session_id: str, request: SummarizeTaskRequest) -> dict:
    from fastapi import HTTPException
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    history = store.get_session_messages(session_id)
    if not history:
        raise HTTPException(status_code=404, detail="Session not found or empty")

    convo_lines = []
    for m in history:
        role = {"user": "用户", "assistant": "AI助手", "system": "系统"}.get(m["role"], m["role"])
        convo_lines.append(f"【{role}】{m['content']}")
    conversation = "\n\n".join(convo_lines)

    options_str = "、".join(_TASK_TYPE_OPTIONS)
    chosen_type_hint = (
        f"\n\n用户已明确任务类型为：「{request.task_type}」。请按该类型组织摘要，不要再询问。"
        if request.task_type else ""
    )

    system_prompt = f"""你是一个任务编排助手。用户刚刚与另一个 AI 助手进行了一段对话，现在希望把对话内容提炼为一个独立、可执行的任务描述，交给下游 worker 执行。

你的职责：
1. 阅读整段对话，识别用户真实意图与最终期望的"产物"（交付物）。
2. 如果你能明确判断交付物形态（例如 {options_str}），就直接输出一段自包含的任务描述。
3. 如果你无法唯一确定交付物形态，就向用户提一个澄清问题，列出候选任务类型供用户选择。

严格使用以下 JSON 格式输出，不要输出任何 JSON 之外的文字、注释或 markdown 代码块：

情况 A（可直接生成）：
{{"status": "ready", "summary": "<一段自包含的任务描述，包含：背景、目标、关键输入/约束、期望产物与交付形式>"}}

情况 B（需要用户澄清任务类型）：
{{"status": "need_clarification", "question": "<给用户看的简短追问>", "options": ["撰写文档", "撰写PPT", ...]}}

要求：
- summary 用中文书写，需要承载下游 worker 执行所需的全部关键信息，不要保留"你/我"这类对话口吻，改为第三人称任务表述。
- summary 结尾应显式标注"交付形式：xxx"。
- 候选 options 必须从以下列表中选取：{options_str}。{chosen_type_hint}
"""

    user_prompt = f"以下是完整的对话历史，请按要求输出 JSON：\n\n{conversation}"
    provider, model, temperature, max_tokens, _ = _resolve_chat_llm(None)

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.3,
            max_tokens=max_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}") from e

    raw = (response.content or "").strip()
    fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, _re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        brace = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if brace:
            raw = brace.group(0)

    try:
        parsed = _json.loads(raw)
    except Exception:
        return {"status": "ready", "summary": response.content or "", "raw_fallback": True}

    status = parsed.get("status")
    if status == "need_clarification" and not request.task_type:
        opts = [o for o in (parsed.get("options") or []) if o in _TASK_TYPE_OPTIONS]
        if not opts:
            opts = list(_TASK_TYPE_OPTIONS)
        return {
            "status": "need_clarification",
            "question": parsed.get("question") or "请选择期望的任务类型：",
            "options": opts,
        }

    summary = parsed.get("summary") or ""
    if request.task_type and request.task_type not in summary:
        summary = f"{summary}\n\n任务类型：{request.task_type}".strip()
    return {"status": "ready", "summary": summary}


# ── Streaming chat ─────────────────────────────────────────────────────────

@router.post("/chat/stream")
async def chat_stream(request: Request, chat_req: ChatRequest):
    (
        _rag_engine, _orchestrator, _task_planner,
        _memory_manager, _preference_learner, _memory_recall,
        _config, _task_results,
    ) = _get_globals()

    _session_id = chat_req.session_id or str(uuid.uuid4())[:8]
    _message = chat_req.message
    _use_rag = chat_req.use_rag
    _active_group_ids = chat_req.active_group_ids
    _worker_id = chat_req.worker_id

    async def generate():
        session = _rag_engine.get_session(_session_id)
        if not session:
            session = _rag_engine.create_session()
        _rag_engine.add_message(_session_id, "user", _message)

        search_results: list[SearchResult] = []
        if _use_rag:
            search_results = await _rag_engine.search(_message, group_ids=_active_group_ids)
            sources_data = [
                {"filename": r.metadata.get("filename", "unknown"), "score": r.score,
                 "doc_id": r.metadata.get("doc_id", ""), "chunk_index": r.metadata.get("chunk_index", 0)}
                for r in search_results
            ]
            yield f"data: {_json.dumps({'type': 'sources', 'data': sources_data})}\n\n"

        image_client = get_image_client()
        video_client = get_video_client()
        media_instruction = ""
        if image_client:
            media_instruction += (
                "\n\n【图像生成能力】当用户明确要求生成/绘制/画一张图片、插图、海报、参考图、示意图等视觉内容时，"
                "你必须在回答的合适位置输出形如 [[IMAGE: 英文或中文的详细画面描述]] 的标记（每张图片一个标记）。"
                "系统会自动将标记替换为真实图像展示给用户。描述要具体、包含风格、主体、场景、光线等要素。"
                "不要在标记外再贴链接，不要解释这是占位符。若用户未要求图片，则不要输出此标记。"
            )
        if video_client:
            media_instruction += (
                "\n\n【视频生成能力】当用户明确要求生成/制作一段视频、短片、动画、演示视频等动态视觉内容时，"
                "你必须在回答的合适位置输出形如 [[VIDEO: 详细画面与动作描述]] 的标记（每段视频一个标记）。"
                "描述要包含主体、动作、场景、时长氛围等。视频生成耗时较长（30s~数分钟），请如实告知用户需要等待。"
                "不要在标记外贴链接，不要解释这是占位符。若用户未要求视频，则不要输出此标记。"
            )
        if search_results:
            messages = _rag_engine.build_rag_prompt(_message, search_results)
            if media_instruction:
                messages[0]["content"] = (messages[0]["content"] or "") + media_instruction
        else:
            messages = [
                {"role": "system", "content": "你是一个智能助手。" + media_instruction},
                {"role": "user", "content": _message},
            ]

        messages = _inject_history(messages, _session_id)

        try:
            provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(_worker_id)
        except HTTPException as exc:
            yield f"data: {_json.dumps({'type': 'error', 'message': exc.detail})}\n\n"
            return

        try:
            from agents.base_agent import LLMResponse
            q = asyncio.Queue()

            def _on_chunk(c: str):
                q.put_nowait(c)

            async def _run_stream():
                try:
                    resp = await provider.chat_stream(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        on_chunk=_on_chunk,
                    )
                    q.put_nowait(resp)
                except Exception as exc:
                    q.put_nowait(exc)

            stream_task = asyncio.create_task(_run_stream())

            raw_text_parts = []
            buffer = ""
            while True:
                item = await q.get()
                if isinstance(item, Exception):
                    yield f"data: {_json.dumps({'type': 'error', 'message': str(item)})}\n\n"
                    return
                elif isinstance(item, LLMResponse):
                    response = item
                    if buffer:
                        yield f"data: {_json.dumps({'type': 'chunk', 'content': buffer})}\n\n"
                    break
                else:
                    chunk = item
                    raw_text_parts.append(chunk)
                    buffer += chunk

                    while buffer:
                        idx = buffer.find("[[")
                        if idx == -1:
                            if buffer:
                                yield f"data: {_json.dumps({'type': 'chunk', 'content': buffer})}\n\n"
                                buffer = ""
                            break

                        if idx > 0:
                            yield f"data: {_json.dumps({'type': 'chunk', 'content': buffer[:idx]})}\n\n"
                            buffer = buffer[idx:]

                        end_idx = buffer.find("]]")
                        if end_idx != -1:
                            tag = buffer[:end_idx+2]
                            if tag.startswith("[[IMAGE:") or tag.startswith("[[VIDEO:") or tag.startswith("[[I2V:"):
                                pass
                            else:
                                yield f"data: {_json.dumps({'type': 'chunk', 'content': tag})}\n\n"
                            buffer = buffer[end_idx+2:]
                        else:
                            if len(buffer) > 1000:
                                yield f"data: {_json.dumps({'type': 'chunk', 'content': buffer[:2]})}\n\n"
                                buffer = buffer[2:]
                            else:
                                break

            raw_text = "".join(raw_text_parts)
            image_prompts = _re.findall(r"\[\[IMAGE:\s*(.+?)\]\]", raw_text, flags=_re.DOTALL)
            video_prompts = _re.findall(r"\[\[VIDEO:\s*(.+?)\]\]", raw_text, flags=_re.DOTALL)
            display_text = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", raw_text, flags=_re.DOTALL).strip()
            i2v_pairs = parse_i2v_markers(raw_text)

            image_urls: list[str] = []
            if image_prompts and image_client:
                for p in image_prompts:
                    prompt_text = p.strip()
                    yield f"data: {_json.dumps({'type': 'image_pending', 'prompt': prompt_text})}\n\n"
                    try:
                        urls = await image_client.generate(prompt_text)
                        for u in urls:
                            image_urls.append(u)
                            yield f"data: {_json.dumps({'type': 'image', 'url': u, 'prompt': prompt_text})}\n\n"
                    except Exception as ie:
                        yield f"data: {_json.dumps({'type': 'image_error', 'prompt': prompt_text, 'message': str(ie)})}\n\n"

            video_urls: list[tuple[str, str]] = []
            if video_prompts and video_client:
                for p in video_prompts:
                    prompt_text = p.strip()
                    yield f"data: {_json.dumps({'type': 'video_pending', 'prompt': prompt_text})}\n\n"
                    try:
                        url = await video_client.generate(prompt_text)
                        video_urls.append((url, prompt_text))
                        yield f"data: {_json.dumps({'type': 'video', 'url': url, 'prompt': prompt_text})}\n\n"
                    except Exception as ve:
                        yield f"data: {_json.dumps({'type': 'video_error', 'prompt': prompt_text, 'message': str(ve)})}\n\n"

            i2v_results: list[tuple[str, str, str]] = []
            if i2v_pairs:
                i2v_client = get_i2v_client()
                for image_url, prompt_text in i2v_pairs:
                    if not i2v_client:
                        yield f"data: {_json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': '图生视频未启用'})}\n\n"
                        continue
                    yield f"data: {_json.dumps({'type': 'i2v_pending', 'prompt': prompt_text, 'image_url': image_url})}\n\n"
                    try:
                        url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                        i2v_results.append((url, prompt_text, image_url))
                        yield f"data: {_json.dumps({'type': 'i2v', 'url': url, 'prompt': prompt_text, 'source_image_url': image_url})}\n\n"
                    except Exception as ie:
                        yield f"data: {_json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': str(ie)})}\n\n"

            answer = display_text
            md_tail: list[str] = []
            if image_urls:
                md_tail += [f"![image]({u})" for u in image_urls]
            if video_urls:
                md_tail += [f"[video:{pt}]({u})" for u, pt in video_urls]
            if i2v_results:
                md_tail += [f"[video:{pt}]({u})" for u, pt, _ in i2v_results]
            if md_tail:
                answer = display_text + "\n\n" + "\n".join(md_tail)
            _rag_engine.add_message(_session_id, "assistant", answer)

            store = _gs()
            if store:
                store.save_message(_session_id, "user", _message)
                store.save_message(_session_id, "assistant", answer)
                existing = store.get_session_messages(_session_id)
                if len(existing) <= 2:
                    title = _message[:30].strip()
                    store.update_session_title(_session_id, title)
                if _memory_manager and _orchestrator:
                    await _memory_manager.compress_if_needed_async(_session_id, _orchestrator._provider)

            cited_ref_ids = _rag_engine.extract_citations_from_text(answer)
            citations: list[dict] = []
            for ref_id in cited_ref_ids:
                try:
                    idx = int(ref_id.split("-")[1]) - 1
                    if 0 <= idx < len(search_results):
                        r = search_results[idx]
                        citation = r.citation
                        if citation:
                            citations.append(citation.to_dict())
                except (ValueError, IndexError):
                    pass

            yield f"data: {_json.dumps({'type': 'done', 'session_id': _session_id, 'worker_id': resolved_worker_id, 'citations': citations})}\n\n"

        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
