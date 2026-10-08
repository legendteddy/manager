from __future__ import annotations

from typing import Any

from .base import RunState, RunStateError

RUN_STATUSES = {
    "running",
    "waiting_approval",
    "executing",
    "completed",
    "blocked",
    "failed",
    "cancelled",
    "recovery_required",
}

ALLOWED_TRANSITIONS = {
    "running": {"running", "waiting_approval", "completed", "blocked", "failed", "recovery_required"},
    "waiting_approval": {"waiting_approval", "executing", "cancelled", "recovery_required"},
    "executing": {"executing", "running", "waiting_approval", "completed", "failed", "recovery_required"},
    "recovery_required": {"running", "waiting_approval", "completed", "cancelled", "recovery_required"},
    "completed": set(),
    "blocked": set(),
    "failed": set(),
    "cancelled": set(),
}


def validate_run_state_shape(state: RunState) -> None:
    required = ("run_id", "task_id", "status", "revision", "created_at", "updated_at")
    missing = [key for key in required if key not in state]
    if missing:
        raise RunStateError(f"run state missing required fields: {', '.join(missing)}")

    if not isinstance(state["run_id"], str) or not state["run_id"]:
        raise RunStateError("run state requires a non-empty run_id")
    if not isinstance(state["task_id"], str) or not state["task_id"]:
        raise RunStateError("run state requires a non-empty task_id")
    if state["status"] not in RUN_STATUSES:
        raise RunStateError(f"run state has unknown status: {state['status']!r}")
    if not isinstance(state["revision"], int) or isinstance(state["revision"], bool) or state["revision"] < 1:
        raise RunStateError("run state revision must be a positive integer")
    for key in ("created_at", "updated_at"):
        if not isinstance(state[key], str) or not state[key]:
            raise RunStateError(f"run state {key} must be non-empty text")

    pending = state.get("pending_action")
    if state["status"] in {"waiting_approval", "executing"} and not isinstance(pending, dict):
        raise RunStateError(f"{state['status']} state requires pending_action")
    if state["status"] == "recovery_required":
        reason = state.get("recovery_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise RunStateError("recovery_required state requires recovery_reason")

    extensions = state.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        raise RunStateError("run state extensions must be an object when present")
    resolution = state.get("recovery_resolution")
    if resolution is not None and not isinstance(resolution, dict):
        raise RunStateError("recovery_resolution must be an object when present")


def validate_run_state_transition(previous: RunState, candidate: RunState) -> None:
    validate_run_state_shape(previous)
    validate_run_state_shape(candidate)
    if previous["run_id"] != candidate["run_id"]:
        raise RunStateError("run_id cannot change across a state transition")
    if previous["task_id"] != candidate["task_id"]:
        raise RunStateError("task_id cannot change across a state transition")
    expected_revision = previous["revision"] + 1
    if candidate["revision"] != expected_revision:
        raise RunStateError("replacement revision must increment by exactly one")

    allowed = ALLOWED_TRANSITIONS[previous["status"]]
    if candidate["status"] not in allowed:
        raise RunStateError(
            f"invalid run-state transition: {previous['status']} -> {candidate['status']}"
        )
