from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


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


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validated_routine_reconciliation(
    task: dict[str, Any], evidence: Any
) -> dict[str, Any] | None:
    """Build reconciliation output only from explicit, already-verified evidence.

    The deterministic control-plane runtime does not discover repositories or
    perform mutations. It must therefore never manufacture an authoritative
    owner, dependent targets, completed propagation, or a verification PASS
    from task wording alone.
    """
    if not isinstance(evidence, dict):
        return None
    if evidence.get("governing_decision_confirmed") is not True:
        return None

    authoritative_owner = evidence.get("authoritative_owner")
    detected_state = evidence.get("detected_state")
    dependencies = evidence.get("dependencies")
    authoritative_update = evidence.get("authoritative_update")
    propagation = evidence.get("propagation")
    verification = evidence.get("verification")

    if not _nonempty_string(authoritative_owner) or not _nonempty_string(detected_state):
        return None
    if (
        not isinstance(dependencies, list)
        or not dependencies
        or any(not _nonempty_string(item) for item in dependencies)
        or len(set(dependencies)) != len(dependencies)
    ):
        return None
    if not isinstance(authoritative_update, dict):
        return None
    if (
        authoritative_update.get("target") != authoritative_owner
        or authoritative_update.get("status") != "completed"
        or not _nonempty_string(authoritative_update.get("evidence"))
    ):
        return None
    if not isinstance(propagation, list) or len(propagation) != len(dependencies):
        return None

    propagation_by_target: dict[str, dict[str, Any]] = {}
    for item in propagation:
        if not isinstance(item, dict):
            return None
        target = item.get("target")
        if (
            target not in dependencies
            or target in propagation_by_target
            or item.get("status") != "completed"
            or not _nonempty_string(item.get("evidence"))
        ):
            return None
        propagation_by_target[target] = item
    if set(propagation_by_target) != set(dependencies):
        return None

    if not isinstance(verification, dict):
        return None
    if verification.get("status") != "pass":
        return None
    if not _nonempty_string(verification.get("details")):
        return None
    residual = verification.get("residual_discrepancies")
    if residual != []:
        return None

    actions = [
        {
            "target": authoritative_owner,
            "action_type": "update_authority",
            "status": "completed",
            "evidence": authoritative_update["evidence"],
        }
    ]
    actions.extend(
        {
            "target": dependency,
            "action_type": "propagate",
            "status": "completed",
            "evidence": propagation_by_target[dependency]["evidence"],
        }
        for dependency in dependencies
    )
    actions.append(
        {
            "target": authoritative_owner,
            "action_type": "verify",
            "status": "completed",
            "evidence": verification["details"],
        }
    )

    return {
        "reconciliation_id": f"reconciliation:{task['task_id']}",
        "classification": "routine",
        "authoritative_owner": authoritative_owner,
        "detected_state": detected_state,
        "dependencies": list(dependencies),
        "actions": actions,
        "verification": {
            "status": "pass",
            "details": verification["details"],
            "residual_discrepancies": [],
        },
    }


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

    if "propagate" in objective_lower and "confirmed" in objective_lower:
        reconciliation = _validated_routine_reconciliation(
            task, prior.get("reconciliation_evidence")
        )
        if reconciliation is None:
            trace = _trace(
                task,
                status="blocked",
                workflow="reconciliation",
                events=[
                    {
                        "event_type": "reconciliation",
                        "status": "unverified",
                        "summary": "Routine reconciliation evidence is incomplete or inconsistent; no authority or completion state was inferred.",
                    }
                ],
            )
            trace["residual_uncertainty"] = [
                "Authoritative owner, dependent propagation, and consistency verification require explicit evidence."
            ]
            return {
                "trace": trace,
                "result": _result(
                    task,
                    status="blocked",
                    finding="Routine reconciliation was not claimed complete because explicit authority, propagation, and verification evidence was not supplied.",
                    owner_decision_required=False,
                ),
            }

        reconciliation_id = reconciliation["reconciliation_id"]
        trace = _trace(
            task,
            status="completed",
            workflow="reconciliation",
            reconciliation_ref=reconciliation_id,
            events=[
                {
                    "event_type": "reconciliation",
                    "status": "completed",
                    "reference": reconciliation_id,
                    "summary": "Explicit evidence confirms authority-first reconciliation completed.",
                },
                {
                    "event_type": "verification",
                    "status": "pass",
                    "summary": reconciliation["verification"]["details"],
                },
            ],
        )
        return {
            "trace": trace,
            "reconciliation": reconciliation,
            "result": _result(
                task,
                status="complete",
                finding="Routine reconciliation control path completed from explicit verified evidence.",
                owner_decision_required=False,
            ),
        }

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
