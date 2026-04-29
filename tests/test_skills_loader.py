"""Tests for the skills loader."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from skills.loader import Skill, list_skills


def _make_skill(root: Path, name: str, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return skill_dir


def test_list_skills_returns_parsed_skill(tmp_path):
    _make_skill(tmp_path, "code-review", "Review a PR", "# How to review\nSteps...")

    skills = list_skills(tmp_path)

    assert len(skills) == 1
    skill = skills[0]
    assert isinstance(skill, Skill)
    assert skill.name == "code-review"
    assert skill.description == "Review a PR"
    assert "# How to review" in skill.body
    assert skill.path == tmp_path / "code-review"
    assert skill.references == []


def test_list_skills_empty_dir(tmp_path):
    assert list_skills(tmp_path) == []


def test_list_skills_missing_dir(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list_skills(missing) == []


import pytest

from skills.loader import load_skill


def test_load_skill_returns_body(tmp_path):
    _make_skill(tmp_path, "code-review", "Review a PR", "# How to review\nSteps...")

    body = load_skill(tmp_path, "code-review")

    assert body.startswith("# How to review")
    assert "---" not in body.split("\n")[0]  # no frontmatter leak


def test_load_skill_with_reference(tmp_path):
    skill_dir = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    (ref_dir / "checklist.md").write_text("- item 1\n- item 2\n", encoding="utf-8")

    content = load_skill(tmp_path, "code-review", ref="checklist.md")

    assert content == "- item 1\n- item 2\n"


def test_load_skill_unknown_name(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path, "nope")


def test_load_skill_unknown_ref(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    with pytest.raises(FileNotFoundError):
        load_skill(tmp_path, "code-review", ref="missing.md")


def test_load_skill_rejects_path_traversal(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    (tmp_path / "secret.txt").write_text("leaked", encoding="utf-8")

    with pytest.raises(ValueError, match="ref must stay inside"):
        load_skill(tmp_path, "code-review", ref="../../secret.txt")


def test_load_skill_rejects_absolute_ref(tmp_path):
    _make_skill(tmp_path, "code-review", "desc", "body")
    with pytest.raises(ValueError, match="ref must stay inside"):
        load_skill(tmp_path, "code-review", ref="/etc/passwd")


def test_list_skills_skips_missing_skill_md(tmp_path):
    (tmp_path / "empty-dir").mkdir()
    assert list_skills(tmp_path) == []


def test_list_skills_skips_malformed_frontmatter(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")

    assert list_skills(tmp_path) == []


def test_list_skills_skips_missing_name_field(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\ndescription: has no name\n---\nbody",
        encoding="utf-8",
    )

    assert list_skills(tmp_path) == []


def test_list_skills_skips_missing_description_field(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: nodesc\n---\nbody",
        encoding="utf-8",
    )

    assert list_skills(tmp_path) == []


def test_list_skills_skips_unclosed_frontmatter(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: x\ndescription: y\nbody never closed", encoding="utf-8")

    assert list_skills(tmp_path) == []


def test_list_skills_populates_references(tmp_path):
    d = _make_skill(tmp_path, "code-review", "desc", "body")
    ref_dir = d / "references"
    ref_dir.mkdir()
    (ref_dir / "a.md").write_text("a", encoding="utf-8")
    (ref_dir / "b.md").write_text("b", encoding="utf-8")

    skills = list_skills(tmp_path)
    assert skills[0].references == ["a.md", "b.md"]
