"""Install Claude Code format skills from GitHub into data/skills/."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from loguru import logger

from .loader import Skill, _load_one, _parse_skill_md

_GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/[^/]+)?(?:/(?P<subpath>.*))?$"
)


def _parse_github_url(url: str) -> tuple[str, str]:
    """Return (clone_url, subpath)."""
    if url.startswith("file://"):
        return url, ""
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"unrecognized GitHub URL: {url}")
    owner = m.group("owner")
    repo = m.group("repo")
    subpath = (m.group("subpath") or "").strip("/")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return clone_url, subpath


def _git_clone(clone_url: str, dest: Path) -> None:
    """Shallow clone; surfaces a clean error on failure."""
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git clone failed for {clone_url}: {e.stderr.strip() or e.stdout.strip()}"
        ) from e


def install_from_github(
    url: str,
    skills_dir: Path,
    name: str | None = None,
    force: bool = False,
) -> Skill:
    """Clone a skill from GitHub into skills_dir. Returns the loaded Skill."""
    clone_url, subpath = _parse_github_url(url)
    skills_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_clone = Path(tmp) / "clone"
        _git_clone(clone_url, tmp_clone)

        source = tmp_clone / subpath if subpath else tmp_clone
        source_skill_md = source / "SKILL.md"
        if not source_skill_md.is_file():
            raise FileNotFoundError(
                f"no SKILL.md found at {subpath or '<repo root>'}"
            )

        frontmatter, _ = _parse_skill_md(source_skill_md)
        fm_name = frontmatter.get("name")
        if not fm_name:
            raise ValueError("SKILL.md frontmatter missing 'name' field")
        final_name = name or fm_name

        target = skills_dir / final_name
        if target.exists():
            if not force:
                raise FileExistsError(
                    f"skill '{final_name}' already installed at {target} — use force=True"
                )
            shutil.rmtree(target)

        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))

        meta = {
            "source_url": url,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
        }
        (target / ".install.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    skill = _load_one(target)
    if skill is None:
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError(
            f"installed skill at {target} failed to load — check frontmatter"
        )
    logger.info(f"installed skill '{skill.name}' from {url}")
    return skill
