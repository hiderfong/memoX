import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastapi import FastAPI

from web.routers.workflows import router

app = FastAPI()
app.include_router(router)

client = TestClient(app)

def test_visualize_endpoint():
    yaml_content = """
name: test
steps:
  - id: step1
    worker: w1
    input: hello
  - id: step2
    worker: w2
    input: ${step1.result}
"""
    response = client.post("/api/workflows/visualize", json={"yaml_content": yaml_content})
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["source"] == "step1"
    assert data["edges"][0]["target"] == "step2"

def test_visualize_endpoint_invalid_yaml():
    response = client.post("/api/workflows/visualize", json={"yaml_content": "invalid: yaml: :"})
    assert response.status_code == 400
    assert "解析失败" in response.json()["detail"]

def test_schema_endpoint():
    response = client.get("/api/workflows/schema")
    assert response.status_code == 200
    schema = response.json()
    assert schema["title"] == "Workflow"
    assert "WorkflowStep" in schema["$defs"]
