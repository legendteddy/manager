from __future__ import annotations

from .base import RunState, RunStateError
from .validation import RUN_STATUSES, validate_run_state_shape

ALLOWED_TRANSITIONS = {
    "running": {
        "running",
        "waiting_approval",
        "completed",
        "blocked",
        "failed",
        "recovery_required",
    },
    "waiting_approval": {
        "waiting_approval",
        "executing",
        "cancelled",
        "recovery_required",
    },
    "executing": {
        "executing",
        "running",
        "waiting_approval",
        "completed",
        "failed",
        "recovery_required",
    },
    "recovery_required": {
        "running",
        "waiting_approval",
        "completed",
        "cancelled",
        "recovery_required",
    },
    "completed": set(),
    "blocked": set(),
    "failed": set(),
    "cancelled": set(),
}


def validate_run_state_transition(previous: RunState, candidate: RunState) -> None:
    validate_run_state_shape(previous)
    validate_run_state_shape(candidate)
    if previous["run_id"] != candidate["run_id"]:
        raise RunStateError("run_id cannot change across a state transition")
    if previous["task_id"] != candidate["task_id"]:
        raise RunStateError("task_id cannot change across a state transition")
    if previous["created_at"] != candidate["created_at"]:
        raise RunStateError("created_at cannot change across a state transition")
    if previous.get("task") != candidate.get("task"):
        raise RunStateError("persisted task cannot change across a state transition")
    expected_revision = previous["revision"] + 1
    if candidate["revision"] != expected_revision:
        raise RunStateError("replacement revision must increment by exactly one")

    allowed = ALLOWED_TRANSITIONS[previous["status"]]
    if candidate["status"] not in allowed:
        raise RunStateError(
            f"invalid run-state transition: {previous['status']} -> {candidate['status']}"
        )
