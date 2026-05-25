import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastapi import FastAPI

import web.routers.workflows as wf_routers
from web.routers.workflows import router
from workflow.engine import WorkflowEngine

app = FastAPI()
app.include_router(router)

client = TestClient(app)

def test_run_step_endpoint():
    yaml_content = """
name: test
steps:
  - id: step1
    worker: echo_worker
    input: hello from step1
"""
    # Create dummy worker
    from agents.worker_pool import get_worker_pool
    pool = get_worker_pool()
    class MockProvider:
        pass

    from agents.worker_pool import WorkerAgent, WorkerConfig
    class DummyWorker(WorkerAgent):
        def __init__(self):
            config = WorkerConfig(name="echo_worker", provider_type="mock", api_key="", model="")
            super().__init__(config=config, tools=None, provider=MockProvider())
        async def execute_task(self, instruction, *args, **kwargs):
            return f"ECHO: {instruction}", {}
    pool.register_worker(DummyWorker())

    # Mock engine
    class MockPersistence:
        def save_run(self, run): pass
        def load_run(self, run_id): return None
        def list_runs(self, *args, **kwargs): return []

    mock_engine = WorkflowEngine(pool, MockProvider(), MockPersistence())
    wf_routers._workflow_engine = mock_engine


    response = client.post("/api/workflows/run_step", json={
        "yaml_content": yaml_content,
        "step_id": "step1",
        "context": {}
    })
    print(response.json())
    assert response.status_code == 200
    data = response.json()
    assert data["step_id"] == "step1"
    assert data["status"] == "completed"
    assert "hello from step1" in data["output"]
