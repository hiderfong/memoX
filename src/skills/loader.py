"""Load Claude Code format skills from the filesystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


@dataclass
class Skill:
    """A parsed skill ready to be injected into a worker's context."""
    name: str
    description: str
    body: str
    path: Path
    references: list[str] = field(default_factory=list)


def _parse_skill_md(skill_md: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_markdown). Raises ValueError on malformed input."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with '---' frontmatter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter block is not closed")
    frontmatter_raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    try:
        frontmatter = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML frontmatter: {e}") from e
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return frontmatter, body


def _load_one(skill_dir: Path) -> Skill | None:
    """Load a single skill directory, or return None (with a warning) if invalid."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        frontmatter, body = _parse_skill_md(skill_md)
    except ValueError as e:
        logger.warning(f"skipping skill {skill_dir.name}: {e}")
        return None

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not name or not description:
        logger.warning(
            f"skipping skill {skill_dir.name}: frontmatter missing name or description"
        )
        return None

    references: list[str] = []
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        references = sorted(
            p.name for p in ref_dir.iterdir() if p.is_file()
        )

    return Skill(
        name=name,
        description=description,
        body=body,
        path=skill_dir,
        references=references,
    )


def list_skills(skills_dir: Path) -> list[Skill]:
    """Scan skills_dir, parse each subdirectory as a Skill. Missing dir -> empty list."""
    if not skills_dir.is_dir():
        return []
    out: list[Skill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill = _load_one(child)
        if skill is not None:
            out.append(skill)
    return out


def load_skill(skills_dir: Path, name: str, ref: str | None = None) -> str:
    """Return the skill body (ref=None) or a references/ file content.

    Raises:
        FileNotFoundError: skill or ref file does not exist.
        ValueError: ref attempts to escape the skill directory.
    """
    skill_dir = (skills_dir / name).resolve()
    skills_root = skills_dir.resolve()
    try:
        skill_dir.relative_to(skills_root)
    except ValueError as e:
        raise ValueError("skill name must stay inside skills_dir") from e

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"skill not found: {name}")

    if ref is None:
        _, body = _parse_skill_md(skill_md)
        return body

    target = (skill_dir / "references" / ref).resolve()
    try:
        target.relative_to(skill_dir.resolve())
    except ValueError as e:
        raise ValueError("ref must stay inside skill directory") from e
    if not target.is_file():
        raise FileNotFoundError(f"reference not found: {name}/{ref}")
    return target.read_text(encoding="utf-8")
