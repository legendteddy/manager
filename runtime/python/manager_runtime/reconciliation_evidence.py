from __future__ import annotations

from typing import Any


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _completed_action_evidence(value: Any, *, target: str) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("target") != target or value.get("status") != "completed":
        return None
    evidence = value.get("evidence")
    return evidence if _nonempty_string(evidence) else None


def enforce_reconciliation_completion(
    context: Any,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed unless reconciliation completion is explicitly evidenced.

    The deterministic reference runtime does not mutate repositories itself.
    Final state equality is therefore corroborating evidence, not proof that an
    authoritative update or dependent propagation was performed by the governed
    workflow. A PASS requires both observed final state and explicit completion
    evidence for every mutation that the modeled reconciliation claims.
    """
    reconciliation = outputs.get("reconciliation")
    if not isinstance(context, dict) or not isinstance(reconciliation, dict):
        return outputs

    verification = reconciliation.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "pass":
        return outputs

    owner = reconciliation.get("authoritative_owner")
    dependencies = reconciliation.get("dependencies") or []
    truth = context.get("confirmed_truth")
    owner_state = context.get("owner_state")
    consumer_states = context.get("consumer_states")
    verified = context.get("verified_states")
    residual = list(verification.get("residual_discrepancies") or [])

    if not _nonempty_string(owner):
        residual.append("Authoritative owner is missing from reconciliation output.")
        owner = ""
    if not isinstance(dependencies, list) or any(not _nonempty_string(item) for item in dependencies):
        residual.append("Dependency list is missing or malformed during completion verification.")
        dependencies = []
    if not _nonempty_string(truth):
        residual.append("Confirmed truth is missing during completion verification.")
    if not isinstance(consumer_states, dict):
        residual.append("Consumer pre-action state is missing or malformed.")
        consumer_states = {}

    valid_verified = isinstance(verified, dict) and all(
        _nonempty_string(target) and _nonempty_string(state)
        for target, state in verified.items()
    )
    if not valid_verified:
        residual.append("Post-action verification state is missing or malformed.")
        verified = {}

    if owner in dependencies:
        residual.append(f"Authoritative owner {owner} cannot also be a dependent consumer.")

    targets = [owner, *(target for target in dependencies if target != owner)]
    for target in targets:
        if target not in verified:
            residual.append(f"Target {target} has no post-action verification state.")
        elif verified[target] != truth:
            residual.append(
                f"Post-action verification for target {target} did not match confirmed truth."
            )

    for target in sorted(set(verified) - {owner} - set(dependencies)):
        residual.append(f"Consumer {target} is absent from the dependency map.")

    owner_update_evidence: str | None = None
    if owner_state != truth:
        owner_update_evidence = _completed_action_evidence(
            context.get("authoritative_update"), target=owner
        )
        if owner_update_evidence is None:
            residual.append(
                f"Authoritative update for {owner} lacks explicit completed action evidence."
            )

    raw_propagation = context.get("propagation")
    propagation_by_target: dict[str, str] = {}
    if isinstance(raw_propagation, list):
        for item in raw_propagation:
            if not isinstance(item, dict):
                residual.append("Propagation completion evidence contains a malformed record.")
                continue
            target = item.get("target")
            if not _nonempty_string(target):
                residual.append("Propagation completion evidence contains a malformed target.")
                continue
            if target in propagation_by_target:
                residual.append(f"Propagation completion evidence for {target} is duplicated.")
                continue
            evidence = _completed_action_evidence(item, target=target)
            if evidence is None:
                residual.append(
                    f"Propagation for {target} lacks explicit completed action evidence."
                )
                continue
            propagation_by_target[target] = evidence
    else:
        raw_propagation = []

    changed_dependencies = {
        target
        for target in dependencies
        if consumer_states.get(target) != truth
    }
    for target in sorted(changed_dependencies - set(propagation_by_target)):
        residual.append(
            f"Propagation for {target} lacks explicit completed action evidence."
        )
    for target in sorted(set(propagation_by_target) - set(dependencies)):
        residual.append(
            f"Propagation completion evidence names undeclared dependency {target}."
        )

    final_verification = context.get("verification")
    final_verification_valid = (
        isinstance(final_verification, dict)
        and final_verification.get("status") == "pass"
        and _nonempty_string(final_verification.get("details"))
        and final_verification.get("residual_discrepancies") == []
    )
    if not final_verification_valid:
        residual.append("Final consistency verification lacks explicit passing evidence.")

    residual = list(dict.fromkeys(residual))
    if not residual:
        for action in reconciliation.get("actions", []):
            target = action.get("target")
            action_type = action.get("action_type")
            if action_type == "update_authority" and target == owner and owner_update_evidence:
                action["evidence"] = owner_update_evidence
            elif action_type == "propagate" and target in propagation_by_target:
                action["evidence"] = propagation_by_target[target]
            elif action_type == "verify" and final_verification_valid:
                action["evidence"] = final_verification["details"]
        verification["details"] = (
            verification.get("details", "")
            + " Explicit action-completion evidence and post-action state verified the authoritative owner and all dependencies."
        ).strip()
        return outputs

    def target_matches_truth(target: str) -> bool:
        return target in verified and verified[target] == truth

    for action in reconciliation.get("actions", []):
        target = action.get("target")
        action_type = action.get("action_type")
        if action_type == "update_authority":
            if not target_matches_truth(target):
                action["status"] = "failed"
            elif owner_update_evidence is None:
                action["status"] = "blocked"
            else:
                action["status"] = "completed"
                action["evidence"] = owner_update_evidence
        elif action_type == "propagate":
            if not target_matches_truth(target):
                action["status"] = "failed"
            elif target not in propagation_by_target:
                action["status"] = "blocked"
            else:
                action["status"] = "completed"
                action["evidence"] = propagation_by_target[target]
        elif action_type == "verify":
            action["status"] = (
                "completed" if final_verification_valid and target_matches_truth(target) else "failed"
            )
            if action["status"] == "completed":
                action["evidence"] = final_verification["details"]

    verification["status"] = "fail"
    verification["details"] = (
        "Reconciliation completion was rejected because explicit action evidence or post-action verification was incomplete or contradictory."
    )
    verification["residual_discrepancies"] = residual

    trace = outputs.get("trace")
    if isinstance(trace, dict):
        trace["status"] = "blocked"
        for event in trace.get("events", []):
            if event.get("event_type") == "reconciliation":
                event["status"] = "incomplete"
            elif event.get("event_type") == "verification":
                event["status"] = "fail"

    result = outputs.get("result")
    if isinstance(result, dict):
        result["status"] = "blocked"
        result["finding"] = (
            "Routine reconciliation is incomplete; explicit action completion and post-action state were not both verified."
        )

    return outputs
