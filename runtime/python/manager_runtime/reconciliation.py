from __future__ import annotations

from typing import Any


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) or not item for item in value):
        return None
    return list(dict.fromkeys(value))


def _surface_map(value: Any) -> dict[str, list[str]] | None:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return None
    result: dict[str, list[str]] = {}
    for target, surfaces in value.items():
        if not isinstance(target, str) or not target:
            return None
        normalized = _string_list(surfaces)
        if normalized is None:
            return None
        result[target] = normalized
    return result


def _material_reconciliation_escalation(
    task: dict[str, Any],
    *,
    reason: str,
    decision_request: str,
    make_approval: Any,
    make_result: Any,
    make_trace: Any,
) -> dict[str, Any]:
    approval = make_approval(task)
    approval["materiality"] = "material"
    approval["reason"] = reason
    approval["recommendation"] = decision_request
    trace = make_trace(
        task,
        status="blocked",
        workflow="reconciliation",
        approval_ref=approval["approval_id"],
        events=[
            {
                "event_type": "reconciliation",
                "status": "material_conflict",
                "summary": reason,
            },
            {
                "event_type": "approval",
                "status": "pending",
                "summary": "Reconciliation stopped before canonical mutation.",
            },
        ],
    )
    trace["classification"]["materiality"] = "material"
    return {
        "trace": trace,
        "approval": approval,
        "result": make_result(
            task,
            status="blocked",
            finding="Reconciliation requires a material human decision before mutation.",
            owner_decision_required=True,
            decision_request=decision_request,
        ),
    }


def _incomplete_reconciliation(
    task: dict[str, Any],
    *,
    finding: str,
    make_result: Any,
    make_trace: Any,
) -> dict[str, Any]:
    return {
        "trace": make_trace(
            task,
            status="blocked",
            workflow="reconciliation",
            events=[
                {
                    "event_type": "reconciliation",
                    "status": "insufficient_evidence",
                    "summary": finding,
                }
            ],
        ),
        "result": make_result(
            task,
            status="blocked",
            finding=finding,
            owner_decision_required=False,
        ),
    }


