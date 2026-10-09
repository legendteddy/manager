from __future__ import annotations

from copy import deepcopy
from typing import Any

from .orchestrator import MODEL_INSTRUCTIONS, run_with_model
from .providers.base import ModelAdapter, ModelPayload, validate_model_response
from .serialization import bounded_json_text
from .tools.base import ToolRegistry, tool_request_fingerprint
from .tools.runtime import execute_tool_request


class AgentLoopError(RuntimeError):
    """Raised when a bounded agent-loop configuration is invalid."""


def _positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AgentLoopError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise AgentLoopError(f"{name} must be >= {minimum}")
    return value


def _set_stop(output: dict[str, Any], *, reason: str, status: str, model_steps: int, tool_calls: int) -> dict[str, Any]:
    trace = output["trace"]
    result = output["result"]
    trace["status"] = status
    result["status"] = status
    if status == "blocked":
        result["uncertainties"] = [f"Bounded agent loop stopped: {reason}."]
    elif status == "failed":
        result["uncertainties"] = [f"Bounded agent loop failed: {reason}."]
    output["agent_loop"] = {
        "status": status,
        "stop_reason": reason,
        "model_steps": model_steps,
        "tool_calls": tool_calls,
    }
    return output


def _serialize_tool_output(result: dict[str, Any], max_chars: int) -> tuple[str, bool]:
    if result.get("redacted"):
        return "Tool executed successfully; output withheld from the model by Manager policy.", True
    return bounded_json_text(result.get("output"), max_chars), False


def _model_trace_event(response: dict[str, Any], step: int) -> dict[str, Any]:
    return {
        "event_type": "model",
        "status": response["status"],
        "reference": response.get("response_id") or response["provider"],
        "summary": f"Bounded model continuation step {step} completed through a provider adapter.",
    }


def _tool_request(*, proposal: dict[str, Any], run_id: str, model_step: int, proposal_index: int) -> dict[str, Any]:
    return {
        "request_id": f"tool-request:step-{model_step}:{proposal_index}:{proposal['proposal_id']}",
        "run_id": run_id,
        "tool_name": proposal["tool_name"],
        "arguments": deepcopy(proposal["arguments"]),
        "target": proposal.get("target"),
        "proposed_by": "model",
        "proposal_ref": proposal["proposal_id"],
    }


def _blocked_tool_result(request: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "tool_name": request["tool_name"],
        "status": "blocked",
        "side_effect_class": "analysis",
        "decision_reason": reason,
        "verification": {"status": "not_required", "details": ""},
        "approval_ref": None,
        "error": None,
        "redacted": False,
    }


