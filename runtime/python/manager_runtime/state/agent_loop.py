from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..agent_loop import (
    _blocked_tool_result,
    _model_trace_event,
    _positive_int,
    _serialize_tool_output,
    _set_stop,
    _tool_request,
)
from ..orchestrator import MODEL_INSTRUCTIONS, run_with_model
from ..providers.base import ModelAdapter, ModelPayload, validate_model_response
from ..tools.base import (
    CONSEQUENTIAL_CLASSES,
    ToolRegistry,
    tool_definition_fingerprint,
    tool_request_fingerprint,
)
from ..tools.runtime import execute_tool_request
from .approvals import resume_tool_approval
from .base import RunState, RunStateError, RunStore


AGENT_LOOP_EXTENSION = "agent_loop"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_authorization_context(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RunStateError("authorization context must be an object")
    result: dict[str, Any] = {}
    for key in ("scope_authorized", "human_intent_confirmed", "target_verified"):
        if key not in value:
            continue
        raw = value[key]
        if not isinstance(raw, bool):
            raise RunStateError(f"authorization field {key!r} must be boolean")
        result[key] = raw
    return result


def _tool_fingerprints(registry: ToolRegistry, allowed_tools: list[str]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for name in allowed_tools:
        registered = registry.get(name)
        if registered is None:
            raise RunStateError(f"allowed tool is not registered: {name}")
        fingerprints[name] = tool_definition_fingerprint(registered.definition)
    return fingerprints


def _checkpoint(
    *,
    adapter: ModelAdapter,
    model: str,
    registry: ToolRegistry,
    allowed_tools: list[str],
    max_model_steps: int,
    max_tool_calls: int,
    max_tool_result_chars: int,
    max_output_tokens: int | None,
    model_steps: int,
    tool_calls: int,
    seen: set[str],
    phase: str,
    current_response: dict[str, Any] | None,
    prior_response_ref: str | None,
    pending_proposal_id: str | None = None,
    pending_request_fingerprint: str | None = None,
    continuation_tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "phase": phase,
        "provider": adapter.provider,
        "model": model,
        "allowed_tools": list(allowed_tools),
        "tool_definition_fingerprints": _tool_fingerprints(registry, allowed_tools),
        "max_model_steps": max_model_steps,
        "max_tool_calls": max_tool_calls,
        "max_tool_result_chars": max_tool_result_chars,
        "max_output_tokens": max_output_tokens,
        "model_steps": model_steps,
        "tool_calls": tool_calls,
        "seen_proposal_fingerprints": sorted(seen),
        "prior_response_ref": prior_response_ref,
        "pending_proposal_id": pending_proposal_id,
        "pending_request_fingerprint": pending_request_fingerprint,
        "current_response": deepcopy(current_response),
        "continuation_tool_results": deepcopy(continuation_tool_results or []),
    }


def _checkpoint_from_state(state: RunState) -> dict[str, Any]:
    extensions = state.get("extensions")
    if not isinstance(extensions, dict):
        raise RunStateError("durable agent loop state is missing extensions")
    checkpoint = extensions.get(AGENT_LOOP_EXTENSION)
    if not isinstance(checkpoint, dict):
        raise RunStateError("durable agent loop checkpoint is missing")
    if checkpoint.get("version") != 1:
        raise RunStateError("unsupported durable agent loop checkpoint version")
    return checkpoint


def _validate_checkpoint(
    state: RunState,
    adapter: ModelAdapter,
    registry: ToolRegistry,
) -> dict[str, Any]:
    checkpoint = _checkpoint_from_state(state)
    if checkpoint.get("provider") != adapter.provider:
        raise RunStateError("model provider changed after the durable checkpoint")
    allowed_tools = checkpoint.get("allowed_tools")
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) and item for item in allowed_tools
    ):
        raise RunStateError("durable checkpoint has invalid allowed_tools")
    stored = checkpoint.get("tool_definition_fingerprints")
    if not isinstance(stored, dict):
        raise RunStateError("durable checkpoint is missing tool definition fingerprints")
    current = _tool_fingerprints(registry, allowed_tools)
    if current != stored:
        raise RunStateError("allowed tool definitions changed after the durable checkpoint")
    return checkpoint


