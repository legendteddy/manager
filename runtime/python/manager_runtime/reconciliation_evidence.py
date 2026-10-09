from __future__ import annotations

from typing import Any


def enforce_reconciliation_completion(
    context: Any,
    outputs: dict[str, Any],
) -> dict[str, Any]:
    """Reject PASS unless declared writes and final verification have receipts.

    This reference runtime validates caller-supplied receipt structure. It cannot
    authenticate that a repository write or verification actually occurred.
    """
    reconciliation = outputs.get("reconciliation")
    if not isinstance(context, dict) or not isinstance(reconciliation, dict):
        return outputs

    verification = reconciliation.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "pass":
        return outputs

    owner = reconciliation.get("authoritative_owner")
    dependencies = reconciliation.get("dependencies")
    truth = context.get("confirmed_truth")
    owner_state = context.get("owner_state")
    consumer_states = context.get("consumer_states")
    verified = context.get("verified_states")
    residual_value = verification.get("residual_discrepancies", [])
    residual = (
        [item for item in residual_value if isinstance(item, str)]
        if isinstance(residual_value, list)
        else []
    )

    valid_dependencies = isinstance(dependencies, list) and all(
        isinstance(target, str) and target for target in dependencies
    )
    if not isinstance(owner, str) or not owner:
        residual.append("Authoritative owner is missing or malformed.")
        owner = ""
    if not valid_dependencies:
        residual.append("Dependency inventory is missing or malformed.")
        dependencies = []
    if not isinstance(truth, str) or not truth:
        residual.append("Confirmed truth is missing or malformed.")
        truth = ""

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

    if not isinstance(consumer_states, dict) or any(
        not isinstance(target, str)
        or not target
        or not isinstance(state, str)
        or not state
        for target, state in consumer_states.items()
    ):
        residual.append("Current consumer-state evidence is missing or malformed.")
        consumer_states = {}

    targets = [owner, *(target for target in dependencies if target != owner)]
    if owner and owner in dependencies:
        residual.append(f"Authoritative owner {owner} cannot also be a dependent consumer.")

    for target in targets:
        if target not in verified:
            residual.append(f"Target {target} has no post-action verification state.")
        elif verified[target] != truth:
            residual.append(
                f"Post-action verification for target {target} did not match confirmed truth."
            )

    for target in sorted(set(verified) - set(targets)):
        residual.append(f"Consumer {target} is absent from the dependency map.")

    required_mutations: set[tuple[str, str]] = set()
    if owner and owner_state != truth:
        required_mutations.add(("update_authority", owner))
    for target in dependencies:
        if target != owner and consumer_states.get(target) != truth:
            required_mutations.add(("propagate", target))

    receipts_value = context.get("action_receipts")
    receipts: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(receipts_value, list):
        residual.append("Action receipts are missing or malformed.")
    else:
        for item in receipts_value:
            if not isinstance(item, dict):
                residual.append("Action receipt is malformed.")
                continue
            action_type = item.get("action_type")
            target = item.get("target")
            if not isinstance(action_type, str) or not isinstance(target, str):
                residual.append("Action receipt target or action type is malformed.")
                continue
            key = (action_type, target)
            if key not in required_mutations:
                residual.append(
                    f"Action receipt for {action_type} on {target} does not match a required mutation."
                )
                continue
            if key in receipts:
                residual.append(f"Duplicate action receipt for {action_type} on {target}.")
                continue
            receipts[key] = item

    for action_type, target in sorted(required_mutations):
        receipt = receipts.get((action_type, target))
        if receipt is None:
            residual.append(f"Required {action_type} receipt for target {target} is missing.")
        elif receipt.get("status") != "completed":
            residual.append(f"Required {action_type} receipt for target {target} is not completed.")
        if receipt is not None and not (
            isinstance(receipt.get("evidence"), str) and receipt["evidence"].strip()
        ):
            residual.append(f"Required {action_type} receipt for target {target} has no evidence.")

    final_receipt = context.get("final_verification_receipt")
    if (
        not isinstance(final_receipt, dict)
        or final_receipt.get("status") != "pass"
        or not isinstance(final_receipt.get("details"), str)
        or not final_receipt["details"].strip()
        or final_receipt.get("residual_discrepancies") != []
    ):
        residual.append(
            "Final verification receipt is missing, malformed, or reports unresolved discrepancies."
        )

    actions = reconciliation.get("actions", [])
    if not isinstance(actions, list):
        actions = []
        reconciliation["actions"] = actions

    if not residual:
        for action in actions:
            if not isinstance(action, dict):
                continue
            key = (action.get("action_type"), action.get("target"))
            receipt = receipts.get(key)
            if receipt is not None:
                action["evidence"] = receipt["evidence"]
        verification["details"] = (
            str(verification.get("details", "")).strip()
            + " Required action receipts and final verification receipt were structurally validated. "
            "The reference runtime does not authenticate receipt origin."
        ).strip()
        return outputs

    filtered_actions = []
    for action in actions:
        if not isinstance(action, dict):
            filtered_actions.append(action)
            continue
        target = action.get("target")
        action_type = action.get("action_type")
        if target == owner and action_type == "propagate":
            continue
        if target in targets:
            if target not in verified:
                action["status"] = "blocked"
            elif verified[target] != truth:
                action["status"] = "failed"
        if action_type in {"update_authority", "propagate"}:
            receipt = receipts.get((action_type, target))
            evidence = receipt.get("evidence") if receipt is not None else None
            if (
                receipt is None
                or receipt.get("status") != "completed"
                or not isinstance(evidence, str)
                or not evidence.strip()
            ):
                action["status"] = "blocked"
        filtered_actions.append(action)
    reconciliation["actions"] = filtered_actions

    verification["status"] = "fail"
    verification["details"] = (
        "Reconciliation completion was rejected because action receipts, final verification, "
        "or post-action states were incomplete or contradictory."
    )
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
            "Routine reconciliation is incomplete; action receipts, final verification, "
            "or post-action states are missing or contradictory."
        )

    return outputs
