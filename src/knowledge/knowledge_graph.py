"""知识图谱构建 - 实体关系图

支持 NetworkX (内存/GML持久化) 和 Neo4j (真实图数据库)。
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    from .document_parser import TextChunk


@dataclass
class Triple:
    """单个三元组：<subject, predicate, object>"""
    subject: str
    predicate: str
    object: str
    source_chunk_id: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "source_chunk_id": self.source_chunk_id,
            "confidence": self.confidence,
        }

    def __hash__(self):
        return hash((self.subject.lower(), self.predicate.lower(), self.object.lower()))

    def __eq__(self, other):
        if not isinstance(other, Triple):
            return False
        return (
            self.subject.lower() == other.subject.lower()
            and self.predicate.lower() == other.predicate.lower()
            and self.object.lower() == other.object.lower()
        )


@dataclass
class GraphSearchResult:
    """知识图谱搜索结果"""
    entity: str          # 匹配的实体名
    triples: list[Triple]  # 相关三元组
    connected_entities: list[str]  # 直接相连的实体
    degree: int          # 该实体的度（连接数）


# ---------------------------------------------------------------------------
# Triple extraction helpers (rule-based fallback)
# ---------------------------------------------------------------------------

def _extract_triples_rule_based(text: str, chunk_id: str = "") -> list[Triple]:
    """基于规则的简单三元组抽取（LLM 不可用时的降级方案）。"""
    triples: list[Triple] = []
    text = text.strip()
    if not text or len(text) < 5:
        return triples

    patterns = [
        (r"([^，。、！？；：,\s]{2,30})\s*(?:是|为|属于|位于|存在于)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:有|拥有|具有|包含|包括)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:与|和|同)\s*([^，。、！？；：,\s]{2,30})\s*(?:关联|相关|交互|合作|通信)", 1, 2),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:通过|利用|使用)\s*([^，。、！？；：,\s]{2,30})\s*(?:实现|完成|达成|完成)\s*([^，。、！？；：,\s]{2,30})", 1, 3),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:构成|组成|形成|产生)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:用于|用来|应用于)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        (r"([^，。、！？；：,\s]{2,30})\s*(?:可以|能够|可以实现|能够实现)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
    ]

    for pattern, sub_idx, obj_idx in patterns:
        for match in re.finditer(pattern, text):
            try:
                subject = match.group(sub_idx).strip()
                obj = match.group(obj_idx).strip()
                pred_map = {
                    (0, 2): "是",
                    (1, 2): "有",
                    (2, 2): "关联",
                    (3, 3): "通过",
                    (4, 2): "构成",
                    (5, 2): "用于",
                    (6, 2): "可以",
                }
                predicate = pred_map.get((patterns.index((pattern, sub_idx, obj_idx)), obj_idx), "关联")
                if len(subject) >= 2 and len(obj) >= 2 and subject != obj:
                    triples.append(Triple(
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        source_chunk_id=chunk_id,
                        confidence=0.6,
                    ))
            except IndexError:
                continue

    return triples


async def _extract_triples_via_llm_batch(
    *,
    chunks: list[tuple[str, str]],
    llm_provider: str = "",
    llm_api_key: str = "",
    llm_base_url: str = "",
    llm_model: str = "",
) -> dict[str, list[Triple]]:
    """Extract triples for several chunks using a configured LLM provider.

    The function is intentionally defensive: any provider failure or malformed
    response returns an empty mapping so callers can fall back to rule-based
    extraction per chunk.
    """
    valid_chunks = [(cid, text) for cid, text in chunks if cid and text and text.strip()]
    if not valid_chunks or not llm_provider or not llm_api_key:
        return {}

    try:
        from src.agents.base_agent import create_provider, get_provider_capabilities
    except ImportError:
        from agents.base_agent import create_provider, get_provider_capabilities

    provider_name = llm_provider.lower().strip()
    capabilities = get_provider_capabilities(provider_name)
    model = llm_model.strip()
    if not model and capabilities and capabilities.well_known_models:
        model = capabilities.well_known_models[0]
    if not model:
        model = {
            "dashscope": "qwen-turbo",
            "openai": "gpt-4o-mini",
            "kimi": "kimi-latest",
            "deepseek": "deepseek-v4-pro",
            "minimax": "MiniMax-M2.7-highspeed",
        }.get(provider_name, "gpt-4o-mini")

    prompt_chunks = [
        {
            "id": chunk_id,
            "text": " ".join(text.split())[:2200],
        }
        for chunk_id, text in valid_chunks
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是知识图谱三元组抽取器。只从用户给出的文本中抽取明确事实，"
                "不要推测，不要添加文本外知识。输出必须是合法 JSON，格式为 "
                "{\"triples\":[{\"chunk_id\":\"...\",\"subject\":\"...\","
                "\"predicate\":\"...\",\"object\":\"...\",\"confidence\":0.0}]}。"
                "每个 chunk 最多 8 条，subject/predicate/object 要短、稳定、适合作为图谱节点或边。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"chunks": prompt_chunks}, ensure_ascii=False),
        },
    ]

    try:
        provider = create_provider(
            provider_name,
            llm_api_key,
            base_url=llm_base_url,
        )
        response = await provider.chat(
            messages,
            model=model,
            temperature=0,
            max_tokens=1800,
        )
    except Exception as exc:
        print(f"[KnowledgeGraph] LLM triple extraction failed: {type(exc).__name__}: {exc}")
        return {}

    return _parse_llm_triple_response(response.content or "", {cid for cid, _ in valid_chunks})


def _parse_jsonish_payload(text: str) -> Any:
    """Parse JSON from raw LLM text, allowing fenced code blocks."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty response")

    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_candidates = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
        start = min(start_candidates) if start_candidates else -1
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start < 0 or end < start:
            raise
        return json.loads(cleaned[start:end + 1])


def _clean_graph_text(value: Any, *, max_len: int = 80) -> str:
    text = " ".join(str(value or "").split())
    text = text.strip(" \t\r\n\"'`，。、；：:;,.!?！？()（）[]【】")
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _safe_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.85
    return max(0.0, min(1.0, confidence))


def _parse_llm_triple_response(content: str, allowed_chunk_ids: set[str]) -> dict[str, list[Triple]]:
    """Normalize provider JSON into chunk-id keyed triples."""
    try:
        payload = _parse_jsonish_payload(content)
    except Exception as exc:
        print(f"[KnowledgeGraph] Failed to parse LLM triple JSON: {type(exc).__name__}: {exc}")
        return {}

    raw_triples = payload.get("triples", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_triples, list):
        return {}

    grouped: dict[str, list[Triple]] = {chunk_id: [] for chunk_id in allowed_chunk_ids}
    seen: set[tuple[str, str, str, str]] = set()
    for item in raw_triples:
        if not isinstance(item, dict):
            continue
        chunk_id = _clean_graph_text(item.get("chunk_id"), max_len=120)
        if chunk_id not in allowed_chunk_ids:
            continue

        subject = _clean_graph_text(item.get("subject"))
        predicate = _clean_graph_text(item.get("predicate"), max_len=40)
        obj = _clean_graph_text(item.get("object"))
        if not subject or not predicate or not obj or subject == obj:
            continue

        key = (chunk_id, subject.lower(), predicate.lower(), obj.lower())
        if key in seen:
            continue
        if len(grouped[chunk_id]) >= 8:
            continue
        seen.add(key)
        grouped[chunk_id].append(Triple(
            subject=subject,
            predicate=predicate,
            object=obj,
            source_chunk_id=chunk_id,
            confidence=_safe_confidence(item.get("confidence")),
        ))

    return {chunk_id: triples for chunk_id, triples in grouped.items() if triples}


def _source_doc_id(source_chunk_id: str) -> str:
    """Best-effort document id extraction from MemoX chunk ids."""
    if not source_chunk_id:
        return ""
    if "_chunk_" in source_chunk_id:
        return source_chunk_id.split("_chunk_", 1)[0]
    return source_chunk_id


def _normalize_exported_triple(item: dict[str, Any]) -> dict[str, Any] | None:
    subject = _clean_graph_text(item.get("subject"))
    predicate = _clean_graph_text(item.get("predicate"), max_len=40)
    obj = _clean_graph_text(item.get("object"))
    if not subject or not predicate or not obj or subject == obj:
        return None
    source_chunk_id = _clean_graph_text(item.get("source_chunk_id"), max_len=120)
    confidence = _safe_confidence(item.get("confidence"))
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "source_chunk_id": source_chunk_id,
        "source_doc_id": _source_doc_id(source_chunk_id),
        "confidence": confidence,
    }


