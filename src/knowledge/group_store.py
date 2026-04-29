"""知识库分组管理"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

UNGROUPED_ID = "ungrouped"


@dataclass
class KnowledgeGroup:
    id: str
    name: str
    color: str
    created_at: str


class GroupStore:
    """分组定义的持久化存储（data/groups.json）"""

    def __init__(self, path: str = "./data/groups.json"):
        self._path = Path(path)
        self._groups: dict[str, KnowledgeGroup] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._groups = {g["id"]: KnowledgeGroup(**g) for g in data}
        if UNGROUPED_ID not in self._groups:
            self._groups[UNGROUPED_ID] = KnowledgeGroup(
                id=UNGROUPED_ID,
                name="未分组",
                color="#999999",
                created_at=datetime.now().isoformat(),
            )
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([asdict(g) for g in self._groups.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_groups(self) -> list[KnowledgeGroup]:
        return list(self._groups.values())

    def get_group(self, group_id: str) -> KnowledgeGroup | None:
        return self._groups.get(group_id)

    def create_group(self, name: str, color: str = "#1890ff") -> KnowledgeGroup:
        group = KnowledgeGroup(
            id=uuid.uuid4().hex[:8],
            name=name,
            color=color,
            created_at=datetime.now().isoformat(),
        )
        self._groups[group.id] = group
        self._save()
        return group

    def update_group(self, group_id: str, name: str | None = None, color: str | None = None) -> KnowledgeGroup:
        if group_id not in self._groups:
            raise KeyError(f"Group not found: {group_id}")
        if group_id == UNGROUPED_ID and name is not None:
            raise ValueError("Cannot rename the ungrouped group")
        group = self._groups[group_id]
        if name is not None:
            group.name = name
        if color is not None:
            group.color = color
        self._save()
        return group

    def delete_group(self, group_id: str) -> None:
        if group_id == UNGROUPED_ID:
            raise ValueError("Cannot delete the ungrouped group")
        if group_id not in self._groups:
            raise KeyError(f"Group not found: {group_id}")
        del self._groups[group_id]
        self._save()
