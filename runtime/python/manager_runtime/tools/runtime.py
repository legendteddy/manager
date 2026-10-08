from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import (
    ToolPayload,
    ToolRegistry,
    tool_request_fingerprint,
    validate_arguments,
    validate_tool_request,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _authorization_flag(authorization: ToolPayload, key: str) -> bool:
    if key not in authorization:
        return False
    value = authorization[key]
    if not isinstance(value, bool):
        raise TypeError(f"authorization field {key!r} must be boolean")
    return value


def _safe_exception_type(exc: BaseException) -> str:
    """Return bounded diagnostic metadata without exception message contents."""
    return type(exc).__name__


def _approval_packet(
    request: ToolPayload,
    *,
    side_effect_class: str,
    status: str = "pending",
    reason: str,
) -> ToolPayload:
    target = request.get("target") or f"tool:{request['tool_name']}"
    risk_class = "critical" if side_effect_class == "sensitive_destructive" else "high"
    return {
        "approval_id": f"approval:{request['request_id']}",
        "run_id": request["run_id"],
        "status": status,
        "action": f"tool:{request['tool_name']}",
        "target": target,
        "material_parameters": request["arguments"],
        "materiality": "material",
        "risk_class": risk_class,
        "reason": reason,
        "recommendation": "Review the exact tool, target, and arguments before execution.",
        "recovery": "No additional side effect is performed until authorization is current.",
        "issued_at": _now(),
        "resolved_at": None,
        "resolved_by": None,
        "approved_by": None,
        "action_fingerprint": tool_request_fingerprint(request),
    }


def _result(
    request: ToolPayload,
    side_effect_class: str,
    *,
    status: str,
    reason: str,
    verification_status: str = "not_required",
    verification_details: str = "",
    output: Any = None,
    approval: ToolPayload | None = None,
    error: str | None = None,
    redacted: bool = False,
) -> ToolPayload:
    result: ToolPayload = {
        "request_id": request["request_id"],
        "tool_name": request["tool_name"],
        "status": status,
        "side_effect_class": side_effect_class,
        "decision_reason": reason,
        "verification": {
            "status": verification_status,
            "details": verification_details,
        },
        "approval_ref": approval["approval_id"] if approval else None,
        "error": error,
        "redacted": redacted,
    }
    if output is not None or status == "executed":
        result["output"] = output
    if approval is not None:
        result["approval"] = approval
    return result


def _approval_state(
    request: ToolPayload,
    authorization: ToolPayload,
    *,
    side_effect_class: str,
    reason: str,
) -> tuple[bool, ToolPayload]:
    supplied = authorization.get("approval")
    expected = tool_request_fingerprint(request)
    if not isinstance(supplied, dict):
        return False, _approval_packet(
            request,
            side_effect_class=side_effect_class,
            status="pending",
            reason=reason,
        )

    if supplied.get("status") != "approved":
        return False, _approval_packet(
            request,
            side_effect_class=side_effect_class,
            status="pending",
            reason=reason,
        )

    if supplied.get("action_fingerprint") != expected:
        stale = _approval_packet(
            request,
            side_effect_class=side_effect_class,
            status="stale",
            reason="The approved tool action no longer matches the current target or arguments.",
        )
        return False, stale

    return True, supplied


def execute_tool_request(
    task: dict[str, Any],
    request: ToolPayload,
    registry: ToolRegistry,
    authorization: ToolPayload | None = None,
) -> ToolPayload:
    """Evaluate and optionally execute one tool request.

    Authorization is trusted application context and must never be copied from
    model output. The model proposal supplies only tool identity and arguments.
    """
    validate_tool_request(request)
    if authorization is not None and not isinstance(authorization, dict):
        return _result(
            request,
            "analysis",
            status="blocked",
            reason="invalid_authorization_context",
            verification_status="not_required",
            error="authorization context must be an object",
        )
    authorization = dict(authorization or {})
    registered = registry.get(request["tool_name"])
    if registered is None:
        return _result(
            request,
            "analysis",
            status="blocked",
            reason="unknown_tool",
            verification_status="not_required",
        )

    definition = registered.definition
    side_effect_class = definition["side_effect_class"]

    try:
        validate_arguments(request["arguments"], definition["input_schema"])
    except (TypeError, ValueError) as exc:
        return _result(
            request,
            side_effect_class,
            status="blocked",
            reason="invalid_arguments",
            verification_status="not_required",
            error=str(exc),
        )

    try:
        scope_authorized = _authorization_flag(authorization, "scope_authorized")
        human_intent_confirmed = _authorization_flag(
            authorization, "human_intent_confirmed"
        )
        target_verified = _authorization_flag(authorization, "target_verified")
    except TypeError as exc:
        return _result(
            request,
            side_effect_class,
            status="blocked",
            reason="invalid_authorization_context",
            verification_status="not_required",
            error=str(exc),
        )

    task_materiality = task.get("classification", {}).get("materiality", "routine")

    if side_effect_class != "analysis" and not scope_authorized:
        return _result(
            request,
            side_effect_class,
            status="blocked",
            reason="scope_not_authorized",
            verification_status="not_required",
        )

    approval: ToolPayload | None = None

    if side_effect_class == "reversible_write" and task_materiality == "material":
        approved, approval = _approval_state(
            request,
            authorization,
            side_effect_class=side_effect_class,
            reason="Material reversible writes require exact human approval.",
        )
        if not approved:
            return _result(
                request,
                side_effect_class,
                status="approval_required",
                reason="material_write_requires_approval",
                verification_status="not_required",
                approval=approval,
            )

    if side_effect_class == "external_commitment":
        if not target_verified:
            return _result(
                request,
                side_effect_class,
                status="blocked",
                reason="external_target_not_verified",
                verification_status="not_required",
            )
        if not human_intent_confirmed:
            approved, approval = _approval_state(
                request,
                authorization,
                side_effect_class=side_effect_class,
                reason="External commitments require explicit human intent.",
            )
            if not approved:
                return _result(
                    request,
                    side_effect_class,
                    status="approval_required",
                    reason="external_commitment_requires_human_intent",
                    verification_status="not_required",
                    approval=approval,
                )

    if side_effect_class == "sensitive_destructive":
        if request.get("target") and not target_verified:
            return _result(
                request,
                side_effect_class,
                status="blocked",
                reason="sensitive_target_not_verified",
                verification_status="not_required",
            )
        approved, approval = _approval_state(
            request,
            authorization,
            side_effect_class=side_effect_class,
            reason="Sensitive or destructive tool actions require exact human approval.",
        )
        if not approved:
            return _result(
                request,
                side_effect_class,
                status="approval_required",
                reason="sensitive_destructive_requires_approval",
                verification_status="not_required",
                approval=approval,
            )

    try:
        output = registered.adapter.execute(dict(request["arguments"]))
    except Exception as exc:
        return _result(
            request,
            side_effect_class,
            status="failed",
            reason="tool_execution_failed",
            verification_status="unverified",
            error=_safe_exception_type(exc),
        )

    redacted = bool(definition.get("sensitive_output", False))
    public_output = None if redacted else output

    if definition["requires_verification"]:
        verifier = getattr(registered.adapter, "verify", None)
        if not callable(verifier):
            return _result(
                request,
                side_effect_class,
                status="failed",
                reason="verification_unavailable",
                verification_status="unverified",
                verification_details="Tool executed but no verification method is available.",
                output=public_output,
                redacted=redacted,
            )
        try:
            verified = bool(verifier(dict(request["arguments"]), output))
        except Exception as exc:
            return _result(
                request,
                side_effect_class,
                status="failed",
                reason="verification_failed",
                verification_status="fail",
                verification_details=f"Verification raised {_safe_exception_type(exc)}.",
                output=public_output,
                error=_safe_exception_type(exc),
                redacted=redacted,
            )
        if not verified:
            return _result(
                request,
                side_effect_class,
                status="failed",
                reason="verification_failed",
                verification_status="fail",
                verification_details="Tool execution completed but verification did not pass.",
                output=public_output,
                redacted=redacted,
            )
        verification_status = "pass"
        verification_details = "Tool execution was independently verified by its adapter."
    else:
        verification_status = "not_required"
        verification_details = "This registered tool does not require post-execution verification."

    return _result(
        request,
        side_effect_class,
        status="executed",
        reason="policy_allowed_execution",
        verification_status=verification_status,
        verification_details=verification_details,
        output=public_output,
        approval=approval,
        redacted=redacted,
    )
