from __future__ import annotations

from typing import Any

# Mirrors Manager's documented local authority order. Evidence-only sources are
# intentionally absent so they can never become execution authority.
AUTHORITY_RANK = {
    "host_platform": 0,
    "maintainer_instruction": 1,
    "repository_governance": 2,
    "subsystem_rule": 3,
    "authoritative_state": 4,
}
EVIDENCE_ONLY_KINDS = {
    "external_content",
    "retrieved_content",
    "older_context",
    "inferred_memory",
}
LOCAL_CONSTRAINT_KINDS = {"repository_governance", "subsystem_rule"}


def _output(
    task: dict[str, Any],
    *,
    trace_status: str,
    result_status: str,
    workflow: str,
    events: list[dict[str, str]],
    finding: str,
) -> dict[str, Any]:
    classification = task["classification"]
    return {
        "trace": {
            "run_id": f"run:{task['task_id']}",
            "status": trace_status,
            "classification": {
                "materiality": classification["materiality"],
                "consequence": classification["consequence"],
                "uncertainty": classification.get("uncertainty", "low"),
            },
            "workflow": workflow,
            "capabilities": [],
            "events": events,
            "residual_uncertainty": [],
        },
        "result": {
            "result_id": f"result:{task['task_id']}",
            "status": result_status,
            "finding": finding,
            "assumptions": [],
            "risks": [],
            "uncertainties": [],
            "confidence": "high",
            "owner_decision_required": False,
            "decision_request": None,
            "next_handoff": None,
        },
    }


def _blocked(
    task: dict[str, Any],
    *,
    workflow: str,
    status: str,
    summary: str,
    finding: str,
    reference: str | None = None,
) -> dict[str, Any]:
    event = {"event_type": "authority", "status": status, "summary": summary}
    if reference:
        event["reference"] = reference
    return _output(
        task,
        trace_status="blocked",
        result_status="blocked",
        workflow=workflow,
        events=[event],
        finding=finding,
    )


def _freshness(source: dict[str, Any]) -> int:
    value = source.get("freshness", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def run_instruction_precedence(
    task: dict[str, Any],
    prior_state: dict[str, Any],
) -> dict[str, Any] | None:
    """Model bootstrap and authority precedence for deterministic evals.

    Hosts classify sources and list required repository context. This evaluator
    never promotes retrieved text, conversation history, or inferred memory into
    execution authority.
    """

    context = prior_state.get("precedence_context")
    if context is None:
        return None
    if not isinstance(context, dict):
        return _blocked(
            task,
            workflow="context_loading",
            status="invalid_precedence_context",
            summary="Malformed precedence context failed closed.",
            finding="Execution is blocked because instruction-precedence context is malformed.",
        )

    required = context.get("required_context", [])
    loaded = context.get("loaded_context", [])
    valid_inventory = (
        isinstance(required, list)
        and all(isinstance(item, str) and item for item in required)
        and isinstance(loaded, list)
        and all(isinstance(item, str) and item for item in loaded)
    )
    if not valid_inventory:
        return _blocked(
            task,
            workflow="context_loading",
            status="invalid_context_inventory",
            summary="Malformed governance-context inventory failed closed.",
            finding="Execution is blocked because governance context inventory is malformed.",
        )

    missing = [item for item in required if item not in loaded]
    if missing:
        return _blocked(
            task,
            workflow="context_loading",
            status="missing_required_context",
            reference=missing[0],
            summary="Required repository governance context was not loaded.",
            finding="Execution is blocked until all required repository governance context is loaded.",
        )

    target_scope = context.get("target_scope")
    sources = context.get("instruction_sources", [])
    if not isinstance(target_scope, str) or not target_scope:
        return _blocked(
            task,
            workflow="instruction_precedence",
            status="missing_target_scope",
            summary="Authority resolution requires an explicit target scope.",
            finding="Execution is blocked because the instruction target scope is unknown.",
        )
    if not isinstance(sources, list):
        return _blocked(
            task,
            workflow="instruction_precedence",
            status="invalid_instruction_sources",
            summary="Malformed instruction-source inventory failed closed.",
            finding="Execution is blocked because instruction sources are malformed.",
        )

    events: list[dict[str, str]] = []
    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    local_constraints: list[str] = []

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            events.append({
                "event_type": "authority",
                "status": "ignored_untrusted_source",
                "summary": "Malformed instruction-like input was treated as evidence only.",
            })
            continue

        source_id = source.get("source_id")
        kind = source.get("source_kind")
        scope = source.get("scope")
        if not all(isinstance(value, str) and value for value in (source_id, kind, scope)):
            events.append({
                "event_type": "authority",
                "status": "ignored_untrusted_source",
                "summary": "Unclassified instruction-like input was treated as evidence only.",
            })
            continue

        if kind in EVIDENCE_ONLY_KINDS or kind not in AUTHORITY_RANK:
            events.append({
                "event_type": "authority",
                "status": "ignored_untrusted_source",
                "reference": source_id,
                "summary": "Evidence-only or unknown content was not treated as authority.",
            })
            continue
        if scope not in {target_scope, "*"}:
            events.append({
                "event_type": "authority",
                "status": "ignored_scope_mismatch",
                "reference": source_id,
                "summary": "Authority for another scope was ignored.",
            })
            continue

        if source.get("constraint_only") is True:
            if kind in LOCAL_CONSTRAINT_KINDS:
                local_constraints.append(source_id)
                events.append({
                    "event_type": "authority",
                    "status": "local_constraint_loaded",
                    "reference": source_id,
                    "summary": "A stricter repository-local constraint was loaded.",
                })
            else:
                events.append({
                    "event_type": "authority",
                    "status": "ignored_invalid_constraint",
                    "reference": source_id,
                    "summary": "This source kind cannot declare a repository-local constraint.",
                })
            continue

        candidates.append((AUTHORITY_RANK[kind], -_freshness(source), -index, source))

    if not candidates:
        events.append({
            "event_type": "authority",
            "status": "no_authoritative_source",
            "summary": "No applicable trusted instruction source remained.",
        })
        return _output(
            task,
            trace_status="blocked",
            result_status="blocked",
            workflow="instruction_precedence",
            events=events,
            finding="Execution is blocked because no applicable authoritative instruction source was available.",
        )

    selected = min(candidates, key=lambda item: item[:3])[3]
    selected_id = selected["source_id"]
    selected_kind = selected["source_kind"]
    events.append({
        "event_type": "authority",
        "status": "selected",
        "reference": selected_id,
        "summary": f"Selected {selected_kind} as the highest applicable authority.",
    })

    # A newer explicit maintainer instruction is itself the higher local authority.
    # Otherwise stricter repository/subsystem constraints remain binding.
    preserved = [] if selected_kind == "maintainer_instruction" else local_constraints
    for source_id in preserved:
        events.append({
            "event_type": "authority",
            "status": "local_constraint_preserved",
            "reference": source_id,
            "summary": "Stricter repository-local governance remained binding.",
        })

    finding = f"Selected authority source {selected_id} for scope {target_scope}."
    if preserved:
        finding += " Preserved local constraints: " + ", ".join(preserved) + "."

    return _output(
        task,
        trace_status="completed",
        result_status="complete",
        workflow="instruction_precedence",
        events=events,
        finding=finding,
    )
