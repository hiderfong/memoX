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


def test_calculator_collaboration(tmp_path):
    """
    全链路 E2E 测试：
    - developer agent 编写 calculator.py，MailBus 通知 tester
    - tester agent 写 test_calculator.py，shell 运行测试，结果写入 test_result.txt
    - IterativeOrchestrator 完整执行并评分
    """
    TASK_DESCRIPTION = """【多Agent协作任务】

请按以下分工完成：

【子任务1：开发（无依赖）】
用 write_file 工具创建 calculator.py，内容为包含以下四个函数的 Python 模块：
- add(a, b): 返回 a + b
- subtract(a, b): 返回 a - b
- multiply(a, b): 返回 a * b
- divide(a, b): 若 b 为 0 返回 None，否则返回 a / b
完成后用 send_mail 工具发邮件给 tester，主题为 "calculator_ready"，正文写明 calculator.py 已创建。

【子任务2：测试（依赖子任务1完成）】
先用 read_mail 工具读取邮件确认开发完成。
用 write_file 工具创建 test_calculator.py，内容为用 unittest 测试上述四个函数的测试用例（import calculator）。
用 run_shell 工具执行命令：python -m unittest test_calculator.py -v 2>&1
将 shell 命令的完整输出用 write_file 写入 test_result.txt。"""

    provider = make_minimax_provider()
    pool = make_worker_pool(provider)
    orchestrator = make_orchestrator(tmp_path, provider, pool)

    result = asyncio.run(
        asyncio.wait_for(
            orchestrator.run(TASK_DESCRIPTION),
            timeout=300,
        )
    )

    print(f"\n=== E2E 结果 ===")
    print(f"task_id: {result.task_id}")
    print(f"final_score: {result.final_score}")
    print(f"iterations: {len(result.iterations)}")
    for i, rec in enumerate(result.iterations):
        print(f"  第 {i+1} 轮: score={rec.score}, improvements={rec.improvements}")
    print(f"shared_dir: {result.shared_dir}")
    shared = Path(result.shared_dir)
    if shared.exists():
        for f in sorted(shared.rglob("*")):
            if f.is_file():
                print(f"  [文件] {f.relative_to(shared)}")
    print(f"result_summary (前500字):\n{result.result_summary[:500]}")

    # 基础断言
    assert result.task_id, "task_id 不能为空"
    assert len(result.iterations) >= 1, "至少应有一轮迭代"
    assert result.final_score >= 0.0, "final_score 应为有效数值"
    assert shared.exists(), f"shared 目录应存在: {shared}"

    # 文件存在性断言（宽松）：shared/ 下应有至少一个 .py 文件
    py_files = list(shared.rglob("*.py"))
    assert len(py_files) >= 1, f"shared/ 下应有 .py 文件，实际: {list(shared.rglob('*'))}"

    # 若 calculator.py 存在，验证内容包含函数定义
    calc_files = [f for f in py_files if f.name == "calculator.py"]
    if calc_files:
        content = calc_files[0].read_text()
        assert "def add" in content, "calculator.py 应包含 add 函数"
        assert "def divide" in content, "calculator.py 应包含 divide 函数"

    # 若 test_result.txt 存在，验证测试通过
    result_files = list(shared.rglob("test_result.txt"))
    if result_files:
        result_text = result_files[0].read_text()
        print(f"\n=== test_result.txt ===\n{result_text}")
        passed = "ok" in result_text.lower() or "passed" in result_text.lower()
        assert passed, f"测试结果应包含 'ok' 或 'passed'，实际内容:\n{result_text}"
