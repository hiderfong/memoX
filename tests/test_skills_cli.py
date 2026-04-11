"""Tests for the skills CLI (python -m skills)."""
import os
import subprocess
import sys
from pathlib import Path


def _init_remote(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)
    (repo_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Test\n---\nbody",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


def _run_cli(skills_dir: Path, *args: str) -> subprocess.CompletedProcess:
    src_dir = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}
    return subprocess.run(
        [sys.executable, "-m", "skills", "--skills-dir", str(skills_dir), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_install_list_remove(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    r = _run_cli(skills_dir, "install", url)
    assert r.returncode == 0, r.stderr
    assert "code-review" in r.stdout

    r = _run_cli(skills_dir, "list")
    assert r.returncode == 0
    assert "code-review" in r.stdout
    assert "Test" in r.stdout

    r = _run_cli(skills_dir, "remove", "code-review")
    assert r.returncode == 0
    assert not (skills_dir / "code-review").exists()


def test_cli_install_refuses_existing(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    _run_cli(skills_dir, "install", url)
    r = _run_cli(skills_dir, "install", url)

    assert r.returncode != 0
    combined = (r.stdout + r.stderr).lower()
    assert "already installed" in combined or "exists" in combined


def test_cli_install_force(tmp_path):
    remote = tmp_path / "remote"
    skills_dir = tmp_path / "skills"
    url = _init_remote(remote)

    _run_cli(skills_dir, "install", url)
    r = _run_cli(skills_dir, "install", url, "--force")

    assert r.returncode == 0
