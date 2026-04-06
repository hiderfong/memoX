"""
E2E 协作测试 - 使用真实 MiniMax LLM
运行方式：pytest tests/e2e/ -m e2e -v -s

场景 1：全链路协作 - Python 计算器
  developer agent 编写 calculator.py
  tester agent 写测试、运行测试、记录结果
"""
import sys, os, asyncio, pytest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

MINIMAX_API_KEY = "${MINIMAX_API_KEY}"
MODEL = "MiniMax-M2.7-highspeed"
BASE_URL = "https://api.minimaxi.com/anthropic/v1"

pytestmark = pytest.mark.e2e


def make_minimax_provider():
    from agents.base_agent import MiniMaxProvider
    return MiniMaxProvider(api_key=MINIMAX_API_KEY, base_url=BASE_URL)


def make_worker_pool(provider):
    from agents.worker_pool import WorkerAgent, WorkerConfig, WorkerPool
    pool = WorkerPool(max_workers=2)
    for name in ("developer", "tester"):
        config = WorkerConfig(
            name=name,
            provider_type="minimax",
            api_key=MINIMAX_API_KEY,
            model=MODEL,
            temperature=0.3,
            max_tokens=4096,
            max_iterations=10,
        )
        pool.register_worker(WorkerAgent(config=config, provider=provider))
    return pool


def make_orchestrator(tmp_path, provider, pool):
    from coordinator.task_planner import TaskPlanner
    from coordinator.iterative_orchestrator import IterativeOrchestrator

    planner = TaskPlanner(provider=provider, worker_pool=pool, model=MODEL, temperature=0.3)
    return IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=None,
        model=MODEL,
        temperature=0.1,
        base_workspace=tmp_path / "workspace",
    )
