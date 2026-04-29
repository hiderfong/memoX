import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.group_store import UNGROUPED_ID, GroupStore


def make_store(tmp_path):
    return GroupStore(path=str(tmp_path / "groups.json"))


def test_ungrouped_always_exists(tmp_path):
    store = make_store(tmp_path)
    groups = store.list_groups()
    ids = [g.id for g in groups]
    assert UNGROUPED_ID in ids


def test_create_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("技术文档", "#1890ff")
    assert g.id != UNGROUPED_ID
    assert g.name == "技术文档"
    assert g.color == "#1890ff"
    assert g.id in [x.id for x in store.list_groups()]


def test_create_group_persists(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("财务", "#52c41a")
    store2 = make_store(tmp_path)
    assert g.id in [x.id for x in store2.list_groups()]


def test_update_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("旧名", "#aaa")
    updated = store.update_group(g.id, name="新名", color="#bbb")
    assert updated.name == "新名"
    assert updated.color == "#bbb"


def test_cannot_rename_ungrouped(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.update_group(UNGROUPED_ID, name="改名")


def test_delete_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("临时", "#ccc")
    store.delete_group(g.id)
    assert g.id not in [x.id for x in store.list_groups()]


def test_cannot_delete_ungrouped(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.delete_group(UNGROUPED_ID)


def test_delete_nonexistent_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.delete_group("nonexistent")


def test_get_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("测试", "#111")
    found = store.get_group(g.id)
    assert found is not None
    assert found.id == g.id


def test_get_nonexistent_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get_group("missing") is None
