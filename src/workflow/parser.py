"""工作流解析器 — P8-1 将 YAML 解析为 Workflow dataclass + DAG 验证"""

import re
from pathlib import Path
from typing import Any

from loguru import logger

from workflow.dsl import StepCondition, Workflow, WorkflowStep


class WorkflowParseError(Exception):
    """解析错误"""


def parse_workflow_yaml(yaml_content: str) -> Workflow:
    """将 YAML 内容解析为 Workflow 对象"""
    try:
        import yaml
        data = yaml.safe_load(yaml_content)
    except ImportError:
        raise WorkflowParseError("PyYAML 未安装: pip install pyyaml")
    except yaml.YAMLError as e:
        raise WorkflowParseError(f"YAML 语法错误: {e}")

    if not data:
        raise WorkflowParseError("空工作流文件")

    workflow_data = data.get("workflow") or data
    name = workflow_data.get("name", "未命名工作流")
    description = workflow_data.get("description", "")
    version = workflow_data.get("version", "1.0")
    steps_data = workflow_data.get("steps", [])

    if not steps_data:
        raise WorkflowParseError("工作流缺少 steps 定义")

    steps: list[WorkflowStep] = []
    for i, step_data in enumerate(steps_data):
        if not isinstance(step_data, dict):
            raise WorkflowParseError(f"步骤 #{i+1} 格式错误: 期望 dict，得到 {type(step_data).__name__}")

        step_id = step_data.get("id")
        if not step_id:
            raise WorkflowParseError(f"步骤 #{i+1} 缺少 id 字段")

        worker = step_data.get("worker")
        if not worker:
            raise WorkflowParseError(f"步骤 '{step_id}' 缺少 worker 字段")

        input_template = step_data.get("input", "")
        if not input_template:
            logger.warning(f"[WorkflowParser] 步骤 '{step_id}' input 为空，将使用默认输入")

        # 解析 condition
        cond_str = step_data.get("condition", "always")
        condition = _parse_condition(cond_str)
        condition_expr = step_data.get("condition_expr", "")

        step = WorkflowStep(
            id=str(step_id),
            worker=str(worker),
            input=str(input_template),
            output_var=str(step_data.get("output", "result")),
            condition=condition,
            condition_expr=condition_expr or _build_condition_expr(step_data),
            timeout_seconds=int(step_data.get("timeout_seconds", 120)),
            retry_on_fail=int(step_data.get("retry_on_fail", 0)),
        )
        steps.append(step)

    workflow = Workflow(
        name=name,
        description=description,
        version=version,
        steps=steps,
        metadata=workflow_data.get("metadata", {}),
    )

    # 验证
    errors = workflow.validate()
    if errors:
        raise WorkflowParseError("工作流验证失败: " + "; ".join(errors))

    return workflow


def parse_workflow_yaml_file(path: str | Path) -> Workflow:
    """从文件加载并解析工作流"""
    p = Path(path)
    if not p.exists():
        raise WorkflowParseError(f"工作流文件不存在: {path}")
    return parse_workflow_yaml(p.read_text(encoding="utf-8"))


def _parse_condition(value: str) -> StepCondition:
    mapping = {
        "always": StepCondition.ALWAYS,
        "if_result": StepCondition.IF_RESULT,
        "if_relevant": StepCondition.IF_RELEVANT,
        "if_failed": StepCondition.IF_FAILED,
    }
    return mapping.get(value.lower(), StepCondition.ALWAYS)


def _build_condition_expr(step_data: dict) -> str:
    """从 step_data 中推断条件表达式"""
    cond = step_data.get("condition", "always")
    if cond == "always":
        return ""
    # 提取 input 中的 ${xxx} 引用
    input_tmpl = step_data.get("input", "")
    refs = re.findall(r'\$\{([^}]+)\}', input_tmpl)
    if refs:
        return f"${{{refs[0]}}}"
    return ""


def resolve_template(template: str, context: dict[str, Any]) -> str:
    """将 ${step.output} 模板替换为实际值"""
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        # 支持 xxx.yyy 访问
        parts = key.split(".")
        value: Any = context
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part, f"${{{key}}}")
            else:
                return match.group(0)  # 无法解析，保留原样
        if isinstance(value, (dict, list)):
            import json
            return json.dumps(value, ensure_ascii=False)
        return str(value) if value is not None else match.group(0)

    return re.sub(r'\$\{([^}]+)\}', replacer, template)
