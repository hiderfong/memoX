"""知识图谱构建 - 基于 NetworkX 的实体关系图（P4-4，实验性）

功能：
- 从文档 chunk 中抽取 <subject, predicate, object> 三元组
- 构建 NetworkX MultiDiGraph，支持实体查询、路径发现、子图提取
- GML 格式持久化到磁盘

依赖：networkx>=3.0
配置：knowledge_base.enable_graph: true/false（config.yaml）
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from .document_parser import TextChunk

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


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
    """基于规则的简单三元组抽取（LLM 不可用时的降级方案）。

    匹配模式：
    - "X 是 / 位于 / 属于 Y"  → (X, 是/位于/属于, Y)
    - "X 有 / 拥有 Y"        → (X, 有, Y)
    - "X 与 Y 相关"           → (X, 相关, Y)
    - "X 通过 / 利用 Y 实现 Z" → (X, 通过, Z)  with intermediate Y
    """
    triples: list[Triple] = []
    text = text.strip()
    if not text or len(text) < 5:
        return triples

    # (subject, predicate_pattern, object_group) — 正则捕获 subject 和 object
    patterns = [
        # X 是 Y
        (r"([^，。、！？；：,\s]{2,30})\s*(?:是|为|属于|位于|存在于)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        # X 有 Y
        (r"([^，。、！？；：,\s]{2,30})\s*(?:有|拥有|具有|包含|包括)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        # X 与 Y 关联/相关/交互
        (r"([^，。、！？；：,\s]{2,30})\s*(?:与|和|同)\s*([^，。、！？；：,\s]{2,30})\s*(?:关联|相关|交互|合作|通信)", 1, 2),
        # X 通过 Y 实现/完成 Z
        (r"([^，。、！？；：,\s]{2,30})\s*(?:通过|利用|使用)\s*([^，。、！？；：,\s]{2,30})\s*(?:实现|完成|达成|完成)\s*([^，。、！？；：,\s]{2,30})", 1, 3),
        # X 构成 Y
        (r"([^，。、！？；：,\s]{2,30})\s*(?:构成|组成|形成|产生)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        # X 用于 Y
        (r"([^，。、！？；：,\s]{2,30})\s*(?:用于|用来|应用于)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
        # X 可以/能够 Y
        (r"([^，。、！？；：,\s]{2,30})\s*(?:可以|能够|可以实现|能够实现)\s*([^，。、！？；：,\s]{2,30})", 1, 2),
    ]

    for pattern, sub_idx, obj_idx in patterns:
        for match in re.finditer(pattern, text):
            try:
                subject = match.group(sub_idx).strip()
                obj = match.group(obj_idx).strip()
                # 从正则中回推 predicate（根据模式索引推断）
                pred_map = {
                    (0, 2): "是",   # "是/为/属于/位于"
                    (1, 2): "有",   # "有/拥有/具有"
                    (2, 2): "关联",  # "与...关联/相关"
                    (3, 3): "通过",  # "通过...实现"
                    (4, 2): "构成",  # "构成/组成/形成"
                    (5, 2): "用于",  # "用于"
                    (6, 2): "可以",  # "可以/能够"
                }
                predicate = pred_map.get((patterns.index((pattern, sub_idx, obj_idx)), obj_idx), "关联")
                if len(subject) >= 2 and len(obj) >= 2 and subject != obj:
                    triples.append(Triple(
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        source_chunk_id=chunk_id,
                        confidence=0.6,  # rule-based 置信度较低
                    ))
            except IndexError:
                continue

    return triples


# ---------------------------------------------------------------------------
# LLM-based triple extraction
# ---------------------------------------------------------------------------

LLM_TRIPLE_EXTRACT_PROMPT = """你是一个信息抽取专家。从以下文本中抽取所有有价值的三元组（知识图谱事实）。

要求：
1. 每个三元组表示为 JSON 对象：{{"subject": "主体", "predicate": "谓词", "object": "客体"}}
2. subject 和 object 应该是文本中的具体实体（人名、组织名、概念、技术名词等）
3. predicate 应该是简洁的关系词（是、有、属于、位于、用于、实现、依赖、包含等）
4. 只抽取明确陈述的事实，不要过度推断
5. 如果没有发现三元组，返回空列表 []

文本：
{chunk_text}

