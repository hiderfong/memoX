"""Tests for WorkerAgent skill integration."""
import sys, os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.base_agent import ToolRegistry
from agents.worker_pool import WorkerAgent, WorkerConfig


def _make_skill(root: Path, name: str, description: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class _FakeProvider:
    async def chat(self, *args, **kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError


def _patch_config(skills_dir: Path):
    """Patch global config so knowledge_base.skills_dir points at skills_dir."""
    from config import get_config

    cfg = get_config()
    cfg.knowledge_base.skills_dir = str(skills_dir)
    return cfg


def test_worker_registers_load_skill_tool_when_skills_configured(tmp_path):
    _make_skill(tmp_path, "code-review", "Review", "body")
    _patch_config(tmp_path)

    cfg = WorkerConfig(
        name="reviewer",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=["code-review"],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    assert worker.tools.get("load_skill") is not None


def test_worker_skips_load_skill_tool_when_no_skills(tmp_path):
    _patch_config(tmp_path)
    cfg = WorkerConfig(
        name="plain",
        provider_type="anthropic",
        api_key="",
        model="claude-sonnet-4-20250514",
        skills=[],
    )
    worker = WorkerAgent(cfg, tools=ToolRegistry(), provider=_FakeProvider())

    assert worker.tools.get("load_skill") is None
