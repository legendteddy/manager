from __future__ import annotations

from typing import Any


def enforce_reconciliation_completion(
    context: Any,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed when a routine reconciliation lacks observed post-action state."""
    reconciliation = outputs.get("reconciliation")
    if not isinstance(context, dict) or not isinstance(reconciliation, dict):
        return outputs

    verification = reconciliation.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "pass":
        return outputs

    owner = reconciliation.get("authoritative_owner")
    dependencies = reconciliation.get("dependencies") or []
    truth = context.get("confirmed_truth")
    verified = context.get("verified_states")
    residual = list(verification.get("residual_discrepancies") or [])

    valid_verified = isinstance(verified, dict) and all(
        isinstance(target, str)
        and target
        and isinstance(state, str)
        and state
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

    if not residual:
        verification["details"] = (
            verification.get("details", "")
            + " Post-action state matched confirmed truth for the authoritative owner and all dependencies."
        ).strip()
        return outputs

    def status_for(target: str) -> str:
        if target not in verified:
            return "blocked"
        return "completed" if verified[target] == truth else "failed"

    filtered_actions: list[dict[str, Any]] = []
    owner_action_seen = False
    for action in reconciliation.get("actions", []):
        target = action.get("target")
        if target == owner:
            if owner_action_seen:
                continue
            owner_action_seen = True
        if target in targets:
            action["status"] = status_for(target)
        filtered_actions.append(action)
    reconciliation["actions"] = filtered_actions

    verification["status"] = "fail"
    verification["details"] = "Reconciliation completion was rejected because post-action evidence was incomplete or contradictory."
    verification["residual_discrepancies"] = list(dict.fromkeys(residual))

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
            "Routine reconciliation is incomplete; post-action state does not verify all declared targets."
        )

    return outputs
