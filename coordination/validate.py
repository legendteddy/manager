#!/usr/bin/env python3
"""Fail-closed structural checks for durable parallel-worker coordination.

This validator detects coordination hazards. It does not assign authority, approve
scope, or select implementations.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
ACTIVE = {"declared", "active", "blocked", "ready_for_handoff"}
HISTORICAL = {"superseded", "withdrawn"}
CLAIM_STATUS = ACTIVE | HISTORICAL
DEP_STATUS = {"pending", "ready", "consumed", "superseded"}
HOTSPOT_MODE = {"exclusive", "sequenced", "integrator_only", "intentional_verification"}
STALE_DISPOSITION = {"unrelated_reviewed", "rebase_required", "integrator_sequence", "superseded"}
HANDOFF_STATUS = {"blocked", "no_change", "ready_for_integration"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return isinstance(value, list) and (not nonempty or bool(value)) and all(_text(x) for x in value)


def _path(spec: str) -> tuple[str, bool]:
    value = spec.strip().lstrip("./")
    prefix = value.endswith("/") or value.endswith("/**")
    if value.endswith("/**"):
        value = value[:-3]
    return value.rstrip("/"), prefix


def _overlap(left: str, right: str) -> bool:
    lp, lprefix = _path(left)
    rp, rprefix = _path(right)
    return (
        not lp
        or not rp
        or lp == rp
        or (lprefix and rp.startswith(lp + "/"))
        or (rprefix and lp.startswith(rp + "/"))
    )


def _covered(path: str, specs: list[str]) -> bool:
    target, _ = _path(path)
    for spec in specs:
        base, prefix = _path(spec)
        if target == base or (prefix and target.startswith(base + "/")):
            return True
    return False


def _finding(code: str, message: str, *workers: str) -> dict[str, Any]:
    return {"code": code, "message": message, "workers": list(workers)}


def _deps_link(left: dict[str, Any], right: dict[str, Any]) -> bool:
    pair = {left.get("worker_id"), right.get("worker_id")}
    return any(
        isinstance(dep, dict)
        and {dep.get("producer_worker"), dep.get("consumer_worker")} == pair
        for claim in (left, right)
        for dep in claim.get("dependencies", [])
    )


def _resolve_current_claims(
    claims: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Resolve current claims without trusting serialized list order."""
    histories: dict[str, list[dict[str, Any]]] = {}
    invalid: set[str] = set()
    for claim in claims:
        worker = claim.get("worker_id")
        if not _text(worker):
            continue
        worker = str(worker)
        histories.setdefault(worker, []).append(claim)
        revision = claim.get("claim_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            invalid.add(worker)
        if claim.get("status") not in CLAIM_STATUS:
            invalid.add(worker)

    current_by_worker: dict[str, dict[str, Any]] = {}
    for worker, history in histories.items():
        by_revision: dict[int, dict[str, Any]] = {}
        for claim in history:
            revision = claim.get("claim_revision")
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
                continue
            if revision in by_revision:
                findings.append(
                    _finding(
                        "CLAIM_REVISION_DUPLICATE",
                        f"{worker}: duplicate claim revision {revision}",
                        worker,
                    )
                )
                invalid.add(worker)
            else:
                by_revision[revision] = claim

        current = [claim for claim in history if claim.get("status") in ACTIVE]
        if len(current) > 1:
            findings.append(
                _finding(
                    "MULTIPLE_CURRENT_CLAIMS",
                    f"{worker}: multiple simultaneous current claims",
                    worker,
                )
            )
            invalid.add(worker)
        elif len(current) == 1 and by_revision:
            current_revision = current[0].get("claim_revision")
            highest_revision = max(by_revision)
            if current_revision != highest_revision:
                findings.append(
                    _finding(
                        "CLAIM_CURRENT_NOT_HIGHEST",
                        f"{worker}: current revision {current_revision!r} is not highest revision {highest_revision}",
                        worker,
                    )
                )
                invalid.add(worker)

        if worker not in invalid and len(current) == 1:
            current_by_worker[worker] = current[0]

    return current_by_worker, histories


def lint_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    claims = state.get("claims", [])
    handoffs = state.get("handoffs", [])
    if not isinstance(claims, list):
        return [_finding("CLAIMS_INVALID", "claims must be a list")]
    if not isinstance(handoffs, list):
        return [_finding("HANDOFFS_INVALID", "handoffs must be a list")]

    current_main = state.get("current_main_sha")
    if current_main is not None and (not isinstance(current_main, str) or not SHA.fullmatch(current_main)):
        findings.append(_finding("CURRENT_MAIN_SHA_INVALID", "current_main_sha must be an exact SHA"))

    valid_claims = [claim for claim in claims if isinstance(claim, dict)]
    if len(valid_claims) != len(claims):
        findings.append(_finding("CLAIM_INVALID", "every claim must be an object"))

    for claim in valid_claims:
        worker = claim.get("worker_id") if _text(claim.get("worker_id")) else "<unknown>"
        for field in ("worker_id", "branch", "mission_id", "mission_summary", "integrator"):
            if not _text(claim.get(field)):
                findings.append(_finding("CLAIM_FIELD_MISSING", f"{worker}: missing {field}", worker))
        revision = claim.get("claim_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            findings.append(_finding("CLAIM_REVISION_INVALID", f"{worker}: invalid claim_revision", worker))
        base_sha = claim.get("base_sha")
        if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
            findings.append(_finding("BASE_SHA_INVALID", f"{worker}: base_sha must be exact", worker))
        for field in ("success_criteria", "scope_keys", "scope", "exclusions"):
            if not _strings(claim.get(field), nonempty=True):
                findings.append(
                    _finding(
                        "CLAIM_FIELD_MISSING",
                        f"{worker}: {field} must be a non-empty string list",
                        worker,
                    )
                )
        if not _strings(claim.get("write_set", [])):
            findings.append(_finding("WRITE_SET_INVALID", f"{worker}: write_set must be a string list", worker))
        if claim.get("status") not in CLAIM_STATUS:
            findings.append(_finding("CLAIM_STATUS_INVALID", f"{worker}: invalid claim status", worker))
        if (
            current_main
            and claim.get("status") in ACTIVE
            and base_sha != current_main
            and claim.get("stale_base_disposition") not in STALE_DISPOSITION
        ):
            findings.append(_finding("STALE_BASE_UNRECONCILED", f"{worker}: stale base has no disposition", worker))

        for hotspot in claim.get("hotspots", []):
            valid = (
                isinstance(hotspot, dict)
                and _text(hotspot.get("name"))
                and hotspot.get("mode") in HOTSPOT_MODE
                and _strings(hotspot.get("coordinated_with", []))
            )
            if not valid:
                findings.append(_finding("HOTSPOT_DECLARATION_INVALID", f"{worker}: invalid hotspot declaration", worker))
        for dep in claim.get("dependencies", []):
            valid = (
                isinstance(dep, dict)
                and all(_text(dep.get(key)) for key in ("producer_worker", "consumer_worker", "interface"))
                and dep.get("status") in DEP_STATUS
                and isinstance(dep.get("blocking"), bool)
            )
            if not valid:
                findings.append(_finding("DEPENDENCY_INVALID", f"{worker}: invalid dependency", worker))
                continue
            if dep["consumer_worker"] != worker:
                findings.append(_finding("DEPENDENCY_CONSUMER_MISMATCH", f"{worker}: dependency consumer mismatch", worker))
            if dep["producer_worker"] == worker:
                findings.append(_finding("DEPENDENCY_SELF_REFERENCE", f"{worker}: dependency producer cannot be self", worker))

    current_by_worker, histories = _resolve_current_claims(valid_claims, findings)

    active = [claim for claim in valid_claims if claim.get("status") in ACTIVE]
    for index, left in enumerate(active):
        lw = str(left.get("worker_id"))
        for right in active[index + 1 :]:
            rw = str(right.get("worker_id"))
            if lw == rw:
                continue
            pair = tuple(sorted((lw, rw)))
            if left.get("branch") == right.get("branch"):
                findings.append(_finding("BRANCH_COLLISION", f"{pair}: same branch", *pair))
            shared_scope = sorted(set(left.get("scope_keys", [])) & set(right.get("scope_keys", [])))
            if shared_scope:
                findings.append(_finding("SCOPE_COLLISION", f"{pair}: duplicate scope keys {shared_scope}", *pair))
            for left_path in left.get("write_set", []):
                for right_path in right.get("write_set", []):
                    if _overlap(left_path, right_path):
                        findings.append(
                            _finding(
                                "WRITE_SET_CONFLICT",
                                f"{pair}: {left_path!r} overlaps {right_path!r}",
                                *pair,
                            )
                        )
            left_hotspots = {
                item["name"]: item
                for item in left.get("hotspots", [])
                if isinstance(item, dict) and _text(item.get("name"))
            }
            right_hotspots = {
                item["name"]: item
                for item in right.get("hotspots", [])
                if isinstance(item, dict) and _text(item.get("name"))
            }
            for name in set(left_hotspots) & set(right_hotspots):
                lh, rh = left_hotspots[name], right_hotspots[name]
                if lh.get("mode") == rh.get("mode") == "intentional_verification":
                    continue
                mutual = rw in lh.get("coordinated_with", []) and lw in rh.get("coordinated_with", [])
                sequenced = lh.get("mode") == rh.get("mode") == "sequenced"
                if not (mutual and sequenced and _deps_link(left, right)):
                    findings.append(
                        _finding(
                            "HOTSPOT_UNCOORDINATED",
                            f"{pair}: hotspot {name!r} lacks reciprocal sequencing",
                            *pair,
                        )
                    )

    branch_heads = state.get("current_branch_heads", {})
    pr_heads = state.get("current_pr_heads", {})
    for handoff in handoffs:
        if not isinstance(handoff, dict):
            findings.append(_finding("HANDOFF_INVALID", "every handoff must be an object"))
            continue
        worker = str(handoff.get("worker_id"))
        claim = current_by_worker.get(worker)
        if claim is None:
            code = "HANDOFF_WITHOUT_CURRENT_CLAIM" if worker in histories else "HANDOFF_WITHOUT_CLAIM"
            findings.append(_finding(code, f"{worker}: no unambiguous current claim", worker))
            continue
        if handoff.get("status") not in HANDOFF_STATUS:
            findings.append(_finding("HANDOFF_STATUS_INVALID", f"{worker}: invalid handoff status", worker))
        if handoff.get("mission_id") != claim.get("mission_id"):
            findings.append(_finding("MISSION_MISMATCH", f"{worker}: mission differs from claim", worker))
        if handoff.get("branch") != claim.get("branch"):
            findings.append(_finding("HANDOFF_BRANCH_MISMATCH", f"{worker}: branch differs from claim", worker))
        if handoff.get("claim_revision") != claim.get("claim_revision"):
            findings.append(_finding("HANDOFF_CLAIM_STALE", f"{worker}: stale claim revision", worker))

        head = handoff.get("head_sha")
        if not isinstance(head, str) or not SHA.fullmatch(head):
            findings.append(_finding("HANDOFF_HEAD_INVALID", f"{worker}: invalid handoff head", worker))
        if isinstance(branch_heads, dict) and branch_heads.get(claim.get("branch")) != head:
            if branch_heads.get(claim.get("branch")):
                findings.append(_finding("BRANCH_HEAD_STALE", f"{worker}: branch advanced after evidence", worker))

        changed = handoff.get("changed_files", [])
        if not _strings(changed):
            findings.append(_finding("CHANGED_FILES_INVALID", f"{worker}: changed_files must be a string list", worker))
        else:
            for path in changed:
                if not _covered(path, claim.get("write_set", [])):
                    findings.append(_finding("OUT_OF_SCOPE_CHANGE", f"{worker}: {path!r} outside write_set", worker))

        if handoff.get("status") == "ready_for_integration":
            if not _text(handoff.get("pr")):
                findings.append(_finding("PR_REFERENCE_MISSING", f"{worker}: PR reference missing", worker))
            pr_head = handoff.get("pr_head_sha")
            if not isinstance(pr_head, str) or not SHA.fullmatch(pr_head):
                findings.append(_finding("PR_HEAD_INVALID", f"{worker}: invalid PR head", worker))
            elif pr_head != head:
                findings.append(_finding("PR_HEAD_MISMATCH", f"{worker}: PR head differs from tested head", worker))
            if isinstance(pr_heads, dict) and pr_heads.get(handoff.get("pr")) not in (None, pr_head):
                findings.append(_finding("PR_HEAD_STALE", f"{worker}: PR advanced after evidence", worker))
            if not _text(handoff.get("summary")) or not _text(handoff.get("selection_notes")):
                findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{worker}: missing summary/selection notes", worker))
            tests = handoff.get("tests")
            if not isinstance(tests, list) or not tests:
                findings.append(_finding("TEST_EVIDENCE_MISSING", f"{worker}: no tests actually run", worker))
            elif any(
                not isinstance(test, dict)
                or not _text(test.get("command"))
                or test.get("result") not in {"pass", "fail"}
                for test in tests
            ):
                findings.append(_finding("TEST_EVIDENCE_INVALID", f"{worker}: malformed test evidence", worker))
            for dep in claim.get("dependencies", []):
                if isinstance(dep, dict) and dep.get("blocking") is True and dep.get("status") == "pending":
                    findings.append(
                        _finding(
                            "BLOCKING_DEPENDENCY_UNRESOLVED",
                            f"{worker}: pending blocker from {dep.get('producer_worker')}",
                            worker,
                        )
                    )

        for field in ("risks", "assumptions", "blockers", "dependencies_consumed", "out_of_scope_findings"):
            if not isinstance(handoff.get(field), list):
                findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{worker}: missing list field {field}", worker))
        for item in handoff.get("out_of_scope_findings", []):
            valid = (
                isinstance(item, dict)
                and all(_text(item.get(key)) for key in ("finding", "owner", "durable_ref"))
                and item.get("status") in {"posted", "acknowledged"}
            )
            if not valid:
                findings.append(
                    _finding(
                        "OUT_OF_SCOPE_HANDOFF_INVALID",
                        f"{worker}: out-of-scope finding lacks durable receipt",
                        worker,
                    )
                )
    return findings


BASE = "a" * 40
HEAD = "3" * 40


def _claim(worker: str = "03", **changes: Any) -> dict[str, Any]:
    value = {
        "worker_id": worker,
        "claim_revision": 1,
        "branch": f"validation/{worker}-work",
        "base_sha": BASE,
        "mission_id": "issue-27",
        "mission_summary": "Validate safe ten-chat parallel engineering.",
        "success_criteria": ["non-overlap", "durable handoffs"],
        "integrator": "10",
        "scope_keys": [f"scope-{worker}"],
        "scope": [f"owned-{worker}"],
        "exclusions": ["other-worker-domains"],
        "write_set": [f"work/{worker}/"],
        "hotspots": [],
        "dependencies": [],
        "status": "active",
    }
    value.update(changes)
    return value


def _handoff(**changes: Any) -> dict[str, Any]:
    value = {
        "worker_id": "03",
        "claim_revision": 1,
        "mission_id": "issue-27",
        "branch": "validation/03-work",
        "head_sha": HEAD,
        "status": "ready_for_integration",
        "pr": "#40",
        "pr_head_sha": HEAD,
        "summary": "Bounded coordination fix.",
        "selection_notes": "Isolated implementation for integrator selection.",
        "changed_files": ["work/03/fix.txt"],
        "tests": [{"command": "coordination evals", "result": "pass"}],
        "risks": [],
        "assumptions": [],
        "blockers": [],
        "dependencies_consumed": [],
        "out_of_scope_findings": [],
    }
    value.update(changes)
    return value


def scenario(case_id: str) -> dict[str, Any]:
    current = _claim()
    state: dict[str, Any] = {
        "current_main_sha": BASE,
        "claims": [current],
        "handoffs": [],
        "current_branch_heads": {"validation/03-work": HEAD},
        "current_pr_heads": {"#40": HEAD},
    }
    if case_id == "overlapping-write-set-rejected":
        state["claims"].append(_claim("08", write_set=["work/03/fix.txt"]))
    elif case_id == "silent-scope-expansion-rejected":
        state["handoffs"] = [_handoff(changed_files=["work/03/fix.txt", "runtime/engine.py"])]
    elif case_id == "shared-hotspot-without-sequencing-rejected":
        current["hotspots"] = [{"name": "evals", "mode": "sequenced", "coordinated_with": []}]
        state["claims"].append(_claim("01", hotspots=[{"name": "evals", "mode": "sequenced", "coordinated_with": []}]))
    elif case_id == "sequenced-shared-hotspot-accepted":
        current["hotspots"] = [{"name": "evals", "mode": "sequenced", "coordinated_with": ["01"]}]
        current["dependencies"] = [
            {
                "producer_worker": "01",
                "consumer_worker": "03",
                "interface": "eval contract",
                "blocking": False,
                "status": "ready",
            }
        ]
        state["claims"].append(
            _claim("01", hotspots=[{"name": "evals", "mode": "sequenced", "coordinated_with": ["03"]}])
        )
    elif case_id == "unfinished-blocking-dependency-rejected":
        current["dependencies"] = [
            {
                "producer_worker": "01",
                "consumer_worker": "03",
                "interface": "required artifact",
                "blocking": True,
                "status": "pending",
            }
        ]
        state["handoffs"] = [_handoff()]
    elif case_id == "stale-base-without-disposition-rejected":
        current["base_sha"] = "b" * 40
    elif case_id == "advanced-pr-state-rejected":
        state["handoffs"] = [_handoff()]
        state["current_pr_heads"]["#40"] = "4" * 40
    elif case_id == "global-mission-mismatch-rejected":
        state["handoffs"] = [_handoff(mission_id="local-only")]
    elif case_id == "lost-out-of-scope-finding-rejected":
        state["handoffs"] = [
            _handoff(out_of_scope_findings=[{"finding": "adjacent defect", "owner": "04", "status": "local_only"}])
        ]
    elif case_id == "completion-without-test-evidence-rejected":
        state["handoffs"] = [_handoff(tests=[])]
    elif case_id == "insufficient-integrator-selection-packet-rejected":
        state["handoffs"] = [_handoff(selection_notes="")]
    elif case_id in {
        "superseded-history-before-current-accepted",
        "superseded-history-after-current-accepted",
    }:
        historical = _claim(claim_revision=1, status="superseded")
        current = _claim(claim_revision=2, status="active")
        if case_id.endswith("before-current-accepted"):
            state["claims"] = [historical, current]
        else:
            state["claims"] = [current, historical]
        state["handoffs"] = [_handoff(claim_revision=2)]
    elif case_id == "duplicate-claim-revision-rejected":
        state["claims"] = [
            _claim(claim_revision=1, status="superseded"),
            _claim(claim_revision=1, status="active"),
        ]
    elif case_id == "multiple-current-claims-rejected":
        state["claims"] = [
            _claim(claim_revision=1, status="active"),
            _claim(claim_revision=2, status="ready_for_handoff"),
        ]
    elif case_id == "current-claim-not-highest-revision-rejected":
        state["claims"] = [
            _claim(claim_revision=1, status="active"),
            _claim(claim_revision=2, status="superseded"),
        ]
    elif case_id == "complete-handoff-accepted":
        state["handoffs"] = [
            _handoff(
                out_of_scope_findings=[
                    {
                        "finding": "adjacent defect",
                        "owner": "04",
                        "durable_ref": "issue-27#comment",
                        "status": "acknowledged",
                    }
                ]
            )
        ]
    else:
        raise ValueError(f"unknown coordination scenario {case_id!r}")
    return state


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(case.get("state")) if isinstance(case.get("state"), dict) else scenario(case["case_id"])
    findings = lint_state(state)
    codes = sorted({finding["code"] for finding in findings})
    expected = case.get("expected", {})
    passed = (not findings) == expected.get("valid") and all(code in codes for code in expected.get("codes", []))
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "actual_valid": not findings,
        "codes": codes,
        "findings": findings,
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("coordination eval file must contain a list of objects")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run structural parallel-coordination evals.")
    parser.add_argument("case_file", nargs="?", default="evals/coordination/cases.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = [evaluate_case(case) for case in load_cases(Path(args.case_file))]
    failed = [result for result in results if not result["passed"]]
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['case_id']}")
        print(f"{len(results) - len(failed)}/{len(results)} coordination eval cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
