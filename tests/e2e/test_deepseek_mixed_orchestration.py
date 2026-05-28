"""DeepSeek + MiniMax multi-agent orchestration E2E.

Run with:
  DEEPSEEK_API_KEY=... MINIMAX_API_KEY=... pytest tests/e2e/test_deepseek_mixed_orchestration.py -q -s
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-v4-pro"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MINIMAX_MODEL = "MiniMax-M2.7-highspeed"
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic/v1"

pytestmark = pytest.mark.e2e


class StaticMixedPlanner:
    async def plan_task(self, task_description, context=None):
        from agents.worker_pool import SubTask, Task
        from coordinator.task_planner import TaskComplexity

        task = Task(
            id="task_deepseek_mixed",
            description=task_description,
            sub_tasks=[
                SubTask(
                    id="deepseek_step",
                    description=(
                        "Use write_file to create deepseek_notes.txt with exactly this content: "
                        "DEEPSEEK_OK=alpha\n"
                        "provider=deepseek-v4-pro\n"
                        "After writing the file, respond with DEEPSEEK_OK=alpha."
                    ),
                    acceptance_criteria=[
                        "deepseek_notes.txt exists",
                        "The file and final response contain DEEPSEEK_OK=alpha",
                    ],
                    assigned_agent="deepseek_analyst",
                ),
                SubTask(
                    id="minimax_step",
                    description=(
                        "Read dependency_results from context. Use write_file to create mixed_report.txt. "
                        "The file content must include both DEEPSEEK_OK=alpha and MINIMAX_OK=beta. "
                        "After writing the file, respond with MINIMAX_OK=beta."
                    ),
                    dependencies=["deepseek_step"],
                    acceptance_criteria=[
                        "mixed_report.txt exists",
                        "mixed_report.txt contains DEEPSEEK_OK=alpha",
                        "mixed_report.txt contains MINIMAX_OK=beta",
                    ],
                    assigned_agent="minimax_writer",
                ),
            ],
        )
        return task, TaskComplexity.MIXED


def _provider_pool():
    from agents.base_agent import MiniMaxProvider, create_provider
    from agents.worker_pool import WorkerAgent, WorkerConfig, WorkerPool

    deepseek_provider = create_provider(
        "deepseek",
        DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    minimax_provider = MiniMaxProvider(api_key=MINIMAX_API_KEY, base_url=MINIMAX_BASE_URL)

    pool = WorkerPool(max_workers=2)
    pool.register_worker(
        WorkerAgent(
            WorkerConfig(
                name="deepseek_analyst",
                provider_type="deepseek",
                api_key=DEEPSEEK_API_KEY,
                model=DEEPSEEK_MODEL,
                temperature=0.1,
                max_tokens=2048,
                max_iterations=8,
            ),
            provider=deepseek_provider,
        )
    )
    pool.register_worker(
        WorkerAgent(
            WorkerConfig(
                name="minimax_writer",
                provider_type="minimax",
                api_key=MINIMAX_API_KEY,
                model=MINIMAX_MODEL,
                temperature=0.1,
                max_tokens=2048,
                max_iterations=8,
            ),
            provider=minimax_provider,
        )
    )
    return deepseek_provider, pool


def _read_first(shared: Path, name: str) -> str:
    matches = list(shared.rglob(name))
    assert matches, f"{name} should be present under shared output"
    return matches[0].read_text(encoding="utf-8")


@pytest.mark.skipif(not DEEPSEEK_API_KEY, reason="DEEPSEEK_API_KEY environment variable not set")
@pytest.mark.skipif(not MINIMAX_API_KEY, reason="MINIMAX_API_KEY environment variable not set")
def test_deepseek_v4_pro_mixed_provider_orchestration(tmp_path):
    from coordinator.iterative_orchestrator import IterativeOrchestrator

    provider, pool = _provider_pool()
    orchestrator = IterativeOrchestrator(
        planner=StaticMixedPlanner(),
        worker_pool=pool,
        provider=provider,
        rag_engine=None,
        model=DEEPSEEK_MODEL,
        temperature=0.1,
        base_workspace=tmp_path / "workspace",
        max_iterations=1,
        quality_threshold=0.3,
    )

    result = asyncio.run(
        asyncio.wait_for(
            orchestrator.run("Validate mixed DeepSeek V4 Pro and MiniMax multi-agent orchestration."),
            timeout=240,
        )
    )

    shared = Path(result.shared_dir)
    assert shared.exists()
    deepseek_notes = _read_first(shared, "deepseek_notes.txt")
    mixed_report = _read_first(shared, "mixed_report.txt")

    assert "DEEPSEEK_OK=alpha" in deepseek_notes
    assert "DEEPSEEK_OK=alpha" in mixed_report
    assert "MINIMAX_OK=beta" in mixed_report
    assert result.task_id == "task_deepseek_mixed"
    assert result.iterations
