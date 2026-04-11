"""Tests for the skill installer."""
import sys, os
import json
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from skills.installer import install_from_github
from skills.loader import list_skills


def _init_remote_repo(repo_dir: Path, skill_name: str = "code-review") -> str:
    """Create a local git repo containing a single skill; return its file:// URL."""
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)

    skill_md = repo_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {skill_name}\ndescription: Test skill\n---\n# body\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def test_install_from_github_happy_path(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    url = _init_remote_repo(remote)

    skill = install_from_github(url, skills_dir)

    assert skill.name == "code-review"
    assert (skills_dir / "code-review" / "SKILL.md").is_file()
    meta = json.loads((skills_dir / "code-review" / ".install.json").read_text())
    assert meta["source_url"] == url
    assert "installed_at" in meta
    assert [s.name for s in list_skills(skills_dir)] == ["code-review"]


def _init_multi_skill_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)

    for sub in ("code-review", "docx"):
        d = repo_dir / sub
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {sub}\ndescription: {sub} skill\n---\n# {sub}\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def test_install_from_github_subpath(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_multi_skill_repo(remote)

    skill = install_from_github(f"{url}#subpath=code-review", skills_dir)

    assert skill.name == "code-review"
    assert (skills_dir / "code-review" / "SKILL.md").is_file()
    assert not (skills_dir / "docx").exists()


def test_install_from_github_name_override(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote, skill_name="code-review")

    skill = install_from_github(url, skills_dir, name="my-review")

    assert skill.name == "code-review"  # frontmatter name unchanged in Skill object
    assert (skills_dir / "my-review" / "SKILL.md").is_file()


def test_install_from_github_refuses_existing_without_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    with pytest.raises(FileExistsError):
        install_from_github(url, skills_dir)


def test_install_from_github_overwrites_with_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote_repo(remote)
    install_from_github(url, skills_dir)

    skill = install_from_github(url, skills_dir, force=True)

    assert skill.name == "code-review"


def test_install_from_github_missing_skill_md(tmp_path):
    remote = tmp_path / "remote-empty"
    remote.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(remote), "config", "user.name", "t"], check=True)
    (remote / "README.md").write_text("no skill here", encoding="utf-8")
    subprocess.run(["git", "-C", str(remote), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(remote), "commit", "-q", "-m", "init"], check=True)

    skills_dir = tmp_path / "skills"
    with pytest.raises(FileNotFoundError, match="no SKILL.md"):
        install_from_github(f"file://{remote}", skills_dir)
