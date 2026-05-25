import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from workflow.dsl import StepCondition, Workflow, WorkflowStep


def test_workflow_to_react_flow():
    wf = Workflow(
        name="test_wf",
        steps=[
            WorkflowStep(id="step1", worker="w1", input="hello"),
            WorkflowStep(id="step2", worker="w2", input="${step1.result}", condition=StepCondition.IF_RESULT),
            WorkflowStep(id="step3", worker="w3", input="${step1.result} and ${step2.result}")
        ]
    )

    rf = wf.to_react_flow()

    # Check nodes
    assert len(rf["nodes"]) == 3
    node_ids = {n["id"] for n in rf["nodes"]}
    assert node_ids == {"step1", "step2", "step3"}

    # Check edges
    assert len(rf["edges"]) == 3
    edge_pairs = {(e["source"], e["target"]) for e in rf["edges"]}
    assert ("step1", "step2") in edge_pairs
    assert ("step1", "step3") in edge_pairs
    assert ("step2", "step3") in edge_pairs

    # Check position calculation (0, 100, 200)
    y_positions = [n["position"]["y"] for n in rf["nodes"]]
    assert y_positions == [0, 100, 200]

    # Check data payload
    step2_data = next(n["data"] for n in rf["nodes"] if n["id"] == "step2")
    assert step2_data["condition"] == "if_result"
