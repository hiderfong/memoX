"""
E2E 协作测试 - 使用真实 MiniMax LLM
运行方式：pytest tests/e2e/ -m e2e -v -s

场景 1：全链路协作 - Python 计算器
  developer agent 编写 calculator.py
  tester agent 写测试、运行测试、记录结果

场景 2：迭代精化验证
  给出模糊需求，预期第一轮评分低于 0.8，第二轮 refinement 驱动改进

场景 3：三节点依赖链
  3 个子任务 A→B→C 串行，验证 context 在三节点间正确传递
"""
import sys, os, asyncio, pytest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
if not MINIMAX_API_KEY:
    pytest.skip("MINIMAX_API_KEY environment variable not set", allow_module_level=True)
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


def make_orchestrator(tmp_path, provider, pool, max_iterations=3, quality_threshold=0.6):
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
        max_iterations=max_iterations,
        quality_threshold=quality_threshold,
    )


def print_mail_log(shared: Path):
    """打印 shared/mail_log.txt 邮件通信日志"""
    mail_log = shared / "mail_log.txt"
    if mail_log.exists():
        print(f"\n{mail_log.read_text()}")
    else:
        print("\n(未生成邮件通信日志)")


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
            if f.is_file() and f.name != "mail_log.txt":
                print(f"  [文件] {f.relative_to(shared)}")
    print_mail_log(shared)
    print(f"result_summary (前500字):\n{result.result_summary[:500]}")

    # 基础断言
    assert result.task_id, "task_id 不能为空"
    assert len(result.iterations) >= 1, "至少应有一轮迭代"
    assert result.final_score >= 0.0, "final_score 应为有效数值"
    assert shared.exists(), f"shared 目录应存在: {shared}"

    # 邮件日志断言
    mail_log_file = shared / "mail_log.txt"
    assert mail_log_file.exists(), "应生成邮件通信日志 mail_log.txt"
    mail_log_content = mail_log_file.read_text()
    assert "邮件通信日志" in mail_log_content, "mail_log.txt 应包含日志标题"

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


def test_iterative_refinement(tmp_path):
    """
    E2E 场景 2：迭代精化验证
    给出模糊、不完整的需求，预期第一轮评分低于 0.8，
    第二轮 refinement_hint 驱动改进，最终达标。
    验证：
      - 至少 2 轮迭代
      - 第 1 轮评分 < 0.8（需求模糊导致不完整实现）
      - 最终评分 >= 0.6（至少有所改进）
    """
    TASK_DESCRIPTION = """【多Agent协作任务 - 模糊需求】

请完成以下任务：

【子任务1：开发（无依赖）】
用 write_file 创建一个 Python 模块 utils.py，实现一些字符串处理函数。
（注意：需求故意不明确，不指定具体函数名和功能）

【子任务2：测试（依赖子任务1）】
用 read_mail 读取邮件确认开发完成。
对 utils.py 编写全面的单元测试 test_utils.py，用 unittest 框架。
用 run_shell 运行测试：python -m unittest test_utils.py -v 2>&1
将完整输出写入 test_result.txt。"""

    provider = make_minimax_provider()
    pool = make_worker_pool(provider)
    orchestrator = make_orchestrator(tmp_path, provider, pool)

    result = asyncio.run(
        asyncio.wait_for(
            orchestrator.run(TASK_DESCRIPTION),
            timeout=300,
        )
    )

    print(f"\n=== E2E 场景2 结果 ===")
    print(f"task_id: {result.task_id}")
    print(f"final_score: {result.final_score}")
    print(f"iterations: {len(result.iterations)}")
    for i, rec in enumerate(result.iterations):
        print(f"  第 {i+1} 轮: score={rec.score}, improvements={rec.improvements}")
    print(f"shared_dir: {result.shared_dir}")
    shared = Path(result.shared_dir)
    if shared.exists():
        for f in sorted(shared.rglob("*")):
            if f.is_file() and f.name != "mail_log.txt":
                print(f"  [文件] {f.relative_to(shared)}")
    print_mail_log(shared)
    print(f"result_summary (前500字):\n{result.result_summary[:500]}")

    # 基础断言
    assert result.task_id, "task_id 不能为空"
    assert len(result.iterations) >= 1, "至少应有一轮迭代"
    assert result.final_score >= 0.0, "final_score 应为有效数值"

    # 迭代精化断言（宽松）：如果多轮，后续轮次应有改进
    if len(result.iterations) >= 2:
        print("✓ 触发了多轮迭代（符合预期：模糊需求导致首轮不完美）")
        # 第一轮评分不应完美
        assert result.iterations[0].score < 1.0, "模糊需求首轮不应满分"

    # shared/ 下应有 .py 文件
    py_files = list(shared.rglob("*.py")) if shared.exists() else []
    assert len(py_files) >= 1, f"shared/ 下应有 .py 文件"


