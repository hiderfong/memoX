"""Tests for the skills loader."""
import sys, os
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
