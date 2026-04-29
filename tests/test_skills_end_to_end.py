"""End-to-end: install -> worker -> tool invocation."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.base_agent import ToolRegistry
from agents.worker_pool import WorkerAgent, WorkerConfig
from skills.installer import install_from_github


def _init_remote_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "t"], check=True)
    (repo_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review a PR\n---\n# full review guide\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo_dir), "add", "SKILL.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    return f"file://{repo_dir}"


class _FakeProvider:
    async def chat(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_install_then_worker_can_load_via_tool(tmp_path):
    from config import get_config
    get_config().knowledge_base.skills_dir = str(tmp_path / "skills")

    remote = tmp_path / "remote"
    url = _init_remote_repo(remote)
    install_from_github(url, Path(get_config().knowledge_base.skills_dir))

    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    prompt = worker._build_system_prompt()
    assert "code-review" in prompt
    assert "Review a PR" in prompt

    tool = worker.tools.get("load_skill")
    assert tool is not None
    result = await tool.execute({"name": "code-review"})
    assert "full review guide" in result
