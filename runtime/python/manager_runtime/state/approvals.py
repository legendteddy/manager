from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..tools.base import ToolRegistry, tool_definition_fingerprint, tool_request_fingerprint
from ..tools.runtime import execute_tool_request
from .base import (
    DurableOperation,
    RunLease,
    RunState,
    RunStateError,
    RunStore,
    require_coordinated_store,
)


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


def _worker_identity(value: str | None) -> str:
    if value is None:
        return f"worker:{uuid.uuid4().hex}"
    if not isinstance(value, str) or not value.strip():
        raise RunStateError("worker_id must be non-empty text when supplied")
    if len(value) > 256:
        raise RunStateError("worker_id is too long")
    return value


def _serialize_recorded_tool_output(
    result: dict[str, Any], max_chars: int
) -> tuple[str, bool]:
    if result.get("redacted"):
        return "Tool executed successfully; output withheld from the model by Manager policy.", True
    try:
        text = json.dumps(
            result.get("output"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
    except (TypeError, ValueError):
        text = str(result.get("output"))
    marker = "...[truncated by Manager]"
    if len(text) > max_chars:
        if max_chars <= len(marker):
            text = marker[:max_chars]
        else:
            text = text[: max_chars - len(marker)] + marker
    return text, False


def _recover_recorded_loop_execution(
    store: RunStore,
    state: RunState,
    registry: ToolRegistry,
    *,
    lease: RunLease | None = None,
) -> RunState | None:
    """Promote a durably recorded verified effect to continuation-ready.

    This path is used only when the tool result itself was persisted as executed
    but the process disappeared before the outer agent loop could checkpoint its
    continuation. No tool is re-executed here.
    """
    result = state.get("last_tool_result")
    extensions = state.get("extensions")
    if not isinstance(result, dict) or result.get("status") != "executed":
        return None
    if not isinstance(extensions, dict):
        return None
    checkpoint = extensions.get("agent_loop")
    if not isinstance(checkpoint, dict) or checkpoint.get("phase") != "waiting_approval":
        return None

    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError("recorded loop execution is missing pending_action")
    request = pending.get("tool_request")
    if not isinstance(request, dict):
        raise RunStateError("recorded loop execution is missing its tool request")
    expected_request = checkpoint.get("pending_request_fingerprint")
    if expected_request != tool_request_fingerprint(request):
        raise RunStateError(
            "recorded loop execution request identity no longer matches checkpoint"
        )

    registered = registry.get(request.get("tool_name"))
    if registered is None:
        raise RunStateError("recorded loop execution tool is no longer registered")
    if pending.get("tool_definition_fingerprint") != tool_definition_fingerprint(
        registered.definition
    ):
        raise RunStateError(
            "recorded loop execution tool definition changed before continuation"
        )

    proposal_id = checkpoint.get("pending_proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id:
        raise RunStateError("recorded loop execution is missing pending proposal identity")
    max_chars = checkpoint.get("max_tool_result_chars")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise RunStateError("recorded loop execution has invalid result-size budget")

    serialized, redacted = _serialize_recorded_tool_output(result, max_chars)
    replacement = deepcopy(state)
    next_checkpoint = deepcopy(checkpoint)
    next_checkpoint["phase"] = "continuation_ready"
    next_checkpoint["continuation_tool_results"] = [
        {
            "proposal_id": proposal_id,
            "status": "executed",
            "output": serialized,
            "redacted": redacted,
        }
    ]
    next_checkpoint["current_response"] = None
    next_checkpoint["pending_proposal_id"] = None
    next_checkpoint["pending_request_fingerprint"] = None
    replacement["extensions"]["agent_loop"] = next_checkpoint
    replacement["status"] = "running"
    replacement["pending_action"] = None
    replacement["recovery_reason"] = None
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()

    trace = replacement.get("trace_snapshot")
    if isinstance(trace, dict):
        trace["status"] = "running"
        trace.setdefault("events", []).append(
            {
                "event_type": "recovery",
                "status": "recorded_execution_resumed",
                "reference": result.get("request_id"),
                "summary": "A verified tool result was already durable; Manager resumed continuation without re-executing the side effect.",
            }
        )
    result_snapshot = replacement.get("result_snapshot")
    if isinstance(result_snapshot, dict):
        result_snapshot["status"] = "partial"
        result_snapshot["owner_decision_required"] = False
        result_snapshot["decision_request"] = None

    if lease is None:
        return store.compare_and_swap(state["run_id"], state["revision"], replacement)
    coordinated = require_coordinated_store(store)
    return coordinated.fenced_compare_and_swap(
        state["run_id"], state["revision"], replacement, lease=lease
    )


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


def _fenced_mark_stale(
    store: RunStore,
    state: RunState,
    reason: str,
    *,
    lease: RunLease,
) -> RunState:
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
    coordinated = require_coordinated_store(store)
    return coordinated.fenced_compare_and_swap(
        state["run_id"], state["revision"], replacement, lease=lease
    )


def _pending_request(state: RunState) -> dict[str, Any]:
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError(f"{state['status']} run is missing pending_action")
    request = pending.get("tool_request")
    if not isinstance(request, dict):
        raise RunStateError(f"{state['status']} run is missing its tool request")
    return request


def _recovery_required(
    store: RunStore,
    state: RunState,
    *,
    lease: RunLease,
    reason: str,
    last_tool_result: dict[str, Any] | None = None,
) -> RunState:
    replacement = deepcopy(state)
    replacement["status"] = "recovery_required"
    replacement["recovery_reason"] = reason
    if last_tool_result is not None:
        replacement["last_tool_result"] = deepcopy(last_tool_result)
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()
    coordinated = require_coordinated_store(store)
    return coordinated.fenced_compare_and_swap(
        state["run_id"], state["revision"], replacement, lease=lease
    )


def _persist_confirmed_operation(
    store: RunStore,
    state: RunState,
    result: dict[str, Any],
    *,
    lease: RunLease,
    success_status: str,
) -> RunState:
    replacement = deepcopy(state)
    replacement["last_tool_result"] = deepcopy(result)
    replacement["revision"] = state["revision"] + 1
    replacement["updated_at"] = _now()
    synthetic_running_view = False
    if success_status == "running":
        # Keep durable state executing until the outer durable-loop CAS commits
        # continuation_ready. A crash in this window is recoverable from the
        # confirmed operation result without another external execution.
        replacement["status"] = "executing"
        synthetic_running_view = True
    else:
        replacement["status"] = "completed"
        replacement["pending_action"] = None
        replacement["recovery_reason"] = None

    coordinated = require_coordinated_store(store)
    persisted = coordinated.fenced_compare_and_swap(
        state["run_id"], state["revision"], replacement, lease=lease
    )
    if synthetic_running_view:
        returned = deepcopy(persisted)
        returned["status"] = "running"
        return returned
    return persisted


def _reuse_existing_operation(
    store: RunStore,
    state: RunState,
    registry: ToolRegistry,
    operation: DurableOperation | None,
    *,
    lease: RunLease,
    success_status: str,
) -> RunState:
    if operation is not None and operation.status == "confirmed":
        result = operation.result
        if not isinstance(result, dict) or result.get("status") != "executed":
            raise RunStateError(
                "confirmed durable operation is missing its executed result"
            )
        return _persist_confirmed_operation(
            store,
            state,
            result,
            lease=lease,
            success_status=success_status,
        )

    request = _pending_request(state)
    status = "missing" if operation is None else operation.status
    reason = (
        "Run execution cannot be proven safe to replay. "
        f"Durable operation {request.get('request_id')!r} is {status!r}; "
        "reconcile external evidence before any new consequential execution."
    )
    return _recovery_required(store, state, lease=lease, reason=reason)


def resume_tool_approval(
    store: RunStore,
    run_id: str,
    registry: ToolRegistry,
    decision: dict[str, Any],
    *,
    current_authorization: dict[str, Any],
    current_request: dict[str, Any] | None = None,
    success_status: str = "completed",
    worker_id: str | None = None,
    lease_ttl_seconds: int = 30,
) -> RunState:
    """Resolve one durable approval under a fenced execution lease.

    Consequential execution requires a coordinated store. The original request
    ID is also the durable operation ID, so retries and worker reassignment do
    not mint a new external-operation identity merely because a response was
    lost. A previously ``started`` or ``outcome_unknown`` operation is never
    executed again automatically. A ``confirmed`` operation may be replayed
    only as local evidence; the external tool is not called again.
    """
    if success_status not in {"completed", "running"}:
        raise RunStateError("success_status must be completed or running")
    if not isinstance(current_authorization, dict):
        raise RunStateError("current authorization must be an object")

    coordinated = require_coordinated_store(store)
    owner_id = _worker_identity(worker_id)
    lease = coordinated.acquire_lease(
        run_id, owner_id, ttl_seconds=lease_ttl_seconds
    )

    try:
        state = coordinated.load(run_id)
        if state is None:
            raise RunStateError(f"unknown run: {run_id}")

        if state["status"] == "executing":
            if success_status == "running":
                recorded = _recover_recorded_loop_execution(
                    coordinated, state, registry, lease=lease
                )
                if recorded is not None:
                    return recorded
            request = _pending_request(state)
            operation = coordinated.load_operation(request["request_id"])
            return _reuse_existing_operation(
                coordinated,
                state,
                registry,
                operation,
                lease=lease,
                success_status=success_status,
            )

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
            return _fenced_mark_stale(
                coordinated,
                state,
                "The requested tool target or arguments changed after the approval checkpoint.",
                lease=lease,
            )

        registered = registry.get(request["tool_name"])
        if registered is None:
            return _fenced_mark_stale(
                coordinated,
                state,
                "The registered tool is no longer available.",
                lease=lease,
            )
        if (
            tool_definition_fingerprint(registered.definition)
            != pending["tool_definition_fingerprint"]
        ):
            return _fenced_mark_stale(
                coordinated,
                state,
                "The registered tool definition or version changed after the approval checkpoint.",
                lease=lease,
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
            return coordinated.fenced_compare_and_swap(
                run_id, state["revision"], cancelled, lease=lease
            )

        side_effect_class = registered.definition["side_effect_class"]
        scope_authorized = _authorization_flag(
            current_authorization, "scope_authorized"
        )
        target_verified = _authorization_flag(
            current_authorization, "target_verified"
        )
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
        executing = coordinated.fenced_compare_and_swap(
            run_id, state["revision"], executing, lease=lease
        )

        operation = coordinated.begin_operation(
            lease=lease,
            operation_id=request["request_id"],
            request_fingerprint=tool_request_fingerprint(request),
        )
        if not operation.claimed:
            return _reuse_existing_operation(
                coordinated,
                executing,
                registry,
                operation,
                lease=lease,
                success_status=success_status,
            )

        authorization = _safe_authorization_context(current_authorization)
        authorization["approval"] = resolved

        # The execution guard spans only the actual adapter/verification call.
        # For SQLite it holds local writer ownership, preventing another process
        # from transferring the lease or resolving recovery while this worker is
        # actively in the uncertain side-effect window.
        with coordinated.execution_guard(lease, ttl_seconds=lease_ttl_seconds):
            result = execute_tool_request(
                executing["task"], request, registry, authorization
            )

        if result["status"] == "executed":
            coordinated.finish_operation(
                operation,
                lease=lease,
                status="confirmed",
                result=result,
            )
            return _persist_confirmed_operation(
                coordinated,
                executing,
                result,
                lease=lease,
                success_status=success_status,
            )

        if result["status"] == "failed":
            # Once the adapter was invoked, a failure response does not prove the
            # real-world side effect did not happen. Verification failure is also
            # evidence of uncertainty, not permission to retry.
            coordinated.finish_operation(
                operation,
                lease=lease,
                status="outcome_unknown",
                result=result,
            )
            return _recovery_required(
                coordinated,
                executing,
                lease=lease,
                reason=(
                    "Consequential tool execution did not produce a verified outcome. "
                    "The external effect may have occurred; do not retry automatically."
                ),
                last_tool_result=result,
            )

        coordinated.finish_operation(
            operation,
            lease=lease,
            status="not_executed",
            result=result,
        )
        final_state = deepcopy(executing)
        final_state["last_tool_result"] = deepcopy(result)
        final_state["revision"] = executing["revision"] + 1
        final_state["updated_at"] = _now()
        if result["status"] == "approval_required":
            final_state["status"] = "waiting_approval"
            final_state["pending_action"]["approval"] = result["approval"]
        else:
            final_state["status"] = "failed"
            final_state["pending_action"] = None
        return coordinated.fenced_compare_and_swap(
            run_id, executing["revision"], final_state, lease=lease
        )
    finally:
        try:
            coordinated.release_lease(lease)
        except RunStateError:
            # A stale worker must not mask the primary result merely because its
            # lease expired or was fenced off while unwinding.
            pass
