from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ContextManager, Protocol, runtime_checkable

RunState = dict[str, Any]


class RunStateError(RuntimeError):
    """Raised when durable run state cannot be safely created or resumed."""


class RunStateConflict(RunStateError):
    """Raised when optimistic concurrency detects a stale state revision."""


class RunLeaseConflict(RunStateConflict):
    """Raised when a worker cannot acquire or use the current run lease."""


class RunLeaseExpired(RunLeaseConflict):
    """Raised when a worker attempts to use an expired or fenced-off lease."""


class OperationConflict(RunStateConflict):
    """Raised when a durable operation identity conflicts with existing evidence."""


@dataclass(frozen=True)
class RunStoreCapabilities:
    """Explicit guarantees supplied by a durable-state backend.

    ``coordination_scope`` is descriptive evidence, not a portability promise.
    The SQLite reference backend reports ``local_multi_process`` because a
    shared SQLite database is not a universal horizontally-scaled datastore.
    """

    optimistic_concurrency: bool
    transactional_cas: bool
    leases: bool
    fencing: bool
    durable_idempotency: bool
    execution_guard: bool
    atomic_recovery_resolution: bool
    coordination_scope: str


@dataclass(frozen=True)
class RunLease:
    """Backend-issued ownership token for one run.

    ``fencing_token`` must increase whenever ownership transfers after expiry or
    release. Callers must treat an older token as permanently stale.
    """

    run_id: str
    owner_id: str
    fencing_token: int
    expires_at_epoch: int


@dataclass(frozen=True)
class DurableOperation:
    """Durable idempotency evidence for one consequential operation."""

    operation_id: str
    run_id: str
    request_fingerprint: str
    fencing_token: int
    status: str
    result: RunState | None = None
    claimed: bool = False


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


@runtime_checkable
class CoordinatedRunStore(RunStore, Protocol):
    """Run store with explicit multi-worker coordination semantics.

    Production execution of consequential effects requires these semantics in
    addition to ordinary revision CAS. A PostgreSQL-class implementation can
    satisfy this protocol with transactional rows/advisory locks; SQLite
    provides a local multi-process reference implementation.
    """

    capabilities: RunStoreCapabilities

    def acquire_lease(
        self, run_id: str, owner_id: str, *, ttl_seconds: int
    ) -> RunLease:
        """Acquire ownership, returning a monotonically fenced lease."""

    def renew_lease(self, lease: RunLease, *, ttl_seconds: int) -> RunLease:
        """Renew only the exact current, unexpired lease."""

    def assert_lease(self, lease: RunLease) -> None:
        """Fail closed unless the exact lease is current and unexpired."""

    def release_lease(self, lease: RunLease) -> None:
        """Release only the exact current lease without resetting its fence."""

    def fenced_compare_and_swap(
        self,
        run_id: str,
        expected_revision: int,
        state: RunState,
        *,
        lease: RunLease,
    ) -> RunState:
        """CAS state only while the supplied fencing token is authoritative."""

    def execution_guard(
        self, lease: RunLease, *, ttl_seconds: int
    ) -> ContextManager[None]:
        """Hold backend ownership across one external execution attempt.

        This is a Manager-worker coordination boundary. It is not proof that an
        arbitrary external system honors fencing tokens or idempotency keys.
        """

    def begin_operation(
        self,
        *,
        lease: RunLease,
        operation_id: str,
        request_fingerprint: str,
    ) -> DurableOperation:
        """Durably claim one operation identity before its external effect."""

    def finish_operation(
        self,
        operation: DurableOperation,
        *,
        lease: RunLease,
        status: str,
        result: RunState | None,
    ) -> DurableOperation:
        """Persist a terminal/uncertain operation outcome under the same fence."""

    def resolve_operation_and_compare_and_swap(
        self,
        run_id: str,
        expected_revision: int,
        state: RunState,
        *,
        lease: RunLease,
        operation_id: str,
        request_fingerprint: str,
        operation_status: str,
        operation_result: RunState | None,
    ) -> RunState:
        """Atomically reconcile an uncertain operation and its run state.

        Recovery evidence must not leave the operation ledger and authoritative
        run row disagreeing because the process crashed between two commits.
        Implementations may accept only reviewed recovery transitions, such as
        ``started``/``outcome_unknown`` to ``confirmed``/``not_executed``.
        """

    def load_operation(self, operation_id: str) -> DurableOperation | None:
        """Load durable evidence for an operation identity."""


def require_coordinated_store(store: RunStore) -> CoordinatedRunStore:
    """Return a store only when it exposes the required execution guarantees."""

    if not isinstance(store, CoordinatedRunStore):
        raise RunStateError(
            "consequential durable execution requires a coordinated run store "
            "with leases, fencing, durable idempotency, and atomic recovery"
        )
    capabilities = store.capabilities
    if not (
        capabilities.optimistic_concurrency
        and capabilities.transactional_cas
        and capabilities.leases
        and capabilities.fencing
        and capabilities.durable_idempotency
        and capabilities.execution_guard
        and capabilities.atomic_recovery_resolution
    ):
        raise RunStateError(
            "state backend does not advertise all guarantees required for "
            "consequential durable execution"
        )
    return store
