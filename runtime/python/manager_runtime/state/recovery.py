from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..agent_loop import _serialize_tool_output
from ..tools.base import ToolRegistry, tool_definition_fingerprint, tool_request_fingerprint
from ..tools.runtime import execute_tool_request
from .base import RunState, RunStateError, RunStore
from .checkpoint_versions import migrate_agent_loop_checkpoint

AGENT_LOOP_EXTENSION = "agent_loop"
RECOVERY_DECISIONS = {"confirmed_succeeded", "confirmed_not_executed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_resolution(run_id: str, resolution: dict[str, Any]) -> None:
    required = ("resolution_id", "run_id", "decision", "decided_by", "decided_at", "evidence")
    missing = [key for key in required if key not in resolution]
    if missing:
        raise RunStateError(f"recovery resolution missing required fields: {', '.join(missing)}")
    if resolution["run_id"] != run_id:
        raise RunStateError("recovery resolution run_id does not match the durable run")
    if resolution["decision"] not in RECOVERY_DECISIONS:
        raise RunStateError("recovery resolution decision is not normalized")
    for key in ("resolution_id", "decided_by", "decided_at", "evidence"):
        if not isinstance(resolution[key], str) or not resolution[key].strip():
            raise RunStateError(f"recovery resolution {key} must be non-empty text")
    if "redacted" in resolution and not isinstance(resolution["redacted"], bool):
        raise RunStateError("recovery resolution redacted must be boolean")


def _current_pending(state: RunState, registry: ToolRegistry) -> tuple[dict[str, Any], Any]:
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError("recovery_required state is missing pending_action")
    request = pending.get("tool_request")
    if not isinstance(request, dict):
        raise RunStateError("recovery_required state is missing its tool request")
    registered = registry.get(request.get("tool_name"))
    if registered is None:
        raise RunStateError("recovery cannot proceed because the registered tool is unavailable")
    stored_fingerprint = pending.get("tool_definition_fingerprint")
    if stored_fingerprint != tool_definition_fingerprint(registered.definition):
        raise RunStateError("recovery cannot proceed because the tool definition changed")
    return request, registered


def _replacement(state: RunState, resolution: dict[str, Any]) -> RunState:
    value = deepcopy(state)
    value["revision"] = state["revision"] + 1
    value["updated_at"] = _now()
    value["recovery_resolution"] = deepcopy(resolution)
    value["recovery_reason"] = None
    return value


def _trace_recovery(state: RunState, decision: str, evidence: str, *, running: bool) -> None:
    trace = state.get("trace_snapshot")
    if not isinstance(trace, dict):
        return
    trace["status"] = "running" if running else state["status"]
    trace.setdefault("events", []).append(
        {
            "event_type": "recovery",
            "status": decision,
            "summary": f"Recovery resolution recorded from explicit external evidence: {evidence}",
        }
    )


def _fresh_approval_after_confirmed_no_effect(
    state: RunState,
    registry: ToolRegistry,
    resolution: dict[str, Any],
    current_authorization: dict[str, Any],
) -> RunState:
    request, registered = _current_pending(state, registry)
    fresh_request = deepcopy(request)
    fresh_request["request_id"] = f"{request['request_id']}:recovery:{state['revision'] + 1}"

    task = deepcopy(state.get("task") or {})
    if not isinstance(task.get("classification"), dict):
        task["classification"] = {}
    task["classification"]["materiality"] = "material"

    context = {
        key: bool(current_authorization.get(key))
        for key in ("scope_authorized", "target_verified")
        if key in current_authorization
    }
    context["human_intent_confirmed"] = False
    result = execute_tool_request(task, fresh_request, registry, context)
    if result.get("status") != "approval_required" or not isinstance(result.get("approval"), dict):
        raise RunStateError(
            "fresh recovery approval could not be created; current scope/target authorization must be re-established first"
        )

    value = _replacement(state, resolution)
    value["status"] = "waiting_approval"
    value["pending_action"] = {
        "tool_request": deepcopy(fresh_request),
        "approval": deepcopy(result["approval"]),
        "tool_definition_fingerprint": tool_definition_fingerprint(registered.definition),
        "authorization_context": context,
    }
    value["last_tool_result"] = deepcopy(result)

    extensions = value.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(AGENT_LOOP_EXTENSION), dict):
        checkpoint = migrate_agent_loop_checkpoint(extensions[AGENT_LOOP_EXTENSION])
        checkpoint["phase"] = "waiting_approval"
        checkpoint["pending_request_fingerprint"] = tool_request_fingerprint(fresh_request)
        value["extensions"][AGENT_LOOP_EXTENSION] = checkpoint

    approval = value["pending_action"]["approval"]
    trace = value.get("trace_snapshot")
    if isinstance(trace, dict):
        trace["status"] = "blocked"
        trace.setdefault("approval_refs", [])
        if approval["approval_id"] not in trace["approval_refs"]:
            trace["approval_refs"].append(approval["approval_id"])
        trace.setdefault("events", []).append(
            {
                "event_type": "recovery",
                "status": "confirmed_not_executed",
                "summary": "External evidence confirmed the prior action did not occur; a fresh approval checkpoint was created.",
            }
        )
    result_snapshot = value.get("result_snapshot")
    if isinstance(result_snapshot, dict):
        result_snapshot["status"] = "blocked"
        result_snapshot["owner_decision_required"] = True
        result_snapshot["decision_request"] = "Review the fresh recovery approval request."
    return value


