from __future__ import annotations

import unittest

from manager_runtime.deployment.health import DependencyCheck, HealthRegistry


class ExplosiveBool:
    def __bool__(self) -> bool:  # pragma: no cover - must never execute
        raise AssertionError("readiness must not invoke arbitrary truthiness")


class ExplosiveString:
    def __str__(self) -> str:  # pragma: no cover - must never execute
        raise AssertionError("readiness must not stringify arbitrary detail objects")


class ExplosiveDependency(DependencyCheck):
    def __getattribute__(self, name):  # pragma: no cover - fields must never be read
        if name in {"name", "check", "critical"}:
            raise AssertionError("readiness registration must not invoke subclass hooks")
        return super().__getattribute__(name)


class HealthRegistryTests(unittest.TestCase):
    def test_starting_process_is_live_but_not_ready(self) -> None:
        registry = HealthRegistry()
        self.assertTrue(registry.liveness()["ok"])
        self.assertFalse(registry.readiness()["ok"])
        self.assertEqual(registry.readiness()["status"], "startup_or_paused")

    def test_critical_dependency_controls_readiness(self) -> None:
        state = {"database": False}
        registry = HealthRegistry()
        registry.register_dependency(
            DependencyCheck("database", lambda: (state["database"], "reachable" if state["database"] else "down"))
        )
        registry.set_accepting_work(True)
        self.assertFalse(registry.readiness()["ok"])
        state["database"] = True
        self.assertTrue(registry.readiness()["ok"])

    def test_noncritical_dependency_does_not_block_readiness(self) -> None:
        registry = HealthRegistry()
        registry.register_dependency(DependencyCheck("metrics", lambda: False, critical=False))
        registry.set_accepting_work(True)
        self.assertTrue(registry.readiness()["ok"])

    def test_dependency_registration_rejects_subclass_without_running_hooks(self) -> None:
        registry = HealthRegistry()
        with self.assertRaisesRegex(TypeError, "DependencyCheck"):
            registry.register_dependency(ExplosiveDependency("state", lambda: True))

    def test_dependency_registration_rejects_invalid_fields(self) -> None:
        registry = HealthRegistry()
        with self.assertRaisesRegex(ValueError, "dependency name"):
            registry.register_dependency(DependencyCheck(" state", lambda: True))
        with self.assertRaisesRegex(TypeError, "check must be callable"):
            registry.register_dependency(DependencyCheck("state", object()))  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "critical must be a boolean"):
            registry.register_dependency(DependencyCheck("state", lambda: True, critical=ExplosiveBool()))  # type: ignore[arg-type]

    def test_accepting_work_requires_real_boolean(self) -> None:
        registry = HealthRegistry()
        with self.assertRaisesRegex(TypeError, "accepting must be a boolean"):
            registry.set_accepting_work(ExplosiveBool())  # type: ignore[arg-type]

    def test_dependency_exception_fails_closed(self) -> None:
        def broken() -> bool:
            raise RuntimeError("synthetic failure")

        registry = HealthRegistry()
        registry.register_dependency(DependencyCheck("state", broken))
        registry.set_accepting_work(True)
        result = registry.readiness()
        self.assertFalse(result["ok"])
        self.assertIn("RuntimeError", result["dependencies"]["state"]["detail"])

    def test_malformed_dependency_tuple_fails_closed_without_probe_exception(self) -> None:
        registry = HealthRegistry()
        registry.register_dependency(DependencyCheck("state", lambda: (True,)))  # type: ignore[arg-type,return-value]
        registry.set_accepting_work(True)
        result = registry.readiness()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "critical_dependency_unavailable")
        self.assertEqual(
            result["dependencies"]["state"]["detail"],
            "invalid dependency check result",
        )

    def test_dependency_result_does_not_run_arbitrary_truthiness(self) -> None:
        registry = HealthRegistry()
        registry.register_dependency(DependencyCheck("state", lambda: ExplosiveBool()))  # type: ignore[arg-type,return-value]
        registry.set_accepting_work(True)
        result = registry.readiness()
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["dependencies"]["state"]["detail"],
            "invalid dependency check result",
        )

    def test_dependency_detail_does_not_run_arbitrary_string_conversion(self) -> None:
        registry = HealthRegistry()
        registry.register_dependency(
            DependencyCheck("state", lambda: (True, ExplosiveString()))  # type: ignore[arg-type,return-value]
        )
        registry.set_accepting_work(True)
        result = registry.readiness()
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["dependencies"]["state"]["detail"],
            "invalid dependency check result",
        )

    def test_dependency_detail_is_bounded_and_single_line(self) -> None:
        for detail in ("x" * 257, "line1\nline2", "bad\x00detail"):
            with self.subTest(detail=repr(detail[:20])):
                registry = HealthRegistry()
                registry.register_dependency(DependencyCheck("state", lambda detail=detail: (True, detail)))
                registry.set_accepting_work(True)
                result = registry.readiness()
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["dependencies"]["state"]["detail"],
                    "invalid dependency check detail",
                )

    def test_graceful_shutdown_drops_readiness_before_liveness(self) -> None:
        registry = HealthRegistry()
        registry.set_accepting_work(True)
        registry.begin_shutdown()
        self.assertTrue(registry.liveness()["ok"])
        self.assertFalse(registry.readiness()["ok"])
        self.assertEqual(registry.readiness()["status"], "draining")
        registry.mark_dead()
        self.assertFalse(registry.liveness()["ok"])
