"""Documents and groups router"""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from auth import AuthUser, require_role

router = APIRouter(prefix="/api", tags=["documents"])


class URLRequest(BaseModel):
    url: str


class DocumentResponse(BaseModel):
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int
    group_id: str = "ungrouped"
    action: Literal["indexed", "skipped", "updated"] = "indexed"


class GroupCreate(BaseModel):
    name: str
    color: str = "#1890ff"


class GroupUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class MoveDocumentGroup(BaseModel):
    group_id: str


class DocumentMediaAsset(BaseModel):
    id: str
    kind: Literal["local_image", "remote_image"]
    name: str
    url: str
    content_type: str | None = None
    access: Literal["signed", "bearer", "public"]


class KnowledgeGraphMergeRequest(BaseModel):
    source: str
    target: str


class KnowledgeGraphTripleRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    source_chunk_id: str = ""
    confidence: float = 1.0


class KnowledgeGraphSplitRequest(BaseModel):
    source: str
    new_entity: str
    triples: list[KnowledgeGraphTripleRequest]


class KnowledgeGraphTripleUpdateRequest(BaseModel):
    old: KnowledgeGraphTripleRequest
    new: KnowledgeGraphTripleRequest


class KnowledgeGraphQualityDecisionRequest(BaseModel):
    candidate_id: str
    status: Literal["accepted", "ignored", "snoozed", "open"]
    note: str = ""
    details: dict | None = None


class KnowledgeGraphQualityDecisionBatchRequest(BaseModel):
    decisions: list[KnowledgeGraphQualityDecisionRequest]


MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB per file


def _clean_required_text(value: str, field_name: str, *, max_len: int = 120) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _get_enabled_knowledge_graph():
    from web.api import _rag_engine

    if not _rag_engine or not getattr(_rag_engine, "_knowledge_graph", None):
        raise HTTPException(status_code=503, detail="知识图谱未初始化或未启用")

    kg = _rag_engine._knowledge_graph
    if not kg.enabled:
        raise HTTPException(status_code=400, detail="知识图谱功能已禁用")
    return kg


def _triple_from_request(request: KnowledgeGraphTripleRequest):
    from knowledge.knowledge_graph import Triple

    subject = _clean_required_text(request.subject, "subject")
    predicate = _clean_required_text(request.predicate, "predicate", max_len=40)
    obj = _clean_required_text(request.object, "object")
    if subject == obj:
        raise HTTPException(status_code=400, detail="subject 与 object 不能相同")
    return Triple(
        subject=subject,
        predicate=predicate,
        object=obj,
        source_chunk_id=" ".join(str(request.source_chunk_id or "").split()).strip()[:120],
        confidence=max(0.0, min(1.0, float(request.confidence))),
    )


def _knowledge_graph_review_decisions() -> dict[str, dict]:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return {}
        return {
            item["candidate_id"]: item
            for item in store.list_knowledge_graph_review_decisions()
        }
    except Exception:
        return {}


def _review_decision_matches_candidate(decision: dict | None, candidate: dict) -> bool:
    if not decision:
        return False
    candidate_fingerprint = candidate.get("fingerprint")
    decision_details = decision.get("details") or {}
    decision_fingerprint = decision_details.get("candidate_fingerprint")
    if not decision_fingerprint or not candidate_fingerprint:
        return True
    return str(decision_fingerprint) == str(candidate_fingerprint)


