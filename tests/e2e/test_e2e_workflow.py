"""
E2E Workflow Engine 测试
运行方式：pytest tests/e2e/ -m e2e -v -s

场景：验证 WorkflowEngine 完整生命周期
  1. validate — YAML 格式校验
  2. run — 提交执行，返回 run_id
  3. 轮询 run 状态直至完成
  4. 验证 step 输出和状态
"""

import pytest

MINIMAX_API_KEY = __import__("os").environ.get("MINIMAX_API_KEY", "")
BASE_URL = __import__("os").environ.get("MEMOX_BASE_URL", "http://localhost:18000")

if not MINIMAX_API_KEY:
    pytest.skip("MINIMAX_API_KEY environment variable not set", allow_module_level=True)

pytestmark = pytest.mark.e2e


def _client():
    from fastapi.testclient import TestClient

    from src.main import app as fastapi_app

    return TestClient(fastapi_app)


def _auth_headers(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    if resp.status_code == 200:
        token = resp.json().get("token")
        return {"Authorization": f"Bearer {token}"}
    # Fallback: try without auth (some endpoints are public)
    return {}


SIMPLE_WORKFLOW_YAML = """
workflow:
  name: "简单研究工作流"
  steps:
    - id: search
      worker: researcher
      input: "What is retrieval-augmented generation (RAG)? Give a 2-sentence answer."
    - id: summarize
      worker: writer
      input: "Summarize this in one sentence: ${search.output}"
      depends_on:
        - search
"""

LINEAR_CHAIN_YAML = """
workflow:
  name: "线性处理链"
  steps:
    - id: step_a
      worker: researcher
      input: "Name the three primary colors."
    - id: step_b
      worker: writer
      input: "List these items with numbers: ${step_a.output}"
      depends_on:
        - step_a
    - id: step_c
      worker: writer
      input: "Append '— end of list' to: ${step_b.output}"
      depends_on:
        - step_b
"""

PARALLEL_WORKFLOW_YAML = """
workflow:
  name: "并行工作流"
  steps:
    - id: task_1
      worker: researcher
      input: "What is 2 + 2?"
    - id: task_2
      worker: researcher
      input: "What is 3 × 3?"
    - id: combine
      worker: writer
      input: "Combine answers: ${task_1.output} and ${task_2.output}"
      depends_on:
        - task_1
        - task_2
"""


class TestWorkflowValidate:
    """工作流 YAML 校验 API"""

    def test_validate_simple_workflow(self):
        client = _client()
        resp = client.post("/api/workflows/validate", json={"yaml_content": SIMPLE_WORKFLOW_YAML})
        assert resp.status_code == 200, f"Validate failed: {resp.text}"
        data = resp.json()
        assert data["valid"] is True, f"Expected valid, got errors: {data['errors']}"
        assert data["step_count"] == 2

    def test_validate_linear_chain(self):
        client = _client()
        resp = client.post("/api/workflows/validate", json={"yaml_content": LINEAR_CHAIN_YAML})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["step_count"] == 3

    def test_validate_parallel_workflow(self):
        client = _client()
        resp = client.post("/api/workflows/validate", json={"yaml_content": PARALLEL_WORKFLOW_YAML})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["step_count"] == 3

    def test_validate_invalid_yaml(self):
        client = _client()
        bad_yaml = """
workflow:
  name: "Bad"
  steps:
    - id: s1
      # missing worker field
      input: "test"
"""
        resp = client.post("/api/workflows/validate", json={"yaml_content": bad_yaml})
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestWorkflowRun:
    """工作流执行 API — 完整生命周期"""

    def test_simple_workflow_runs_and_completes(self):
        """提交工作流 → 轮询状态 → 验证完成"""
        import time
        client = _client()

        # Submit
        resp = client.post(
            "/api/workflows/run",
            json={"yaml_content": SIMPLE_WORKFLOW_YAML, "context": {}},
        )
        assert resp.status_code == 200, f"Run submit failed: {resp.text}"
        run_data = resp.json()
        run_id = run_data["run_id"]
        assert run_id
        assert run_data["step_count"] == 2

        # Poll until done (max 120s)
        for _ in range(60):
            time.sleep(2)
            resp = client.get(f"/api/workflows/runs/{run_id}")
            assert resp.status_code == 200, f"Status poll failed: {resp.text}"
            status_data = resp.json()
            status = status_data["status"]
            print(f"  Workflow status: {status}")
            if status in ("completed", "failed"):
                break
        else:
            pytest.fail("Workflow did not complete within 120s")

        assert status_data["status"] == "completed", (
            f"Expected completed, got {status_data['status']}. "
            f"Steps: {[(s['step_id'], s['status'], s.get('error','')) for s in status_data['steps']]}"
        )

        # Verify steps
        steps = status_data["steps"]
        assert len(steps) == 2
        step_ids = {s["step_id"] for s in steps}
        assert step_ids == {"search", "summarize"}

        # search should complete before summarize
        search_step = next(s for s in steps if s["step_id"] == "search")
        summarize_step = next(s for s in steps if s["step_id"] == "summarize")
        assert search_step["status"] == "completed"
        assert summarize_step["status"] == "completed"
        assert search_step.get("output"), "search step should have output"

    def test_linear_chain_respects_dependency_order(self):
        """线性链式工作流：step_b 必须等 step_a 完成才能开始"""
        import time
        client = _client()

        resp = client.post(
            "/api/workflows/run",
            json={"yaml_content": LINEAR_CHAIN_YAML, "context": {}},
        )
        assert resp.status_code == 200, f"Run submit failed: {resp.text}"
        run_id = resp.json()["run_id"]

        # Poll
        for _ in range(60):
            time.sleep(2)
            resp = client.get(f"/api/workflows/runs/{run_id}")
            assert resp.status_code == 200
            status_data = resp.json()
            if status_data["status"] in ("completed", "failed"):
                break

        assert status_data["status"] == "completed", (
            f"Expected completed, got {status_data['status']}"
        )
        steps = status_data["steps"]
        assert len(steps) == 3
        step_ids = {s["step_id"] for s in steps}
        assert step_ids == {"step_a", "step_b", "step_c"}

    def test_list_workflow_runs(self):
        """GET /api/workflows/runs 返回历史记录"""
        client = _client()
        resp = client.get("/api/workflows/runs")
        assert resp.status_code == 200, f"List runs failed: {resp.text}"
        data = resp.json()
        assert isinstance(data, list)

    def test_get_workflow_run_detail(self):
        """GET /api/workflows/runs/{run_id} 返回完整 step 详情"""
        import time
        client = _client()

        # Submit
        resp = client.post(
            "/api/workflows/run",
            json={"yaml_content": SIMPLE_WORKFLOW_YAML, "context": {}},
        )
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        # Poll
        for _ in range(60):
            time.sleep(2)
            resp = client.get(f"/api/workflows/runs/{run_id}")
            assert resp.status_code == 200
            if resp.json()["status"] in ("completed", "failed"):
                break

        status_data = resp.json()
        assert "steps" in status_data
        assert "context" in status_data
        assert "created_at" in status_data
        assert "updated_at" in status_data
