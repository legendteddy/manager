from __future__ import annotations

from datetime import datetime
from typing import Any

from ..providers.base import validate_model_response
from ..tools.base import tool_request_fingerprint, validate_tool_request
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

_RUN_STATE_KEYS = {
    "run_id",
    "task_id",
    "status",
    "revision",
    "created_at",
    "updated_at",
    "task",
    "pending_action",
    "last_tool_result",
    "trace_snapshot",
    "result_snapshot",
    "recovery_reason",
    "recovery_resolution",
    "extensions",
}
_TASK_KEYS = {
    "task_id",
    "objective",
    "decision_context",
    "inputs",
    "classification",
    "requested_capabilities",
    "authority",
    "extensions",
}
_CLASSIFICATION_KEYS = {
    "materiality",
    "consequence",
    "uncertainty",
    "reversibility",
    "sensitivity",
}
_PENDING_ACTION_KEYS = {
    "tool_request",
    "approval",
    "tool_definition_fingerprint",
    "authorization_context",
}
_APPROVAL_KEYS = {
    "approval_id",
    "run_id",
    "status",
    "action",
    "target",
    "material_parameters",
    "materiality",
    "risk_class",
    "reason",
    "recommendation",
    "recovery",
    "issued_at",
    "resolved_at",
    "resolved_by",
    "approved_by",
    "action_fingerprint",
    "extensions",
}
_TOOL_RESULT_KEYS = {
    "request_id",
    "tool_name",
    "status",
    "side_effect_class",
    "decision_reason",
    "output",
    "approval_ref",
    "approval",
    "error",
    "redacted",
    "verification",
    "extensions",
}
_CHECKPOINT_KEYS = {
    "version",
    "phase",
    "provider",
    "model",
    "allowed_tools",
    "tool_definition_fingerprints",
    "max_model_steps",
    "max_tool_calls",
    "max_tool_result_chars",
    "max_output_tokens",
    "model_steps",
    "tool_calls",
    "seen_proposal_fingerprints",
    "prior_response_ref",
    "pending_proposal_id",
    "pending_request_fingerprint",
    "current_response",
    "continuation_tool_results",
    "extensions",
}
_RESULT_KEYS = {
    "result_id",
    "status",
    "finding",
    "recommendation",
    "material_evidence",
    "assumptions",
    "risks",
    "uncertainties",
    "confidence",
    "owner_decision_required",
    "decision_request",
    "next_handoff",
    "extensions",
}
_RECOVERY_RESOLUTION_KEYS = {
    "resolution_id",
    "run_id",
    "decision",
    "decided_by",
    "decided_at",
    "evidence",
    "output",
    "redacted",
    "extensions",
}
_AUTHORIZATION_FLAGS = {
    "scope_authorized",
    "human_intent_confirmed",
    "target_verified",
}


def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "text" if allow_empty else "non-empty text"
        raise RunStateError(f"{label} must be {qualifier}")
    return value


