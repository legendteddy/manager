from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable


CheckCallable = Callable[[], bool | tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    name: str
    check: CheckCallable
    critical: bool = True


class HealthRegistry:
    """Thread-safe liveness/readiness state for a hosting service.

    Liveness answers whether the process should be restarted. Readiness answers
    whether new work can be accepted safely. During graceful shutdown readiness
    becomes false first while liveness remains true until the process is actually
    exiting.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._alive = True
        self._accepting_work = False
        self._draining = False
        self._dependencies: dict[str, DependencyCheck] = {}

    def register_dependency(self, dependency: DependencyCheck) -> None:
        if not dependency.name or dependency.name.strip() != dependency.name:
            raise ValueError("dependency name must be a non-empty normalized string")
        with self._lock:
            if dependency.name in self._dependencies:
                raise ValueError(f"dependency already registered: {dependency.name}")
            self._dependencies[dependency.name] = dependency

    def set_accepting_work(self, accepting: bool) -> None:
        with self._lock:
            if self._draining and accepting:
                raise RuntimeError("cannot become ready after graceful drain has started")
            self._accepting_work = bool(accepting)

    def begin_shutdown(self) -> None:
        with self._lock:
            self._draining = True
            self._accepting_work = False

    def mark_dead(self) -> None:
        with self._lock:
            self._alive = False
            self._accepting_work = False

    def liveness(self) -> dict[str, object]:
        with self._lock:
            alive = self._alive
            draining = self._draining
        return {
            "ok": alive,
            "status": "live" if alive else "dead",
            "draining": draining,
        }

    @staticmethod
    def _evaluate_dependency(dependency: DependencyCheck) -> tuple[bool, str]:
        """Evaluate one readiness dependency without trusting callback output.

        Dependency checks sit on an operational safety boundary. A buggy or hostile
        check must make readiness fail closed instead of crashing the probe or
        running arbitrary coercion hooks while formatting diagnostic output.
        """
        try:
            result = dependency.check()
        except Exception as exc:
            return False, f"check raised {type(exc).__name__}"

        if isinstance(result, bool):
            return result, "ok" if result else "unavailable"

        if type(result) is not tuple or len(result) != 2:
            return False, "invalid dependency check result"

        ok, detail = result
        if not isinstance(ok, bool) or type(detail) is not str:
            return False, "invalid dependency check result"
        if not detail or len(detail) > 256 or any(char in detail for char in ("\x00", "\r", "\n")):
            return False, "invalid dependency check detail"
        return ok, detail

    def readiness(self) -> dict[str, object]:
        with self._lock:
            alive = self._alive
            accepting = self._accepting_work
            draining = self._draining
            dependencies = tuple(self._dependencies.values())

        checks: dict[str, dict[str, object]] = {}
        critical_ok = True
        for dependency in dependencies:
            ok, detail = self._evaluate_dependency(dependency)
            checks[dependency.name] = {
                "ok": ok,
                "critical": dependency.critical,
                "detail": detail,
            }
            if dependency.critical and not ok:
                critical_ok = False

        ready = alive and accepting and not draining and critical_ok
        if not alive:
            reason = "process_not_live"
        elif draining:
            reason = "draining"
        elif not accepting:
            reason = "startup_or_paused"
        elif not critical_ok:
            reason = "critical_dependency_unavailable"
        else:
            reason = "ready"
        return {
            "ok": ready,
            "status": reason,
            "dependencies": checks,
        }
