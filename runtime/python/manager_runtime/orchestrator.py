from __future__ import annotations

from typing import Any

from .engine import run as run_control_plane
from .providers.base import ModelAdapter, ModelPayload, validate_model_response

MODEL_INSTRUCTIONS = (
    "You are a bounded text-generation capability inside Manager. "
    "Produce only the requested user-facing content. "
    "Do not claim that you approved, executed, deployed, persisted, or verified "
    "an external side effect. Governance, authority, approvals, tool execution, "
    "and reconciliation are controlled outside the model."
)


def _model_not_called(
    output: dict[str, Any], adapter: ModelAdapter, reason: str
) -> dict[str, Any]:
    output["model"] = {
        "status": "not_called",
        "provider": adapter.provider,
        "reason": reason,
    }
    return output


def run_with_model(
    task_input: dict[str, Any],
    adapter: ModelAdapter,
    *,
    model: str,
    max_output_tokens: int | None = None,
    allow_non_public_input: bool = False,
) -> dict[str, Any]:
    """Run deterministic governance first, then a bounded model call if eligible.

    Only the direct workflow is model-backed in this reference stage. Material
    or blocked work never reaches the provider. Non-public inputs are withheld
    by default unless the embedding application explicitly permits them.
    """
    output = run_control_plane(task_input)
    trace = output["trace"]
    task = task_input["task"]
    classification = task["classification"]

    if trace["status"] != "completed":
        return _model_not_called(output, adapter, "control_plane_blocked")

    if trace["workflow"] != "direct":
        return _model_not_called(output, adapter, "workflow_not_model_backed")

    if (
        classification.get("sensitivity", "unknown") != "public"
        and not allow_non_public_input
    ):
        return _model_not_called(
            output, adapter, "non_public_input_requires_explicit_opt_in"
        )

    model_input = task_input.get("model_input")
    if model_input is None:
        model_input = task["objective"]
    if not isinstance(model_input, str) or not model_input.strip():
        raise ValueError("model_input must be non-empty text when supplied")

    request: ModelPayload = {
        "request_id": f"model-request:{task['task_id']}",
        "model": model,
        "instructions": MODEL_INSTRUCTIONS,
        "input": model_input,
        "metadata": {"task_id": task["task_id"]},
    }
    if max_output_tokens is not None:
        request["max_output_tokens"] = max_output_tokens

    response = adapter.generate(request)
    validate_model_response(response)

    trace["events"].append(
        {
            "event_type": "model",
            "status": response["status"],
            "reference": response.get("response_id") or response["provider"],
            "summary": "Bounded model generation completed through a provider adapter.",
        }
    )
    capability = f"model-provider:{response['provider']}"
    if capability not in trace["capabilities"]:
        trace["capabilities"].append(capability)

    result = output["result"]
    result["finding"] = response["output_text"]
    result["material_evidence"] = [
        {
            "type": "model_response",
            "provider": response["provider"],
            "model": response["model"],
            "response_id": response.get("response_id"),
        }
    ]
    output["model_response"] = response
    return output
