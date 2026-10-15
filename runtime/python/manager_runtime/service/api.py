from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

_CANONICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_DANGEROUS_KEYS = {"__proto__", "prototype", "constructor"}


class ApiError(RuntimeError):
    """Structured error safe to expose at the service boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retryable: bool = False,
        recovery_required: bool = False,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.recovery_required = recovery_required
        self.ambiguous = ambiguous

    def envelope(self, request_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "recovery_required": self.recovery_required,
        }
        payload: dict[str, Any] = {"ok": False, "request_id": request_id, "error": error}
        if run_id is not None:
            payload["run_id"] = run_id
        return payload


@dataclass(frozen=True, slots=True)
class JsonLimits:
    max_depth: int = 32
    max_nodes: int = 10_000
    max_string_chars: int = 65_536

    def __post_init__(self) -> None:
        for name, value in (
            ("max_depth", self.max_depth),
            ("max_nodes", self.max_nodes),
            ("max_string_chars", self.max_string_chars),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


def validate_json_limits(value: Any, limits: JsonLimits) -> None:
    """Bound hostile JSON after decoding without recursively trusting it."""

    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise ApiError("invalid_request", "request JSON is too complex")
        if depth > limits.max_depth:
            raise ApiError("invalid_request", "request JSON is nested too deeply")
        if current is None or type(current) is bool:
            continue
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            continue
        if isinstance(current, str):
            if len(current) > limits.max_string_chars or "\x00" in current:
                raise ApiError("invalid_request", "request text exceeds safe limits")
            continue
        if isinstance(current, list):
            for item in reversed(current):
                stack.append((item, depth + 1))
            continue
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ApiError("invalid_request", "JSON object keys must be strings")
                if key in _DANGEROUS_KEYS:
                    raise ApiError("invalid_request", "request contains a reserved field")
                if len(key) > 256 or "\x00" in key:
                    raise ApiError("invalid_request", "request field name exceeds safe limits")
                stack.append((item, depth + 1))
            continue
        raise ApiError("invalid_request", "request contains an unsupported JSON value")


def canonical_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CANONICAL_ID.fullmatch(value) is None:
        raise ApiError("invalid_request", f"{field} must be a canonical identifier")
    return value


def canonical_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise ApiError(
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-128 canonical ASCII characters",
        )
    return value


def require_object(
    value: Any,
    *,
    allowed: set[str] | None = None,
    required: set[str] | None = None,
    label: str = "request",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiError("invalid_request", f"{label} must be a JSON object")
    if allowed is not None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ApiError("invalid_request", f"{label} has unknown fields")
    missing = sorted((required or set()) - set(value))
    if missing:
        raise ApiError("invalid_request", f"{label} is missing required fields")
    return value


def validate_task_input(value: Any) -> dict[str, Any]:
    payload = require_object(
        value,
        allowed={"task", "prior_state", "untrusted_content", "model_input"},
        required={"task"},
    )
    task = require_object(
        payload["task"],
        allowed={
            "task_id",
            "objective",
            "decision_context",
            "inputs",
            "classification",
            "requested_capabilities",
            "authority",
            "extensions",
        },
        required={"task_id", "objective", "classification"},
        label="task",
    )
    canonical_id(task["task_id"], "task.task_id")
    objective = task["objective"]
    if not isinstance(objective, str) or not objective.strip() or len(objective) > 65_536:
        raise ApiError("invalid_request", "task.objective must be bounded non-empty text")
    classification = require_object(
        task["classification"],
        allowed={"materiality", "consequence", "uncertainty", "reversibility", "sensitivity"},
        required={"materiality", "consequence", "uncertainty"},
        label="task.classification",
    )
    if classification["materiality"] not in {"routine", "material"}:
        raise ApiError("invalid_request", "task.classification.materiality is invalid")
    if classification["consequence"] not in {"low", "medium", "high", "critical"}:
        raise ApiError("invalid_request", "task.classification.consequence is invalid")
    if classification["uncertainty"] not in {"low", "medium", "high"}:
        raise ApiError("invalid_request", "task.classification.uncertainty is invalid")
    if "reversibility" in classification and classification["reversibility"] not in {
        "reversible",
        "partially_reversible",
        "irreversible",
        "unknown",
    }:
        raise ApiError("invalid_request", "task.classification.reversibility is invalid")
    if "sensitivity" in classification and classification["sensitivity"] not in {
        "public",
        "internal",
        "sensitive",
        "unknown",
    }:
        raise ApiError("invalid_request", "task.classification.sensitivity is invalid")
    for name in ("inputs", "authority", "extensions"):
        if name in task and not isinstance(task[name], dict):
            raise ApiError("invalid_request", f"task.{name} must be an object")
    if "decision_context" in task and not isinstance(task["decision_context"], str):
        raise ApiError("invalid_request", "task.decision_context must be text")
    if "requested_capabilities" in task:
        values = task["requested_capabilities"]
        if (
            not isinstance(values, list)
            or len(values) > 128
            or not all(isinstance(item, str) and item and len(item) <= 256 for item in values)
            or len(set(values)) != len(values)
        ):
            raise ApiError(
                "invalid_request",
                "task.requested_capabilities must contain unique bounded strings",
            )
    if "prior_state" in payload and not isinstance(payload["prior_state"], dict):
        raise ApiError("invalid_request", "prior_state must be an object")
    if "untrusted_content" in payload:
        content = payload["untrusted_content"]
        if (
            not isinstance(content, list)
            or len(content) > 64
            or not all(isinstance(item, str) and item and len(item) <= 65_536 for item in content)
        ):
            raise ApiError("invalid_request", "untrusted_content must be a bounded text list")
    if "model_input" in payload:
        model_input = payload["model_input"]
        if not isinstance(model_input, str) or not model_input.strip() or len(model_input) > 65_536:
            raise ApiError("invalid_request", "model_input must be bounded non-empty text")
    return payload


def validate_empty_command(value: Any) -> dict[str, Any]:
    return require_object(value, allowed=set(), label="command")


def validate_approval_decision(value: Any) -> dict[str, Any]:
    payload = require_object(
        value,
        allowed={"approval_id", "decision", "decided_by", "decided_at", "comment", "extensions"},
        required={"approval_id", "decision", "decided_by", "decided_at"},
        label="approval decision",
    )
    canonical_id(payload["approval_id"], "approval_id")
    if payload["decision"] not in {"approved", "rejected"}:
        raise ApiError("invalid_request", "approval decision must be approved or rejected")
    for field in ("decided_by", "decided_at"):
        item = payload[field]
        if not isinstance(item, str) or not item.strip() or len(item) > 512:
            raise ApiError("invalid_request", f"{field} must be bounded non-empty text")
    if "comment" in payload and (
        not isinstance(payload["comment"], str) or len(payload["comment"]) > 4096
    ):
        raise ApiError("invalid_request", "comment must be bounded text")
    if "extensions" in payload and not isinstance(payload["extensions"], dict):
        raise ApiError("invalid_request", "extensions must be an object")
    return payload


def validate_recovery_resolution(value: Any) -> dict[str, Any]:
    payload = require_object(
        value,
        allowed={
            "resolution_id",
            "run_id",
            "decision",
            "decided_by",
            "decided_at",
            "evidence",
            "redacted",
            "output",
        },
        required={"resolution_id", "run_id", "decision", "decided_by", "decided_at", "evidence"},
        label="recovery resolution",
    )
    canonical_id(payload["resolution_id"], "resolution_id")
    canonical_id(payload["run_id"], "run_id")
    if payload["decision"] not in {"confirmed_succeeded", "confirmed_not_executed", "cancelled"}:
        raise ApiError("invalid_request", "recovery decision is invalid")
    for field in ("decided_by", "decided_at", "evidence"):
        item = payload[field]
        if not isinstance(item, str) or not item.strip() or len(item) > 4096:
            raise ApiError("invalid_request", f"{field} must be bounded non-empty text")
    if "redacted" in payload and type(payload["redacted"]) is not bool:
        raise ApiError("invalid_request", "redacted must be boolean")
    return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def public_run_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """Project durable state onto a deliberately narrow network contract."""

    run_id = canonical_id(state.get("run_id"), "run_id")
    task_id = canonical_id(state.get("task_id"), "task_id")
    status = state.get("status")
    revision = state.get("revision")
    if not isinstance(status, str) or not status:
        raise ApiError("state_unavailable", "run state is not safely readable", status=503, retryable=True)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ApiError("state_unavailable", "run state is not safely readable", status=503, retryable=True)
    view: dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "status": status,
        "revision": revision,
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "recovery_required": status == "recovery_required",
    }
    result = state.get("result_snapshot")
    if isinstance(result, dict):
        view["result"] = copy.deepcopy(result)
    pending = state.get("pending_action")
    if status == "waiting_approval" and isinstance(pending, dict):
        approval = pending.get("approval")
        if isinstance(approval, dict):
            approval_id = approval.get("approval_id")
            approval_status = approval.get("status")
            if isinstance(approval_id, str) and isinstance(approval_status, str):
                view["approval"] = {
                    "approval_id": approval_id,
                    "status": approval_status,
                }
    return view


@runtime_checkable
class ServiceBackend(Protocol):
    """Application-owned bridge from HTTP into Manager's governed runtime."""

    def submit(self, task_input: dict[str, Any], *, subject: str) -> dict[str, Any]: ...

    def get_run(self, run_id: str, *, subject: str) -> dict[str, Any]: ...

    def resume(self, run_id: str, *, subject: str) -> dict[str, Any]: ...

    def cancel(self, run_id: str, *, subject: str) -> dict[str, Any]: ...

    def decide_approval(
        self, run_id: str, decision: dict[str, Any], *, subject: str
    ) -> dict[str, Any]: ...

    def resolve_recovery(
        self, run_id: str, resolution: dict[str, Any], *, subject: str
    ) -> dict[str, Any]: ...

    def readiness(self) -> bool | tuple[bool, str]: ...


