from __future__ import annotations

from typing import Any

from ..tools.base import ToolRegistry, tool_request_fingerprint
from .approvals_core import checkpoint_pending_tool_approval
from .approvals_core import resume_tool_approval as _resume_tool_approval_core
from .base import OperationConflict, RunState, RunStateError, RunStore, require_coordinated_store

__all__ = ["checkpoint_pending_tool_approval", "resume_tool_approval"]


def _pending_request(state: RunState) -> dict[str, Any]:
    pending = state.get("pending_action")
    if not isinstance(pending, dict):
        raise RunStateError(f"{state['status']} run is missing pending_action")
    request = pending.get("tool_request")
    if not isinstance(request, dict):
        raise RunStateError(f"{state['status']} run is missing its tool request")
    return request


def _validate_existing_operation_identity(store: RunStore, state: RunState) -> None:
    """Reject cross-run/corrupted durable operation evidence before reuse.

    Normal operation creation already enforces this binding transactionally.
    This check protects the restart path, which can encounter historical or
    externally corrupted rows before ``begin_operation`` runs again.
    """
    coordinated = require_coordinated_store(store)
    request = _pending_request(state)
    operation_id = request.get("request_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise RunStateError("executing run has an invalid durable operation identity")
    operation = coordinated.load_operation(operation_id)
    if operation is None:
        return

    expected_fingerprint = tool_request_fingerprint(request)
    if (
        operation.run_id != state["run_id"]
        or operation.request_fingerprint != expected_fingerprint
    ):
        raise OperationConflict(
            "durable operation evidence does not match the executing run/request"
        )

    if operation.status == "confirmed":
        result = operation.result
        if (
            not isinstance(result, dict)
            or result.get("status") != "executed"
            or result.get("request_id") != operation_id
            or result.get("tool_name") != request.get("tool_name")
        ):
            raise OperationConflict(
                "confirmed durable operation evidence is inconsistent with its request identity"
            )


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
    state = store.load(run_id)
    if state is not None and state.get("status") == "executing":
        _validate_existing_operation_identity(store, state)

    return _resume_tool_approval_core(
        store,
        run_id,
        registry,
        decision,
        current_authorization=current_authorization,
        current_request=current_request,
        success_status=success_status,
        worker_id=worker_id,
        lease_ttl_seconds=lease_ttl_seconds,
    )
