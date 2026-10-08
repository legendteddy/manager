from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

RunState = dict[str, Any]


class RunStateError(RuntimeError):
    """Raised when durable run state cannot be safely created or resumed."""


class RunStateConflict(RunStateError):
    """Raised when optimistic concurrency detects a stale state revision."""


@runtime_checkable
class RunStore(Protocol):
    """Durable storage boundary for Manager run checkpoints."""

    def create(self, state: RunState) -> RunState:
        """Persist a new run state at revision 1."""

    def load(self, run_id: str) -> RunState | None:
        """Load the most recent persisted state for a run."""

    def compare_and_swap(
        self, run_id: str, expected_revision: int, state: RunState
    ) -> RunState:
        """Replace a run only when its current revision matches expected_revision."""
