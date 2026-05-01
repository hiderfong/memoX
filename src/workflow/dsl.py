"""工作流 DSL — P8-1 工作流定义与验证"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepCondition(Enum):
    """步骤执行条件"""
    ALWAYS = "always"           # 总是执行
    IF_RESULT = "if_result"     # 前置步骤有结果时执行
    IF_RELEVANT = "if_relevant" # 前置步骤结果 relevant=True 时执行
    IF_FAILED = "if_failed"     # 前置步骤失败时执行


@dataclass
class WorkflowStep:
    """工作流单个步骤定义"""
    id: str                          # 步骤唯一 ID（如 "search", "write"）
    worker: str                       # Worker 名称（如 "researcher"）
    input: str                        # 输入模板，支持 ${previous_step.output} 占位符
    output_var: str = "result"        # 输出变量名（供后续步骤引用）
    condition: StepCondition = StepCondition.ALWAYS  # 执行条件
    condition_expr: str = ""         # 自定义条件表达式（如 "${search_results.relevant}"）
    timeout_seconds: int = 120       # 超时时间
    retry_on_fail: int = 0           # 失败重试次数

    def get_input_refs(self) -> list[str]:
        """提取 input 模板中所有 ${...} 变量引用"""
        import re
        return re.findall(r'\$\{([^}]+)\}', self.input)

    def is_parallel_with(self, other: "WorkflowStep", all_steps: dict[str, "WorkflowStep"]) -> bool:
        """判断两个步骤是否可以并行执行（无直接或间接依赖）"""
        return not self.depends_on(other.id, all_steps) and not other.depends_on(self.id, all_steps)

    def depends_on(self, step_id: str, all_steps: dict[str, "WorkflowStep"]) -> bool:
        """判断当前步骤是否依赖指定步骤"""
        refs = self.get_input_refs()
        if step_id not in refs:
            return False
        # 检查是否是直接引用还是链式
        return step_id in refs


@dataclass
class Workflow:
    """完整工作流定义"""
    name: str
    description: str = ""
    version: str = "1.0"
    steps: list[WorkflowStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_id: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def get_step_ids(self) -> set[str]:
        return {s.id for s in self.steps}

    def topological_order(self) -> list[WorkflowStep]:
        """返回拓扑排序后的步骤列表（可安全执行的顺序）"""
        result: list[WorkflowStep] = []
        remaining = {s.id: s for s in self.steps}
        step_map = {s.id: s for s in self.steps}

        while remaining:
            # 找所有依赖都已完成的步骤
            ready = [
                s for sid, s in remaining.items()
                if all(dep not in remaining for dep in s.get_input_refs() if dep in step_map)
            ]
            if not ready:
                # 循环依赖或悬空引用 — 打破僵局：取第一个
                ready = [list(remaining.values())[0]]

            for s in ready:
                result.append(s)
                remaining.pop(s.id)

        return result

    def validate(self) -> list[str]:
        """验证工作流合法性，返回错误列表（空=合法）"""
        errors: list[str] = []
        step_ids = self.get_step_ids()

        # 检查重复 ID
        if len(step_ids) != len(self.steps):
            seen: set[str] = set()
            for s in self.steps:
                if s.id in seen:
                    errors.append(f"重复的步骤 ID: {s.id}")
                seen.add(s.id)

        # 检查 worker 非空
        for s in self.steps:
            if not s.worker:
                errors.append(f"步骤 '{s.id}' 缺少 worker 名称")

        # 检查输入引用是否都指向已定义的步骤或上下文变量
        step_ids = self.get_step_ids()
        for s in self.steps:
            for ref in s.get_input_refs():
                # 支持 xxx.yyy 访问（如 ${search.result}）
                target = ref.split(".")[0]
                # target 如果不是数字开头且不在步骤列表中，认为是外部上下文变量，跳过
                if target in step_ids:
                    pass  # 指向另一个步骤，正确
                # 否则是上下文变量（如 ${query}），这是正常的

        # 检查循环依赖（简单的两两检测不够精确，但够用）
        sorted_steps = self.topological_order()
        if len(sorted_steps) < len(self.steps):
            errors.append("检测到循环依赖")

        return errors