def _persist_knowledge_graph_quality_decision(
    request: KnowledgeGraphQualityDecisionRequest,
    user: AuthUser,
) -> dict:
    from storage import get_store

    candidate_id = _clean_required_text(request.candidate_id, "candidate_id", max_len=512)
    store = get_store()
    if store is None:
        raise HTTPException(status_code=503, detail="持久化存储未初始化")
    try:
        return store.set_knowledge_graph_review_decision(
            candidate_id,
            request.status,
            note=request.note[:500],
            details=request.details or {},
            username=user.username,
            user_role=user.role,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _persist_knowledge_graph_quality_snapshot(metrics: dict) -> dict | None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return None
        return store.save_knowledge_graph_quality_snapshot(metrics)
    except Exception:
        return None


def _alert_fingerprint(alerts: list[dict]) -> str:
    if not alerts:
        return "ok"
    return "|".join(sorted(f"{alert.get('level')}:{alert.get('code')}" for alert in alerts))


def _record_knowledge_graph_quality_alert_event(metrics: dict, snapshot: dict | None) -> dict | None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return None
        alerts = list(metrics.get("alerts") or [])
        fingerprint = _alert_fingerprint(alerts)
        trigger = metrics.get("trigger") if isinstance(metrics.get("trigger"), dict) else None
        latest = store.get_latest_ops_event("knowledge_graph_quality_alert")
        latest_details = latest.get("details") if latest else {}
        if not isinstance(latest_details, dict):
            latest_details = {}
        latest_trigger = latest_details.get("trigger") if isinstance(latest_details.get("trigger"), dict) else None
        if (
            latest
            and latest_details.get("alert_fingerprint") == fingerprint
            and (
                not trigger
                or (latest_trigger and latest_trigger.get("doc_id") == trigger.get("doc_id"))
            )
        ):
            return latest
        if not alerts and (not latest or latest.get("status") == "ok"):
            return None

        status = "ok"
        if any(alert.get("level") == "error" for alert in alerts):
            status = "error"
        elif alerts:
            status = "warning"
        message = "图谱质量告警已恢复"
        action = "quality_recovered"
        if alerts:
            message = "；".join(str(alert.get("title") or alert.get("code")) for alert in alerts[:3])
            action = "quality_alert"
        return store.record_ops_event(
            event_type="knowledge_graph_quality_alert",
            status=status,
            action=action,
            message=message,
            details={
                "alert_fingerprint": fingerprint,
                "alerts": alerts,
                "health_score": metrics.get("health_score"),
                "risk_level": metrics.get("risk_level"),
                "open_review_backlog_ratio": metrics.get("open_review_backlog_ratio"),
                "previous_health_score": trigger.get("previous_health_score") if trigger else None,
                "health_drop": trigger.get("health_drop") if trigger else None,
                "trigger": trigger,
                "snapshot_id": snapshot.get("id") if snapshot else None,
            },
        )
    except Exception:
        return None


def _latest_knowledge_graph_quality_snapshot() -> dict | None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return None
        snapshots = store.list_knowledge_graph_quality_snapshots(limit=1)
        return snapshots[-1] if snapshots else None
    except Exception:
        return None


def _knowledge_graph_governance_actions(summary: dict, quality_gate: dict, trigger: dict | None) -> list[dict]:
    actions: list[dict] = [{
        "type": "review_quality_queue",
        "title": "处理图谱质量审核队列",
        "description": "进入知识图谱质量审核队列，优先处理高风险候选。",
        "target": "/documents",
    }]
    if int(summary.get("duplicate_entity_count") or 0) > 0:
        actions.append({
            "type": "merge_duplicate_entities",
            "title": "合并疑似重复实体",
            "description": f"{summary.get('duplicate_entity_count')} 个候选可能适合合并。",
            "target": "/documents",
        })
    if int(summary.get("low_confidence_relation_count") or 0) > 0:
        actions.append({
            "type": "review_low_confidence_relations",
            "title": "复核低置信度关系",
            "description": f"{summary.get('low_confidence_relation_count')} 条关系低于当前置信度阈值。",
            "target": "/documents",
        })
    if int(summary.get("isolated_relation_count") or 0) > 0:
        actions.append({
            "type": "review_isolated_relations",
            "title": "清理孤立弱关系",
            "description": f"{summary.get('isolated_relation_count')} 条关系缺少足够上下文支撑。",
            "target": "/documents",
        })
    split_count = int(summary.get("conflicting_relation_count") or 0) + int(summary.get("ambiguous_entity_count") or 0)
    if split_count > 0:
        actions.append({
            "type": "split_ambiguous_entities",
            "title": "拆分多义或冲突实体",
            "description": f"{split_count} 个候选可能需要实体拆分或关系修正。",
            "target": "/documents",
        })
    if quality_gate.get("passed") is False:
        actions.append({
            "type": "resolve_quality_gate",
            "title": "恢复图谱质量门禁",
            "description": quality_gate.get("message") or "图谱质量门禁未通过。",
            "target": "/system",
        })
    if trigger and int(trigger.get("health_drop") or 0) > 0:
        actions.append({
            "type": "inspect_trigger_document",
            "title": "复查最近导入来源",
            "description": f"{trigger.get('filename') or trigger.get('doc_id')} 导入后健康分下降 {trigger.get('health_drop')}。",
            "target": "/documents",
        })
    return actions


def _record_knowledge_graph_governance_task(payload: dict, snapshot: dict | None) -> dict | None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return None
        summary = payload.get("summary") or {}
        metrics = summary.get("quality_metrics") or {}
        quality_gate = summary.get("quality_gate") or metrics.get("quality_gate") or {}
        trigger = summary.get("trigger") or metrics.get("trigger")
        health_drop = int((trigger or {}).get("health_drop") or 0) if isinstance(trigger, dict) else 0
        gate_failed = quality_gate.get("passed") is False
        actionable_alerts = [
            alert
            for alert in metrics.get("alerts", [])
            if alert.get("level") in {"warning", "error"} and alert.get("code") != "no_relations"
        ]
        trigger_action = trigger.get("action") if isinstance(trigger, dict) else ""
        import_regression = trigger_action in {"document_upload", "url_import"} and health_drop >= 10
        should_open = gate_failed or import_regression or bool(actionable_alerts)
        latest = store.get_latest_ops_event("knowledge_graph_governance_task")
        latest_details = latest.get("details") if latest else {}
        if not isinstance(latest_details, dict):
            latest_details = {}

        if not should_open:
            if latest and latest.get("status") != "ok":
                return store.record_ops_event(
                    event_type="knowledge_graph_governance_task",
                    status="ok",
                    action="governance_task_resolved",
                    message="知识图谱治理任务已恢复",
                    details={
                        "previous_task_key": latest_details.get("task_key"),
                        "snapshot_id": snapshot.get("id") if snapshot else None,
                        "health_score": metrics.get("health_score"),
                    },
                )
            return None

        violation_codes = [
            str(item.get("code"))
            for item in quality_gate.get("violations", [])
            if item.get("code")
        ]
        alert_codes = [
            str(item.get("code"))
            for item in actionable_alerts
            if item.get("code")
        ]
        trigger_key = ""
        if isinstance(trigger, dict):
            trigger_key = trigger.get("doc_id") or trigger.get("filename") or ""
        task_key = "|".join([
            str(trigger_key or "global"),
            ",".join(sorted(violation_codes)) or "gate-ok",
            ",".join(sorted(alert_codes)) or "no-alert",
        ])
        if latest and latest.get("status") != "ok" and latest_details.get("task_key") == task_key:
            return latest

        actions = _knowledge_graph_governance_actions(summary, quality_gate, trigger if isinstance(trigger, dict) else None)
        governance_status = "error" if any(alert.get("level") == "error" for alert in actionable_alerts) else "warning"
        return store.record_ops_event(
            event_type="knowledge_graph_governance_task",
            status=governance_status,
            action="governance_task_opened",
            message=quality_gate.get("message") or "知识图谱需要治理",
            details={
                "task_key": task_key,
                "snapshot_id": snapshot.get("id") if snapshot else None,
                "health_score": metrics.get("health_score"),
                "risk_level": metrics.get("risk_level"),
                "relation_count": metrics.get("relation_count"),
                "open_candidate_count": metrics.get("open_candidate_count"),
                "health_drop": health_drop,
                "trigger": trigger,
                "quality_gate": quality_gate,
                "alerts": actionable_alerts,
                "candidate_counts": {
                    "duplicate_entity": summary.get("duplicate_entity_count", 0),
                    "low_confidence_relation": summary.get("low_confidence_relation_count", 0),
                    "isolated_relation": summary.get("isolated_relation_count", 0),
                    "conflicting_relation": summary.get("conflicting_relation_count", 0),
                    "ambiguous_entity": summary.get("ambiguous_entity_count", 0),
                },
                "suggested_actions": actions,
            },
        )
    except Exception:
        return None


def _knowledge_graph_quality_trigger(
    trigger: dict | None,
    *,
    metrics: dict,
    previous_snapshot: dict | None,
) -> dict | None:
    if not trigger:
        return None
    previous_metrics = previous_snapshot.get("metrics") if previous_snapshot and isinstance(previous_snapshot.get("metrics"), dict) else (previous_snapshot or {})
    previous_health = int(previous_metrics.get("health_score") or 0)
    current_health = int(metrics.get("health_score") or 0)
    health_drop = max(0, previous_health - current_health) if previous_health else 0
    return {
        "action": str(trigger.get("action") or "unknown"),
        "doc_id": str(trigger.get("doc_id") or ""),
        "filename": str(trigger.get("filename") or ""),
        "document_action": str(trigger.get("document_action") or ""),
        "governance_action": str(trigger.get("governance_action") or ""),
        "candidate_id": str(trigger.get("candidate_id") or ""),
        "decision_status": str(trigger.get("decision_status") or ""),
        "actor": trigger.get("actor") if isinstance(trigger.get("actor"), dict) else None,
        "chunk_count": int(trigger.get("chunk_count") or 0),
        "relation_count": int(metrics.get("relation_count") or 0),
        "previous_health_score": previous_health or None,
        "current_health_score": current_health,
        "health_drop": health_drop,
    }


def _build_knowledge_graph_quality_payload(
    *,
    confidence_threshold: float,
    limit: int,
    status: Literal["open", "all", "accepted", "ignored", "snoozed"],
    trigger: dict | None = None,
) -> dict:
    from knowledge.knowledge_graph import (
        evaluate_knowledge_graph_quality_gate,
        knowledge_graph_quality_alerts,
        knowledge_graph_quality_payload,
    )
    from web.api import _config

    kg = _get_enabled_knowledge_graph()
    stats = kg.stats()
    triples = kg.export_triples() if stats.get("nodes", 0) else []
    payload = knowledge_graph_quality_payload(
        triples,
        stats=stats,
        confidence_threshold=confidence_threshold,
        limit=200,
    )
    decisions = _knowledge_graph_review_decisions()
    generated_candidates = list(payload["candidates"])
    filtered_candidates = []
    open_candidate_count = 0
    decided_candidate_count = 0
    for candidate in generated_candidates:
        decision = decisions.get(candidate["id"])
        matching_decision = decision if _review_decision_matches_candidate(decision, candidate) else None
        if matching_decision:
            candidate["decision"] = matching_decision
        elif decision:
            candidate["stale_decision"] = decision
        decision_status = matching_decision["status"] if matching_decision else "open"
        if decision_status == "open":
            open_candidate_count += 1
        else:
            decided_candidate_count += 1
        if status == "all":
            filtered_candidates.append(candidate)
        elif status == "open":
            if decision_status == "open":
                filtered_candidates.append(candidate)
        elif decision_status == status:
            filtered_candidates.append(candidate)

    payload["candidates"] = filtered_candidates[:limit]
    payload["summary"]["returned_candidates"] = len(payload["candidates"])
    payload["summary"]["hidden_decided_count"] = sum(
        1
        for candidate in generated_candidates
        if (
            _review_decision_matches_candidate(decisions.get(candidate["id"]), candidate)
            and decisions.get(candidate["id"], {}).get("status") in {"accepted", "ignored", "snoozed"}
        )
    )
    payload["summary"]["stale_decision_count"] = sum(
        1
        for candidate in generated_candidates
        if decisions.get(candidate["id"]) and not _review_decision_matches_candidate(decisions.get(candidate["id"]), candidate)
    )
    previous_snapshot = _latest_knowledge_graph_quality_snapshot()
    metrics = payload["summary"].setdefault("quality_metrics", {})
    metrics["open_candidate_count"] = open_candidate_count
    metrics["decided_candidate_count"] = decided_candidate_count
    relation_count = int(metrics.get("relation_count") or 0)
    metrics["open_review_backlog_ratio"] = round(open_candidate_count / relation_count, 3) if relation_count else 0.0
    source_trigger = _knowledge_graph_quality_trigger(
        trigger,
        metrics=metrics,
        previous_snapshot=previous_snapshot,
    )
    if source_trigger:
        metrics["trigger"] = source_trigger
        payload["summary"]["trigger"] = source_trigger
    metrics["alerts"] = knowledge_graph_quality_alerts(
        metrics,
        previous_snapshot=previous_snapshot,
    )
    quality_gate_policy = getattr(getattr(_config, "knowledge_base", None), "graph_quality_gate", {})
    quality_gate = evaluate_knowledge_graph_quality_gate(metrics, quality_gate_policy)
    metrics["quality_gate"] = quality_gate
    payload["summary"]["quality_gate"] = quality_gate
    snapshot = _persist_knowledge_graph_quality_snapshot(metrics)
    if snapshot:
        payload["summary"]["latest_snapshot_id"] = snapshot["id"]
    alert_event = _record_knowledge_graph_quality_alert_event(metrics, snapshot)
    if alert_event:
        payload["summary"]["latest_alert_event_id"] = alert_event["id"]
    governance_task = _record_knowledge_graph_governance_task(payload, snapshot)
    if governance_task:
        payload["summary"]["latest_governance_task_event_id"] = governance_task["id"]
    payload["thresholds"]["limit"] = limit
    payload["filters"] = {"status": status}
    return payload


def _record_knowledge_graph_quality_after_document(trigger: dict) -> dict | None:
    try:
        return _build_knowledge_graph_quality_payload(
            confidence_threshold=0.6,
            limit=1,
            status="all",
            trigger=trigger,
        )["summary"].get("quality_gate")
    except Exception:
        return None


def _record_knowledge_graph_quality_after_governance(trigger: dict) -> dict | None:
    try:
        return _build_knowledge_graph_quality_payload(
            confidence_threshold=0.6,
            limit=1,
            status="all",
            trigger={
                "action": "graph_governance",
                **trigger,
            },
        )["summary"]
    except Exception:
        return None


def _check_url_not_ssrf(url: str) -> None:
    """Raise HTTPException if URL resolves to a private/reserved IP (SSRF protection)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL 无法解析主机名")

    try:
        ip_str = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"DNS 解析失败: {e}") from e

    ip = ipaddress.ip_address(ip_str)
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
        raise HTTPException(status_code=400, detail=f"不允许访问私有/保留 IP 地址: {ip_str}")


# ── Documents ────────────────────────────────────────────────────────────────


@router.get("/documents")
async def list_documents() -> list[DocumentResponse]:
    """列出所有文档"""
    from web.api import _rag_engine
    docs = _rag_engine.list_documents()
    return [
        DocumentResponse(
            id=d.id,
            filename=d.filename,
            type=d.type,
            chunk_count=d.chunk_count,
            created_at=d.created_at,
            size=d.size,
            group_id=d.group_id,
        )
        for d in docs
    ]


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    group_id: str = Form(default="ungrouped"),
) -> DocumentResponse:
    """上传文档"""
    import asyncio
    import contextlib
    import uuid
    from pathlib import Path

    from web.api import _config, _rag_engine

    # 检查 Content-Length
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl = int(content_length)
            if cl > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件大小 {cl / 1024 / 1024:.1f} MB 超过上限 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f} MB",
                )
        except ValueError:
            pass

    upload_dir = Path(_config.knowledge_base.upload_directory)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / f"{uuid.uuid4().hex}_{file.filename}"
    bytes_written = 0
    try:
        with file_path.open("wb") as dest:
            while chunk := await file.read(65536):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE:
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件大小超过上限 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f} MB",
                    )
                dest.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"文件保存失败: {str(e)}") from e

    try:
        result = await asyncio.wait_for(
            _rag_engine.add_document(file_path, group_id=group_id, original_filename=file.filename),
            timeout=300.0
        )
        doc_info = result.doc_info
        action = result.action
        if action != "skipped":
            _record_knowledge_graph_quality_after_document({
                "action": "document_upload",
                "document_action": action,
                "doc_id": doc_info.id,
                "filename": doc_info.filename,
                "chunk_count": doc_info.chunk_count,
            })
        return DocumentResponse(
            id=doc_info.id,
            filename=doc_info.filename,
            type=doc_info.type,
            chunk_count=doc_info.chunk_count,
            created_at=doc_info.created_at,
            size=doc_info.size,
            group_id=doc_info.group_id,
            action=action,
        )
    except asyncio.TimeoutError as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="文档处理超时，请尝试上传更小的文件或简化文档格式") from e
    except TimeoutError as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail=str(e)) from e
    except ValueError as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {type(e).__name__}: {str(e)}") from e





@router.get("/knowledge/graph")
async def get_knowledge_graph(
    entity: str = Query("", description="聚焦实体名称"),
    q: str = Query("", description="实体搜索关键字"),
    depth: int = Query(1, ge=0, le=4, description="实体邻域查询深度"),
    limit: int = Query(1000, description="最大返回节点数"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0, description="最小关系置信度"),
    predicate: str = Query("", description="关系谓词过滤"),
):
    """获取知识图谱可视化数据"""
    from knowledge.knowledge_graph import knowledge_graph_payload

    kg = _get_enabled_knowledge_graph()
    stats = kg.stats()
    if stats.get("nodes", 0) == 0:
        return knowledge_graph_payload(
            [],
            stats=stats,
            entity=entity,
            query=q,
            depth=depth,
            limit=limit,
            min_confidence=min_confidence,
            predicate=predicate,
        )

    triples = kg.export_triples()
    return knowledge_graph_payload(
        triples,
        stats=stats,
        entity=entity,
        query=q,
        depth=depth,
        limit=limit,
        min_confidence=min_confidence,
        predicate=predicate,
    )


@router.get("/knowledge/graph/quality")
async def get_knowledge_graph_quality(
    confidence_threshold: float = Query(0.6, ge=0.0, le=1.0, description="低置信度审核阈值"),
    limit: int = Query(50, ge=1, le=200, description="最大返回候选数"),
    status: Literal["open", "all", "accepted", "ignored", "snoozed"] = Query(
        "open",
        description="候选状态过滤；open 默认隐藏已处理/忽略/稍后",
    ),
):
    """获取知识图谱质量审核候选。"""
    return _build_knowledge_graph_quality_payload(
        confidence_threshold=confidence_threshold,
        limit=limit,
        status=status,
    )


@router.get("/knowledge/graph/quality/history")
async def get_knowledge_graph_quality_history(
    limit: int = Query(30, ge=1, le=500, description="最大返回快照数"),
) -> dict:
    """获取知识图谱质量指标历史快照。"""
    from storage import get_store

    store = get_store()
    if store is None:
        return {"snapshots": []}
    return {"snapshots": store.list_knowledge_graph_quality_snapshots(limit=limit)}


@router.post("/knowledge/graph/quality/decisions")
async def set_knowledge_graph_quality_decision(
    request: KnowledgeGraphQualityDecisionRequest,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """保存知识图谱质量候选审核决策（仅管理员）。"""
    decision = _persist_knowledge_graph_quality_decision(request, user)
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "quality_decision",
        "candidate_id": decision["candidate_id"],
        "decision_status": decision["status"],
        "actor": {"username": user.username, "role": user.role},
    })
    return {
        "success": True,
        "decision": decision,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.post("/knowledge/graph/quality/decisions/batch")
async def set_knowledge_graph_quality_decisions_batch(
    request: KnowledgeGraphQualityDecisionBatchRequest,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """批量保存知识图谱质量候选审核决策（仅管理员）。"""
    if not request.decisions:
        raise HTTPException(status_code=400, detail="decisions 不能为空")
    if len(request.decisions) > 100:
        raise HTTPException(status_code=400, detail="单次最多批量处理 100 个候选")
    decisions = [
        _persist_knowledge_graph_quality_decision(item, user)
        for item in request.decisions
    ]
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "quality_decision_batch",
        "candidate_id": ",".join(decision["candidate_id"] for decision in decisions[:10]),
        "decision_status": ",".join(sorted({decision["status"] for decision in decisions})),
        "actor": {"username": user.username, "role": user.role},
    })
    return {
        "success": True,
        "updated": len(decisions),
        "decisions": decisions,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.post("/knowledge/graph/entities/merge")
async def merge_knowledge_graph_entities(
    request: KnowledgeGraphMergeRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """合并重复实体（仅管理员）。"""
    source = _clean_required_text(request.source, "source")
    target = _clean_required_text(request.target, "target")
    if source.lower() == target.lower():
        raise HTTPException(status_code=400, detail="source 与 target 不能相同")

    kg = _get_enabled_knowledge_graph()
    result = kg.merge_entities(source, target)
    if not result.get("merged"):
        reason = result.get("reason", "merge_failed")
        if reason == "source_not_found":
            raise HTTPException(status_code=404, detail="源实体不存在")
        raise HTTPException(status_code=400, detail=reason)
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "merge_entities",
        "candidate_id": f"merge:{source}->{target}",
    })
    return {
        "success": True,
        **result,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.post("/knowledge/graph/entities/split")
async def split_knowledge_graph_entity(
    request: KnowledgeGraphSplitRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """按选定关系把实体的一部分证据拆到新实体（仅管理员）。"""
    source = _clean_required_text(request.source, "source")
    new_entity = _clean_required_text(request.new_entity, "new_entity")
    if source.lower() == new_entity.lower():
        raise HTTPException(status_code=400, detail="source 与 new_entity 不能相同")
    if not request.triples:
        raise HTTPException(status_code=400, detail="triples 不能为空")
    if len(request.triples) > 100:
        raise HTTPException(status_code=400, detail="单次最多拆分 100 条关系")

    kg = _get_enabled_knowledge_graph()
    result = kg.split_entity(source, new_entity, [_triple_from_request(item) for item in request.triples])
    if not result.get("split"):
        reason = result.get("reason", "split_failed")
        if reason == "source_not_found":
            raise HTTPException(status_code=404, detail="源实体不存在")
        if reason == "unsupported":
            raise HTTPException(status_code=400, detail="当前知识图谱后端暂不支持实体拆分")
        if reason == "no_matching_edges":
            raise HTTPException(status_code=404, detail="未找到可拆分的匹配关系")
        raise HTTPException(status_code=400, detail=reason)
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "split_entity",
        "candidate_id": f"split:{source}->{new_entity}",
    })
    return {
        "success": True,
        **result,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.put("/knowledge/graph/triples")
async def update_knowledge_graph_triple(
    request: KnowledgeGraphTripleUpdateRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修正单条图谱关系（仅管理员）。"""
    old = _triple_from_request(request.old)
    new = _triple_from_request(request.new)
    kg = _get_enabled_knowledge_graph()
    if not kg.update_triple(old, new):
        raise HTTPException(status_code=404, detail="待修正关系不存在")
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "update_triple",
        "candidate_id": f"triple:{old.subject}|{old.predicate}|{old.object}",
    })
    return {
        "success": True,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.post("/knowledge/graph/triples/delete")
async def delete_knowledge_graph_triple(
    request: KnowledgeGraphTripleRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除单条图谱关系（仅管理员）。"""
    triple = _triple_from_request(request)
    kg = _get_enabled_knowledge_graph()
    if not kg.delete_triple(triple):
        raise HTTPException(status_code=404, detail="待删除关系不存在")
    quality_summary = _record_knowledge_graph_quality_after_governance({
        "governance_action": "delete_triple",
        "candidate_id": f"triple:{triple.subject}|{triple.predicate}|{triple.object}",
    })
    return {
        "success": True,
        "quality_gate": quality_summary.get("quality_gate") if quality_summary else None,
        "governance_task_event_id": quality_summary.get("latest_governance_task_event_id") if quality_summary else None,
    }


@router.post("/documents/url")
async def import_url(request: URLRequest) -> DocumentResponse:
    """从 URL 抓取网页并导入知识库"""
    import asyncio
    import datetime
    import re
    import uuid

    from web.api import WebPageParser, _rag_engine

    url = request.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")

    _check_url_not_ssrf(url)

    doc_id = uuid.uuid4().hex[:8]
    parser = WebPageParser()

    try:
        doc_info_raw = await asyncio.wait_for(
            parser.fetch_url(url, doc_id),
            timeout=35.0,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail="网页抓取超时") from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"网页抓取失败: {type(e).__name__}: {str(e)}") from e

    chunks = await parser.chunk(doc_info_raw, chunk_size=500, overlap=50)
    created_at = datetime.datetime.now().isoformat()
    file_size = len(doc_info_raw.content.encode())
    for chunk in chunks:
        chunk.metadata["doc_id"] = doc_id
        chunk.metadata["filename"] = url
        chunk.metadata["type"] = "webpage"
        chunk.metadata["group_id"] = "ungrouped"
        chunk.metadata["created_at"] = created_at
        chunk.metadata["file_size"] = file_size
        chunk.metadata["chunk_count"] = len(chunks)

    if chunks:
        await _rag_engine._index_document_chunks(chunks, "documents")

    from knowledge import DocumentInfo
    doc_info = DocumentInfo(
        id=doc_id,
        filename=url,
        type="webpage",
        chunk_count=len(chunks),
        created_at=created_at,
        size=file_size,
    )
    _rag_engine._documents[doc_id] = doc_info
    _record_knowledge_graph_quality_after_document({
        "action": "url_import",
        "document_action": "indexed",
        "doc_id": doc_info.id,
        "filename": doc_info.filename,
        "chunk_count": doc_info.chunk_count,
    })

    return DocumentResponse(
        id=doc_info.id,
        filename=doc_info.filename,
        type=doc_info.type,
        chunk_count=doc_info.chunk_count,
        created_at=doc_info.created_at,
        size=doc_info.size,
    )


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除文档（仅管理员）"""
    from web.api import _rag_engine

    success = await _rag_engine.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True, "message": "Document deleted"}


# ── Groups ──────────────────────────────────────────────────────────────────


@router.get("/groups")
async def list_groups() -> list[dict]:
    """列出所有分组（含各组文档数）"""
    from web.api import _group_store, _rag_engine

    docs = _rag_engine.list_documents()
    count_map: dict[str, int] = {}
    for doc in docs:
        count_map[doc.group_id] = count_map.get(doc.group_id, 0) + 1
    return [
        {
            "id": g.id,
            "name": g.name,
            "color": g.color,
            "created_at": g.created_at,
            "doc_count": count_map.get(g.id, 0),
        }
        for g in _group_store.list_groups()
    ]


@router.post("/groups")
async def create_group(request: GroupCreate) -> dict:
    """新建分组"""
    from web.api import _group_store

    group = _group_store.create_group(request.name, request.color)
    return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at, "doc_count": 0}


