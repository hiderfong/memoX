import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.worker_pool import WorkerAgent, WorkerConfig, get_worker_pool
from workflow.dsl import Workflow, WorkflowStep
from workflow.engine import WorkflowEngine, WorkflowRunStatus


class MockProvider:
    pass

class DummyWorker(WorkerAgent):
    def __init__(self):
        config = WorkerConfig(name="test_worker", provider_type="mock", api_key="", model="")
        super().__init__(config=config, tools=None, provider=MockProvider())

    async def execute_task(self, instruction, *args, **kwargs):
        desc = instruction.description if hasattr(instruction, 'description') else instruction
        return f"Processed: {desc}", {}

class MockPersistence:
    def save_run(self, run): pass
    def load_run(self, run_id): return None
    def list_runs(self, *args, **kwargs): return []

@pytest.mark.asyncio
async def test_workflow_map_and_condition():
    pool = get_worker_pool()
    pool.register_worker(DummyWorker())

    engine = WorkflowEngine(pool, None, MockPersistence())

    wf = Workflow(
        name="test_map_cond",
        steps=[
            WorkflowStep(
                id="step1",
                worker="test_worker",
                input="${item}",
                map_over="${items_list}",
                output_var="mapped_result"
            ),
            WorkflowStep(
                id="step2_skip",
                worker="test_worker",
                input="Should be skipped",
                condition_expr="${skip_flag} == True",
                output_var="skip_res"
            ),
            WorkflowStep(
                id="step3_run",
                worker="test_worker",
                input="Should run",
                condition_expr="${skip_flag} == False",
                output_var="run_res"
            )
        ]
    )

    context = {
        "items_list": ["apple", "banana"],
        "skip_flag": True
    }

    run = await engine.execute(wf, context)

    # Check map
    assert run.context["mapped_result"] == ["Processed: apple", "Processed: banana"]

    # Check conditions
    # step2_skip has skip_flag == True, so it should RUN
    assert run.get_step_record("step2_skip").status == WorkflowRunStatus.COMPLETED
    assert run.context["skip_res"] == "Processed: Should be skipped"

    # step3_run has skip_flag == False, so it should SKIP (eval(True == False) -> False -> Skip)
    assert run.get_step_record("step3_run").status == WorkflowRunStatus.COMPLETED
    assert run.get_step_record("step3_run").output == "(跳过)"
