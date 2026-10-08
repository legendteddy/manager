from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..tools.base import ToolRegistry, tool_definition_fingerprint, tool_request_fingerprint
from ..tools.runtime import execute_tool_request
from .base import RunState, RunStateError, RunStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _authorization_flag(source: dict[str, Any], key: str) -> bool:
    if key not in source:
        return False
    value = source[key]
    if not isinstance(value, bool):
        raise RunStateError(f"current authorization field {key!r} must be boolean")
    return value


def _safe_authorization_context(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value or {}
    if not isinstance(source, dict):
        raise RunStateError("authorization context must be an object")
    result: dict[str, Any] = {}
    for key in ("scope_authorized", "human_intent_confirmed", "target_verified"):
        if key not in source:
            continue
        raw = source[key]
        if not isinstance(raw, bool):
            raise RunStateError(f"authorization field {key!r} must be boolean")
        result[key] = raw
    return result


def checkpoint_pending_tool_approval(
    task: dict[str, Any],
    request: dict[str, Any],
    tool_result: dict[str, Any],
    registry: ToolRegistry,
    store: RunStore,
    *,
    authorization_context: dict[str, Any] | None = None,
    trace_snapshot: dict[str, Any] | None = None,
    result_snapshot: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
) -> RunState:
    """Persist an approval interruption before the run leaves the current process."""
    if tool_result.get("status") != "approval_required":
        raise RunStateError("only approval_required tool results can be checkpointed")
    approval = tool_result.get("approval")
    if not isinstance(approval, dict):
        raise RunStateError("approval_required result must include its approval packet")
    registered = registry.get(request["tool_name"])
    if registered is None:
        raise RunStateError("cannot checkpoint an unregistered tool")
    now = _now()
    state: RunState = {
        "run_id": request["run_id"],
        "task_id": task["task_id"],
        "status": "waiting_approval",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "task": deepcopy(task),
        "pending_action": {
            "tool_request": deepcopy(request),
            "approval": deepcopy(approval),
            "tool_definition_fingerprint": tool_definition_fingerprint(
                registered.definition
            ),
            "authorization_context": _safe_authorization_context(
                authorization_context
            ),
        },
        "last_tool_result": deepcopy(tool_result),
        "trace_snapshot": deepcopy(trace_snapshot),
        "result_snapshot": deepcopy(result_snapshot),
        "recovery_reason": None,
        "extensions": deepcopy(extensions or {}),
    }
    return store.create(state)


def _mark_stale(store: RunStore, state: RunState, reason: str) -> RunState:
    replacement = deepcopy(state)
    pending = replacement["pending_action"]
    approval = pending["approval"]
    approval["status"] = "stale"
    approval["reason"] = reason
    approval["resolved_at"] = None
    approval["resolved_by"] = None
    approval["approved_by"] = None
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()
    replacement["status"] = "waiting_approval"
    return store.compare_and_swap(state["run_id"], state["revision"], replacement)


def resume_tool_approval(
    store: RunStore,
    run_id: str,
    registry: ToolRegistry,
    decision: dict[str, Any],
    *,
    current_authorization: dict[str, Any],
    current_request: dict[str, Any] | None = None,
    success_status: str = "completed",
) -> RunState:
    """Resolve a persisted approval and resume exactly one tool action.

    The function deliberately does not auto-retry a run found in `executing`.
    Such a state may mean the external side effect happened before the process
    crashed, so the run is moved to `recovery_required` instead.

    `success_status` defaults to `completed`. Durable multi-step workflows may
    use `running`; in that mode an executed result remains durably `executing`
    with its pending action until the outer loop atomically checkpoints the
    continuation. This closes the post-effect/pre-continuation crash window.
    """
    if success_status not in {"completed", "running"}:
        raise RunStateError("success_status must be completed or running")
    if not isinstance(current_authorization, dict):
        raise RunStateError("current authorization must be an object")

    state = store.load(run_id)
    if state is None:
        raise RunStateError(f"unknown run: {run_id}")

    if state["status"] == "executing":
        recovery = deepcopy(state)
        recovery["status"] = "recovery_required"
        recovery["recovery_reason"] = (
            "Run was interrupted after durable execution intent was recorded. "
            "Do not retry the external action automatically; reconcile its real outcome first."
        )
        recovery["revision"] = state["revision"] + 1
        recovery["updated_at"] = _now()
        return store.compare_and_swap(run_id, state["revision"], recovery)

    if state["status"] != "waiting_approval":
        raise RunStateError(f"run is not waiting for approval: {state['status']}")

    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError("waiting run is missing pending_action")
    stored_request = pending["tool_request"]
    request = deepcopy(current_request or stored_request)
    approval = pending["approval"]

    if approval.get("status") != "pending":
        raise RunStateError(
            "pending approval packet is no longer fresh; create a new approval checkpoint"
        )
    if decision.get("approval_id") != approval.get("approval_id"):
        raise RunStateError("approval decision does not match the pending approval")
    if decision.get("decision") not in {"approved", "rejected"}:
        raise RunStateError("approval decision must be approved or rejected")
    if not decision.get("decided_by") or not decision.get("decided_at"):
        raise RunStateError("approval decision requires decided_by and decided_at")

    if tool_request_fingerprint(request) != tool_request_fingerprint(stored_request):
        return _mark_stale(
            store,
            state,
            "The requested tool target or arguments changed after the approval checkpoint.",
        )

    registered = registry.get(request["tool_name"])
    if registered is None:
        return _mark_stale(store, state, "The registered tool is no longer available.")
    if (
        tool_definition_fingerprint(registered.definition)
        != pending["tool_definition_fingerprint"]
    ):
        return _mark_stale(
            store,
            state,
            "The registered tool definition or version changed after the approval checkpoint.",
        )

    resolved = deepcopy(approval)
    resolved["status"] = decision["decision"]
    resolved["resolved_at"] = decision["decided_at"]
    resolved["resolved_by"] = decision["decided_by"]
    resolved["approved_by"] = (
        decision["decided_by"] if decision["decision"] == "approved" else None
    )

    if decision["decision"] == "rejected":
        cancelled = deepcopy(state)
        cancelled["status"] = "cancelled"
        cancelled["pending_action"]["approval"] = resolved
        cancelled["revision"] = state["revision"] + 1
        cancelled["updated_at"] = _now()
        return store.compare_and_swap(run_id, state["revision"], cancelled)

    side_effect_class = registered.definition["side_effect_class"]
    scope_authorized = _authorization_flag(current_authorization, "scope_authorized")
    target_verified = _authorization_flag(current_authorization, "target_verified")
    _authorization_flag(current_authorization, "human_intent_confirmed")

    if side_effect_class != "analysis" and not scope_authorized:
        raise RunStateError("current scope authorization is required before resume")
    if (
        side_effect_class in {"external_commitment", "sensitive_destructive"}
        and request.get("target")
        and not target_verified
    ):
        raise RunStateError("current target verification is required before resume")

    executing = deepcopy(state)
    executing["status"] = "executing"
    executing["pending_action"]["approval"] = resolved
    executing["revision"] = state["revision"] + 1
    executing["updated_at"] = _now()
    executing = store.compare_and_swap(run_id, state["revision"], executing)

    authorization = _safe_authorization_context(current_authorization)
    authorization["approval"] = resolved
    result = execute_tool_request(executing["task"], request, registry, authorization)

    final_state = deepcopy(executing)
    final_state["last_tool_result"] = result
    final_state["revision"] = executing["revision"] + 1
    final_state["updated_at"] = _now()
    if result["status"] == "executed":
        if success_status == "running":
            # Preserve the durable execution marker and pending action until the
            # outer agent-loop checkpoint records the continuation-ready state.
            final_state["status"] = "executing"
        else:
            final_state["status"] = "completed"
            final_state["pending_action"] = None
    elif result["status"] == "approval_required":
        final_state["status"] = "waiting_approval"
        final_state["pending_action"]["approval"] = result["approval"]
    else:
        final_state["status"] = "failed"
        final_state["pending_action"] = None
    return store.compare_and_swap(run_id, executing["revision"], final_state)
