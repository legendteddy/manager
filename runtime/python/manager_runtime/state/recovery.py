from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..tools.base import ToolRegistry, tool_request_fingerprint
from .base import OperationConflict, RunState, RunStateError, RunStore, require_coordinated_store
from .recovery_core import RECOVERY_DECISIONS
from .recovery_core import resolve_recovery_required as _resolve_recovery_required_core
from .recovery_core import _now, _recovery_worker_identity, _validate_resolution

__all__ = ["resolve_recovery_required"]


def _pending_request(state: RunState) -> dict[str, Any]:
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError("recovery_required state is missing pending_action")
    request = pending.get("tool_request")
    if not isinstance(request, dict):
        raise RunStateError("recovery_required state is missing its tool request")
    return request


def _reject_conflicting_terminal_operation(store: RunStore, state: RunState) -> None:
    """Require uncertain ledger evidence before an evidence-changing recovery.

    Atomic recovery makes a terminal operation plus ``recovery_required`` run an
    inconsistent state. Silently accepting a second, possibly different recovery
    payload would let the run row diverge from the durable operation ledger.
    """
    coordinated = require_coordinated_store(store)
    request = _pending_request(state)
    operation_id = request.get("request_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise RunStateError("recovery_required run has an invalid operation identity")
    operation = coordinated.load_operation(operation_id)
    if operation is None:
        return
    if (
        operation.run_id != state["run_id"]
        or operation.request_fingerprint != tool_request_fingerprint(request)
    ):
        raise OperationConflict(
            "recovery operation evidence does not match the pending action"
        )
    if operation.status not in {"started", "outcome_unknown"}:
        raise OperationConflict(
            "recovery_required run conflicts with an already resolved durable operation"
        )


def _cancel_recovery_without_tool_dependency(
    store: RunStore,
    run_id: str,
    resolution: dict[str, Any],
    *,
    worker_id: str | None,
    lease_ttl_seconds: int,
) -> RunState:
    """Cancel an uncertain run without requiring a live historical tool adapter."""
    coordinated = require_coordinated_store(store)
    lease = coordinated.acquire_lease(
        run_id,
        _recovery_worker_identity(worker_id),
        ttl_seconds=lease_ttl_seconds,
    )
    try:
        state = coordinated.load(run_id)
        if state is None:
            raise RunStateError(f"unknown run: {run_id}")
        if state["status"] != "recovery_required":
            raise RunStateError(f"run is not recovery_required: {state['status']}")

        replacement = deepcopy(state)
        replacement["revision"] = state["revision"] + 1
        replacement["updated_at"] = _now()
        replacement["recovery_resolution"] = deepcopy(resolution)
        replacement["recovery_reason"] = None
        replacement["status"] = "cancelled"
        trace = replacement.get("trace_snapshot")
        if isinstance(trace, dict):
            trace["status"] = "cancelled"
            trace.setdefault("events", []).append(
                {
                    "event_type": "recovery",
                    "status": "cancelled",
                    "summary": "Recovery was explicitly cancelled; Manager did not retry the uncertain action.",
                }
            )

        # Deliberately do not rewrite a started/outcome_unknown operation row.
        # Cancellation means Manager will not retry; it is not evidence that the
        # external effect succeeded or failed.
        return coordinated.fenced_compare_and_swap(
            run_id,
            state["revision"],
            replacement,
            lease=lease,
        )
    finally:
        try:
            coordinated.release_lease(lease)
        except RunStateError:
            pass


def resolve_recovery_required(
    store: RunStore,
    run_id: str,
    registry: ToolRegistry,
    resolution: dict[str, Any],
    *,
    current_authorization: dict[str, Any] | None = None,
    worker_id: str | None = None,
    lease_ttl_seconds: int = 30,
) -> RunState:
    _validate_resolution(run_id, resolution)
    if resolution["decision"] == "cancelled":
        return _cancel_recovery_without_tool_dependency(
            store,
            run_id,
            resolution,
            worker_id=worker_id,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    if resolution["decision"] not in RECOVERY_DECISIONS:
        raise RunStateError("recovery resolution decision is not normalized")
    state = store.load(run_id)
    if state is not None and state.get("status") == "recovery_required":
        _reject_conflicting_terminal_operation(store, state)

    return _resolve_recovery_required_core(
        store,
        run_id,
        registry,
        resolution,
        current_authorization=current_authorization,
        worker_id=worker_id,
        lease_ttl_seconds=lease_ttl_seconds,
    )