def _confirmed_success(
    state: RunState,
    registry: ToolRegistry,
    resolution: dict[str, Any],
) -> RunState:
    request, registered = _current_pending(state, registry)
    approval = state["pending_action"].get("approval") or {}
    redacted = bool(resolution.get("redacted", registered.definition.get("sensitive_output", False)))
    result: dict[str, Any] = {
        "request_id": request["request_id"],
        "tool_name": request["tool_name"],
        "status": "executed",
        "side_effect_class": registered.definition["side_effect_class"],
        "decision_reason": "recovery_confirmed_external_success",
        "approval_ref": approval.get("approval_id"),
        "error": None,
        "redacted": redacted,
        "verification": {
            "status": "pass",
            "details": resolution["evidence"],
        },
    }
    if "output" in resolution and not redacted:
        result["output"] = deepcopy(resolution["output"])

    value = _replacement(state, resolution)
    value["last_tool_result"] = result
    value["pending_action"] = None

    extensions = value.get("extensions")
    checkpoint = None
    if isinstance(extensions, dict) and isinstance(extensions.get(AGENT_LOOP_EXTENSION), dict):
        checkpoint = migrate_agent_loop_checkpoint(extensions[AGENT_LOOP_EXTENSION])

    if checkpoint is None:
        value["status"] = "completed"
        _trace_recovery(value, "confirmed_succeeded", resolution["evidence"], running=False)
        return value

    proposal_id = checkpoint.get("pending_proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        raise RunStateError("durable loop recovery is missing pending proposal identity")
    expected = checkpoint.get("pending_request_fingerprint")
    if expected != tool_request_fingerprint(request):
        raise RunStateError("durable loop recovery request identity no longer matches its checkpoint")

    serialized, continuation_redacted = _serialize_tool_output(
        result, int(checkpoint["max_tool_result_chars"])
    )
    checkpoint["phase"] = "continuation_ready"
    checkpoint["continuation_tool_results"] = [
        {
            "proposal_id": proposal_id,
            "status": "executed",
            "output": serialized,
            "redacted": continuation_redacted,
        }
    ]
    checkpoint["current_response"] = None
    checkpoint["pending_proposal_id"] = None
    checkpoint["pending_request_fingerprint"] = None
    value["extensions"][AGENT_LOOP_EXTENSION] = checkpoint
    value["status"] = "running"
    _trace_recovery(value, "confirmed_succeeded", resolution["evidence"], running=True)
    result_snapshot = value.get("result_snapshot")
    if isinstance(result_snapshot, dict):
        result_snapshot["status"] = "partial"
        result_snapshot["owner_decision_required"] = False
        result_snapshot["decision_request"] = None
    return value


def resolve_recovery_required(
    store: RunStore,
    run_id: str,
    registry: ToolRegistry,
    resolution: dict[str, Any],
    *,
    current_authorization: dict[str, Any] | None = None,
) -> RunState:
    """Resolve `recovery_required` only from explicit external evidence.

    `confirmed_succeeded` records an externally verified successful effect and,
    for a durable agent loop, returns the checkpoint to `continuation_ready`.
    `confirmed_not_executed` creates a fresh approval checkpoint with a new
    request/approval identity. `cancelled` terminates the run without retrying.
    """
    _validate_resolution(run_id, resolution)
    state = store.load(run_id)
    if state is None:
        raise RunStateError(f"unknown run: {run_id}")
    if state["status"] != "recovery_required":
        raise RunStateError(f"run is not recovery_required: {state['status']}")

    decision = resolution["decision"]
    if decision == "confirmed_succeeded":
        value = _confirmed_success(state, registry, resolution)
    elif decision == "confirmed_not_executed":
        value = _fresh_approval_after_confirmed_no_effect(
            state,
            registry,
            resolution,
            current_authorization or {},
        )
    else:
        value = _replacement(state, resolution)
        value["status"] = "cancelled"
        trace = value.get("trace_snapshot")
        if isinstance(trace, dict):
            trace["status"] = "cancelled"
            trace.setdefault("events", []).append(
                {
                    "event_type": "recovery",
                    "status": "cancelled",
                    "summary": "Recovery was explicitly cancelled; Manager did not retry the uncertain action.",
                }
            )

    return store.compare_and_swap(run_id, state["revision"], value)