def run_reconciliation(
    task: dict[str, Any],
    context: Any,
    *,
    make_approval: Any,
    make_result: Any,
    make_trace: Any,
) -> dict[str, Any]:
    if context is not None and not isinstance(context, dict):
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Reconciliation context is malformed.",
        )
    if context is None:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding=(
                "Routine reconciliation cannot be verified until the authoritative owner, "
                "confirmed truth, and dependency evidence are supplied."
            ),
        )

    change_scope = context.get("change_scope")
    if change_scope not in {"routine_propagation", "material_rule_change"}:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Reconciliation change scope is missing or unrecognized.",
        )

    if change_scope == "material_rule_change":
        return _material_reconciliation_escalation(
            task,
            make_approval=make_approval,
            make_result=make_result,
            make_trace=make_trace,
            reason=(
                "Repository evidence shows that the proposed reconciliation would establish "
                "or change a material rule rather than merely propagate confirmed truth."
            ),
            decision_request="Approve or reject the material rule change before reconciliation.",
        )

    candidate_owners = _string_list(context.get("candidate_owners", []))
    if candidate_owners is None:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Candidate repository owners are malformed; reconciliation cannot proceed safely.",
        )

    authoritative_owner = context.get("authoritative_owner")
    if authoritative_owner is not None and (
        not isinstance(authoritative_owner, str) or not authoritative_owner
    ):
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="The authoritative owner identifier is malformed.",
        )

    if len(candidate_owners) > 1 and authoritative_owner is None:
        return _material_reconciliation_escalation(
            task,
            make_approval=make_approval,
            make_result=make_result,
            make_trace=make_trace,
            reason="Multiple repositories appear equally authoritative for the same rule.",
            decision_request="Select the authoritative repository/domain owner before propagation.",
        )

    if authoritative_owner is None:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="No authoritative repository owner has been established.",
        )

    if candidate_owners and authoritative_owner not in candidate_owners:
        return _material_reconciliation_escalation(
            task,
            make_approval=make_approval,
            make_result=make_result,
            make_trace=make_trace,
            reason=(
                "The selected authoritative owner conflicts with the repositories currently "
                "identified as authority candidates."
            ),
            decision_request="Resolve the authoritative repository/domain owner before propagation.",
        )

    confirmation_evidence = context.get("confirmation_evidence")
    authority_evidence = context.get("authority_evidence")
    if not isinstance(confirmation_evidence, str) or not confirmation_evidence:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Evidence that the propagated truth was already confirmed is missing.",
        )
    if not isinstance(authority_evidence, str) or not authority_evidence:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Evidence for the selected authoritative repository owner is missing.",
        )

    confirmed_truth = context.get("confirmed_truth")
    owner_state = context.get("owner_state")
    if not isinstance(confirmed_truth, str) or not confirmed_truth:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Confirmed canonical truth is missing or malformed.",
        )
    if not isinstance(owner_state, str) or not owner_state:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Current authoritative-owner state is missing or malformed.",
        )

    dependency_evidence = context.get("dependency_evidence")
    if not isinstance(dependency_evidence, str) or not dependency_evidence:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Evidence that dependencies were traced from the authoritative owner is missing.",
        )

    dependencies = _string_list(context.get("dependencies"))
    inspected_dependencies = _string_list(context.get("inspected_dependencies"))
    if dependencies is None or inspected_dependencies is None:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Dependency inventory or inspection evidence is missing or malformed.",
        )

    consumer_states = context.get("consumer_states")
    if not isinstance(consumer_states, dict) or any(
        not isinstance(target, str)
        or not target
        or not isinstance(state, str)
        or not state
        for target, state in consumer_states.items()
    ):
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Consumer-state evidence is missing or malformed.",
        )

    required_surfaces = _surface_map(context.get("required_surfaces"))
    reconciled_surfaces = _surface_map(context.get("reconciled_surfaces"))
    if required_surfaces is None or reconciled_surfaces is None:
        return _incomplete_reconciliation(
            task,
            make_result=make_result,
            make_trace=make_trace,
            finding="Reconciliation surface evidence is malformed.",
        )

    dependency_set = set(dependencies)
    inspected_set = set(inspected_dependencies)
    discovered_consumers = (
        set(consumer_states) | set(required_surfaces) | set(reconciled_surfaces) | inspected_set
    )

    residual: list[str] = []
    for target in sorted(dependency_set - inspected_set):
        residual.append(f"Dependency {target} was not inspected.")
    for target in sorted(dependency_set - set(required_surfaces)):
        residual.append(f"Dependency {target} has no required-surface inventory.")
    for target in sorted(dependency_set - set(reconciled_surfaces)):
        residual.append(f"Dependency {target} has no reconciled-surface evidence.")
    for target in sorted(discovered_consumers - dependency_set):
        residual.append(f"Consumer {target} is absent from the dependency map.")
    for target in sorted(dependency_set - set(consumer_states)):
        residual.append(f"Dependency {target} has no current-state evidence.")

    actions: list[dict[str, str]] = []
    if owner_state != confirmed_truth:
        actions.append(
            {
                "target": authoritative_owner,
                "action_type": "update_authority",
                "status": "completed",
                "evidence": "Confirmed truth was written to the authoritative owner before consumers.",
            }
        )
    else:
        actions.append(
            {
                "target": authoritative_owner,
                "action_type": "verify",
                "status": "completed",
                "evidence": "Authoritative owner already contained the confirmed truth.",
            }
        )

    for target in dependencies:
        if target not in inspected_set or target not in consumer_states:
            continue
        if consumer_states[target] != confirmed_truth:
            actions.append(
                {
                    "target": target,
                    "action_type": "propagate",
                    "status": "completed",
                    "evidence": "Consumer differed from confirmed authoritative truth.",
                }
            )
        actions.append(
            {
                "target": target,
                "action_type": "verify",
                "status": "completed",
                "evidence": "Consumer was checked against confirmed authoritative truth.",
            }
        )

        required = set(required_surfaces.get(target, []))
        reconciled = set(reconciled_surfaces.get(target, []))
        for surface in sorted(required - reconciled):
            residual.append(f"Dependency {target} still requires reconciliation of {surface}.")

    remembered_state = context.get("remembered_state")
    memory_note = ""
    if isinstance(remembered_state, str) and remembered_state and remembered_state != owner_state:
        memory_note = (
            " Remembered context disagreed with current authoritative repository state and "
            "was not used as canonical truth."
        )

    verification_status = "pass" if not residual else "fail"
    verification_details = (
        "Authority-first reconciliation used current repository-owned truth and checked "
        "all declared dependency evidence."
        + memory_note
    )
    reconciliation_id = f"reconciliation:{task['task_id']}"
    reconciliation = {
        "reconciliation_id": reconciliation_id,
        "classification": "routine",
        "authoritative_owner": authoritative_owner,
        "detected_state": "Confirmed non-material truth was compared with authoritative and dependent state.",
        "dependencies": dependencies,
        "actions": actions,
        "verification": {
            "status": verification_status,
            "details": verification_details,
            "residual_discrepancies": residual,
        },
    }

    completed = verification_status == "pass"
    trace = make_trace(
        task,
        status="completed" if completed else "blocked",
        workflow="reconciliation",
        reconciliation_ref=reconciliation_id,
        events=[
            {
                "event_type": "reconciliation",
                "status": "completed" if completed else "incomplete",
                "reference": reconciliation_id,
                "summary": (
                    "Authority-first reconciliation completed."
                    if completed
                    else "Reconciliation stopped short of a completion claim."
                ),
            },
            {
                "event_type": "verification",
                "status": verification_status,
                "summary": (
                    "No residual dependency contradiction remained."
                    if completed
                    else "Residual dependency or propagation gaps remain."
                ),
            },
        ],
    )
    return {
        "trace": trace,
        "reconciliation": reconciliation,
        "result": make_result(
            task,
            status="complete" if completed else "blocked",
            finding=(
                "Routine reconciliation completed with authoritative-owner and dependency evidence."
                if completed
                else "Routine reconciliation is incomplete; residual dependency discrepancies remain."
            ),
            owner_decision_required=False,
        ),
    }
