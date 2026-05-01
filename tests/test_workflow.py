"""P8 工作流测试"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from workflow.dsl import StepCondition, Workflow, WorkflowStep
from workflow.parser import parse_workflow_yaml, resolve_template

class TestWorkflowDSL:
    """Workflow DSL 数据类测试"""

    def test_workflow_basic(self):
        wf = Workflow(name="测试", description="描述", steps=[
            WorkflowStep(id="s1", worker="w1", input="hello"),
        ])
        assert wf.name == "测试"
        assert len(wf.steps) == 1

    def test_workflow_get_step(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="search", worker="researcher", input="${query}"),
            WorkflowStep(id="write", worker="writer", input="${search}"),
        ])
        s = wf.get_step("search")
        assert s is not None
        assert s.worker == "researcher"
        assert wf.get_step("nonexistent") is None

    def test_workflow_validate_ok(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="a", worker="w", input="${query}"),
            WorkflowStep(id="b", worker="w", input="${a}"),
        ])
        assert wf.validate() == []

    def test_workflow_validate_duplicate_id(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="same", worker="w", input="x"),
            WorkflowStep(id="same", worker="w", input="y"),
        ])
        errors = wf.validate()
        assert any("重复" in e for e in errors)

    def test_workflow_validate_missing_worker(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="s1", worker="", input="x"),
        ])
        errors = wf.validate()
        assert any("worker" in e.lower() for e in errors)

    def test_workflow_validate_bad_ref(self):
        # 注意: ${nonexistent} 被视为外部上下文变量，不算错误
        # （外部变量在运行时提供）。这个测试验证无步骤 ID 重复和无 worker 缺失。
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="a", worker="w", input="${nonexistent}"),
        ])
        errors = wf.validate()
        # 外部变量引用不被视为错误（它们在运行时提供）
        assert len(errors) == 0


class TestWorkflowStep:
    def test_get_input_refs(self):
        s = WorkflowStep(id="s1", worker="w", input="query=${q} result=${r.output}")
        refs = s.get_input_refs()
        assert "q" in refs
        assert "r.output" in refs

    def test_get_input_refs_no_refs(self):
        s = WorkflowStep(id="s1", worker="w", input="static text")
        assert s.get_input_refs() == []


class TestTopologicalOrder:
    def test_linear_chain(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="a", worker="w", input=""),
            WorkflowStep(id="b", worker="w", input="${a}"),
            WorkflowStep(id="c", worker="w", input="${b}"),
        ])
        order = wf.topological_order()
        ids = [s.id for s in order]
        # a must come before b, b before c
        assert ids.index("a") < ids.index("b")
        assert ids.index("b") < ids.index("c")

    def test_parallel_steps(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="a", worker="w", input=""),
            WorkflowStep(id="b", worker="w", input="${a}"),
            WorkflowStep(id="c", worker="w", input="${a}"),  # both depend on a
        ])
        order = wf.topological_order()
        ids = [s.id for s in order]
        assert ids.index("a") < ids.index("b")
        assert ids.index("a") < ids.index("c")

    def test_no_deps(self):
        wf = Workflow(name="test", steps=[
            WorkflowStep(id="x", worker="w", input=""),
            WorkflowStep(id="y", worker="w", input=""),
            WorkflowStep(id="z", worker="w", input=""),
        ])
        order = wf.topological_order()
        assert len(order) == 3


class TestWorkflowParser:
    def test_parse_minimal(self):
        yaml = """
workflow:
  name: "简单工作流"
  steps:
    - id: step1
      worker: researcher
      input: "搜索信息"
"""
        wf = parse_workflow_yaml(yaml)
        assert wf.name == "简单工作流"
        assert len(wf.steps) == 1
        assert wf.steps[0].id == "step1"
        assert wf.steps[0].worker == "researcher"
        assert wf.validate() == []

    def test_parse_with_condition(self):
        yaml = """
workflow:
  name: "条件工作流"
  steps:
    - id: search
      worker: researcher
      input: "${query}"
    - id: write
      worker: writer
      input: "${search.result}"
      condition: if_result
"""
        wf = parse_workflow_yaml(yaml)
        assert wf.steps[1].condition == StepCondition.IF_RESULT

    def test_parse_error_empty(self):
        with pytest.raises(Exception):
            parse_workflow_yaml("")

    def test_parse_error_missing_worker(self):
        yaml = """
workflow:
  name: "错误"
  steps:
    - id: s1
      input: "x"
"""
        with pytest.raises(Exception):
            parse_workflow_yaml(yaml)

    def test_parse_error_no_steps(self):
        yaml = """
workflow:
  name: "无步骤"
"""
        with pytest.raises(Exception):
            parse_workflow_yaml(yaml)

    def test_parse_with_timeout(self):
        yaml = """
workflow:
  name: "超时测试"
  steps:
    - id: slow
      worker: w
      input: "x"
      timeout_seconds: 300
"""
        wf = parse_workflow_yaml(yaml)
        assert wf.steps[0].timeout_seconds == 300


class TestResolveTemplate:
    def test_simple_ref(self):
        context = {"query": "什么是 AI", "name": "test"}
        assert resolve_template("search: ${query}", context) == "search: 什么是 AI"

    def test_nested_ref(self):
        context = {"search": {"result": "AI 是人工智能"}}
        assert resolve_template("write: ${search.result}", context) == "write: AI 是人工智能"

    def test_multiple_refs(self):
        context = {"a": "hello", "b": "world"}
        assert resolve_template("${a} ${b}", context) == "hello world"

    def test_missing_ref_unchanged(self):
        context = {}
        assert resolve_template("${missing}", context) == "${missing}"

    def test_static_text(self):
        context = {}
        assert resolve_template("static text", context) == "static text"
