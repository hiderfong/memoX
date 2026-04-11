"""LoadSkillTool — lazy skill loading via ToolRegistry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.base_agent import BaseTool

from .loader import load_skill


class LoadSkillTool(BaseTool):
    """Worker-scoped tool for loading skill content on demand.

    Each worker gets its own instance with an ``allowed_skills`` whitelist
    derived from its WorkerConfig.skills. This prevents cross-worker skill
    leakage even when the same skills_dir is shared across the pool.
    """

    def __init__(self, skills_dir: Path, allowed_skills: set[str]):
        self._skills_dir = skills_dir
        self._allowed = set(allowed_skills)

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "Load the full content of a skill by name. Use this when you see a "
            "skill listed in '可用技能' and want its detailed instructions. Pass "
            "`ref` to fetch a sub-reference file from the skill's references/ dir."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to load (must be enabled for this worker).",
                },
                "ref": {
                    "type": "string",
                    "description": "Optional sub-reference filename under the skill's references/ directory.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, arguments: dict) -> Any:
        name = arguments.get("name")
        if not isinstance(name, str) or not name:
            return "Error: 'name' is required."
        if name not in self._allowed:
            return f"Error: skill '{name}' is not enabled for this worker."
        ref = arguments.get("ref")
        try:
            return load_skill(self._skills_dir, name, ref)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except ValueError as e:
            return f"Error: {e}"