def run_bounded_agent_loop(
    task_input: dict[str, Any],
    adapter: ModelAdapter,
    registry: ToolRegistry,
    *,
    model: str,
    allowed_tools: list[str],
    authorization_contexts: dict[str, dict[str, Any]] | None = None,
    max_model_steps: int = 4,
    max_tool_calls: int = 8,
    max_tool_result_chars: int = 8000,
    max_output_tokens: int | None = None,
    allow_non_public_input: bool = False,
) -> dict[str, Any]:
    """Run a bounded model -> governed tool -> model continuation loop."""
    max_model_steps = _positive_int("max_model_steps", max_model_steps)
    max_tool_calls = _positive_int("max_tool_calls", max_tool_calls, allow_zero=True)
    max_tool_result_chars = _positive_int("max_tool_result_chars", max_tool_result_chars)

    definitions = registry.model_definitions(allowed_tools)
    output = run_with_model(
        task_input,
        adapter,
        model=model,
        max_output_tokens=max_output_tokens,
        allow_non_public_input=allow_non_public_input,
        tool_definitions=definitions,
    )
    response = output.get("model_response")
    if not isinstance(response, dict):
        output["agent_loop"] = {
            "status": output["trace"]["status"],
            "stop_reason": "model_not_called",
            "model_steps": 0,
            "tool_calls": 0,
        }
        return output

    model_steps = 1
    tool_calls = 0
    seen_proposals: set[str] = set()
    allowed = set(allowed_tools)
    authorization_contexts = authorization_contexts or {}
    task = task_input["task"]
    trace = output["trace"]
    result_envelope = output["result"]
    output["model_responses"] = [deepcopy(response)]
    output["tool_results"] = []

    while True:
        if response["status"] != "completed":
            return _set_stop(
                output,
                reason="model_response_not_completed",
                status="failed" if response["status"] == "failed" else "blocked",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
        proposals = response.get("tool_proposals") or []
        if not proposals:
            output["agent_loop"] = {
                "status": "completed",
                "stop_reason": "final_model_response",
                "model_steps": model_steps,
                "tool_calls": tool_calls,
            }
            trace["status"] = "completed"
            result_envelope["status"] = "completed"
            result_envelope["finding"] = response["output_text"]
            return output
        if model_steps >= max_model_steps:
            return _set_stop(output, reason="model_step_budget_exhausted", status="blocked", model_steps=model_steps, tool_calls=tool_calls)
        if not response.get("response_id"):
            return _set_stop(output, reason="continuation_reference_missing", status="blocked", model_steps=model_steps, tool_calls=tool_calls)
        if tool_calls + len(proposals) > max_tool_calls:
            return _set_stop(output, reason="tool_call_budget_exhausted", status="blocked", model_steps=model_steps, tool_calls=tool_calls)

        prepared: list[tuple[dict[str, Any], str]] = []
        batch_fingerprints: set[str] = set()
        for index, proposal in enumerate(proposals):
            request = _tool_request(proposal=proposal, run_id=trace["run_id"], model_step=model_steps, proposal_index=index)
            fingerprint = tool_request_fingerprint(request)
            if fingerprint in seen_proposals or fingerprint in batch_fingerprints:
                return _set_stop(output, reason="repeated_tool_proposal", status="blocked", model_steps=model_steps, tool_calls=tool_calls)
            batch_fingerprints.add(fingerprint)
            prepared.append((request, fingerprint))

        if len(prepared) > 1:
            for request, _ in prepared:
                registered = registry.get(request["tool_name"])
                if request["tool_name"] not in allowed or registered is None:
                    return _set_stop(output, reason="tool_batch_preflight_blocked", status="blocked", model_steps=model_steps, tool_calls=tool_calls)
                if registered.definition["side_effect_class"] not in {"analysis", "read"}:
                    return _set_stop(output, reason="consequential_multi_tool_batch_requires_serialization", status="blocked", model_steps=model_steps, tool_calls=tool_calls)

        continuation_results: list[dict[str, Any]] = []
        round_results: list[dict[str, Any]] = []
        for request, fingerprint in prepared:
            tool_name = request["tool_name"]
            if tool_name not in allowed:
                tool_result = _blocked_tool_result(request, "tool_not_exposed_to_model")
            else:
                context = dict(authorization_contexts.get(tool_name, {}))
                context.pop("approval", None)
                registered = registry.get(tool_name)
                if registered is not None and registered.definition["side_effect_class"] in {"analysis", "read"}:
                    context.setdefault("scope_authorized", True)
                tool_result = execute_tool_request(task, request, registry, context)
            tool_calls += 1
            seen_proposals.add(fingerprint)
            round_results.append(tool_result)
            output["tool_results"].append(tool_result)
            trace["events"].append({
                "event_type": "tool",
                "status": tool_result["status"],
                "reference": request["request_id"],
                "summary": f"Bounded tool request for {tool_name} ended with status {tool_result['status']}.",
            })
            approval_ref = tool_result.get("approval_ref")
            if approval_ref:
                trace.setdefault("approval_refs", [])
                if approval_ref not in trace["approval_refs"]:
                    trace["approval_refs"].append(approval_ref)
            if tool_result["status"] == "executed":
                serialized, redacted = _serialize_tool_output(tool_result, max_tool_result_chars)
                continuation_results.append({
                    "proposal_id": request["proposal_ref"],
                    "status": "executed",
                    "output": serialized,
                    "redacted": redacted,
                })

        statuses = {item["status"] for item in round_results}
        if "approval_required" in statuses:
            result_envelope["owner_decision_required"] = True
            result_envelope["decision_request"] = "Review the pending tool approval request. Approval is not reused automatically across agent-loop actions."
            return _set_stop(output, reason="tool_approval_required", status="blocked", model_steps=model_steps, tool_calls=tool_calls)
        if "failed" in statuses:
            return _set_stop(output, reason="tool_execution_or_verification_failed", status="failed", model_steps=model_steps, tool_calls=tool_calls)
        if "blocked" in statuses:
            return _set_stop(output, reason="tool_policy_blocked", status="blocked", model_steps=model_steps, tool_calls=tool_calls)

        request: ModelPayload = {
            "request_id": f"model-request:{task['task_id']}:step:{model_steps + 1}",
            "model": model,
            "instructions": MODEL_INSTRUCTIONS,
            "input": "Continue the bounded task using only the verified tool results supplied by Manager.",
            "tools": definitions,
            "continuation": {
                "prior_response_ref": response["response_id"],
                "tool_results": continuation_results,
            },
            "metadata": {"task_id": task["task_id"], "model_step": model_steps + 1},
        }
        if max_output_tokens is not None:
            request["max_output_tokens"] = max_output_tokens

        attempted_step = model_steps + 1
        try:
            response = adapter.generate(request)
            validate_model_response(response, expected_provider=adapter.provider)
        except Exception:
            trace["events"].append({
                "event_type": "model",
                "status": "failed",
                "reference": adapter.provider,
                "summary": "Bounded model continuation failed at the provider boundary.",
            })
            return _set_stop(
                output,
                reason="model_provider_boundary_failed",
                status="failed",
                model_steps=attempted_step,
                tool_calls=tool_calls,
            )
        model_steps = attempted_step
        trace["events"].append(_model_trace_event(response, model_steps))
        capability = f"model-provider:{response['provider']}"
        if capability not in trace["capabilities"]:
            trace["capabilities"].append(capability)
        output["model_response"] = response
        output["model_responses"].append(deepcopy(response))
        result_envelope["finding"] = response["output_text"]
        evidence = result_envelope.setdefault("material_evidence", [])
        evidence.append({
            "type": "model_response",
            "provider": response["provider"],
            "model": response["model"],
            "response_id": response.get("response_id"),
        })