请返回 JSON 数组："""


async def _extract_triples_via_llm(
    text: str,
    chunk_id: str,
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str,
) -> list[Triple]:
    """通过 LLM 抽取三元组（需要配置有效的 LLM）"""
    import httpx

    payload = {
        "model": "qwen-plus",  # 通用模型，可覆盖
        "messages": [
            {"role": "user", "content": LLM_TRIPLE_EXTRACT_PROMPT.format(chunk_text=text[:1500])}
        ],
        "max_tokens": 512,
        "temperature": 0.1,
    }
    headers: dict[str, str] = {
        "Authorization": f"Bearer {llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # 提取 JSON 数组
        json_match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
        if not json_match:
            return []
        items = json.loads(json_match.group())
        triples: list[Triple] = []
        for item in items:
            if not all(k in item for k in ("subject", "predicate", "object")):
                continue
            s, p, o = item["subject"].strip(), item["predicate"].strip(), item["object"].strip()
            if len(s) >= 2 and len(o) >= 2 and s != o:
                triples.append(Triple(
                    subject=s,
                    predicate=p,
                    object=o,
                    source_chunk_id=chunk_id,
                    confidence=0.9,
                ))
        return triples
    except Exception:
        return []


# ---------------------------------------------------------------------------
# KnowledgeGraph class
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """知识图谱 - 基于 NetworkX MultiDiGraph

    图中节点：实体名称（str）
    边：(subject, object, key=predicate)，支持同一对实体多关系
    """

    VERSION = 1

    def __init__(
        self,
        persist_path: str = "./data/knowledge_graph.gml",
        enabled: bool = True,
    ):
        self.persist_path = Path(persist_path)
        self.enabled = enabled
        self._lock = threading.Lock()

        # NetworkX MultiDiGraph：支持同一对节点多条不同 predicate 的边
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()

        if self.enabled:
            self._graph.graph["name"] = "MemoX Knowledge Graph"
            self._graph.graph["version"] = self.VERSION
            self._load()

    # -------------------------------------------------------------------------
    # Persistence (GML)
    # -------------------------------------------------------------------------

    def _load(self) -> None:
        """从 GML 文件加载图（如果存在）"""
        if not self.persist_path.exists():
            return
        try:
            self._graph = nx.read_gml(str(self.persist_path))
        except Exception:
            # 文件损坏时从空图开始
            self._graph = nx.MultiDiGraph()
            self._graph.graph["name"] = "MemoX Knowledge Graph"
            self._graph.graph["version"] = self.VERSION

    def save(self) -> None:
        """持久化图到 GML 文件"""
        if not self.enabled:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        # GML 不支持多值属性，用 metadata 存额外信息
        nx.write_gml(self._graph, str(self.persist_path))

    # -------------------------------------------------------------------------
    # Graph mutation
    # -------------------------------------------------------------------------

    def add_triple(self, triple: Triple) -> None:
        """添加单个三元组到图中"""
        if not self.enabled:
            return
        with self._lock:
            # 添加节点（带属性）
            for node in (triple.subject, triple.object):
                if node not in self._graph:
                    self._graph.add_node(node, label=node)

            # MultiDiGraph 支持同一 (u,v) 对多条边，用 key=predicate 区分
            # 先尝试移除旧边（同一 subject+predicate+object 组合）
            try:
                self._graph.remove_edge(triple.subject, triple.object, key=triple.predicate)
            except nx.NetworkXError:
                pass
            self._graph.add_edge(
                triple.subject,
                triple.object,
                key=triple.predicate,
                predicate=triple.predicate,
                source_chunk_id=triple.source_chunk_id,
                confidence=triple.confidence,
            )

    def remove_by_chunk_id(self, chunk_id: str) -> int:
        """删除所有来自指定 chunk 的三元组"""
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
            # 清理孤立节点
            for node in list(self._graph.nodes()):
                if self._graph.degree(node) == 0:
                    self._graph.remove_node(node)
        return removed

    def clear(self) -> None:
        """清空所有三元组"""
        if not self.enabled:
            return
        with self._lock:
            self._graph.clear()

    # -------------------------------------------------------------------------
    # Build from chunks
    # -------------------------------------------------------------------------

    async def build_from_chunks(
        self,
        chunks: list[TextChunk],
        *,
        llm_provider: str = "",
        llm_api_key: str = "",
        llm_base_url: str = "",
        use_llm: bool = False,
    ) -> dict:
        """从文档 chunks 构建/更新知识图谱。

        Args:
            chunks: TextChunk 列表
            llm_provider: LLM provider name（e.g. "dashscope"）
            llm_api_key: API key
            llm_base_url: API base URL
            use_llm: 是否优先使用 LLM 抽取（需要有效的 LLM 配置）

        Returns:
            {"added": N, "chunks_processed": M, "method": "llm"|"rule"}
        """
        if not self.enabled:
            return {"added": 0, "chunks_processed": 0, "method": "disabled"}

        added = 0
        method = "rule"
        for chunk in chunks:
            if not chunk.content or len(chunk.content.strip()) < 10:
                continue

            triples: list[Triple] = []
            if use_llm and llm_api_key:
                triples = await _extract_triples_via_llm(
                    text=chunk.content,
                    chunk_id=chunk.id,
                    llm_provider=llm_provider,
                    llm_api_key=llm_api_key,
                    llm_base_url=llm_base_url,
                )
                method = "llm"

            # LLM 失败或未启用时降级到规则
            if not triples:
                triples = _extract_triples_rule_based(chunk.content, chunk.id)

            for t in triples:
                self.add_triple(t)
                added += 1

        self.save()
        return {"added": added, "chunks_processed": len(chunks), "method": method}

    def build_from_triples(self, triples: list[Triple]) -> int:
        """直接从 Triple 列表构建图（外部已抽取好的场景）"""
        if not self.enabled:
            return 0
        for t in triples:
            self.add_triple(t)
        self.save()
        return len(triples)

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    def search(self, query: str, top_k: int = 10) -> GraphSearchResult | None:
        """模糊匹配实体名，返回相关三元组。

        使用子串匹配 + 实体度（连接数）排序。
        """
        if not self.enabled:
            return None
        query_lower = query.lower()
        with self._lock:
            # 找所有包含 query 作为子串的实体（区分大小写优先）
            candidates: list[tuple[str, int]] = []
            for node in self._graph.nodes():
                if query_lower in node.lower():
                    degree = self._graph.degree(node)
                    # 精确匹配优先
                    score = 2 if node == query else (1.5 if node.lower() == query_lower else 1)
                    candidates.append((node, degree * score))

            if not candidates:
                return None

            # 按 score * degree 降序排列，取 top_k
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_entity = candidates[0][0]

            # 收集相关三元组
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
        """提取指定实体周围的子图（ego graph）。

        Args:
            entity: 中心实体名
            depth: 扩展深度（1=直接邻居，2=邻居的邻居）
        """
        if not self.enabled:
            return nx.MultiDiGraph()
        with self._lock:
            if entity not in self._graph:
                return nx.MultiDiGraph()
            # ego_graph 包含 depth=1 的邻居；重复调用可扩展深度
            g = nx.ego_graph(self._graph, entity, radius=depth, undirected=True)
            return g

    def get_paths_between(
        self,
        source: str,
        target: str,
        max_length: int = 4,
    ) -> list[list[str]]:
        """查找两个实体之间的所有最短路径（不超过 max_length 跳）。

        返回路径列表，每条路径是节点名列表 [src, ..., tgt]。
        """
        if not self.enabled:
            return []
        with self._lock:
            if source not in self._graph or target not in self._graph:
                return []
            try:
                return list(
                    nx.all_shortest_paths(self._graph, source, target, weight=None)
                )
            except nx.NetworkXNoPath:
                return []

    def get_neighbors(
        self,
        entity: str,
        edge_predicate: str | None = None,
    ) -> list[tuple[str, str]]:
        """获取实体的所有邻居及关系。

        Args:
            entity: 实体名
            edge_predicate: 可选，只返回指定谓词的邻居
        Returns:
            [(邻居实体, predicate), ...]
        """
        if not self.enabled:
            return []
        with self._lock:
            if entity not in self._graph:
                return []
            neighbors: list[tuple[str, str]] = []
            for u, v, k, data in self._graph.edges(entity, keys=True, data=True):
                p = data.get("predicate", k) if isinstance(k, str) else k
                if edge_predicate is None or p == edge_predicate:
                    # u is entity, v is neighbor (or vice versa for directed)
                    neighbor = v if u == entity else u
                    neighbors.append((neighbor, p))
            return neighbors

    def stats(self) -> dict:
        """返回图的统计信息"""
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
        """导出所有三元组为字典列表"""
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
# Global singleton
# ---------------------------------------------------------------------------

_kg_instance: KnowledgeGraph | None = None
_kg_lock = threading.Lock()


def get_knowledge_graph(
    persist_path: str = "./data/knowledge_graph.gml",
    enabled: bool = True,
) -> KnowledgeGraph:
    """获取知识图谱单例（线程安全）"""
    global _kg_instance
    if _kg_instance is None:
        with _kg_lock:
            if _kg_instance is None:
                _kg_instance = KnowledgeGraph(persist_path=persist_path, enabled=enabled)
    return _kg_instance