def _summary(state: RunState, output: dict[str, Any] | None = None) -> dict[str, Any]:
    checkpoint = _checkpoint_from_state(state)
    result = output if output is not None else {
        "trace": deepcopy(state.get("trace_snapshot") or {}),
        "result": deepcopy(state.get("result_snapshot") or {}),
    }
    result["durable_state"] = {
        "run_id": state["run_id"],
        "status": state["status"],
        "revision": state["revision"],
        "phase": checkpoint.get("phase"),
    }
    result.setdefault(
        "agent_loop",
        {
            "status": state["status"],
            "stop_reason": (
                "waiting_approval"
                if state["status"] == "waiting_approval"
                else state["status"]
            ),
            "model_steps": checkpoint.get("model_steps", 0),
            "tool_calls": checkpoint.get("tool_calls", 0),
        },
    )
    if state.get("last_tool_result") is not None:
        result.setdefault("tool_results", [deepcopy(state["last_tool_result"])])
    return result


def _new_state(
    task: dict[str, Any],
    output: dict[str, Any],
    checkpoint: dict[str, Any],
    store: RunStore,
) -> RunState:
    now = _now()
    state: RunState = {
        "run_id": output["trace"]["run_id"],
        "task_id": task["task_id"],
        "status": "running",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "task": deepcopy(task),
        "pending_action": None,
        "last_tool_result": None,
        "trace_snapshot": deepcopy(output["trace"]),
        "result_snapshot": deepcopy(output["result"]),
        "recovery_reason": None,
        "extensions": {AGENT_LOOP_EXTENSION: deepcopy(checkpoint)},
    }
    return store.create(state)


def _cas_state(
    store: RunStore,
    state: RunState,
    *,
    status: str | None = None,
    checkpoint: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None | object = ...,
    last_tool_result: dict[str, Any] | None | object = ...,
    recovery_reason: str | None | object = ...,
) -> RunState:
    replacement = deepcopy(state)
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()
    if status is not None:
        replacement["status"] = status
    if checkpoint is not None:
        replacement.setdefault("extensions", {})[AGENT_LOOP_EXTENSION] = deepcopy(
            checkpoint
        )
    if output is not None:
        replacement["trace_snapshot"] = deepcopy(output["trace"])
        replacement["result_snapshot"] = deepcopy(output["result"])
    if pending_action is not ...:
        replacement["pending_action"] = deepcopy(pending_action)
    if last_tool_result is not ...:
        replacement["last_tool_result"] = deepcopy(last_tool_result)
    if recovery_reason is not ...:
        replacement["recovery_reason"] = recovery_reason
    return store.compare_and_swap(state["run_id"], state["revision"], replacement)


def _persist_stop(
    store: RunStore,
    state: RunState,
    output: dict[str, Any],
    *,
    stop_status: str,
    reason: str,
) -> dict[str, Any]:
    checkpoint = _checkpoint_from_state(state)
    checkpoint = deepcopy(checkpoint)
    checkpoint["phase"] = "terminal"
    persisted_status = stop_status if stop_status in {"completed", "failed", "blocked"} else "blocked"
    terminal = _cas_state(
        store,
        state,
        status=persisted_status,
        checkpoint=checkpoint,
        output=output,
        pending_action=None,
        recovery_reason=(None if persisted_status == "completed" else reason),
    )
    return _summary(terminal, output)