@router.put("/groups/{group_id}")
async def update_group(
    group_id: str,
    request: GroupUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改分组名称或颜色（仅管理员）"""
    from web.api import _group_store

    try:
        group = _group_store.update_group(group_id, request.name, request.color)
        return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at}
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Group not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除分组，其下文档自动归回未分组（仅管理员）"""
    from knowledge.group_store import UNGROUPED_ID
    from web.api import _group_store, _rag_engine

    try:
        docs = _rag_engine.list_documents()
        for doc in docs:
            if doc.group_id == group_id:
                _rag_engine.move_document_group(doc.id, UNGROUPED_ID)
        _group_store.delete_group(group_id)
        return {"success": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/documents/{doc_id}/group")
async def move_document_group(
    doc_id: str,
    request: MoveDocumentGroup,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改文档所属分组（仅管理员）"""
    from web.api import _group_store, _rag_engine

    if not _group_store.get_group(request.group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    success = _rag_engine.move_document_group(doc_id, request.group_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True}


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: str) -> dict:
    """获取文档的所有分块内容"""
    from web.api import _rag_engine

    chunks = _rag_engine.get_document_chunks(doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found or has no chunks")
    filename = chunks[0].get("metadata", {}).get("filename", "unknown")
    return {
        "doc_id": doc_id,
        "filename": filename,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "id": c["id"],
                "content": c["content"],
                "index": c["chunk_index"],
            }
            for c in chunks
        ],
    }


@router.get("/documents/{doc_id}/media-assets")
async def get_document_media_assets(doc_id: str, request: Request) -> dict:
    """列出知识库文档中可用于图生视频的图片素材。"""
    import mimetypes
    import re
    import time
    from pathlib import Path
    from urllib.parse import quote

    from web.api import (
        _file_access_config,
        _file_signature,
        _file_signing_secret,
        _rag_engine,
        _upload_file_path,
        _validate_upload_file_name,
    )

    chunks = _rag_engine.get_document_chunks(doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found or has no chunks")

    base_url = str(request.base_url).rstrip("/")
    assets: dict[str, DocumentMediaAsset] = {}

    def local_url_for(name: str) -> tuple[str, Literal["signed", "bearer"]]:
        if _file_signing_secret():
            expires = int(time.time()) + _file_access_config().signed_url_ttl_seconds
            signature = _file_signature(name, expires)
            return (
                f"{base_url}/api/files/{quote(name, safe='')}?expires={expires}&signature={signature}",
                "signed",
            )
        return f"{base_url}/api/files/{quote(name, safe='')}", "bearer"

    for chunk in chunks:
        metadata = chunk.get("metadata") or {}
        if metadata.get("type") == "image" and metadata.get("path"):
            path = Path(str(metadata["path"]))
            name = _validate_upload_file_name(path.name)
            upload_path = _upload_file_path(name)
            if upload_path.is_file():
                url, access = local_url_for(name)
                assets.setdefault(
                    f"local:{name}",
                    DocumentMediaAsset(
                        id=f"local:{name}",
                        kind="local_image",
                        name=name,
                        url=url,
                        content_type=mimetypes.guess_type(name)[0],
                        access=access,
                    ),
                )

        content = chunk.get("content") or ""
        matches = re.findall(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", content)
        matches.extend(
            re.findall(r"https?://[^\s)\"']+\.(?:png|jpe?g|webp|gif)(?:\?[^\s)\"']*)?", content, flags=re.I)
        )
        for url in matches:
            assets.setdefault(
                f"remote:{url}",
                DocumentMediaAsset(
                    id=f"remote:{url}",
                    kind="remote_image",
                    name=url.rsplit("/", 1)[-1].split("?", 1)[0] or "remote-image",
                    url=url,
                    content_type=mimetypes.guess_type(url)[0],
                    access="public",
                ),
            )

    filename = chunks[0].get("metadata", {}).get("filename", "unknown")
    return {
        "doc_id": doc_id,
        "filename": filename,
        "assets": [asset.model_dump() for asset in assets.values()],
    }


@router.get("/documents/search")
async def search_documents(q: str, group_ids: str | None = None) -> dict:
    """全文搜索文档"""
    from web.api import _rag_engine

    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    gids = group_ids.split(",") if group_ids else None
    results = await _rag_engine.search(q, group_ids=gids, top_k=20)
    valid_docs = {d.id: d.filename for d in _rag_engine.list_documents()}
    seen_docs: dict[str, dict] = {}
    for r in results:
        doc_id = r.metadata.get("doc_id", "")
        if doc_id not in valid_docs:
            continue
        if doc_id not in seen_docs or r.score > seen_docs[doc_id]["score"]:
            seen_docs[doc_id] = {
                "doc_id": doc_id,
                "filename": valid_docs[doc_id],
                "content": r.content,
                "score": r.score,
                "chunk_index": r.metadata.get("chunk_index", 0),
                "group_id": r.metadata.get("group_id", "ungrouped"),
            }
    return {
        "query": q,
        "results": sorted(seen_docs.values(), key=lambda x: x["score"], reverse=True),
    }