AuthorizationResolver = Callable[[str, str], dict[str, Any]]


class DurableRuntimeBackend:
    """Governed durable-loop backend for the production HTTP surface."""

    def __init__(
        self,
        *,
        store: Any,
        adapter: Any,
        registry: Any,
        model: str,
        allowed_tools: list[str],
        authorization_resolver: AuthorizationResolver | None = None,
        max_model_steps: int = 4,
        max_tool_calls: int = 8,
        max_tool_result_chars: int = 8000,
        max_output_tokens: int | None = None,
        allow_non_public_input: bool = False,
        lease_ttl_seconds: int = 300,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be non-empty text")
        if not isinstance(allowed_tools, list) or not all(
            isinstance(item, str) and item for item in allowed_tools
        ):
            raise ValueError("allowed_tools must contain non-empty strings")
        self.store = store
        self.adapter = adapter
        self.registry = registry
        self.model = model
        self.allowed_tools = list(allowed_tools)
        self.authorization_resolver = authorization_resolver or (lambda _subject, _tool: {})
        self.max_model_steps = max_model_steps
        self.max_tool_calls = max_tool_calls
        self.max_tool_result_chars = max_tool_result_chars
        self.max_output_tokens = max_output_tokens
        self.allow_non_public_input = allow_non_public_input
        self.lease_ttl_seconds = lease_ttl_seconds
        registry.model_definitions(self.allowed_tools)

    def readiness(self) -> bool | tuple[bool, str]:
        try:
            self.store.load("health:service-probe")
        except Exception as exc:
            return False, f"durable state unavailable ({type(exc).__name__})"
        capabilities = getattr(self.store, "capabilities", None)
        required = (
            "optimistic_concurrency",
            "transactional_cas",
            "leases",
            "fencing",
            "durable_idempotency",
            "execution_guard",
            "atomic_recovery_resolution",
        )
        if capabilities is None or not all(bool(getattr(capabilities, name, False)) for name in required):
            return False, "durable coordination guarantees unavailable"
        return True

    def _contexts(self, subject: str) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        for tool_name in self.allowed_tools:
            value = self.authorization_resolver(subject, tool_name)
            if not isinstance(value, dict):
                raise ApiError("authorization_unavailable", "trusted authorization is unavailable", status=503, retryable=True)
            contexts[tool_name] = dict(value)
        return contexts

    @staticmethod
    def _translate_state_error(exc: Exception) -> ApiError:
        name = type(exc).__name__
        if name in {"RunStateConflict", "RunLeaseConflict", "RunLeaseExpired", "OperationConflict"}:
            return ApiError("run_conflict", "run state changed concurrently", status=409, retryable=True)
        return ApiError("run_state_error", "run state could not be processed safely", status=409)

    def submit(self, task_input: dict[str, Any], *, subject: str) -> dict[str, Any]:
        try:
            from ..state import run_durable_agent_loop

            result = run_durable_agent_loop(
                task_input,
                self.adapter,
                self.registry,
                self.store,
                model=self.model,
                allowed_tools=self.allowed_tools,
                authorization_contexts=self._contexts(subject),
                max_model_steps=self.max_model_steps,
                max_tool_calls=self.max_tool_calls,
                max_tool_result_chars=self.max_tool_result_chars,
                max_output_tokens=self.max_output_tokens,
                allow_non_public_input=self.allow_non_public_input,
                lease_ttl_seconds=self.lease_ttl_seconds,
            )
            durable = result.get("durable_state")
            run_id = durable.get("run_id") if isinstance(durable, dict) else None
            if not isinstance(run_id, str):
                run_id = f"run:{task_input['task']['task_id']}"
            state = self.store.load(run_id)
            if state is None:
                raise ApiError(
                    "durable_run_not_created",
                    "the selected governed path did not create a resumable durable run",
                    status=409,
                )
            return public_run_view(state)
        except ApiError:
            raise
        except Exception as exc:
            raise self._translate_state_error(exc) from exc

    def get_run(self, run_id: str, *, subject: str) -> dict[str, Any]:
        del subject
        try:
            state = self.store.load(run_id)
        except Exception as exc:
            raise self._translate_state_error(exc) from exc
        if state is None:
            raise ApiError("run_not_found", "run was not found", status=404)
        return public_run_view(state)

    def resume(self, run_id: str, *, subject: str) -> dict[str, Any]:
        try:
            from ..state import resume_durable_agent_loop

            resume_durable_agent_loop(
                self.store,
                run_id,
                self.adapter,
                self.registry,
                authorization_contexts=self._contexts(subject),
                lease_ttl_seconds=self.lease_ttl_seconds,
            )
            return self.get_run(run_id, subject=subject)
        except ApiError:
            raise
        except Exception as exc:
            raise self._translate_state_error(exc) from exc

    def decide_approval(
        self, run_id: str, decision: dict[str, Any], *, subject: str
    ) -> dict[str, Any]:
        try:
            state = self.store.load(run_id)
            if state is None:
                raise ApiError("run_not_found", "run was not found", status=404)
            pending = state.get("pending_action")
            approval = pending.get("approval") if isinstance(pending, dict) else None
            if not isinstance(approval, dict) or approval.get("approval_id") != decision.get("approval_id"):
                raise ApiError("approval_conflict", "approval does not match the pending action", status=409)
            from ..state import resume_durable_agent_loop

            resume_durable_agent_loop(
                self.store,
                run_id,
                self.adapter,
                self.registry,
                authorization_contexts=self._contexts(subject),
                decision=decision,
                lease_ttl_seconds=self.lease_ttl_seconds,
            )
            return self.get_run(run_id, subject=subject)
        except ApiError:
            raise
        except Exception as exc:
            raise self._translate_state_error(exc) from exc

    def resolve_recovery(
        self, run_id: str, resolution: dict[str, Any], *, subject: str
    ) -> dict[str, Any]:
        if resolution.get("run_id") != run_id:
            raise ApiError("recovery_conflict", "recovery run_id does not match the route", status=409)
        try:
            state = self.store.load(run_id)
            if state is None:
                raise ApiError("run_not_found", "run was not found", status=404)
            pending = state.get("pending_action")
            request = pending.get("tool_request") if isinstance(pending, dict) else None
            tool_name = request.get("tool_name") if isinstance(request, dict) else None
            current_authorization = (
                self.authorization_resolver(subject, tool_name)
                if isinstance(tool_name, str)
                else {}
            )
            if not isinstance(current_authorization, dict):
                raise ApiError("authorization_unavailable", "trusted authorization is unavailable", status=503, retryable=True)
            from ..state import resolve_recovery_required

            resolve_recovery_required(
                self.store,
                run_id,
                self.registry,
                resolution,
                current_authorization=current_authorization,
            )
            return self.get_run(run_id, subject=subject)
        except ApiError:
            raise
        except Exception as exc:
            raise self._translate_state_error(exc) from exc

    def cancel(self, run_id: str, *, subject: str) -> dict[str, Any]:
        del subject
        try:
            state = self.store.load(run_id)
            if state is None:
                raise ApiError("run_not_found", "run was not found", status=404)
            status = state.get("status")
            if status == "waiting_approval":
                replacement = copy.deepcopy(state)
                replacement["status"] = "cancelled"
                replacement["revision"] = state["revision"] + 1
                replacement["updated_at"] = utc_now()
                replacement["pending_action"] = None
                replacement["recovery_reason"] = None
                trace = replacement.get("trace_snapshot")
                if isinstance(trace, dict):
                    trace["status"] = "cancelled"
                    trace.setdefault("events", []).append(
                        {
                            "event_type": "cancellation",
                            "status": "cancelled",
                            "summary": "Run cancelled at an approval safe point before execution.",
                        }
                    )
                result = replacement.get("result_snapshot")
                if isinstance(result, dict):
                    result["status"] = "cancelled"
                    result["owner_decision_required"] = False
                    result["decision_request"] = None
                state = self.store.compare_and_swap(run_id, state["revision"], replacement)
                return public_run_view(state)
            if status == "recovery_required":
                raise ApiError(
                    "recovery_resolution_required",
                    "recovery_required runs must be cancelled through the recovery endpoint with explicit evidence",
                    status=409,
                    recovery_required=True,
                )
            if status in {"running", "executing"}:
                raise ApiError(
                    "cancellation_not_safe",
                    "run cannot be forcibly cancelled while execution ownership may be active",
                    status=409,
                )
            return public_run_view(state)
        except ApiError:
            raise
        except Exception as exc:
            raise self._translate_state_error(exc) from exc