def _durable_tool_result(
    task: dict[str, Any],
    request: dict[str, Any],
    registry: ToolRegistry,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate one tool, forcing consequential actions through durable approval.

    Analysis/read tools may execute directly. Every consequential side-effect
    class is converted to the existing exact approval path before execution so
    a durable checkpoint exists before a side effect can occur.
    """
    registered = registry.get(request["tool_name"])
    if registered is None:
        return _blocked_tool_result(request, "unknown_tool")
    definition = registered.definition
    side_effect_class = definition["side_effect_class"]
    context = dict(authorization)
    context.pop("approval", None)

    if side_effect_class not in CONSEQUENTIAL_CLASSES:
        if side_effect_class == "read":
            context.setdefault("scope_authorized", True)
        return execute_tool_request(task, request, registry, context)

    policy_task = deepcopy(task)
    if side_effect_class == "reversible_write":
        policy_task.setdefault("classification", {})["materiality"] = "material"
    if side_effect_class == "external_commitment":
        context["human_intent_confirmed"] = False
    return execute_tool_request(policy_task, request, registry, context)


def _pending_action(
    request: dict[str, Any],
    tool_result: dict[str, Any],
    registry: ToolRegistry,
    authorization_context: dict[str, Any],
) -> dict[str, Any]:
    approval = tool_result.get("approval")
    if not isinstance(approval, dict):
        raise RunStateError("approval_required tool result is missing approval packet")
    registered = registry.get(request["tool_name"])
    if registered is None:
        raise RunStateError("cannot checkpoint an unregistered tool")
    return {
        "tool_request": deepcopy(request),
        "approval": deepcopy(approval),
        "tool_definition_fingerprint": tool_definition_fingerprint(
            registered.definition
        ),
        "authorization_context": _safe_authorization_context(
            authorization_context
        ),
    }


def _append_model_evidence(output: dict[str, Any], response: dict[str, Any]) -> None:
    output["model_response"] = response
    output.setdefault("model_responses", []).append(deepcopy(response))
    output["result"]["finding"] = response["output_text"]
    evidence = output["result"].setdefault("material_evidence", [])
    evidence.append(
        {
            "type": "model_response",
            "provider": response["provider"],
            "model": response["model"],
            "response_id": response.get("response_id"),
        }
    )


def _continuation_request(
    task: dict[str, Any],
    checkpoint: dict[str, Any],
    definitions: list[dict[str, Any]],
) -> ModelPayload:
    prior = checkpoint.get("prior_response_ref")
    if not isinstance(prior, str) or not prior:
        raise RunStateError("continuation checkpoint is missing prior_response_ref")
    results = checkpoint.get("continuation_tool_results")
    if not isinstance(results, list) or not results:
        raise RunStateError("continuation checkpoint is missing verified tool results")
    request: ModelPayload = {
        "request_id": f"model-request:{task['task_id']}:step:{checkpoint['model_steps'] + 1}",
        "model": checkpoint["model"],
        "instructions": MODEL_INSTRUCTIONS,
        "input": "Continue the bounded task using only the verified tool results supplied by Manager.",
        "tools": definitions,
        "continuation": {
            "prior_response_ref": prior,
            "tool_results": deepcopy(results),
        },
        "metadata": {
            "task_id": task["task_id"],
            "model_step": checkpoint["model_steps"] + 1,
        },
    }
    if checkpoint.get("max_output_tokens") is not None:
        request["max_output_tokens"] = checkpoint["max_output_tokens"]
    return request


def _advance(
    store: RunStore,
    state: RunState,
    adapter: ModelAdapter,
    registry: ToolRegistry,
    *,
    authorization_contexts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Advance a persisted loop until completion, approval, or another stop."""
    while True:
        checkpoint = _validate_checkpoint(state, adapter, registry)
        phase = checkpoint.get("phase")
        task = state["task"]
        output = {
            "trace": deepcopy(state.get("trace_snapshot") or {}),
            "result": deepcopy(state.get("result_snapshot") or {}),
            "tool_results": [],
            "model_responses": [],
        }
        output["trace"]["status"] = "running"
        output["result"]["owner_decision_required"] = False
        output["result"]["decision_request"] = None

        allowed_tools = checkpoint["allowed_tools"]
        definitions = registry.model_definitions(allowed_tools)
        seen = set(checkpoint["seen_proposal_fingerprints"])
        model_steps = int(checkpoint["model_steps"])
        tool_calls = int(checkpoint["tool_calls"])

        if phase == "continuation_ready":
            if model_steps >= checkpoint["max_model_steps"]:
                output = _set_stop(
                    output,
                    reason="model_step_budget_exhausted",
                    status="blocked",
                    model_steps=model_steps,
                    tool_calls=tool_calls,
                )
                return _persist_stop(
                    store,
                    state,
                    output,
                    stop_status="blocked",
                    reason="model_step_budget_exhausted",
                )
            request = _continuation_request(task, checkpoint, definitions)
            try:
                response = adapter.generate(request)
                validate_model_response(response, expected_provider=adapter.provider)
            except Exception:
                output = _set_stop(
                    output,
                    reason="model_provider_boundary_failed",
                    status="failed",
                    model_steps=model_steps,
                    tool_calls=tool_calls,
                )
                return _persist_stop(
                    store,
                    state,
                    output,
                    stop_status="failed",
                    reason="model_provider_boundary_failed",
                )
            model_steps += 1
            output["trace"]["events"].append(_model_trace_event(response, model_steps))
            capability = f"model-provider:{response['provider']}"
            if capability not in output["trace"].setdefault("capabilities", []):
                output["trace"]["capabilities"].append(capability)
            _append_model_evidence(output, response)
            checkpoint = _checkpoint(
                adapter=adapter,
                model=checkpoint["model"],
                registry=registry,
                allowed_tools=allowed_tools,
                max_model_steps=checkpoint["max_model_steps"],
                max_tool_calls=checkpoint["max_tool_calls"],
                max_tool_result_chars=checkpoint["max_tool_result_chars"],
                max_output_tokens=checkpoint.get("max_output_tokens"),
                model_steps=model_steps,
                tool_calls=tool_calls,
                seen=seen,
                phase="response_ready",
                current_response=response,
                prior_response_ref=response.get("response_id"),
            )
            state = _cas_state(
                store,
                state,
                status="running",
                checkpoint=checkpoint,
                output=output,
                last_tool_result=state.get("last_tool_result"),
            )
            continue

        if phase != "response_ready":
            raise RunStateError(f"cannot advance durable loop from phase: {phase}")

        response = checkpoint.get("current_response")
        if not isinstance(response, dict):
            raise RunStateError("response_ready checkpoint is missing current_response")
        try:
            validate_model_response(response, expected_provider=adapter.provider)
        except Exception:
            output = _set_stop(
                output,
                reason="model_response_invalid",
                status="failed",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="failed",
                reason="model_response_invalid",
            )
        output["model_response"] = deepcopy(response)
        output["model_responses"] = [deepcopy(response)]

        if response["status"] != "completed":
            stop_status = "failed" if response["status"] == "failed" else "blocked"
            output = _set_stop(
                output,
                reason="model_response_not_completed",
                status=stop_status,
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status=stop_status,
                reason="model_response_not_completed",
            )

        proposals = response.get("tool_proposals") or []
        if not proposals:
            output["trace"]["status"] = "completed"
            output["result"]["status"] = "completed"
            output["result"]["finding"] = response["output_text"]
            output["agent_loop"] = {
                "status": "completed",
                "stop_reason": "final_model_response",
                "model_steps": model_steps,
                "tool_calls": tool_calls,
            }
            return _persist_stop(
                store,
                state,
                output,
                stop_status="completed",
                reason="final_model_response",
            )

        if model_steps >= checkpoint["max_model_steps"]:
            output = _set_stop(
                output,
                reason="model_step_budget_exhausted",
                status="blocked",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="blocked",
                reason="model_step_budget_exhausted",
            )

        response_id = response.get("response_id")
        if not isinstance(response_id, str) or not response_id:
            output = _set_stop(
                output,
                reason="continuation_reference_missing",
                status="blocked",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="blocked",
                reason="continuation_reference_missing",
            )

        if tool_calls + len(proposals) > checkpoint["max_tool_calls"]:
            output = _set_stop(
                output,
                reason="tool_call_budget_exhausted",
                status="blocked",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="blocked",
                reason="tool_call_budget_exhausted",
            )

        prepared: list[tuple[dict[str, Any], str]] = []
        batch_fingerprints: set[str] = set()
        for index, proposal in enumerate(proposals):
            request = _tool_request(
                proposal=proposal,
                run_id=state["run_id"],
                model_step=model_steps,
                proposal_index=index,
            )
            fingerprint = tool_request_fingerprint(request)
            if fingerprint in seen or fingerprint in batch_fingerprints:
                output = _set_stop(
                    output,
                    reason="repeated_tool_proposal",
                    status="blocked",
                    model_steps=model_steps,
                    tool_calls=tool_calls,
                )
                return _persist_stop(
                    store,
                    state,
                    output,
                    stop_status="blocked",
                    reason="repeated_tool_proposal",
                )
            batch_fingerprints.add(fingerprint)
            prepared.append((request, fingerprint))

        if len(prepared) > 1:
            for request, _ in prepared:
                registered = registry.get(request["tool_name"])
                if request["tool_name"] not in allowed_tools or registered is None:
                    output = _set_stop(
                        output,
                        reason="tool_batch_preflight_blocked",
                        status="blocked",
                        model_steps=model_steps,
                        tool_calls=tool_calls,
                    )
                    return _persist_stop(
                        store,
                        state,
                        output,
                        stop_status="blocked",
                        reason="tool_batch_preflight_blocked",
                    )
                if registered.definition["side_effect_class"] in CONSEQUENTIAL_CLASSES:
                    output = _set_stop(
                        output,
                        reason="consequential_multi_tool_batch_requires_serialization",
                        status="blocked",
                        model_steps=model_steps,
                        tool_calls=tool_calls,
                    )
                    return _persist_stop(
                        store,
                        state,
                        output,
                        stop_status="blocked",
                        reason="consequential_multi_tool_batch_requires_serialization",
                    )

        continuation_results: list[dict[str, Any]] = []
        round_results: list[dict[str, Any]] = []
        pending_request: dict[str, Any] | None = None
        pending_result: dict[str, Any] | None = None

        for request, fingerprint in prepared:
            tool_name = request["tool_name"]
            if tool_name not in allowed_tools:
                tool_result = _blocked_tool_result(request, "tool_not_exposed_to_model")
            else:
                context = dict(authorization_contexts.get(tool_name, {}))
                tool_result = _durable_tool_result(task, request, registry, context)

            tool_calls += 1
            seen.add(fingerprint)
            round_results.append(tool_result)
            output["tool_results"].append(tool_result)
            output["trace"]["events"].append(
                {
                    "event_type": "tool",
                    "status": tool_result["status"],
                    "reference": request["request_id"],
                    "summary": f"Durable bounded tool request for {tool_name} ended with status {tool_result['status']}.",
                }
            )
            approval_ref = tool_result.get("approval_ref")
            if approval_ref:
                output["trace"].setdefault("approval_refs", [])
                if approval_ref not in output["trace"]["approval_refs"]:
                    output["trace"]["approval_refs"].append(approval_ref)

            if tool_result["status"] == "executed":
                serialized, redacted = _serialize_tool_output(
                    tool_result, checkpoint["max_tool_result_chars"]
                )
                continuation_results.append(
                    {
                        "proposal_id": request["proposal_ref"],
                        "status": "executed",
                        "output": serialized,
                        "redacted": redacted,
                    }
                )
            elif tool_result["status"] == "approval_required":
                pending_request = request
                pending_result = tool_result

        statuses = {item["status"] for item in round_results}
        if "approval_required" in statuses:
            if len(round_results) != 1 or pending_request is None or pending_result is None:
                raise RunStateError("durable approval checkpoint requires a serialized single action")
            output["trace"]["status"] = "blocked"
            output["result"]["status"] = "blocked"
            output["result"]["owner_decision_required"] = True
            output["result"]["decision_request"] = "Review the pending durable tool approval request."
            output["agent_loop"] = {
                "status": "blocked",
                "stop_reason": "tool_approval_required",
                "model_steps": model_steps,
                "tool_calls": tool_calls,
            }
            next_checkpoint = _checkpoint(
                adapter=adapter,
                model=checkpoint["model"],
                registry=registry,
                allowed_tools=allowed_tools,
                max_model_steps=checkpoint["max_model_steps"],
                max_tool_calls=checkpoint["max_tool_calls"],
                max_tool_result_chars=checkpoint["max_tool_result_chars"],
                max_output_tokens=checkpoint.get("max_output_tokens"),
                model_steps=model_steps,
                tool_calls=tool_calls,
                seen=seen,
                phase="waiting_approval",
                current_response=response,
                prior_response_ref=response_id,
                pending_proposal_id=pending_request["proposal_ref"],
                pending_request_fingerprint=tool_request_fingerprint(pending_request),
            )
            waiting = _cas_state(
                store,
                state,
                status="waiting_approval",
                checkpoint=next_checkpoint,
                output=output,
                pending_action=_pending_action(
                    pending_request,
                    pending_result,
                    registry,
                    authorization_contexts.get(pending_request["tool_name"], {}),
                ),
                last_tool_result=pending_result,
            )
            return _summary(waiting, output)

        if "failed" in statuses:
            output = _set_stop(
                output,
                reason="tool_execution_or_verification_failed",
                status="failed",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="failed",
                reason="tool_execution_or_verification_failed",
            )

        if "blocked" in statuses:
            output = _set_stop(
                output,
                reason="tool_policy_blocked",
                status="blocked",
                model_steps=model_steps,
                tool_calls=tool_calls,
            )
            return _persist_stop(
                store,
                state,
                output,
                stop_status="blocked",
                reason="tool_policy_blocked",
            )

        next_checkpoint = _checkpoint(
            adapter=adapter,
            model=checkpoint["model"],
            registry=registry,
            allowed_tools=allowed_tools,
            max_model_steps=checkpoint["max_model_steps"],
            max_tool_calls=checkpoint["max_tool_calls"],
            max_tool_result_chars=checkpoint["max_tool_result_chars"],
            max_output_tokens=checkpoint.get("max_output_tokens"),
            model_steps=model_steps,
            tool_calls=tool_calls,
            seen=seen,
            phase="continuation_ready",
            current_response=response,
            prior_response_ref=response_id,
            continuation_tool_results=continuation_results,
        )
        state = _cas_state(
            store,
            state,
            status="running",
            checkpoint=next_checkpoint,
            output=output,
            last_tool_result=(round_results[-1] if round_results else None),
        )


def run_durable_agent_loop(
    task_input: dict[str, Any],
    adapter: ModelAdapter,
    registry: ToolRegistry,
    store: RunStore,
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
    """Start a bounded agent loop with durable checkpoints between loop phases.

    Durable mode is intentionally stricter than Stage 7: every consequential
    tool action must cross an exact durable approval checkpoint before execution.
    Analysis/read results and normalized model responses are checkpointed between
    continuation phases so a later process can resume without replaying an
    approved side effect.
    """
    max_model_steps = _positive_int("max_model_steps", max_model_steps)
    max_tool_calls = _positive_int("max_tool_calls", max_tool_calls, allow_zero=True)
    max_tool_result_chars = _positive_int(
        "max_tool_result_chars", max_tool_result_chars
    )
    authorization_contexts = authorization_contexts or {}
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
        output["durable_state"] = None
        output["agent_loop"] = {
            "status": output["trace"]["status"],
            "stop_reason": "model_not_called",
            "model_steps": 0,
            "tool_calls": 0,
        }
        return output

    try:
        validate_model_response(response, expected_provider=adapter.provider)
    except Exception:
        output["trace"]["status"] = "failed"
        output["result"]["status"] = "failed"
        output["result"]["finding"] = "Model response failed durable boundary validation."
        output["durable_state"] = None
        output["agent_loop"] = {
            "status": "failed",
            "stop_reason": "model_response_invalid",
            "model_steps": 1,
            "tool_calls": 0,
        }
        return output
    output["model_responses"] = [deepcopy(response)]
    output["tool_results"] = []
    checkpoint = _checkpoint(
        adapter=adapter,
        model=model,
        registry=registry,
        allowed_tools=allowed_tools,
        max_model_steps=max_model_steps,
        max_tool_calls=max_tool_calls,
        max_tool_result_chars=max_tool_result_chars,
        max_output_tokens=max_output_tokens,
        model_steps=1,
        tool_calls=0,
        seen=set(),
        phase="response_ready",
        current_response=response,
        prior_response_ref=response.get("response_id"),
    )
    state = _new_state(task_input["task"], output, checkpoint, store)
    return _advance(
        store,
        state,
        adapter,
        registry,
        authorization_contexts=authorization_contexts,
    )


def _mark_waiting_approval_stale(
    store: RunStore,
    state: RunState,
    reason: str,
) -> RunState:
    replacement = deepcopy(state)
    pending = replacement.get("pending_action")
    if not isinstance(pending, dict) or not isinstance(pending.get("approval"), dict):
        raise RunStateError("waiting durable loop is missing its approval packet")
    pending["approval"]["status"] = "stale"
    pending["approval"]["reason"] = reason
    pending["approval"]["resolved_at"] = None
    pending["approval"]["resolved_by"] = None
    pending["approval"]["approved_by"] = None
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()
    replacement["status"] = "waiting_approval"
    replacement["recovery_reason"] = reason
    return store.compare_and_swap(state["run_id"], state["revision"], replacement)


def _checkpoint_after_approved_action(
    store: RunStore,
    state: RunState,
    adapter: ModelAdapter,
    registry: ToolRegistry,
) -> RunState:
    checkpoint = _validate_checkpoint(state, adapter, registry)
    result = state.get("last_tool_result")
    if not isinstance(result, dict) or result.get("status") != "executed":
        raise RunStateError("resumed durable action did not produce an executed result")
    pending_proposal_id = checkpoint.get("pending_proposal_id")
    if not isinstance(pending_proposal_id, str) or not pending_proposal_id:
        raise RunStateError("durable approval checkpoint is missing pending proposal identity")
    serialized, redacted = _serialize_tool_output(
        result, checkpoint["max_tool_result_chars"]
    )
    trace = deepcopy(state.get("trace_snapshot") or {})
    trace["status"] = "running"
    # resume_tool_approval clears pending_action after successful execution, so
    # the exact proposal identity is preserved in the loop checkpoint instead.
    trace.setdefault("events", []).append(
        {
            "event_type": "tool",
            "status": "executed",
            "reference": result["request_id"],
            "summary": "Approved durable tool action executed and verified before model continuation.",
        }
    )
    result_snapshot = deepcopy(state.get("result_snapshot") or {})
    result_snapshot["owner_decision_required"] = False
    result_snapshot["decision_request"] = None
    result_snapshot["status"] = "partial"
    output = {"trace": trace, "result": result_snapshot}
    next_checkpoint = deepcopy(checkpoint)
    next_checkpoint["phase"] = "continuation_ready"
    next_checkpoint["continuation_tool_results"] = [
        {
            "proposal_id": pending_proposal_id,
            "status": "executed",
            "output": serialized,
            "redacted": redacted,
        }
    ]
    next_checkpoint["current_response"] = None
    next_checkpoint["pending_proposal_id"] = None
    next_checkpoint["pending_request_fingerprint"] = None
    return _cas_state(
        store,
        state,
        status="running",
        checkpoint=next_checkpoint,
        output=output,
        pending_action=None,
        last_tool_result=result,
    )


def resume_durable_agent_loop(
    store: RunStore,
    run_id: str,
    adapter: ModelAdapter,
    registry: ToolRegistry,
    *,
    authorization_contexts: dict[str, dict[str, Any]] | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a durable loop from approval, continuation, or recovery state."""
    authorization_contexts = authorization_contexts or {}
    state = store.load(run_id)
    if state is None:
        raise RunStateError(f"unknown run: {run_id}")

    if state["status"] == "executing":
        recovered = resume_tool_approval(
            store,
            run_id,
            registry,
            decision or {},
            current_authorization={},
            success_status="running",
        )
        return _summary(recovered)

    if state["status"] in {"completed", "failed", "blocked", "cancelled", "recovery_required"}:
        return _summary(state)

    if state["status"] == "waiting_approval":
        try:
            checkpoint = _validate_checkpoint(state, adapter, registry)
        except RunStateError as exc:
            stale = _mark_waiting_approval_stale(store, state, str(exc))
            return _summary(stale)
        if decision is None:
            return _summary(state)
        pending = state.get("pending_action")
        if not isinstance(pending, dict):
            raise RunStateError("waiting durable loop is missing pending_action")
        request = pending["tool_request"]
        if tool_request_fingerprint(request) != checkpoint.get(
            "pending_request_fingerprint"
        ):
            stale = _mark_waiting_approval_stale(
                store,
                state,
                "Pending request identity no longer matches the durable loop checkpoint.",
            )
            return _summary(stale)
        context = authorization_contexts.get(request["tool_name"], {})
        resumed = resume_tool_approval(
            store,
            run_id,
            registry,
            decision,
            current_authorization=context,
            success_status="running",
        )
        if resumed["status"] != "running":
            return _summary(resumed)
        state = _checkpoint_after_approved_action(
            store, resumed, adapter, registry
        )

    if state["status"] != "running":
        return _summary(state)

    _validate_checkpoint(state, adapter, registry)
    return _advance(
        store,
        state,
        adapter,
        registry,
        authorization_contexts=authorization_contexts,
    )