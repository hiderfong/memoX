"""知识图谱构建 - 实体关系图

支持 NetworkX (内存/GML持久化) 和 Neo4j (真实图数据库)。
"""

from __future__ import annotations

import contextlib
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
        use_llm: bool = False,
    ) -> dict:
        """从文档 chunks 构建/更新知识图谱。"""
        if not self.enabled:
            return {"added": 0, "chunks_processed": 0, "method": "disabled"}

        added = 0
        method = "rule"

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
                )

                for chunk in batch:
                    triples = batch_results.get(chunk.id)
                    if triples is None: # LLM failed or skipped this chunk, fallback
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
        return {"added": added, "chunks_processed": len(chunks), "method": method}

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
            for node in list(self._graph.nodes()):
                if self._graph.degree(node) == 0:
                    self._graph.remove_node(node)
        return removed

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

        query = """
        MATCH p = shortestPath((s:Entity {name: $source})-[:RELATION*..%d]-(t:Entity {name: $target}))
        RETURN [node in nodes(p) | node.name] as path_nodes
        """ % max_length
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
