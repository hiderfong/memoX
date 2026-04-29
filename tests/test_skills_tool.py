"""Tests for LoadSkillTool."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from skills.tool import LoadSkillTool


def _make_skill(root: Path, name: str, description: str, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return d


@pytest.mark.asyncio
async def test_load_skill_tool_returns_body(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "# body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review"})

    assert result.startswith("# body")


@pytest.mark.asyncio
async def test_load_skill_tool_rejects_unlisted_skill(tmp_path):
    _make_skill(tmp_path, "secret", "desc", "body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "secret"})

    assert "not enabled" in result
    assert "secret" in result


@pytest.mark.asyncio
async def test_load_skill_tool_missing_skill_returns_error_string(tmp_path):
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review"})

    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_load_skill_tool_reference_file(tmp_path):
    d = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = d / "references"
    ref_dir.mkdir()
    (ref_dir / "checklist.md").write_text("checklist content", encoding="utf-8")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review", "ref": "checklist.md"})

    assert result == "checklist content"


@pytest.mark.asyncio
async def test_load_skill_tool_path_traversal_returns_error_string(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    tool = LoadSkillTool(tmp_path, allowed_skills={"code-review"})

    result = await tool.execute({"name": "code-review", "ref": "../../../etc/passwd"})

    assert result.startswith("Error:")


def test_load_skill_tool_has_input_schema():
    tool = LoadSkillTool(Path("/tmp"), allowed_skills=set())
    assert tool.name == "load_skill"
    assert isinstance(tool.description, str) and tool.description
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "ref" in schema["properties"]
    assert schema["required"] == ["name"]