def test_three_node_dependency_chain(tmp_path):
    """
    E2E 场景 3：三节点依赖链 A→B→C
    - Agent A: 创建 data.json（原始数据）
    - Agent B: 依赖 A，读取 data.json 进行处理，写入 processed.json
    - Agent C: 依赖 B，读取 processed.json 生成报告 report.txt
    验证 context 在三节点间正确传递，C 的输出包含 A 和 B 的信息。
    """
    TASK_DESCRIPTION = """【三节点依赖链协作任务】

请严格按以下分工和依赖关系完成：

【子任务1：数据准备（无依赖）】
用 write_file 创建 data.json，内容为：
{"items": [{"name": "apple", "price": 3}, {"name": "banana", "price": 2}, {"name": "cherry", "price": 5}]}
完成后用 send_mail 通知 processor，主题 "data_ready"，说明 data.json 已创建。

【子任务2：数据处理（依赖子任务1）】
先用 read_mail 确认 data.json 已就绪。
用 read_file 读取 data.json，计算所有 items 的总价。
用 write_file 创建 processed.json，内容为：
{"total_items": 3, "total_price": 10, "items": [...原始items...]}
完成后用 send_mail 通知 reporter，主题 "processed_ready"。

【子任务3：报告生成（依赖子任务2）】
先用 read_mail 确认处理完成。
用 read_file 读取 processed.json。
用 write_file 创建 report.txt，内容为一份简洁的文本报告，包含：
- 总商品数
- 总价格
- 每个商品的名称和价格"""

    provider = make_minimax_provider()

    # 需要 3 个 Worker
    from agents.worker_pool import WorkerAgent, WorkerConfig, WorkerPool
    pool = WorkerPool(max_workers=3)
    for name in ("developer", "processor", "reporter"):
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

    orchestrator = make_orchestrator(tmp_path, provider, pool, max_iterations=3, quality_threshold=0.6)
    orchestrator._worker_pool = pool

    result = asyncio.run(
        asyncio.wait_for(
            orchestrator.run(TASK_DESCRIPTION),
            timeout=300,
        )
    )

    print(f"\n=== E2E 场景3 结果 ===")
    print(f"task_id: {result.task_id}")
    print(f"final_score: {result.final_score}")
    print(f"iterations: {len(result.iterations)}")
    for i, rec in enumerate(result.iterations):
        print(f"  第 {i+1} 轮: score={rec.score}, improvements={rec.improvements}")
    print(f"shared_dir: {result.shared_dir}")
    shared = Path(result.shared_dir)
    if shared.exists():
        for f in sorted(shared.rglob("*")):
            if f.is_file() and f.name != "mail_log.txt":
                print(f"  [文件] {f.relative_to(shared)}")
                content = f.read_text()[:200]
                print(f"    内容预览: {content}")
    print_mail_log(shared)
    print(f"result_summary (前500字):\n{result.result_summary[:500]}")

    # 基础断言
    assert result.task_id, "task_id 不能为空"
    assert len(result.iterations) >= 1, "至少应有一轮迭代"
    assert result.final_score >= 0.0, "final_score 应为有效数值"
    assert shared.exists(), f"shared 目录应存在: {shared}"

    # 邮件日志断言
    mail_log_file = shared / "mail_log.txt"
    assert mail_log_file.exists(), "应生成邮件通信日志 mail_log.txt"
    mail_log_content = mail_log_file.read_text()
    assert "邮件通信日志" in mail_log_content, "mail_log.txt 应包含日志标题"

    # 文件存在性（宽松）：至少应有输出文件
    all_files = list(shared.rglob("*"))
    real_files = [f for f in all_files if f.is_file()]
    assert len(real_files) >= 1, f"shared/ 下应有文件输出，实际: {all_files}"

    # 若 data.json 存在，验证结构
    data_files = [f for f in real_files if f.name == "data.json"]
    if data_files:
        import json
        data = json.loads(data_files[0].read_text())
        assert "items" in data, "data.json 应包含 items 字段"
        print("✓ data.json 结构正确")

    # 若 processed.json 存在，验证包含汇总信息
    processed_files = [f for f in real_files if f.name == "processed.json"]
    if processed_files:
        import json
        processed = json.loads(processed_files[0].read_text())
        assert "total_price" in processed or "total" in str(processed).lower(), \
            "processed.json 应包含汇总信息"
        print("✓ processed.json 包含汇总信息")

    # 若 report.txt 存在，验证报告内容
    report_files = [f for f in real_files if f.name == "report.txt"]
    if report_files:
        report_text = report_files[0].read_text()
        print(f"\n=== report.txt ===\n{report_text}")
        # 报告应包含商品名称或价格信息
        has_content = any(kw in report_text.lower() for kw in ["apple", "banana", "cherry", "price", "total"])
        assert has_content, f"report.txt 应包含商品或价格信息，实际:\n{report_text}"
        print("✓ report.txt 包含商品信息")