def _require_int(value: Any, label: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise RunStateError(f"{label} must be an integer >= {minimum}")
    return value


def _require_datetime(value: Any, label: str) -> str:
    text = _require_text(value, label)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise RunStateError(f"{label} must be an RFC 3339 date-time") from exc
    if parsed.tzinfo is None:
        raise RunStateError(f"{label} must include a timezone")
    return text


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RunStateError(f"{label} has unknown fields: {', '.join(unknown)}")


def _validate_task(task: Any, state_task_id: str) -> None:
    if not isinstance(task, dict):
        raise RunStateError("persisted run task must be an object")
    _reject_unknown(task, _TASK_KEYS, "persisted task")
    for key in ("task_id", "objective", "classification"):
        if key not in task:
            raise RunStateError(f"persisted task is missing required field: {key}")
    if _require_text(task["task_id"], "persisted task task_id") != state_task_id:
        raise RunStateError("persisted task task_id does not match run state task_id")
    _require_text(task["objective"], "persisted task objective")

    classification = task["classification"]
    if not isinstance(classification, dict):
        raise RunStateError("persisted task classification must be an object")
    _reject_unknown(
        classification, _CLASSIFICATION_KEYS, "persisted task classification"
    )
    for key in ("materiality", "consequence", "uncertainty"):
        if key not in classification:
            raise RunStateError(f"persisted task classification is missing {key}")
    if classification["materiality"] not in {"routine", "material"}:
        raise RunStateError("persisted task materiality is invalid")
    if classification["consequence"] not in {"low", "medium", "high", "critical"}:
        raise RunStateError("persisted task consequence is invalid")
    if classification["uncertainty"] not in {"low", "medium", "high"}:
        raise RunStateError("persisted task uncertainty is invalid")
    if "reversibility" in classification and classification["reversibility"] not in {
        "reversible",
        "partially_reversible",
        "irreversible",
        "unknown",
    }:
        raise RunStateError("persisted task reversibility is invalid")
    if "sensitivity" in classification and classification["sensitivity"] not in {
        "public",
        "internal",
        "sensitive",
        "unknown",
    }:
        raise RunStateError("persisted task sensitivity is invalid")
    if "decision_context" in task and not isinstance(task["decision_context"], str):
        raise RunStateError("persisted task decision_context must be text")
    for key in ("inputs", "authority", "extensions"):
        if key in task and not isinstance(task[key], dict):
            raise RunStateError(f"persisted task {key} must be an object")
    if "requested_capabilities" in task:
        capabilities = task["requested_capabilities"]
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise RunStateError(
                "persisted task requested_capabilities must be a string array"
            )
        if len(set(capabilities)) != len(capabilities):
            raise RunStateError(
                "persisted task requested_capabilities must be unique"
            )


def _validate_tool_request(request: Any, run_id: str) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RunStateError("pending tool_request must be an object")
    try:
        validate_tool_request(request)
    except (TypeError, ValueError) as exc:
        raise RunStateError("pending tool_request is invalid") from exc
    if request.get("run_id") != run_id:
        raise RunStateError("pending tool_request run_id does not match run state")
    for key in ("request_id", "run_id", "tool_name"):
        _require_text(request.get(key), f"pending tool_request {key}")
    if request.get("target") is not None and not isinstance(request.get("target"), str):
        raise RunStateError("pending tool_request target must be text or null")
    if request.get("proposal_ref") is not None and not isinstance(
        request.get("proposal_ref"), str
    ):
        raise RunStateError("pending tool_request proposal_ref must be text or null")
    if "extensions" in request and not isinstance(request["extensions"], dict):
        raise RunStateError("pending tool_request extensions must be an object")
    return request


def _validate_approval(
    approval: Any,
    run_id: str,
    *,
    require_complete: bool,
) -> dict[str, Any]:
    """Validate an approval packet at the strength appropriate to its phase.

    Live waiting/executing states require the complete runtime approval identity,
    including the exact action fingerprint. A recovery_required row may contain
    a legacy/minimal approval packet because recovery never reuses it as current
    authorization; recovery re-establishes authority before any new execution.
    """
    if not isinstance(approval, dict):
        raise RunStateError("pending approval must be an object")
    _reject_unknown(approval, _APPROVAL_KEYS, "pending approval")

    required = {"approval_id", "status"}
    if require_complete:
        required.update(
            {
                "run_id",
                "action",
                "target",
                "materiality",
                "reason",
                "issued_at",
                "action_fingerprint",
            }
        )
    missing = sorted(required - set(approval))
    if missing:
        raise RunStateError(
            f"pending approval is missing required fields: {', '.join(missing)}"
        )

    _require_text(approval["approval_id"], "pending approval approval_id")
    if approval["status"] not in {
        "pending",
        "approved",
        "rejected",
        "expired",
        "stale",
    }:
        raise RunStateError("pending approval status is invalid")

    if "run_id" in approval:
        if _require_text(approval["run_id"], "pending approval run_id") != run_id:
            raise RunStateError("pending approval run_id does not match run state")
    for key in ("action", "target", "reason"):
        if key in approval:
            _require_text(approval[key], f"pending approval {key}")
    if "materiality" in approval and approval["materiality"] not in {
        "routine",
        "material",
    }:
        raise RunStateError("pending approval materiality is invalid")
    if "issued_at" in approval:
        _require_datetime(approval["issued_at"], "pending approval issued_at")
    if approval.get("resolved_at") is not None:
        _require_datetime(approval["resolved_at"], "pending approval resolved_at")
    for key in ("resolved_by", "approved_by", "risk_class", "recommendation", "recovery"):
        if key in approval and approval[key] is not None and not isinstance(
            approval[key], str
        ):
            raise RunStateError(f"pending approval {key} must be text or null")
    if "material_parameters" in approval and not isinstance(
        approval["material_parameters"], dict
    ):
        raise RunStateError("pending approval material_parameters must be an object")
    if "action_fingerprint" in approval:
        _require_text(
            approval["action_fingerprint"], "pending approval action_fingerprint"
        )
    if "extensions" in approval and not isinstance(approval["extensions"], dict):
        raise RunStateError("pending approval extensions must be an object")
    return approval


def _validate_pending_action(
    value: Any,
    run_id: str,
    *,
    require_complete_approval: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunStateError("pending_action must be an object")
    _reject_unknown(value, _PENDING_ACTION_KEYS, "pending_action")
    for key in ("tool_request", "approval", "tool_definition_fingerprint"):
        if key not in value:
            raise RunStateError(f"pending_action is missing required field: {key}")
    request = _validate_tool_request(value["tool_request"], run_id)
    approval = _validate_approval(
        value["approval"], run_id, require_complete=require_complete_approval
    )
    _require_text(
        value["tool_definition_fingerprint"],
        "pending_action tool_definition_fingerprint",
    )
    authorization = value.get("authorization_context", {})
    if not isinstance(authorization, dict):
        raise RunStateError("pending_action authorization_context must be an object")
    for key in _AUTHORIZATION_FLAGS:
        if key in authorization and not isinstance(authorization[key], bool):
            raise RunStateError(f"persisted authorization field {key!r} must be boolean")
    fingerprint = approval.get("action_fingerprint")
    if fingerprint is not None and fingerprint != tool_request_fingerprint(request):
        raise RunStateError(
            "pending approval action fingerprint does not match tool request"
        )
    return value


def _validate_tool_result(
    value: Any,
    pending: dict[str, Any] | None,
    run_id: str,
) -> None:
    if not isinstance(value, dict):
        raise RunStateError("last_tool_result must be an object")
    _reject_unknown(value, _TOOL_RESULT_KEYS, "last_tool_result")
    for key in (
        "request_id",
        "tool_name",
        "status",
        "side_effect_class",
        "verification",
    ):
        if key not in value:
            raise RunStateError(f"last_tool_result is missing required field: {key}")
    _require_text(value["request_id"], "last_tool_result request_id")
    _require_text(value["tool_name"], "last_tool_result tool_name")
    if value["status"] not in {"executed", "blocked", "approval_required", "failed"}:
        raise RunStateError("last_tool_result status is invalid")
    if value["side_effect_class"] not in {
        "analysis",
        "read",
        "reversible_write",
        "external_commitment",
        "sensitive_destructive",
    }:
        raise RunStateError("last_tool_result side_effect_class is invalid")
    verification = value["verification"]
    if not isinstance(verification, dict) or verification.get("status") not in {
        "pass",
        "fail",
        "not_required",
        "unverified",
    }:
        raise RunStateError("last_tool_result verification is invalid")
    if set(verification) - {"status", "details"}:
        raise RunStateError("last_tool_result verification has unknown fields")
    if "details" in verification and not isinstance(verification["details"], str):
        raise RunStateError("last_tool_result verification details must be text")
    if "redacted" in value and not isinstance(value["redacted"], bool):
        raise RunStateError("last_tool_result redacted must be boolean")
    if value.get("approval") is not None:
        approval = _validate_approval(
            value["approval"],
            run_id,
            require_complete=value["status"] == "approval_required",
        )
        if value.get("approval_ref") not in {None, approval["approval_id"]}:
            raise RunStateError(
                "last_tool_result approval_ref does not match approval packet"
            )
    if value["status"] == "approval_required" and not isinstance(
        value.get("approval"), dict
    ):
        raise RunStateError(
            "approval_required last_tool_result requires an approval packet"
        )
    if pending is not None:
        request = pending["tool_request"]
        if (
            value["request_id"] != request["request_id"]
            or value["tool_name"] != request["tool_name"]
        ):
            raise RunStateError(
                "last_tool_result identity does not match pending tool request"
            )


def _validate_result_snapshot(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise RunStateError("result_snapshot must be an object or null")
    _reject_unknown(value, _RESULT_KEYS, "result_snapshot")
    for key in ("result_id", "status", "finding", "owner_decision_required"):
        if key not in value:
            raise RunStateError(f"result_snapshot is missing required field: {key}")
    _require_text(value["result_id"], "result_snapshot result_id")
    if value["status"] not in {
        "complete",
        "completed",
        "partial",
        "blocked",
        "failed",
        "unverified",
    }:
        raise RunStateError("result_snapshot status is invalid")
    if not isinstance(value["finding"], str):
        raise RunStateError("result_snapshot finding must be text")
    if not isinstance(value["owner_decision_required"], bool):
        raise RunStateError(
            "result_snapshot owner_decision_required must be boolean"
        )
    for key in ("assumptions", "risks", "uncertainties"):
        if key in value and (
            not isinstance(value[key], list)
            or not all(isinstance(item, str) for item in value[key])
        ):
            raise RunStateError(f"result_snapshot {key} must be a string array")
    if "material_evidence" in value and (
        not isinstance(value["material_evidence"], list)
        or not all(isinstance(item, dict) for item in value["material_evidence"])
    ):
        raise RunStateError(
            "result_snapshot material_evidence must be an object array"
        )
    if value.get("decision_request") is not None and not isinstance(
        value.get("decision_request"), str
    ):
        raise RunStateError(
            "result_snapshot decision_request must be text or null"
        )
    if "extensions" in value and not isinstance(value["extensions"], dict):
        raise RunStateError("result_snapshot extensions must be an object")


def _validate_recovery_resolution(value: Any, run_id: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise RunStateError("recovery_resolution must be an object or null")
    _reject_unknown(value, _RECOVERY_RESOLUTION_KEYS, "recovery_resolution")
    for key in (
        "resolution_id",
        "run_id",
        "decision",
        "decided_by",
        "decided_at",
        "evidence",
    ):
        if key not in value:
            raise RunStateError(
                f"recovery_resolution is missing required field: {key}"
            )
    _require_text(value["resolution_id"], "recovery_resolution resolution_id")
    if _require_text(value["run_id"], "recovery_resolution run_id") != run_id:
        raise RunStateError("recovery_resolution run_id does not match run state")
    if value["decision"] not in {
        "confirmed_succeeded",
        "confirmed_not_executed",
        "cancelled",
    }:
        raise RunStateError("recovery_resolution decision is invalid")
    _require_text(value["decided_by"], "recovery_resolution decided_by")
    _require_datetime(value["decided_at"], "recovery_resolution decided_at")
    _require_text(value["evidence"], "recovery_resolution evidence")
    if "redacted" in value and not isinstance(value["redacted"], bool):
        raise RunStateError("recovery_resolution redacted must be boolean")
    if "extensions" in value and not isinstance(value["extensions"], dict):
        raise RunStateError("recovery_resolution extensions must be an object")


def _validate_checkpoint(
    checkpoint: Any,
    state: RunState,
    pending: dict[str, Any] | None,
) -> None:
    if not isinstance(checkpoint, dict):
        raise RunStateError("agent_loop extension must be an object")
    _reject_unknown(checkpoint, _CHECKPOINT_KEYS, "agent_loop checkpoint")
    required = _CHECKPOINT_KEYS - {"max_output_tokens", "extensions"}
    missing = sorted(key for key in required if key not in checkpoint)
    if missing:
        raise RunStateError(
            f"agent_loop checkpoint is missing required fields: {', '.join(missing)}"
        )
    if checkpoint["version"] != 1:
        raise RunStateError("agent_loop checkpoint version is unsupported")
    phase = checkpoint["phase"]
    if phase not in {
        "response_ready",
        "waiting_approval",
        "continuation_ready",
        "terminal",
    }:
        raise RunStateError("agent_loop checkpoint phase is invalid")
    _require_text(checkpoint["provider"], "agent_loop provider")
    _require_text(checkpoint["model"], "agent_loop model")

    allowed_tools = checkpoint["allowed_tools"]
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) and item for item in allowed_tools
    ):
        raise RunStateError(
            "agent_loop allowed_tools must be a non-empty-text array"
        )
    if len(set(allowed_tools)) != len(allowed_tools):
        raise RunStateError("agent_loop allowed_tools must be unique")
    fingerprints = checkpoint["tool_definition_fingerprints"]
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(allowed_tools):
        raise RunStateError(
            "agent_loop tool fingerprints must exactly cover allowed_tools"
        )
    if not all(isinstance(value, str) and value for value in fingerprints.values()):
        raise RunStateError("agent_loop tool fingerprints must be non-empty text")

    max_model_steps = _require_int(
        checkpoint["max_model_steps"], "agent_loop max_model_steps", minimum=1
    )
    max_tool_calls = _require_int(
        checkpoint["max_tool_calls"], "agent_loop max_tool_calls", minimum=0
    )
    max_tool_result_chars = _require_int(
        checkpoint["max_tool_result_chars"],
        "agent_loop max_tool_result_chars",
        minimum=1,
    )
    model_steps = _require_int(
        checkpoint["model_steps"], "agent_loop model_steps", minimum=1
    )
    tool_calls = _require_int(
        checkpoint["tool_calls"], "agent_loop tool_calls", minimum=0
    )
    if model_steps > max_model_steps:
        raise RunStateError("agent_loop model_steps exceeds its durable budget")
    if tool_calls > max_tool_calls:
        raise RunStateError("agent_loop tool_calls exceeds its durable budget")
    max_output_tokens = checkpoint.get("max_output_tokens")
    if max_output_tokens is not None:
        _require_int(
            max_output_tokens, "agent_loop max_output_tokens", minimum=1
        )

    seen = checkpoint["seen_proposal_fingerprints"]
    if not isinstance(seen, list) or not all(
        isinstance(item, str) and item for item in seen
    ):
        raise RunStateError(
            "agent_loop seen proposal fingerprints must be non-empty text"
        )
    if len(set(seen)) != len(seen):
        raise RunStateError("agent_loop seen proposal fingerprints must be unique")
    if len(seen) > tool_calls:
        raise RunStateError(
            "agent_loop seen proposal fingerprints exceed tool call count"
        )

    for key in (
        "prior_response_ref",
        "pending_proposal_id",
        "pending_request_fingerprint",
    ):
        if checkpoint[key] is not None and not isinstance(checkpoint[key], str):
            raise RunStateError(f"agent_loop {key} must be text or null")

    current_response = checkpoint["current_response"]
    if current_response is not None:
        try:
            validate_model_response(
                current_response, expected_provider=checkpoint["provider"]
            )
        except (TypeError, ValueError) as exc:
            raise RunStateError("agent_loop current_response is invalid") from exc

    continuation = checkpoint["continuation_tool_results"]
    if not isinstance(continuation, list):
        raise RunStateError(
            "agent_loop continuation_tool_results must be an array"
        )
    proposal_ids: set[str] = set()
    for item in continuation:
        if not isinstance(item, dict) or set(item) != {
            "proposal_id",
            "status",
            "output",
            "redacted",
        }:
            raise RunStateError(
                "agent_loop continuation tool result is malformed"
            )
        proposal_id = _require_text(
            item["proposal_id"], "agent_loop continuation proposal_id"
        )
        if proposal_id in proposal_ids:
            raise RunStateError(
                "agent_loop continuation proposal_ids must be unique"
            )
        proposal_ids.add(proposal_id)
        if (
            item["status"] != "executed"
            or not isinstance(item["output"], str)
            or not isinstance(item["redacted"], bool)
        ):
            raise RunStateError(
                "agent_loop continuation tool result is invalid"
            )
        if len(item["output"]) > max_tool_result_chars:
            raise RunStateError(
                "agent_loop continuation output exceeds durable result-size budget"
            )

    if phase == "response_ready":
        if current_response is None:
            raise RunStateError(
                "response_ready checkpoint requires current_response"
            )
        if continuation:
            raise RunStateError(
                "response_ready checkpoint must not contain continuation results"
            )
        if (
            checkpoint["pending_proposal_id"] is not None
            or checkpoint["pending_request_fingerprint"] is not None
        ):
            raise RunStateError(
                "response_ready checkpoint must not contain pending action identity"
            )
    elif phase == "waiting_approval":
        if pending is None:
            raise RunStateError(
                "waiting_approval checkpoint requires pending_action"
            )
        if not checkpoint["prior_response_ref"]:
            raise RunStateError(
                "waiting_approval checkpoint requires prior_response_ref"
            )
        # A live waiting_approval checkpoint must retain the response that
        # proposed the action. Executing/recovery states may intentionally clear
        # it after the effect is recorded while retaining exact request identity.
        if state["status"] == "waiting_approval" and current_response is None:
            raise RunStateError(
                "live waiting_approval checkpoint requires current_response"
            )
        if (
            not checkpoint["pending_proposal_id"]
            or not checkpoint["pending_request_fingerprint"]
        ):
            raise RunStateError(
                "waiting_approval checkpoint requires pending proposal identity"
            )
        request = pending["tool_request"]
        if checkpoint["pending_request_fingerprint"] != tool_request_fingerprint(
            request
        ):
            raise RunStateError(
                "agent_loop pending request fingerprint does not match pending_action"
            )
        if request.get("proposal_ref") != checkpoint["pending_proposal_id"]:
            raise RunStateError(
                "agent_loop pending proposal id does not match pending_action"
            )
        if continuation:
            raise RunStateError(
                "waiting_approval checkpoint must not contain continuation results"
            )
    elif phase == "continuation_ready":
        if not checkpoint["prior_response_ref"] or not continuation:
            raise RunStateError(
                "continuation_ready checkpoint requires prior response and tool results"
            )
        if (
            checkpoint["pending_proposal_id"] is not None
            or checkpoint["pending_request_fingerprint"] is not None
        ):
            raise RunStateError(
                "continuation_ready checkpoint must not contain pending action identity"
            )

    if state["status"] in {"waiting_approval", "executing"} and phase != "waiting_approval":
        raise RunStateError(
            "durable run status does not match waiting approval checkpoint phase"
        )
    if state["status"] == "running" and phase not in {
        "response_ready",
        "continuation_ready",
    }:
        raise RunStateError(
            "running durable loop has an incompatible checkpoint phase"
        )
    if state["status"] in {"completed", "blocked", "failed"} and phase != "terminal":
        raise RunStateError(
            "terminal durable loop has a non-terminal checkpoint phase"
        )
    if "extensions" in checkpoint and not isinstance(
        checkpoint["extensions"], dict
    ):
        raise RunStateError(
            "agent_loop checkpoint extensions must be an object"
        )


def validate_run_state_shape(state: RunState) -> None:
    if not isinstance(state, dict):
        raise RunStateError("run state must be an object")
    _reject_unknown(state, _RUN_STATE_KEYS, "run state")
    required = (
        "run_id",
        "task_id",
        "status",
        "revision",
        "created_at",
        "updated_at",
    )
    missing = [key for key in required if key not in state]
    if missing:
        raise RunStateError(
            f"run state missing required fields: {', '.join(missing)}"
        )

    run_id = _require_text(state["run_id"], "run state run_id")
    task_id = _require_text(state["task_id"], "run state task_id")
    if state["status"] not in RUN_STATUSES:
        raise RunStateError(f"run state has unknown status: {state['status']!r}")
    _require_int(state["revision"], "run state revision", minimum=1)
    _require_datetime(state["created_at"], "run state created_at")
    _require_datetime(state["updated_at"], "run state updated_at")

    task = state.get("task")
    if task is not None:
        _validate_task(task, task_id)

    pending_value = state.get("pending_action")
    strict_approval = state["status"] in {"waiting_approval", "executing"}
    pending = (
        None
        if pending_value is None
        else _validate_pending_action(
            pending_value,
            run_id,
            require_complete_approval=strict_approval,
        )
    )
    if state["status"] in {"waiting_approval", "executing"} and pending is None:
        raise RunStateError(f"{state['status']} state requires pending_action")

    last_tool_result = state.get("last_tool_result")
    if last_tool_result is not None:
        _validate_tool_result(last_tool_result, pending, run_id)

    trace = state.get("trace_snapshot")
    if trace is not None and not isinstance(trace, dict):
        raise RunStateError("trace_snapshot must be an object or null")
    if isinstance(trace, dict) and trace.get("run_id") not in {None, run_id}:
        raise RunStateError("trace_snapshot run_id does not match run state")
    _validate_result_snapshot(state.get("result_snapshot"))

    reason = state.get("recovery_reason")
    if reason is not None and not isinstance(reason, str):
        raise RunStateError("recovery_reason must be text or null")
    if state["status"] == "recovery_required" and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise RunStateError("recovery_required state requires recovery_reason")
    _validate_recovery_resolution(state.get("recovery_resolution"), run_id)

    extensions = state.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        raise RunStateError(
            "run state extensions must be an object when present"
        )
    if isinstance(extensions, dict) and "agent_loop" in extensions:
        if task is None:
            raise RunStateError(
                "durable agent loop state requires persisted task"
            )
        _validate_checkpoint(extensions["agent_loop"], state, pending)
