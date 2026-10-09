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

_RECOVERY_EXIT_STATUSES = {
    "confirmed_succeeded": {"running", "completed"},
    "confirmed_not_executed": {"waiting_approval"},
    "cancelled": {"cancelled"},
}


def _validate_recovery_resolution_transition(
    previous: RunState, candidate: RunState
) -> None:
    previous_resolution = previous.get("recovery_resolution")
    candidate_resolution = candidate.get("recovery_resolution")

    if (
        previous["status"] == "recovery_required"
        and candidate["status"] != "recovery_required"
    ):
        if previous_resolution is not None:
            raise RunStateError(
                "recovery_required state already contains resolution evidence before exit"
            )
        if not isinstance(candidate_resolution, dict):
            raise RunStateError(
                "leaving recovery_required requires explicit recovery_resolution evidence"
            )
        if candidate.get("recovery_reason") is not None:
            raise RunStateError(
                "leaving recovery_required must clear recovery_reason"
            )
        decision = candidate_resolution.get("decision")
        allowed = _RECOVERY_EXIT_STATUSES.get(decision, set())
        if candidate["status"] not in allowed:
            raise RunStateError(
                "recovery resolution decision is incompatible with the target run status"
            )
        return

    if previous_resolution is None:
        if candidate_resolution is not None:
            raise RunStateError(
                "recovery_resolution can only be recorded when leaving recovery_required"
            )
        return

    if candidate_resolution != previous_resolution:
        raise RunStateError("recovery_resolution is immutable once recorded")


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

    _validate_recovery_resolution_transition(previous, candidate)
