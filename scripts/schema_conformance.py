from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "python"
sys.path.insert(0, str(RUNTIME))

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
except ImportError as exc:  # pragma: no cover - CI installs the conformance extra
    raise SystemExit("schema conformance requires jsonschema>=4.23,<5") from exc

from manager_runtime.agent_loop import run_bounded_agent_loop
from manager_runtime.engine import run
from manager_runtime.orchestrator import run_with_model, run_with_model_and_tools
from manager_runtime.state import SQLiteRunStore, resume_durable_agent_loop, run_durable_agent_loop
from manager_runtime.tools import ToolRegistry

CONTRACTS = ROOT / "contracts"


def load_schemas() -> dict[str, dict]:
    schemas: dict[str, dict] = {}
    for path in sorted(CONTRACTS.glob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(value)
        schemas[path.name] = value
    return schemas


SCHEMAS = load_schemas()
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
FORMAT_CHECKER = FormatChecker()


def validate(schema_name: str, instance: object, label: str) -> None:
    schema = SCHEMAS[schema_name]
    validator = Draft202012Validator(
        schema, registry=REGISTRY, format_checker=FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        lines = [f"{label} failed {schema_name}:"]
        for error in errors:
            path = ".".join(str(part) for part in error.path) or "<root>"
            lines.append(f"  {path}: {error.message}")
        raise AssertionError("\n".join(lines))


def task_input(*, materiality: str = "routine", objective: str = "Return a synthetic answer.") -> dict:
    return {
        "task": {
            "task_id": f"conformance-{materiality}",
            "objective": objective,
            "classification": {
                "materiality": materiality,
                "consequence": "high" if materiality == "material" else "low",
                "uncertainty": "low",
                "reversibility": "reversible",
                "sensitivity": "public",
            },
        },
        "trusted_policy_context": [],
        "untrusted_content": [],
        "model_input": "Return a synthetic answer.",
    }


class SequenceAdapter:
    provider = "conformance-provider"

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def generate(self, request: dict) -> dict:
        self.calls.append(deepcopy(request))
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError("unexpected conformance model call")
        return deepcopy(self.responses[index])


def model_response(response_id: str, *, proposals: list[dict] | None = None, text: str = "synthetic final") -> dict:
    return {
        "response_id": response_id,
        "provider": "conformance-provider",
        "model": "synthetic-model",
        "status": "completed",
        "output_text": text,
        "tool_proposals": proposals or [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


class SyntheticTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments: dict):
        self.calls.append(deepcopy(arguments))
        return {"value": arguments["value"]}

    def verify(self, arguments: dict, output) -> bool:
        return output == {"value": arguments["value"]}


def tool_definition(name: str, side_effect_class: str) -> dict:
    definition = {
        "name": name,
        "description": f"Synthetic {name} tool.",
        "side_effect_class": side_effect_class,
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "requires_verification": side_effect_class
        in {"reversible_write", "external_commitment", "sensitive_destructive"},
        "sensitive_output": False,
    }
    if definition["requires_verification"]:
        definition["version"] = "1"
    return definition


def validate_common_output(output: dict, label: str) -> None:
    validate("trace.schema.json", output["trace"], f"{label}.trace")
    validate("result.schema.json", output["result"], f"{label}.result")
    if isinstance(output.get("approval"), dict):
        validate("approval.schema.json", output["approval"], f"{label}.approval")
    if isinstance(output.get("handoff"), dict):
        validate("handoff.schema.json", output["handoff"], f"{label}.handoff")
    if isinstance(output.get("reconciliation"), dict):
        validate(
            "reconciliation.schema.json",
            output["reconciliation"],
            f"{label}.reconciliation",
        )
    if isinstance(output.get("model_response"), dict):
        validate(
            "model-response.schema.json",
            output["model_response"],
            f"{label}.model_response",
        )
    for index, item in enumerate(output.get("tool_results") or []):
        validate("tool-result.schema.json", item, f"{label}.tool_results[{index}]")


def validate_eval_fixtures() -> None:
    for path in sorted((ROOT / "evals" / "cases").glob("*.json")):
        validate("eval-case.schema.json", json.loads(path.read_text()), str(path))


def validate_deterministic_outputs() -> None:
    direct = run(task_input())
    validate_common_output(direct, "direct")

    material = run(task_input(materiality="material"))
    validate_common_output(material, "material")

    specialist = run(task_input(objective="Use a specialist for synthetic analysis."))
    validate_common_output(specialist, "specialist")

    reconciliation = run(
        task_input(objective="Propagate a confirmed synthetic non-material change.")
    )
    validate_common_output(reconciliation, "reconciliation")


def validate_model_and_tool_outputs() -> None:
    adapter = SequenceAdapter([model_response("model-1")])
    output = run_with_model(task_input(), adapter, model="synthetic-model")
    validate_common_output(output, "model")
    validate("model-request.schema.json", adapter.calls[0], "model.request")

    registry = ToolRegistry()
    read_tool = SyntheticTool()
    definition = tool_definition("lookup", "read")
    registry.register(definition, read_tool)
    validate("tool-definition.schema.json", definition, "tool.definition")
    proposal = {
        "proposal_id": "proposal-read",
        "tool_name": "lookup",
        "arguments": {"value": "x"},
        "target": None,
        "source_ref": None,
    }
    validate("tool-proposal.schema.json", proposal, "tool.proposal")
    tool_adapter = SequenceAdapter([model_response("model-2", proposals=[proposal])])
    governed = run_with_model_and_tools(
        task_input(),
        tool_adapter,
        registry,
        model="synthetic-model",
        allowed_tools=["lookup"],
    )
    validate_common_output(governed, "governed")
    validate("model-request.schema.json", tool_adapter.calls[0], "governed.request")

    loop_adapter = SequenceAdapter(
        [
            model_response("loop-1", proposals=[proposal]),
            model_response("loop-2", text="loop complete"),
        ]
    )
    looped = run_bounded_agent_loop(
        task_input(),
        loop_adapter,
        registry,
        model="synthetic-model",
        allowed_tools=["lookup"],
    )
    validate_common_output(looped, "bounded_loop")
    for index, request in enumerate(loop_adapter.calls):
        validate("model-request.schema.json", request, f"bounded_loop.request[{index}]")


def validate_durable_outputs() -> None:
    registry = ToolRegistry()
    destructive = SyntheticTool()
    definition = tool_definition("destroy", "sensitive_destructive")
    registry.register(definition, destructive)
    proposal = {
        "proposal_id": "proposal-destroy",
        "tool_name": "destroy",
        "arguments": {"value": "x"},
        "target": "synthetic-target",
        "source_ref": None,
    }
    adapter = SequenceAdapter(
        [
            model_response("durable-1", proposals=[proposal]),
            model_response("durable-2", text="durable complete"),
        ]
    )
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteRunStore(Path(directory) / "state.sqlite3")
        output = run_durable_agent_loop(
            task_input(),
            adapter,
            registry,
            store,
            model="synthetic-model",
            allowed_tools=["destroy"],
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
        )
        validate_common_output(output, "durable.waiting")
        state = store.load(output["durable_state"]["run_id"])
        assert state is not None
        validate("run-state.schema.json", state, "durable.waiting.state")
        validate(
            "agent-loop-checkpoint.schema.json",
            state["extensions"]["agent_loop"],
            "durable.waiting.checkpoint",
        )
        validate(
            "approval.schema.json",
            state["pending_action"]["approval"],
            "durable.waiting.approval",
        )

        approval = state["pending_action"]["approval"]
        completed = resume_durable_agent_loop(
            store,
            state["run_id"],
            adapter,
            registry,
            authorization_contexts={
                "destroy": {"scope_authorized": True, "target_verified": True}
            },
            decision={
                "approval_id": approval["approval_id"],
                "decision": "approved",
                "decided_by": "synthetic-human",
                "decided_at": "2026-10-08T00:00:00Z",
            },
        )
        validate_common_output(completed, "durable.completed")
        final_state = store.load(state["run_id"])
        assert final_state is not None
        validate("run-state.schema.json", final_state, "durable.completed.state")
        validate(
            "agent-loop-checkpoint.schema.json",
            final_state["extensions"]["agent_loop"],
            "durable.completed.checkpoint",
        )


def main() -> int:
    validate_eval_fixtures()
    validate_deterministic_outputs()
    validate_model_and_tool_outputs()
    validate_durable_outputs()
    print(
        f"Schema conformance passed: {len(SCHEMAS)} schemas, eval fixtures, and executable runtime artifacts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
