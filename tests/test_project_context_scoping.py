import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from web.routers import chat, scheduled, tasks


def test_project_id_overrides_active_group_ids():
    assert chat._resolve_active_group_ids("project_a", ["group_b"]) == ["project_a"]
    assert tasks._resolve_active_group_ids("project_a", ["group_b"]) == ["project_a"]
    assert scheduled._resolve_active_group_ids("project_a", ["group_b"]) == ["project_a"]


def test_active_group_ids_are_preserved_without_project_id():
    group_ids = ["group_a", "group_b"]

    assert chat._resolve_active_group_ids(None, group_ids) == group_ids
    assert tasks._resolve_active_group_ids("", group_ids) == group_ids
    assert scheduled._resolve_active_group_ids(None, group_ids) == group_ids