def _entity_degree_map(triples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for triple in triples:
        for key in ("subject", "object"):
            name = str(triple[key])
            if name not in entities:
                entities[name] = {
                    "id": name,
                    "name": name,
                    "degree": 0,
                    "evidence_count": 0,
                    "source_doc_ids": set(),
                }
            entities[name]["degree"] += 1
            entities[name]["evidence_count"] += 1
            if triple.get("source_doc_id"):
                entities[name]["source_doc_ids"].add(triple["source_doc_id"])
    return entities


def _select_entity(triples: list[dict[str, Any]], query: str) -> str:
    clean_query = _clean_graph_text(query)
    if not clean_query:
        return ""
    entities = _entity_degree_map(triples)
    query_lower = clean_query.lower()
    exact = [name for name in entities if name.lower() == query_lower]
    if exact:
        return max(exact, key=lambda name: entities[name]["degree"])
    partial = [name for name in entities if query_lower in name.lower()]
    if partial:
        return max(partial, key=lambda name: entities[name]["degree"])
    return ""


def _expand_entity_scope(triples: list[dict[str, Any]], entity: str, depth: int) -> set[str]:
    if not entity:
        return set()
    selected = {entity}
    frontier = {entity}
    safe_depth = max(0, min(int(depth), 4))
    for _ in range(safe_depth):
        next_frontier: set[str] = set()
        for triple in triples:
            subject = triple["subject"]
            obj = triple["object"]
            if subject in frontier:
                next_frontier.add(obj)
            if obj in frontier:
                next_frontier.add(subject)
        next_frontier -= selected
        if not next_frontier:
            break
        selected.update(next_frontier)
        frontier = next_frontier
    return selected


def knowledge_graph_payload(
    triples: list[dict[str, Any]],
    *,
    stats: dict[str, Any] | None = None,
    entity: str = "",
    query: str = "",
    depth: int = 1,
    limit: int = 1000,
    min_confidence: float = 0.0,
    predicate: str = "",
) -> dict[str, Any]:
    """Convert exported triples into a graph-exploration API payload.

    The payload is deliberately frontend-friendly: it contains renderable
    nodes/links, top entities, predicate facets, active filters, and lightweight
    provenance fields for relation evidence tables.
    """
    normalized = [
        triple
        for triple in (_normalize_exported_triple(item) for item in triples)
        if triple is not None
    ]

    safe_limit = max(1, min(int(limit), 5000))
    safe_confidence = max(0.0, min(1.0, float(min_confidence)))
    selected_predicate = _clean_graph_text(predicate, max_len=40)
    selected_entity = _select_entity(normalized, entity or query)

    filtered = [
        triple
        for triple in normalized
        if triple["confidence"] >= safe_confidence
        and (not selected_predicate or triple["predicate"] == selected_predicate)
    ]

    if selected_entity:
        scoped_entities = _expand_entity_scope(filtered, selected_entity, depth)
        visible_triples = [
            triple
            for triple in filtered
            if triple["subject"] in scoped_entities and triple["object"] in scoped_entities
        ]
    else:
        visible_triples = filtered

    visible_triples = sorted(
        visible_triples,
        key=lambda item: (item["confidence"], item["subject"], item["predicate"], item["object"]),
        reverse=True,
    )[:safe_limit]

    visible_entities = _entity_degree_map(visible_triples)
    nodes = []
    for name, info in visible_entities.items():
        nodes.append({
            "id": name,
            "name": name,
            "val": max(1, info["degree"]),
            "degree": info["degree"],
            "evidence_count": info["evidence_count"],
            "source_doc_count": len(info["source_doc_ids"]),
            "matched": name == selected_entity,
        })
    nodes.sort(key=lambda item: (item["matched"], item["degree"], item["name"]), reverse=True)

    links = [
        {
            "source": triple["subject"],
            "target": triple["object"],
            "label": triple["predicate"],
            "predicate": triple["predicate"],
            "confidence": triple["confidence"],
            "source_chunk_id": triple["source_chunk_id"],
            "source_doc_id": triple["source_doc_id"],
        }
        for triple in visible_triples
    ]

    all_entities = _entity_degree_map(filtered)
    entity_summaries = []
    for name, info in all_entities.items():
        if query and query.lower() not in name.lower():
            continue
        entity_summaries.append({
            "id": name,
            "name": name,
            "degree": info["degree"],
            "evidence_count": info["evidence_count"],
            "source_doc_count": len(info["source_doc_ids"]),
        })
    entity_summaries.sort(key=lambda item: (item["degree"], item["name"]), reverse=True)

    predicate_counts: dict[str, int] = {}
    for triple in normalized:
        if triple["confidence"] >= safe_confidence:
            predicate_counts[triple["predicate"]] = predicate_counts.get(triple["predicate"], 0) + 1
    predicates = [
        {"predicate": name, "count": count}
        for name, count in sorted(predicate_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    payload_stats = dict(stats or {})
    payload_stats.update({
        "visible_nodes": len(nodes),
        "visible_edges": len(links),
        "total_triples": len(normalized),
        "filtered_triples": len(filtered),
    })

    return {
        "nodes": nodes,
        "links": links,
        "stats": payload_stats,
        "entities": entity_summaries[:50],
        "predicates": predicates,
        "matched_entity": selected_entity or None,
        "filters": {
            "entity": entity,
            "query": query,
            "depth": max(0, min(int(depth), 4)),
            "limit": safe_limit,
            "min_confidence": safe_confidence,
            "predicate": selected_predicate,
        },
    }


def _entity_merge_key(name: str) -> str:
    return re.sub(r"[\s_\-·.。・/\\:：]+", "", name).lower()


def _candidate_triple_payload(triple: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": triple["subject"],
        "predicate": triple["predicate"],
        "object": triple["object"],
        "source_chunk_id": triple.get("source_chunk_id", ""),
        "confidence": triple.get("confidence", 1.0),
    }


def _candidate_fingerprint(candidate: dict[str, Any]) -> str:
    payload = {
        "type": candidate.get("type"),
        "entities": candidate.get("entities", []),
        "triple": candidate.get("triple"),
        "related_triples": candidate.get("related_triples", []),
        "action": candidate.get("action"),
        "reasons": candidate.get("reasons", []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _split_entity_name(source: str, object_name: str) -> str:
    suggested = _clean_graph_text(f"{source} {object_name}")
    return suggested if suggested and suggested.lower() != source.lower() else f"{source} split"


def _identity_split_suggestion(
    subject: str,
    items: list[dict[str, Any]],
    normalized: list[dict[str, Any]],
) -> dict[str, Any] | None:
    by_object: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_object.setdefault(item["object"], []).append(item)
    if len(by_object) < 2:
        return None

    incident = [
        item
        for item in normalized
        if item["subject"] == subject or item["object"] == subject
    ]
    clusters: list[tuple[int, int, str, list[dict[str, Any]]]] = []
    for object_name, identity_items in by_object.items():
        doc_ids = {item["source_doc_id"] for item in identity_items if item.get("source_doc_id")}
        cluster = [
            item
            for item in incident
            if item in identity_items or (doc_ids and item.get("source_doc_id") in doc_ids)
        ]
        if not cluster:
            cluster = identity_items
        clusters.append((len(cluster), len(doc_ids), object_name, cluster))

    _, _, object_name, cluster = sorted(clusters, key=lambda item: (item[0], item[1], item[2]))[0]
    related = [_candidate_triple_payload(item) for item in cluster[:10]]
    if not related:
        return None
    return {
        "type": "split_entity",
        "source": subject,
        "new_entity": _split_entity_name(subject, object_name),
        "triples": related,
    }


def _relation_signature(triple: dict[str, Any], entity: str) -> str:
    direction = "out" if triple["subject"] == entity else "in"
    return f"{direction}:{triple['predicate']}"


def _relation_neighbor_key(triple: dict[str, Any], entity: str) -> str:
    neighbor = triple["object"] if triple["subject"] == entity else triple["subject"]
    return _entity_merge_key(neighbor)


def _set_overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _cluster_anchor(entity: str, cluster: list[dict[str, Any]], identity_predicates: set[str]) -> str:
    identity_neighbors = [
        item["object"]
        for item in cluster
        if item["subject"] == entity and item["predicate"] in identity_predicates
    ]
    if identity_neighbors:
        return sorted(identity_neighbors, key=lambda value: (len(value), value))[0]
    neighbors = [
        (
            -float(item.get("confidence", 0.0)),
            _relation_neighbor_key(item, entity),
            item["object"] if item["subject"] == entity else item["subject"],
        )
        for item in cluster
    ]
    return sorted(neighbors)[0][2] if neighbors else "split"


def _ambiguous_entity_split_candidates(
    normalized: list[dict[str, Any]],
    *,
    identity_predicates: set[str],
    skip_entities: set[str],
) -> list[dict[str, Any]]:
    incident_by_entity: dict[str, list[dict[str, Any]]] = {}
    for triple in normalized:
        incident_by_entity.setdefault(triple["subject"], []).append(triple)
        incident_by_entity.setdefault(triple["object"], []).append(triple)

    candidates: list[dict[str, Any]] = []
    for entity, incident in sorted(incident_by_entity.items()):
        if entity in skip_entities or len(incident) < 4:
            continue
        doc_groups: dict[str, list[dict[str, Any]]] = {}
        for item in incident:
            doc_id = item.get("source_doc_id") or ""
            if doc_id:
                doc_groups.setdefault(doc_id, []).append(item)
        if len(doc_groups) < 2:
            continue

        def doc_group_sort_key(
            entry: tuple[str, list[dict[str, Any]]],
            current_entity: str = entity,
        ) -> tuple[int, int, str]:
            doc_id, cluster = entry
            has_identity = any(item["subject"] == current_entity and item["predicate"] in identity_predicates for item in cluster)
            return (0 if has_identity else 1, len(cluster), doc_id)

        for doc_id, cluster in sorted(doc_groups.items(), key=doc_group_sort_key):
            if len(cluster) < 2 or len(cluster) > 10:
                continue
            rest = [item for item in incident if item.get("source_doc_id") != doc_id]
            if len(rest) < 2:
                continue

            cluster_signatures = {_relation_signature(item, entity) for item in cluster}
            rest_signatures = {_relation_signature(item, entity) for item in rest}
            cluster_neighbors = {_relation_neighbor_key(item, entity) for item in cluster}
            rest_neighbors = {_relation_neighbor_key(item, entity) for item in rest}
            signature_overlap = _set_overlap_ratio(cluster_signatures, rest_signatures)
            neighbor_overlap = _set_overlap_ratio(cluster_neighbors, rest_neighbors)
            has_identity_anchor = any(item["subject"] == entity and item["predicate"] in identity_predicates for item in cluster + rest)
            distinctive_neighbors = cluster_neighbors - rest_neighbors
            if signature_overlap > 0.2 or neighbor_overlap > 0.15:
                continue
            if not has_identity_anchor and len(distinctive_neighbors) < 2:
                continue

            related = [_candidate_triple_payload(item) for item in sorted(
                cluster,
                key=lambda item: (item.get("source_chunk_id", ""), item["predicate"], item["object"]),
            )[:10]]
            anchor = _cluster_anchor(entity, cluster, identity_predicates)
            score = round(min(0.9, 0.64 + (1 - signature_overlap) * 0.14 + (1 - neighbor_overlap) * 0.12), 3)
            candidates.append({
                "id": f"ambiguous_entity:{entity}|{doc_id}",
                "type": "ambiguous_entity",
                "severity": _issue_severity(score),
                "score": score,
                "title": f"疑似多义实体：{entity}",
                "description": "该实体在不同来源文档中的谓词和邻居几乎不重叠，建议确认是否需要拆分。",
                "entities": [entity, _split_entity_name(entity, anchor)],
                "related_triples": related[:5],
                "action": {
                    "type": "split_entity",
                    "source": entity,
                    "new_entity": _split_entity_name(entity, anchor),
                    "triples": related,
                },
                "reasons": ["source_doc_cluster_divergence", "predicate_neighbor_divergence"],
            })
            break
    return candidates


def _issue_severity(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def _ratio(part: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return round(float(part) / float(total), 3)


def _quality_alert(
    level: str,
    code: str,
    title: str,
    message: str,
    *,
    value: int | float,
    threshold: int | float,
    action: str,
) -> dict[str, Any]:
    return {
        "level": level,
        "code": code,
        "title": title,
        "message": message,
        "value": value,
        "threshold": threshold,
        "action": action,
    }


def knowledge_graph_quality_alerts(
    metrics: dict[str, Any],
    *,
    previous_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    relation_count = int(metrics.get("relation_count") or 0)
    if relation_count <= 0:
        return [_quality_alert(
            "warning",
            "no_relations",
            "暂无可评估关系",
            "当前知识图谱没有可评估三元组，质量趋势会在图谱抽取后建立。",
            value=0,
            threshold=1,
            action="先导入文档并开启知识图谱抽取。",
        )]

    alerts: list[dict[str, Any]] = []
    health_score = int(metrics.get("health_score") or 0)
    low_confidence_ratio = float(metrics.get("low_confidence_ratio") or 0.0)
    isolated_ratio = float(metrics.get("isolated_relation_ratio") or 0.0)
    backlog_ratio = float(metrics.get("open_review_backlog_ratio", metrics.get("review_backlog_ratio") or 0.0) or 0.0)

    if health_score < 50:
        alerts.append(_quality_alert(
            "error",
            "health_score_critical",
            "图谱健康分过低",
            f"当前健康分为 {health_score}，建议优先清理低置信度和孤立关系。",
            value=health_score,
            threshold=50,
            action="处理高分风险候选，并复查抽取配置或源文档质量。",
        ))
    elif health_score < 75:
        alerts.append(_quality_alert(
            "warning",
            "health_score_warning",
            "图谱健康分需关注",
            f"当前健康分为 {health_score}，低于长期使用建议阈值。",
            value=health_score,
            threshold=75,
            action="优先处理质量审核队列中的高风险候选。",
        ))

    if low_confidence_ratio >= 0.35:
        alerts.append(_quality_alert(
            "error",
            "low_confidence_ratio_critical",
            "低置信度关系比例过高",
            f"低置信度关系占比约 {round(low_confidence_ratio * 100)}%。",
            value=low_confidence_ratio,
            threshold=0.35,
            action="删除或修正低置信度关系，并检查抽取模型和阈值。",
        ))
    elif low_confidence_ratio >= 0.15:
        alerts.append(_quality_alert(
            "warning",
            "low_confidence_ratio_warning",
            "低置信度关系偏多",
            f"低置信度关系占比约 {round(low_confidence_ratio * 100)}%。",
            value=low_confidence_ratio,
            threshold=0.15,
            action="抽样复核低置信度关系，必要时提高导入文本质量。",
        ))

    if isolated_ratio >= 0.35:
        alerts.append(_quality_alert(
            "error",
            "isolated_relation_ratio_critical",
            "孤立关系比例过高",
            f"孤立关系占比约 {round(isolated_ratio * 100)}%。",
            value=isolated_ratio,
            threshold=0.35,
            action="复核孤立关系是否为噪声，并补充相关文档上下文。",
        ))
    elif isolated_ratio >= 0.2:
        alerts.append(_quality_alert(
            "warning",
            "isolated_relation_ratio_warning",
            "孤立关系偏多",
            f"孤立关系占比约 {round(isolated_ratio * 100)}%。",
            value=isolated_ratio,
            threshold=0.2,
            action="优先查看孤立关系候选，确认是否需要删除或补充证据。",
        ))

    if backlog_ratio >= 0.75:
        alerts.append(_quality_alert(
            "error",
            "review_backlog_critical",
            "待审积压过高",
            f"待处理候选约占关系数的 {round(backlog_ratio * 100)}%。",
            value=backlog_ratio,
            threshold=0.75,
            action="使用批量审核处理明显噪声，再逐条处理高风险候选。",
        ))
    elif backlog_ratio >= 0.4:
        alerts.append(_quality_alert(
            "warning",
            "review_backlog_warning",
            "待审积压偏高",
            f"待处理候选约占关系数的 {round(backlog_ratio * 100)}%。",
            value=backlog_ratio,
            threshold=0.4,
            action="安排一次图谱清理，优先处理重复实体和低置信度关系。",
        ))

    if previous_snapshot:
        previous_health = int(previous_snapshot.get("health_score") or previous_snapshot.get("metrics", {}).get("health_score") or 0)
        health_drop = previous_health - health_score
        if health_drop >= 20:
            alerts.append(_quality_alert(
                "error",
                "health_drop_critical",
                "图谱健康分明显下降",
                f"健康分较上次快照下降 {health_drop} 分。",
                value=health_drop,
                threshold=20,
                action="检查最近导入文档、抽取模型配置和新增审核候选。",
            ))
        elif health_drop >= 10:
            alerts.append(_quality_alert(
                "warning",
                "health_drop_warning",
                "图谱健康分下降",
                f"健康分较上次快照下降 {health_drop} 分。",
                value=health_drop,
                threshold=10,
                action="复查最近新增关系和质量审核队列变化。",
            ))

    return alerts


def _quality_gate_value(policy: Any, name: str, default: Any) -> Any:
    if isinstance(policy, dict):
        return policy.get(name, default)
    return getattr(policy, name, default)


def _quality_gate_violation(
    code: str,
    title: str,
    message: str,
    *,
    value: int | float,
    threshold: int | float,
) -> dict[str, Any]:
    return {
        "level": "warning",
        "code": code,
        "title": title,
        "message": message,
        "value": value,
        "threshold": threshold,
    }


def evaluate_knowledge_graph_quality_gate(metrics: dict[str, Any], policy: Any) -> dict[str, Any]:
    """Evaluate quality metrics against a non-blocking graph governance gate."""
    enabled = bool(_quality_gate_value(policy, "enabled", True))
    thresholds = {
        "min_health_score": int(_quality_gate_value(policy, "min_health_score", 75)),
        "max_low_confidence_ratio": float(_quality_gate_value(policy, "max_low_confidence_ratio", 0.15)),
        "max_isolated_relation_ratio": float(_quality_gate_value(policy, "max_isolated_relation_ratio", 0.2)),
        "max_open_review_backlog_ratio": float(_quality_gate_value(policy, "max_open_review_backlog_ratio", 0.4)),
        "require_relations": bool(_quality_gate_value(policy, "require_relations", False)),
        "min_relation_count": int(_quality_gate_value(policy, "min_relation_count", 1)),
    }
    health_score = int(metrics.get("health_score") or 0)
    relation_count = int(metrics.get("relation_count") or 0)
    low_confidence_ratio = float(metrics.get("low_confidence_ratio") or 0.0)
    isolated_relation_ratio = float(metrics.get("isolated_relation_ratio") or 0.0)
    open_backlog_ratio = float(
        metrics.get("open_review_backlog_ratio", metrics.get("review_backlog_ratio") or 0.0) or 0.0
    )
    evaluated_metrics = {
        "health_score": health_score,
        "relation_count": relation_count,
        "low_confidence_ratio": low_confidence_ratio,
        "isolated_relation_ratio": isolated_relation_ratio,
        "open_review_backlog_ratio": open_backlog_ratio,
    }
    if not enabled:
        return {
            "enabled": False,
            "passed": True,
            "status": "ok",
            "message": "知识图谱质量门禁未启用。",
            "violations": [],
            "thresholds": thresholds,
            "metrics": evaluated_metrics,
        }

    if relation_count <= 0 and not thresholds["require_relations"]:
        return {
            "enabled": True,
            "passed": True,
            "status": "ok",
            "message": "暂无知识图谱关系，门禁会在首批关系生成后开始评估。",
            "violations": [],
            "thresholds": thresholds,
            "metrics": evaluated_metrics,
        }

    violations: list[dict[str, Any]] = []
    if thresholds["require_relations"] and relation_count < thresholds["min_relation_count"]:
        violations.append(_quality_gate_violation(
            "relation_count_below_gate",
            "关系数量不足",
            f"当前可评估关系数为 {relation_count}，低于门禁阈值 {thresholds['min_relation_count']}。",
            value=relation_count,
            threshold=thresholds["min_relation_count"],
        ))
    if health_score < thresholds["min_health_score"]:
        violations.append(_quality_gate_violation(
            "health_score_below_gate",
            "健康分低于门禁",
            f"当前健康分为 {health_score}，低于门禁阈值 {thresholds['min_health_score']}。",
            value=health_score,
            threshold=thresholds["min_health_score"],
        ))
    if low_confidence_ratio > thresholds["max_low_confidence_ratio"]:
        violations.append(_quality_gate_violation(
            "low_confidence_ratio_above_gate",
            "低置信度比例超标",
            f"低置信度关系占比约 {round(low_confidence_ratio * 100)}%，高于门禁阈值。",
            value=low_confidence_ratio,
            threshold=thresholds["max_low_confidence_ratio"],
        ))
    if isolated_relation_ratio > thresholds["max_isolated_relation_ratio"]:
        violations.append(_quality_gate_violation(
            "isolated_relation_ratio_above_gate",
            "孤立关系比例超标",
            f"孤立关系占比约 {round(isolated_relation_ratio * 100)}%，高于门禁阈值。",
            value=isolated_relation_ratio,
            threshold=thresholds["max_isolated_relation_ratio"],
        ))
    if open_backlog_ratio > thresholds["max_open_review_backlog_ratio"]:
        violations.append(_quality_gate_violation(
            "open_review_backlog_ratio_above_gate",
            "待审积压比例超标",
            f"待审候选约占关系数的 {round(open_backlog_ratio * 100)}%，高于门禁阈值。",
            value=open_backlog_ratio,
            threshold=thresholds["max_open_review_backlog_ratio"],
        ))

    passed = not violations
    return {
        "enabled": True,
        "passed": passed,
        "status": "ok" if passed else "warning",
        "message": "知识图谱质量门禁通过。" if passed else f"知识图谱质量门禁未通过：{len(violations)} 项指标超出阈值。",
        "violations": violations,
        "thresholds": thresholds,
        "metrics": evaluated_metrics,
    }


def _knowledge_graph_quality_metrics(
    normalized: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    entities: dict[str, dict[str, Any]],
    *,
    average_confidence: float,
) -> dict[str, Any]:
    relation_count = len(normalized)
    source_doc_ids = {item["source_doc_id"] for item in normalized if item.get("source_doc_id")}
    source_chunk_ids = {item["source_chunk_id"] for item in normalized if item.get("source_chunk_id")}
    candidate_count = len(candidates)
    duplicate_count = sum(1 for item in candidates if item["type"] == "duplicate_entity")
    low_confidence_count = sum(1 for item in candidates if item["type"] == "low_confidence_relation")
    isolated_count = sum(1 for item in candidates if item["type"] == "isolated_relation")
    conflict_count = sum(1 for item in candidates if item["type"] == "conflicting_relation")
    ambiguous_count = sum(1 for item in candidates if item["type"] == "ambiguous_entity")

    low_confidence_ratio = _ratio(low_confidence_count, relation_count)
    isolated_ratio = _ratio(isolated_count, relation_count)
    review_backlog_ratio = _ratio(candidate_count, relation_count)
    duplicate_entity_ratio = _ratio(duplicate_count, len(entities))
    conflict_ratio = _ratio(conflict_count, relation_count)
    ambiguous_entity_ratio = _ratio(ambiguous_count, len(entities))

    if relation_count:
        health_score = round(max(0.0, min(100.0, (
            average_confidence * 55
            + (1 - min(low_confidence_ratio, 1.0)) * 15
            + (1 - min(isolated_ratio, 1.0)) * 15
            + (1 - min(review_backlog_ratio, 1.0)) * 15
        ))))
    else:
        health_score = 0

    risk_level = "low"
    if health_score < 50 or low_confidence_ratio >= 0.35 or isolated_ratio >= 0.35:
        risk_level = "high"
    elif health_score < 75 or low_confidence_ratio >= 0.15 or review_backlog_ratio >= 0.4:
        risk_level = "medium"

    result = {
        "health_score": health_score,
        "risk_level": risk_level,
        "relation_count": relation_count,
        "entity_count": len(entities),
        "source_doc_count": len(source_doc_ids),
        "source_chunk_count": len(source_chunk_ids),
        "triples_per_source_chunk": round(relation_count / len(source_chunk_ids), 2) if source_chunk_ids else 0.0,
        "candidate_count": candidate_count,
        "duplicate_entity_ratio": duplicate_entity_ratio,
        "low_confidence_ratio": low_confidence_ratio,
        "isolated_relation_ratio": isolated_ratio,
        "conflicting_relation_ratio": conflict_ratio,
        "ambiguous_entity_ratio": ambiguous_entity_ratio,
        "review_backlog_ratio": review_backlog_ratio,
    }
    result["alerts"] = knowledge_graph_quality_alerts(result)
    return result


def knowledge_graph_quality_payload(
    triples: list[dict[str, Any]],
    *,
    stats: dict[str, Any] | None = None,
    confidence_threshold: float = 0.6,
    limit: int = 50,
) -> dict[str, Any]:
    """Generate lightweight review candidates for graph governance.

    The result is intentionally deterministic and side-effect free so it can be
    recomputed on every page load. Mutation remains explicit through the
    dedicated merge/update/delete endpoints.
    """
    normalized = [
        triple
        for triple in (_normalize_exported_triple(item) for item in triples)
        if triple is not None
    ]
    safe_threshold = max(0.0, min(1.0, float(confidence_threshold)))
    safe_limit = max(1, min(int(limit), 200))
    entities = _entity_degree_map(normalized)
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_candidate(candidate: dict[str, Any]) -> None:
        candidate_id = str(candidate["id"])
        if candidate_id in seen_ids:
            return
        seen_ids.add(candidate_id)
        candidate["fingerprint"] = _candidate_fingerprint(candidate)
        candidates.append(candidate)

    merge_groups: dict[str, list[str]] = {}
    for name in entities:
        key = _entity_merge_key(name)
        if len(key) >= 2:
            merge_groups.setdefault(key, []).append(name)

    for names in merge_groups.values():
        unique_names = sorted(set(names), key=lambda name: (-entities[name]["degree"], len(name), name))
        if len(unique_names) < 2:
            continue
        target = unique_names[0]
        for source in unique_names[1:]:
            score = 0.96
            add_candidate({
                "id": f"duplicate_entity:{source}->{target}",
                "type": "duplicate_entity",
                "severity": _issue_severity(score),
                "score": score,
                "title": f"疑似重复实体：{source} / {target}",
                "description": "实体名称仅在空格、符号或大小写上不同，建议确认是否合并。",
                "entities": [source, target],
                "action": {"type": "merge_entities", "source": source, "target": target},
                "reasons": ["normalized_name_match"],
            })

    sorted_entity_names = sorted(entities)
    for idx, left in enumerate(sorted_entity_names):
        left_key = _entity_merge_key(left)
        if len(left_key) < 4:
            continue
        for right in sorted_entity_names[idx + 1:]:
            right_key = _entity_merge_key(right)
            if len(right_key) < 4 or left_key == right_key:
                continue
            similarity = difflib.SequenceMatcher(None, left_key, right_key).ratio()
            if similarity < 0.92:
                continue
            target, source = sorted(
                [left, right],
                key=lambda name: (-entities[name]["degree"], len(name), name),
            )
            add_candidate({
                "id": f"similar_entity:{source}->{target}",
                "type": "duplicate_entity",
                "severity": _issue_severity(similarity),
                "score": round(similarity, 3),
                "title": f"疑似近似实体：{source} / {target}",
                "description": "实体名称高度相似，建议人工确认是否为同一概念。",
                "entities": [source, target],
                "action": {"type": "merge_entities", "source": source, "target": target},
                "reasons": ["high_name_similarity"],
            })

    for triple in normalized:
        confidence = float(triple["confidence"])
        if confidence >= safe_threshold:
            continue
        score = min(1.0, safe_threshold - confidence + 0.55)
        payload = _candidate_triple_payload(triple)
        add_candidate({
            "id": (
                "low_confidence:"
                f"{triple['subject']}|{triple['predicate']}|{triple['object']}|{triple['source_chunk_id']}"
            ),
            "type": "low_confidence_relation",
            "severity": _issue_severity(score),
            "score": round(score, 3),
            "title": f"低置信度关系：{triple['subject']} {triple['predicate']} {triple['object']}",
            "description": f"置信度 {confidence:.2f} 低于当前阈值 {safe_threshold:.2f}，建议修正或删除。",
            "triple": payload,
            "action": {"type": "review_triple", "triple": payload},
            "reasons": ["low_confidence"],
        })

    for triple in normalized:
        if triple["confidence"] >= max(safe_threshold, 0.75):
            continue
        subject_degree = int(entities.get(triple["subject"], {}).get("degree", 0))
        object_degree = int(entities.get(triple["object"], {}).get("degree", 0))
        if subject_degree > 1 or object_degree > 1:
            continue
        score = 0.68
        payload = _candidate_triple_payload(triple)
        add_candidate({
            "id": (
                "isolated_relation:"
                f"{triple['subject']}|{triple['predicate']}|{triple['object']}|{triple['source_chunk_id']}"
            ),
            "type": "isolated_relation",
            "severity": _issue_severity(score),
            "score": score,
            "title": f"孤立关系：{triple['subject']} {triple['predicate']} {triple['object']}",
            "description": "关系两端都只出现一次且置信度不高，可能是抽取噪声。",
            "triple": payload,
            "action": {"type": "review_triple", "triple": payload},
            "reasons": ["isolated_low_evidence"],
        })

    identity_predicates = {"是", "为", "属于", "位于", "存在于"}
    identity_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for triple in normalized:
        if triple["predicate"] in identity_predicates:
            identity_groups.setdefault((triple["subject"], triple["predicate"]), []).append(triple)

    identity_conflict_subjects: set[str] = set()
    for (subject, predicate), items in identity_groups.items():
        objects = sorted({item["object"] for item in items})
        if len(objects) < 2:
            continue
        identity_conflict_subjects.add(subject)
        score = 0.7
        related = [_candidate_triple_payload(item) for item in items[:5]]
        action = _identity_split_suggestion(subject, items, normalized) or {
            "type": "review_group",
            "triples": related,
        }
        add_candidate({
            "id": f"conflicting_relation:{subject}|{predicate}",
            "type": "conflicting_relation",
            "severity": _issue_severity(score),
            "score": score,
            "title": f"一对多身份关系：{subject} {predicate} 多个对象",
            "description": "同一主体在身份类关系上指向多个对象，建议确认是否需要拆分实体或修正关系。",
            "entities": [subject, *objects[:4]],
            "related_triples": related,
            "action": action,
            "reasons": ["identity_predicate_many_objects", "split_entity_suggestion"],
        })

    for candidate in _ambiguous_entity_split_candidates(
        normalized,
        identity_predicates=identity_predicates,
        skip_entities=identity_conflict_subjects,
    ):
        add_candidate(candidate)

    candidates.sort(key=lambda item: (item["score"], item["title"]), reverse=True)
    limited_candidates = candidates[:safe_limit]
    average_confidence = (
        round(sum(float(item["confidence"]) for item in normalized) / len(normalized), 3)
        if normalized
        else 0.0
    )
    summary: dict[str, Any] = {
        "total_candidates": len(candidates),
        "returned_candidates": len(limited_candidates),
        "duplicate_entity_count": sum(1 for item in candidates if item["type"] == "duplicate_entity"),
        "low_confidence_relation_count": sum(1 for item in candidates if item["type"] == "low_confidence_relation"),
        "isolated_relation_count": sum(1 for item in candidates if item["type"] == "isolated_relation"),
        "conflicting_relation_count": sum(1 for item in candidates if item["type"] == "conflicting_relation"),
        "ambiguous_entity_count": sum(1 for item in candidates if item["type"] == "ambiguous_entity"),
        "average_confidence": average_confidence,
        "quality_metrics": _knowledge_graph_quality_metrics(
            normalized,
            candidates,
            entities,
            average_confidence=average_confidence,
        ),
    }
    if stats:
        summary["nodes"] = stats.get("nodes", 0)
        summary["edges"] = stats.get("edges", 0)

    return {
        "summary": summary,
        "candidates": limited_candidates,
        "thresholds": {
            "confidence_threshold": safe_threshold,
            "limit": safe_limit,
        },
    }


# ---------------------------------------------------------------------------
# KnowledgeGraph Base Class
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """知识图谱接口"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def save(self) -> None:
        pass

    def add_triple(self, triple: Triple) -> None:
        pass

    def remove_by_chunk_id(self, chunk_id: str) -> int:
        return 0

    def remove_by_doc_id(self, doc_id: str) -> int:
        return 0

    def merge_entities(self, source: str, target: str) -> dict[str, Any]:
        return {"merged": False, "reason": "unsupported"}

    def split_entity(self, source: str, new_entity: str, triples: list[Triple]) -> dict[str, Any]:
        return {"split": False, "reason": "unsupported"}

    def delete_triple(self, triple: Triple) -> bool:
        return False

    def update_triple(self, old: Triple, new: Triple) -> bool:
        return False

    def clear(self) -> None:
        pass

    def search(self, query: str, top_k: int = 10) -> GraphSearchResult | None:
        return None

    def query_subgraph(self, entity: str, depth: int = 2) -> nx.MultiDiGraph:
        return nx.MultiDiGraph()

    def get_paths_between(self, source: str, target: str, max_length: int = 4) -> list[list[str]]:
        return []

    def get_neighbors(self, entity: str, edge_predicate: str | None = None) -> list[tuple[str, str]]:
        return []

    def stats(self) -> dict:
        return {"enabled": self.enabled}

    def export_triples(self) -> list[dict]:
        return []

    async def build_from_chunks(
        self,
        chunks: list[TextChunk],
        *,
        llm_provider: str = "",
        llm_api_key: str = "",
        llm_base_url: str = "",
        llm_model: str = "",
        use_llm: bool = False,
    ) -> dict:
        """从文档 chunks 构建/更新知识图谱。"""
        if not self.enabled:
            return {"added": 0, "chunks_processed": 0, "method": "disabled"}

        added = 0
        method = "rule"
        fallback_chunks = 0

        valid_chunks = [c for c in chunks if c.content and len(c.content.strip()) >= 10]

        if use_llm and llm_api_key:
            method = "llm"
            batch_size = 5
            for i in range(0, len(valid_chunks), batch_size):
                batch = valid_chunks[i:i+batch_size]
                batch_data = [(c.id, c.content) for c in batch]

                batch_results = await _extract_triples_via_llm_batch(
                    chunks=batch_data,
                    llm_provider=llm_provider,
                    llm_api_key=llm_api_key,
                    llm_base_url=llm_base_url,
                    llm_model=llm_model,
                )

                for chunk in batch:
                    triples = batch_results.get(chunk.id)
                    if triples is None: # LLM failed or skipped this chunk, fallback
                        fallback_chunks += 1
                        triples = _extract_triples_rule_based(chunk.content, chunk.id)

                    for t in triples:
                        self.add_triple(t)
                        added += 1
        else:
            for chunk in valid_chunks:
                triples = _extract_triples_rule_based(chunk.content, chunk.id)
                for t in triples:
                    self.add_triple(t)
                    added += 1

        self.save()
        return {
            "added": added,
            "chunks_processed": len(chunks),
            "method": method,
            "llm_fallback_chunks": fallback_chunks,
        }

    def build_from_triples(self, triples: list[Triple]) -> int:
        """直接从 Triple 列表构建图（外部已抽取好的场景）"""
        if not self.enabled:
            return 0
        for t in triples:
            self.add_triple(t)
        self.save()
        return len(triples)


# ---------------------------------------------------------------------------
# NetworkX Implementation
# ---------------------------------------------------------------------------

class NetworkXKnowledgeGraph(KnowledgeGraph):
    VERSION = 1

    def __init__(self, persist_path: str = "./data/knowledge_graph.gml", enabled: bool = True):
        super().__init__(enabled)
        self.persist_path = Path(persist_path)
        self._lock = threading.Lock()
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()

        if self.enabled:
            self._graph.graph["name"] = "MemoX Knowledge Graph"
            self._graph.graph["version"] = self.VERSION
            self._load()

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            self._graph = nx.read_gml(str(self.persist_path))
        except Exception:
            self._graph = nx.MultiDiGraph()
            self._graph.graph["name"] = "MemoX Knowledge Graph"
            self._graph.graph["version"] = self.VERSION

    def save(self) -> None:
        if not self.enabled:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        nx.write_gml(self._graph, str(self.persist_path))

    def add_triple(self, triple: Triple) -> None:
        if not self.enabled:
            return
        with self._lock:
            for node in (triple.subject, triple.object):
                if node not in self._graph:
                    self._graph.add_node(node, label=node)

            with contextlib.suppress(nx.NetworkXError):
                self._graph.remove_edge(triple.subject, triple.object, key=triple.predicate)
            self._graph.add_edge(
                triple.subject,
                triple.object,
                key=triple.predicate,
                predicate=triple.predicate,
                source_chunk_id=triple.source_chunk_id,
                confidence=triple.confidence,
            )

    def _cleanup_orphan_nodes_locked(self) -> int:
        removed = 0
        for node in list(self._graph.nodes()):
            if self._graph.degree(node) == 0:
                self._graph.remove_node(node)
                removed += 1
        return removed

    def _merge_edge_attrs_locked(
        self,
        subject: str,
        obj: str,
        predicate: str,
        attrs: dict[str, Any],
    ) -> bool:
        if subject == obj:
            return False
        if subject not in self._graph:
            self._graph.add_node(subject, label=subject)
        if obj not in self._graph:
            self._graph.add_node(obj, label=obj)

        existing = self._graph.get_edge_data(subject, obj, key=predicate)
        if existing is not None:
            new_confidence = _safe_confidence(attrs.get("confidence"))
            existing_confidence = _safe_confidence(existing.get("confidence"))
            if new_confidence >= existing_confidence:
                existing["predicate"] = predicate
                existing["confidence"] = new_confidence
                existing["source_chunk_id"] = str(attrs.get("source_chunk_id", ""))
            return False

        self._graph.add_edge(
            subject,
            obj,
            key=predicate,
            predicate=predicate,
            source_chunk_id=str(attrs.get("source_chunk_id", "")),
            confidence=_safe_confidence(attrs.get("confidence")),
        )
        return True

    def remove_by_chunk_id(self, chunk_id: str) -> int:
        if not self.enabled:
            return 0
        removed = 0
        with self._lock:
            edges_to_remove = [
                (u, v, k)
                for u, v, k, data in self._graph.edges(keys=True, data=True)
                if data.get("source_chunk_id") == chunk_id
            ]
            for u, v, k in edges_to_remove:
                self._graph.remove_edge(u, v, key=k)
                removed += 1
            self._cleanup_orphan_nodes_locked()
        return removed

    def remove_by_doc_id(self, doc_id: str) -> int:
        if not self.enabled:
            return 0
        removed = 0
        prefix = f"{doc_id}_chunk_"
        with self._lock:
            edges_to_remove = [
                (u, v, k)
                for u, v, k, data in self._graph.edges(keys=True, data=True)
                if data.get("source_chunk_id") == doc_id
                or str(data.get("source_chunk_id", "")).startswith(prefix)
            ]
            for u, v, k in edges_to_remove:
                self._graph.remove_edge(u, v, key=k)
                removed += 1
            self._cleanup_orphan_nodes_locked()
        return removed

    def merge_entities(self, source: str, target: str) -> dict[str, Any]:
        if not self.enabled:
            return {"merged": False, "reason": "disabled"}

        source = _clean_graph_text(source)
        target = _clean_graph_text(target)
        if not source or not target:
            return {"merged": False, "reason": "empty_entity"}
        if source.lower() == target.lower():
            return {"merged": False, "reason": "same_entity"}

        with self._lock:
            if source not in self._graph:
                return {"merged": False, "reason": "source_not_found"}
            if target not in self._graph:
                self._graph.add_node(target, label=target)

            redirected_edges = 0
            deduplicated_edges = 0
            removed_self_loops = 0
            source_edges = [
                (u, v, k, dict(data))
                for u, v, k, data in self._graph.edges(keys=True, data=True)
                if u == source or v == source
            ]

            for u, v, k, data in source_edges:
                if not self._graph.has_edge(u, v, key=k):
                    continue
                new_subject = target if u == source else u
                new_object = target if v == source else v
                predicate = _clean_graph_text(data.get("predicate", k), max_len=40) or str(k)

                self._graph.remove_edge(u, v, key=k)
                if new_subject == new_object:
                    removed_self_loops += 1
                    continue

                added = self._merge_edge_attrs_locked(new_subject, new_object, predicate, data)
                if added:
                    redirected_edges += 1
                else:
                    deduplicated_edges += 1

            if source in self._graph:
                self._graph.remove_node(source)
            removed_orphan_nodes = self._cleanup_orphan_nodes_locked()
            self.save()
            return {
                "merged": True,
                "source": source,
                "target": target,
                "redirected_edges": redirected_edges,
                "deduplicated_edges": deduplicated_edges,
                "removed_self_loops": removed_self_loops,
                "removed_orphan_nodes": removed_orphan_nodes,
                "nodes": self._graph.number_of_nodes(),
                "edges": self._graph.number_of_edges(),
            }

    def split_entity(self, source: str, new_entity: str, triples: list[Triple]) -> dict[str, Any]:
        if not self.enabled:
            return {"split": False, "reason": "disabled"}

        source = _clean_graph_text(source)
        new_entity = _clean_graph_text(new_entity)
        if not source or not new_entity:
            return {"split": False, "reason": "empty_entity"}
        if source.lower() == new_entity.lower():
            return {"split": False, "reason": "same_entity"}

        with self._lock:
            if source not in self._graph:
                return {"split": False, "reason": "source_not_found"}

            moved_edges = 0
            deduplicated_edges = 0
            skipped_edges = 0
            for triple in triples:
                subject = _clean_graph_text(triple.subject)
                predicate = _clean_graph_text(triple.predicate, max_len=40)
                obj = _clean_graph_text(triple.object)
                source_chunk_id = _clean_graph_text(triple.source_chunk_id, max_len=120)
                if not subject or not predicate or not obj or (subject != source and obj != source):
                    skipped_edges += 1
                    continue
                if not self._graph.has_edge(subject, obj, key=predicate):
                    skipped_edges += 1
                    continue

                edge_data = dict(self._graph.edges[subject, obj, predicate])
                if source_chunk_id and edge_data.get("source_chunk_id", "") != source_chunk_id:
                    skipped_edges += 1
                    continue

                new_subject = new_entity if subject == source else subject
                new_object = new_entity if obj == source else obj
                if new_subject == new_object:
                    skipped_edges += 1
                    continue

                self._graph.remove_edge(subject, obj, key=predicate)
                edge_data["confidence"] = triple.confidence if triple.confidence is not None else edge_data.get("confidence", 1.0)
                added = self._merge_edge_attrs_locked(new_subject, new_object, predicate, edge_data)
                if added:
                    moved_edges += 1
                else:
                    deduplicated_edges += 1

            if moved_edges == 0 and deduplicated_edges == 0:
                return {"split": False, "reason": "no_matching_edges", "skipped_edges": skipped_edges}

            removed_orphan_nodes = self._cleanup_orphan_nodes_locked()
            self.save()
            return {
                "split": True,
                "source": source,
                "new_entity": new_entity,
                "moved_edges": moved_edges,
                "deduplicated_edges": deduplicated_edges,
                "skipped_edges": skipped_edges,
                "removed_orphan_nodes": removed_orphan_nodes,
                "nodes": self._graph.number_of_nodes(),
                "edges": self._graph.number_of_edges(),
            }

    def delete_triple(self, triple: Triple) -> bool:
        if not self.enabled:
            return False

        subject = _clean_graph_text(triple.subject)
        predicate = _clean_graph_text(triple.predicate, max_len=40)
        obj = _clean_graph_text(triple.object)
        source_chunk_id = _clean_graph_text(triple.source_chunk_id, max_len=120)
        if not subject or not predicate or not obj:
            return False

        with self._lock:
            if not self._graph.has_edge(subject, obj, key=predicate):
                return False
            edge_data = self._graph.edges[subject, obj, predicate]
            if source_chunk_id and edge_data.get("source_chunk_id", "") != source_chunk_id:
                return False
            self._graph.remove_edge(subject, obj, key=predicate)
            self._cleanup_orphan_nodes_locked()
            self.save()
            return True

    def update_triple(self, old: Triple, new: Triple) -> bool:
        if not self.enabled:
            return False

        old_subject = _clean_graph_text(old.subject)
        old_predicate = _clean_graph_text(old.predicate, max_len=40)
        old_object = _clean_graph_text(old.object)
        old_source_chunk_id = _clean_graph_text(old.source_chunk_id, max_len=120)
        new_subject = _clean_graph_text(new.subject)
        new_predicate = _clean_graph_text(new.predicate, max_len=40)
        new_object = _clean_graph_text(new.object)
        if (
            not old_subject
            or not old_predicate
            or not old_object
            or not new_subject
            or not new_predicate
            or not new_object
            or new_subject == new_object
        ):
            return False

        with self._lock:
            if not self._graph.has_edge(old_subject, old_object, key=old_predicate):
                return False
            old_edge_data = dict(self._graph.edges[old_subject, old_object, old_predicate])
            if old_source_chunk_id and old_edge_data.get("source_chunk_id", "") != old_source_chunk_id:
                return False

            self._graph.remove_edge(old_subject, old_object, key=old_predicate)
            self._merge_edge_attrs_locked(
                new_subject,
                new_object,
                new_predicate,
                {
                    "source_chunk_id": new.source_chunk_id or old_edge_data.get("source_chunk_id", ""),
                    "confidence": new.confidence,
                },
            )
            self._cleanup_orphan_nodes_locked()
            self.save()
            return True

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._graph.clear()

    def search(self, query: str, top_k: int = 10) -> GraphSearchResult | None:
        if not self.enabled:
            return None
        query_lower = query.lower()
        with self._lock:
            candidates: list[tuple[str, int]] = []
            for node in self._graph.nodes():
                if query_lower in node.lower():
                    degree = self._graph.degree(node)
                    score = 2 if node == query else (1.5 if node.lower() == query_lower else 1)
                    candidates.append((node, degree * score))

            if not candidates:
                return None

            candidates.sort(key=lambda x: x[1], reverse=True)
            best_entity = candidates[0][0]

            result_triples: list[Triple] = []
            connected: set[str] = set()

            for u, v, k, data in self._graph.edges(keys=True, data=True):
                if u == best_entity or v == best_entity:
                    result_triples.append(Triple(
                        subject=u,
                        predicate=data.get("predicate", k),
                        object=v,
                        source_chunk_id=data.get("source_chunk_id", ""),
                        confidence=data.get("confidence", 1.0),
                    ))
                    if u == best_entity:
                        connected.add(v)
                    else:
                        connected.add(u)

            return GraphSearchResult(
                entity=best_entity,
                triples=result_triples,
                connected_entities=sorted(connected),
                degree=self._graph.degree(best_entity),
            )

    def query_subgraph(self, entity: str, depth: int = 2) -> nx.MultiDiGraph:
        if not self.enabled:
            return nx.MultiDiGraph()
        with self._lock:
            if entity not in self._graph:
                return nx.MultiDiGraph()
            g = nx.ego_graph(self._graph, entity, radius=depth, undirected=True)
            return g

    def get_paths_between(self, source: str, target: str, max_length: int = 4) -> list[list[str]]:
        if not self.enabled:
            return []
        with self._lock:
            if source not in self._graph or target not in self._graph:
                return []
            try:
                return list(nx.all_shortest_paths(self._graph, source, target, weight=None))
            except nx.NetworkXNoPath:
                return []

    def get_neighbors(self, entity: str, edge_predicate: str | None = None) -> list[tuple[str, str]]:
        if not self.enabled:
            return []
        with self._lock:
            if entity not in self._graph:
                return []
            neighbors: list[tuple[str, str]] = []
            for u, v, k, data in self._graph.edges(entity, keys=True, data=True):
                p = data.get("predicate", k) if isinstance(k, str) else k
                if edge_predicate is None or p == edge_predicate:
                    neighbor = v if u == entity else u
                    neighbors.append((neighbor, p))
            return neighbors

    def stats(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        with self._lock:
            return {
                "enabled": True,
                "nodes": self._graph.number_of_nodes(),
                "edges": self._graph.number_of_edges(),
                "version": self.VERSION,
                "persist_path": str(self.persist_path),
            }

    def export_triples(self) -> list[dict]:
        if not self.enabled:
            return []
        with self._lock:
            return [
                {
                    "subject": u,
                    "predicate": data.get("predicate", k),
                    "object": v,
                    "source_chunk_id": data.get("source_chunk_id", ""),
                    "confidence": data.get("confidence", 1.0),
                }
                for u, v, k, data in self._graph.edges(keys=True, data=True)
            ]


# ---------------------------------------------------------------------------
# Neo4j Implementation
# ---------------------------------------------------------------------------

class Neo4jKnowledgeGraph(KnowledgeGraph):
    def __init__(self, uri: str, user: str, password: str, enabled: bool = True):
        super().__init__(enabled)
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None

        if self.enabled:
            try:
                from neo4j import GraphDatabase
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            except ImportError:
                print("Neo4j python driver is not installed. Run: uv add neo4j")
                self.enabled = False
            except Exception as e:
                print(f"Failed to connect to Neo4j: {e}")
                self.enabled = False

    def __del__(self):
        if self.driver is not None:
            self.driver.close()

    def add_triple(self, triple: Triple) -> None:
        if not self.enabled or not self.driver:
            return

        # Cypher to merge subject and object, and then create relationship
        query = """
        MERGE (s:Entity {name: $subject})
        MERGE (o:Entity {name: $object})
        MERGE (s)-[r:RELATION {predicate: $predicate}]->(o)
        SET r.source_chunk_id = $source_chunk_id,
            r.confidence = $confidence
        """
        try:
            with self.driver.session() as session:
                session.run(query,
                            subject=triple.subject,
                            object=triple.object,
                            predicate=triple.predicate,
                            source_chunk_id=triple.source_chunk_id,
                            confidence=triple.confidence)
        except Exception as e:
            print(f"[Neo4j] Failed to add triple: {e}")

    def remove_by_chunk_id(self, chunk_id: str) -> int:
        if not self.enabled or not self.driver:
            return 0

        query = """
        MATCH ()-[r:RELATION {source_chunk_id: $chunk_id}]->()
        DELETE r
        """
        # Also clean up orphan nodes
        cleanup_query = """
        MATCH (n:Entity)
        WHERE NOT (n)--()
        DELETE n
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, chunk_id=chunk_id)
                counters = result.consume().counters
                deleted_rels = counters.relationships_deleted
                session.run(cleanup_query)
                return deleted_rels
        except Exception as e:
            print(f"[Neo4j] Failed to remove chunk {chunk_id}: {e}")
            return 0

    def remove_by_doc_id(self, doc_id: str) -> int:
        if not self.enabled or not self.driver:
            return 0

        query = """
        MATCH ()-[r:RELATION]->()
        WHERE r.source_chunk_id = $doc_id OR r.source_chunk_id STARTS WITH $prefix
        DELETE r
        """
        cleanup_query = """
        MATCH (n:Entity)
        WHERE NOT (n)--()
        DELETE n
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, doc_id=doc_id, prefix=f"{doc_id}_chunk_")
                counters = result.consume().counters
                deleted_rels = counters.relationships_deleted
                session.run(cleanup_query)
                return deleted_rels
        except Exception as e:
            print(f"[Neo4j] Failed to remove doc {doc_id}: {e}")
            return 0

    def merge_entities(self, source: str, target: str) -> dict[str, Any]:
        if not self.enabled or not self.driver:
            return {"merged": False, "reason": "disabled"}

        source = _clean_graph_text(source)
        target = _clean_graph_text(target)
        if not source or not target:
            return {"merged": False, "reason": "empty_entity"}
        if source.lower() == target.lower():
            return {"merged": False, "reason": "same_entity"}

        exists_query = """
        MATCH (s:Entity {name: $source})
        RETURN count(s) as c
        """
        ensure_target_query = """
        MERGE (:Entity {name: $target})
        """
        outgoing_query = """
        MATCH (s:Entity {name: $source})-[r:RELATION]->(o:Entity)
        WHERE o.name <> $target
        MATCH (t:Entity {name: $target})
        MERGE (t)-[nr:RELATION {predicate: r.predicate}]->(o)
        SET nr.source_chunk_id = r.source_chunk_id,
            nr.confidence = CASE
                WHEN nr.confidence IS NULL OR r.confidence >= nr.confidence THEN r.confidence
                ELSE nr.confidence
            END
        DELETE r
        """
        incoming_query = """
        MATCH (i:Entity)-[r:RELATION]->(s:Entity {name: $source})
        WHERE i.name <> $target
        MATCH (t:Entity {name: $target})
        MERGE (i)-[nr:RELATION {predicate: r.predicate}]->(t)
        SET nr.source_chunk_id = r.source_chunk_id,
            nr.confidence = CASE
                WHEN nr.confidence IS NULL OR r.confidence >= nr.confidence THEN r.confidence
                ELSE nr.confidence
            END
        DELETE r
        """
        remove_self_loops_query = """
        MATCH (:Entity {name: $source})-[r:RELATION]-(:Entity {name: $target})
        DELETE r
        """
        delete_source_query = """
        MATCH (s:Entity {name: $source})
        DETACH DELETE s
        """
        cleanup_query = """
        MATCH (n:Entity)
        WHERE NOT (n)--()
        DELETE n
        """
        try:
            with self.driver.session() as session:
                exists = session.run(exists_query, source=source).single()
                if not exists or exists["c"] == 0:
                    return {"merged": False, "reason": "source_not_found"}

                session.run(ensure_target_query, target=target)
                outgoing = session.run(outgoing_query, source=source, target=target).consume().counters.relationships_deleted
                incoming = session.run(incoming_query, source=source, target=target).consume().counters.relationships_deleted
                removed_self_loops = session.run(
                    remove_self_loops_query,
                    source=source,
                    target=target,
                ).consume().counters.relationships_deleted
                session.run(delete_source_query, source=source)
                removed_orphan_nodes = session.run(cleanup_query).consume().counters.nodes_deleted
                return {
                    "merged": True,
                    "source": source,
                    "target": target,
                    "redirected_edges": outgoing + incoming,
                    "deduplicated_edges": 0,
                    "removed_self_loops": removed_self_loops,
                    "removed_orphan_nodes": removed_orphan_nodes,
                }
        except Exception as e:
            print(f"[Neo4j] Failed to merge entities: {e}")
            return {"merged": False, "reason": "neo4j_error", "error": str(e)}

    def delete_triple(self, triple: Triple) -> bool:
        if not self.enabled or not self.driver:
            return False

        subject = _clean_graph_text(triple.subject)
        predicate = _clean_graph_text(triple.predicate, max_len=40)
        obj = _clean_graph_text(triple.object)
        source_chunk_id = _clean_graph_text(triple.source_chunk_id, max_len=120)
        if not subject or not predicate or not obj:
            return False

        query = """
        MATCH (s:Entity {name: $subject})-[r:RELATION {predicate: $predicate}]->(o:Entity {name: $object})
        WHERE $source_chunk_id = "" OR r.source_chunk_id = $source_chunk_id
        DELETE r
        """
        cleanup_query = """
        MATCH (n:Entity)
        WHERE NOT (n)--()
        DELETE n
        """
        try:
            with self.driver.session() as session:
                result = session.run(
                    query,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    source_chunk_id=source_chunk_id,
                )
                deleted = result.consume().counters.relationships_deleted
                if deleted:
                    session.run(cleanup_query)
                return deleted > 0
        except Exception as e:
            print(f"[Neo4j] Failed to delete triple: {e}")
            return False

    def update_triple(self, old: Triple, new: Triple) -> bool:
        if not self.enabled or not self.driver:
            return False
        if not _clean_graph_text(new.subject) or not _clean_graph_text(new.predicate) or not _clean_graph_text(new.object):
            return False
        if _clean_graph_text(new.subject) == _clean_graph_text(new.object):
            return False
        deleted = self.delete_triple(old)
        if not deleted:
            return False
        self.add_triple(Triple(
            subject=_clean_graph_text(new.subject),
            predicate=_clean_graph_text(new.predicate, max_len=40),
            object=_clean_graph_text(new.object),
            source_chunk_id=_clean_graph_text(new.source_chunk_id, max_len=120) or old.source_chunk_id,
            confidence=_safe_confidence(new.confidence),
        ))
        return True

    def clear(self) -> None:
        if not self.enabled or not self.driver:
            return
        query = "MATCH (n) DETACH DELETE n"
        try:
            with self.driver.session() as session:
                session.run(query)
        except Exception as e:
            print(f"[Neo4j] Failed to clear graph: {e}")

    def search(self, query: str, top_k: int = 10) -> GraphSearchResult | None:
        if not self.enabled or not self.driver:
            return None

        # Match entity by substring, order by degree
        match_query = """
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower($query)
        WITH e, size((e)--()) as degree
        ORDER BY degree DESC
        LIMIT 1
        RETURN e.name as entity_name, degree
        """
        try:
            with self.driver.session() as session:
                result = session.run(match_query, query=query).single()
                if not result:
                    return None

                best_entity = result["entity_name"]
                degree = result["degree"]

                # Get connected triples
                triples_query = """
                MATCH (s:Entity {name: $entity})-[r]->(o:Entity)
                RETURN s.name as subject, r.predicate as predicate, o.name as object, r.source_chunk_id as source_chunk_id, r.confidence as confidence
                UNION
                MATCH (s:Entity)-[r]->(o:Entity {name: $entity})
                RETURN s.name as subject, r.predicate as predicate, o.name as object, r.source_chunk_id as source_chunk_id, r.confidence as confidence
                LIMIT $limit
                """
                triples_result = session.run(triples_query, entity=best_entity, limit=top_k * 2)

                result_triples = []
                connected = set()

                for record in triples_result:
                    subj = record["subject"]
                    obj = record["object"]
                    result_triples.append(Triple(
                        subject=subj,
                        predicate=record["predicate"],
                        object=obj,
                        source_chunk_id=record["source_chunk_id"] or "",
                        confidence=record["confidence"] or 1.0,
                    ))
                    if subj == best_entity:
                        connected.add(obj)
                    else:
                        connected.add(subj)

                return GraphSearchResult(
                    entity=best_entity,
                    triples=result_triples,
                    connected_entities=sorted(connected),
                    degree=degree,
                )
        except Exception as e:
            print(f"[Neo4j] Failed to search: {e}")
            return None

    def query_subgraph(self, entity: str, depth: int = 2) -> nx.MultiDiGraph:
        # Placeholder for returning networkx graph from Neo4j subgraph
        # Not heavily used in production right now
        return nx.MultiDiGraph()

    def get_paths_between(self, source: str, target: str, max_length: int = 4) -> list[list[str]]:
        if not self.enabled or not self.driver:
            return []

        safe_max_length = max(1, int(max_length))
        query = f"""
        MATCH p = shortestPath((s:Entity {{name: $source}})-[:RELATION*..{safe_max_length}]-(t:Entity {{name: $target}}))
        RETURN [node in nodes(p) | node.name] as path_nodes
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, source=source, target=target)
                paths = []
                for record in result:
                    paths.append(record["path_nodes"])
                return paths
        except Exception as e:
            print(f"[Neo4j] Failed to get paths: {e}")
            return []

    def get_neighbors(self, entity: str, edge_predicate: str | None = None) -> list[tuple[str, str]]:
        if not self.enabled or not self.driver:
            return []

        if edge_predicate:
            query = """
            MATCH (e:Entity {name: $entity})-[r:RELATION {predicate: $predicate}]-(n:Entity)
            RETURN n.name as neighbor, r.predicate as predicate
            """
            params = {"entity": entity, "predicate": edge_predicate}
        else:
            query = """
            MATCH (e:Entity {name: $entity})-[r:RELATION]-(n:Entity)
            RETURN n.name as neighbor, r.predicate as predicate
            """
            params = {"entity": entity}

        try:
            with self.driver.session() as session:
                result = session.run(query, **params)
                neighbors = []
                for record in result:
                    neighbors.append((record["neighbor"], record["predicate"]))
                return neighbors
        except Exception as e:
            print(f"[Neo4j] Failed to get neighbors: {e}")
            return []

    def stats(self) -> dict:
        if not self.enabled or not self.driver:
            return {"enabled": False}
        try:
            with self.driver.session() as session:
                nodes = session.run("MATCH (n:Entity) RETURN count(n) as c").single()["c"]
                edges = session.run("MATCH ()-[r:RELATION]->() RETURN count(r) as c").single()["c"]
                return {
                    "enabled": True,
                    "nodes": nodes,
                    "edges": edges,
                    "type": "neo4j",
                }
        except Exception as e:
            print(f"[Neo4j] Failed to get stats: {e}")
            return {"enabled": False, "error": str(e)}

    def export_triples(self) -> list[dict]:
        if not self.enabled or not self.driver:
            return []
        try:
            query = """
            MATCH (s:Entity)-[r:RELATION]->(o:Entity)
            RETURN s.name as subject, r.predicate as predicate, o.name as object, r.source_chunk_id as source_chunk_id, r.confidence as confidence
            """
            with self.driver.session() as session:
                result = session.run(query)
                return [
                    {
                        "subject": record["subject"],
                        "predicate": record["predicate"],
                        "object": record["object"],
                        "source_chunk_id": record["source_chunk_id"] or "",
                        "confidence": record["confidence"] or 1.0,
                    }
                    for record in result
                ]
        except Exception as e:
            print(f"[Neo4j] Failed to export triples: {e}")
            return []


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_kg_instance: KnowledgeGraph | None = None
_kg_lock = threading.Lock()


def get_knowledge_graph(
    config: Any = None,
) -> KnowledgeGraph:
    """获取知识图谱单例（线程安全）"""
    global _kg_instance
    if _kg_instance is None:
        with _kg_lock:
            if _kg_instance is None:
                if config is None:
                    # 默认使用 NetworkX 作为降级
                    _kg_instance = NetworkXKnowledgeGraph()
                else:
                    enabled = config.enable_graph
                    if getattr(config, "graph_type", "networkx") == "neo4j":
                        _kg_instance = Neo4jKnowledgeGraph(
                            uri=config.neo4j_uri,
                            user=config.neo4j_user,
                            password=config.neo4j_password,
                            enabled=enabled
                        )
                    else:
                        _kg_instance = NetworkXKnowledgeGraph(
                            persist_path=config.graph_persist_path,
                            enabled=enabled
                        )
    return _kg_instance
