from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .reconciliation import run_reconciliation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(action: str, target: str, params: dict[str, Any]) -> str:
    payload = json.dumps(
        {"action": action, "target": target, "material_parameters": params},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _approval(task: dict[str, Any], *, status: str = "pending") -> dict[str, Any]:
    task_id = task["task_id"]
    objective = task["objective"]
    target = f"task:{task_id}"
    params = {"objective": objective}
    return {
        "approval_id": f"approval:{task_id}",
        "run_id": f"run:{task_id}",
        "status": status,
        "action": objective,
        "target": target,
        "material_parameters": params,
        "materiality": task["classification"]["materiality"],
        "risk_class": task["classification"]["consequence"],
        "reason": "Human approval is required for this material or consequential action.",
        "recommendation": "Review the exact action and parameters before execution.",
        "recovery": "No side effect is performed until approval is current.",
        "issued_at": _now(),
        "resolved_at": None,
        "approved_by": None,
        "action_fingerprint": _fingerprint(objective, target, params),
    }


def _result(
    task: dict[str, Any],
    *,
    status: str,
    finding: str,
    owner_decision_required: bool,
    decision_request: str | None = None,
) -> dict[str, Any]:
    return {
        "result_id": f"result:{task['task_id']}",
        "status": status,
        "finding": finding,
        "assumptions": [],
        "risks": [],
        "uncertainties": [],
        "confidence": "high",
        "owner_decision_required": owner_decision_required,
        "decision_request": decision_request,
        "next_handoff": None,
    }


def _trace(
    task: dict[str, Any],
    *,
    status: str,
    workflow: str,
    events: list[dict[str, str]],
    approval_ref: str | None = None,
    reconciliation_ref: str | None = None,
) -> dict[str, Any]:
    classification = task["classification"]
    trace = {
        "run_id": f"run:{task['task_id']}",
        "status": status,
        "classification": {
            "materiality": classification["materiality"],
            "consequence": classification["consequence"],
            "uncertainty": classification.get("uncertainty", "low"),
        },
        "workflow": workflow,
        "capabilities": [],
        "events": events,
        "residual_uncertainty": [],
    }
    if approval_ref:
        trace["approval_refs"] = [approval_ref]
    if reconciliation_ref:
        trace["reconciliation_refs"] = [reconciliation_ref]
    return trace


def run(task_input: dict[str, Any]) -> dict[str, Any]:
    """Run the deterministic reference control plane.

    This runtime decides governance/routing behavior and emits contract-shaped
    artifacts. It intentionally does not execute arbitrary domain work or
    external side effects.
    """
    task = task_input["task"]
    classification = task["classification"]
    objective = task["objective"]
    objective_lower = objective.lower()
    prior = task_input.get("prior_state") or {}
    untrusted = task_input.get("untrusted_content") or []

    if (
        prior.get("approval_status") == "approved"
        and prior.get("approved_action_fingerprint")
        and prior.get("current_action_fingerprint")
        and prior["approved_action_fingerprint"] != prior["current_action_fingerprint"]
    ):
        approval = _approval(task, status="stale")
        approval["reason"] = "The reviewed action fingerprint no longer matches the current action."
        trace = _trace(
            task,
            status="blocked",
            workflow="approval",
            approval_ref=approval["approval_id"],
            events=[
                {
                    "event_type": "approval",
                    "status": "stale",
                    "summary": "Prior approval is stale because material action identity changed.",
                }
            ],
        )
        return {
            "trace": trace,
            "approval": approval,
            "result": _result(
                task,
                status="blocked",
                finding="Execution is blocked until fresh approval is obtained.",
                owner_decision_required=True,
                decision_request="Approve or reject the current action parameters.",
            ),
        }

    if classification["materiality"] == "material":
        approval = _approval(task)
        trace = _trace(
            task,
            status="blocked",
            workflow="approval",
            approval_ref=approval["approval_id"],
            events=[
                {
                    "event_type": "approval",
                    "status": "pending",
                    "summary": "Material action requires explicit human approval before mutation.",
                }
            ],
        )
        return {
            "trace": trace,
            "approval": approval,
            "result": _result(
                task,
                status="blocked",
                finding="Material work was analyzed but not executed.",
                owner_decision_required=True,
                decision_request="Approve or reject the exact proposed action.",
            ),
        }

    reconciliation_context = prior.get("reconciliation_context")
    reconciliation_intent = reconciliation_context is not None or (
        "propagate" in objective_lower and "confirmed" in objective_lower
    )
    if reconciliation_intent:
        return run_reconciliation(
            task,
            reconciliation_context,
            make_approval=_approval,
            make_result=_result,
            make_trace=_trace,
        )

    if "specialist" in objective_lower:
        trace = _trace(
            task,
            status="completed",
            workflow="specialist",
            events=[
                {
                    "event_type": "handoff",
                    "status": "bounded",
                    "reference": f"handoff:{task['task_id']}",
                    "summary": "Specialist handoff is limited to analysis and recommendation.",
                }
            ],
        )
        trace["capabilities"] = ["specialist-analysis"]
        return {
            "trace": trace,
            "handoff": {
                "handoff_id": f"handoff:{task['task_id']}",
                "objective": objective,
                "decision_owner": "primary-agent",
                "scope": ["analyze", "recommend"],
                "exclusions": ["modify production", "external communication"],
                "authority": {
                    "research": True,
                    "analyze": True,
                    "recommend": True,
                    "call_tools": False,
                    "modify_state": False,
                    "external_communication": False,
                    "destructive_action": False,
                },
                "output_contract": {"type": "analysis"},
                "completion_condition": "Return analysis and recommendation only.",
                "return_to": "primary-agent",
            },
            "result": _result(
                task,
                status="complete",
                finding="A bounded specialist-analysis path was selected.",
                owner_decision_required=False,
            ),
        }

    public_sensitive = (
        classification.get("sensitivity") == "sensitive"
        and "public" in objective_lower
    )
    injection_detected = bool(untrusted)

    events = [
        {
            "event_type": "route",
            "status": "selected",
            "summary": "Direct execution selected as the minimum sufficient workflow.",
        }
    ]
    finding = "Direct control path selected; no unnecessary delegation or approval was introduced."

    if injection_detected:
        events.append(
            {
                "event_type": "evaluation",
                "status": "ignored_untrusted_instructions",
                "summary": "Untrusted content was treated as evidence and did not widen authority.",
            }
        )
        finding = "Untrusted instructions were ignored and the original read-only objective was preserved."

    if public_sensitive:
        events.append(
            {
                "event_type": "evaluation",
                "status": "private_source_excluded",
                "summary": "Sensitive source identifiers were excluded from the modeled public output.",
            }
        )
        finding = "Prepared a synthetic public-safe example with sensitive source identifiers excluded."

    trace = _trace(
        task,
        status="completed",
        workflow="direct",
        events=events,
    )
    return {
        "trace": trace,
        "result": _result(
            task,
            status="complete",
            finding=finding,
            owner_decision_required=False,
        ),
    }
