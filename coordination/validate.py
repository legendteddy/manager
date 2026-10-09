#!/usr/bin/env python3
"""Fail-closed structural checks for durable parallel-worker coordination.

This helper detects coordination hazards. It never assigns authority, approves scope,
or selects an implementation.
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
CLAIM_STATUS = ACTIVE | {"superseded", "withdrawn"}
DEP_STATUS = {"pending", "ready", "consumed", "superseded"}
HOTSPOT_MODE = {"exclusive", "sequenced", "integrator_only", "intentional_verification"}
STALE_DISPOSITION = {"unrelated_reviewed", "rebase_required", "integrator_sequence", "superseded"}
HANDOFF_STATUS = {"blocked", "no_change", "ready_for_integration"}


def _text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _strings(v: Any, *, nonempty: bool = False) -> bool:
    return isinstance(v, list) and (not nonempty or bool(v)) and all(_text(x) for x in v)


def _path(spec: str) -> tuple[str, bool]:
    value = spec.strip().lstrip("./")
    prefix = value.endswith("/") or value.endswith("/**")
    if value.endswith("/**"):
        value = value[:-3]
    return value.rstrip("/"), prefix


def _overlap(a: str, b: str) -> bool:
    ap, apre = _path(a)
    bp, bpre = _path(b)
    return (
        not ap
        or not bp
        or ap == bp
        or (apre and bp.startswith(ap + "/"))
        or (bpre and ap.startswith(bp + "/"))
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


def _deps_link(a: dict[str, Any], b: dict[str, Any]) -> bool:
    pair = {a.get("worker_id"), b.get("worker_id")}
    for claim in (a, b):
        for dep in claim.get("dependencies", []):
            if isinstance(dep, dict) and {dep.get("producer_worker"), dep.get("consumer_worker")} == pair:
                return True
    return False


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

    valid_claims = [c for c in claims if isinstance(c, dict)]
    if len(valid_claims) != len(claims):
        findings.append(_finding("CLAIM_INVALID", "every claim must be an object"))

    for c in valid_claims:
        w = c.get("worker_id") if _text(c.get("worker_id")) else "<unknown>"
        for field in ("worker_id", "branch", "mission_id", "mission_summary", "integrator"):
            if not _text(c.get(field)):
                findings.append(_finding("CLAIM_FIELD_MISSING", f"{w}: missing {field}", w))
        if not isinstance(c.get("claim_revision"), int) or isinstance(c.get("claim_revision"), bool) or c["claim_revision"] < 1:
            findings.append(_finding("CLAIM_REVISION_INVALID", f"{w}: invalid claim_revision", w))
        if not isinstance(c.get("base_sha"), str) or not SHA.fullmatch(c["base_sha"]):
            findings.append(_finding("BASE_SHA_INVALID", f"{w}: base_sha must be exact", w))
        for field in ("success_criteria", "scope_keys", "scope", "exclusions"):
            if not _strings(c.get(field), nonempty=True):
                findings.append(_finding("CLAIM_FIELD_MISSING", f"{w}: {field} must be a non-empty string list", w))
        if not _strings(c.get("write_set", [])):
            findings.append(_finding("WRITE_SET_INVALID", f"{w}: write_set must be a string list", w))
        if c.get("status") not in CLAIM_STATUS:
            findings.append(_finding("CLAIM_STATUS_INVALID", f"{w}: invalid claim status", w))
        if current_main and c.get("status") in ACTIVE and c.get("base_sha") != current_main and c.get("stale_base_disposition") not in STALE_DISPOSITION:
            findings.append(_finding("STALE_BASE_UNRECONCILED", f"{w}: stale base has no disposition", w))

        for h in c.get("hotspots", []):
            if not isinstance(h, dict) or not _text(h.get("name")) or h.get("mode") not in HOTSPOT_MODE or not _strings(h.get("coordinated_with", [])):
                findings.append(_finding("HOTSPOT_DECLARATION_INVALID", f"{w}: invalid hotspot declaration", w))
        for d in c.get("dependencies", []):
            if not isinstance(d, dict) or not all(_text(d.get(k)) for k in ("producer_worker", "consumer_worker", "interface")) or d.get("status") not in DEP_STATUS or not isinstance(d.get("blocking"), bool):
                findings.append(_finding("DEPENDENCY_INVALID", f"{w}: invalid dependency", w))
                continue
            if d["consumer_worker"] != w:
                findings.append(_finding("DEPENDENCY_CONSUMER_MISMATCH", f"{w}: dependency consumer mismatch", w))
            if d["producer_worker"] == w:
                findings.append(_finding("DEPENDENCY_SELF_REFERENCE", f"{w}: dependency producer cannot be self", w))

    active = [c for c in valid_claims if c.get("status") in ACTIVE]
    for i, a in enumerate(active):
        aw = str(a.get("worker_id"))
        for b in active[i + 1 :]:
            bw = str(b.get("worker_id"))
            pair = tuple(sorted((aw, bw)))
            if a.get("branch") == b.get("branch"):
                findings.append(_finding("BRANCH_COLLISION", f"{pair}: same branch", *pair))
            shared_scope = sorted(set(a.get("scope_keys", [])) & set(b.get("scope_keys", [])))
            if shared_scope:
                findings.append(_finding("SCOPE_COLLISION", f"{pair}: duplicate scope keys {shared_scope}", *pair))
            for left in a.get("write_set", []):
                for right in b.get("write_set", []):
                    if _overlap(left, right):
                        findings.append(_finding("WRITE_SET_CONFLICT", f"{pair}: {left!r} overlaps {right!r}", *pair))
            ah = {x["name"]: x for x in a.get("hotspots", []) if isinstance(x, dict) and _text(x.get("name"))}
            bh = {x["name"]: x for x in b.get("hotspots", []) if isinstance(x, dict) and _text(x.get("name"))}
            for name in set(ah) & set(bh):
                if ah[name].get("mode") == bh[name].get("mode") == "intentional_verification":
                    continue
                mutual = bw in ah[name].get("coordinated_with", []) and aw in bh[name].get("coordinated_with", [])
                sequenced = ah[name].get("mode") == bh[name].get("mode") == "sequenced"
                if not (mutual and sequenced and _deps_link(a, b)):
                    findings.append(_finding("HOTSPOT_UNCOORDINATED", f"{pair}: hotspot {name!r} lacks reciprocal sequencing", *pair))

    by_worker = {str(c.get("worker_id")): c for c in valid_claims}
    branch_heads = state.get("current_branch_heads", {})
    pr_heads = state.get("current_pr_heads", {})
    for h in handoffs:
        if not isinstance(h, dict):
            findings.append(_finding("HANDOFF_INVALID", "every handoff must be an object"))
            continue
        w = str(h.get("worker_id"))
        c = by_worker.get(w)
        if c is None:
            findings.append(_finding("HANDOFF_WITHOUT_CLAIM", f"{w}: no matching claim", w))
            continue
        if h.get("status") not in HANDOFF_STATUS:
            findings.append(_finding("HANDOFF_STATUS_INVALID", f"{w}: invalid handoff status", w))
        if h.get("mission_id") != c.get("mission_id"):
            findings.append(_finding("MISSION_MISMATCH", f"{w}: mission differs from claim", w))
        if h.get("branch") != c.get("branch"):
            findings.append(_finding("HANDOFF_BRANCH_MISMATCH", f"{w}: branch differs from claim", w))
        if h.get("claim_revision") != c.get("claim_revision"):
            findings.append(_finding("HANDOFF_CLAIM_STALE", f"{w}: stale claim revision", w))
        head = h.get("head_sha")
        if not isinstance(head, str) or not SHA.fullmatch(head):
            findings.append(_finding("HANDOFF_HEAD_INVALID", f"{w}: invalid handoff head", w))
        if isinstance(branch_heads, dict) and branch_heads.get(c.get("branch")) and branch_heads[c["branch"]] != head:
            findings.append(_finding("BRANCH_HEAD_STALE", f"{w}: branch advanced after evidence", w))

        changed = h.get("changed_files", [])
        if not _strings(changed):
            findings.append(_finding("CHANGED_FILES_INVALID", f"{w}: changed_files must be a string list", w))
        else:
            for path in changed:
                if not _covered(path, c.get("write_set", [])):
                    findings.append(_finding("OUT_OF_SCOPE_CHANGE", f"{w}: {path!r} outside write_set", w))

        if h.get("status") == "ready_for_integration":
            if not _text(h.get("pr")):
                findings.append(_finding("PR_REFERENCE_MISSING", f"{w}: PR reference missing", w))
            pr_head = h.get("pr_head_sha")
            if not isinstance(pr_head, str) or not SHA.fullmatch(pr_head):
                findings.append(_finding("PR_HEAD_INVALID", f"{w}: invalid PR head", w))
            elif pr_head != head:
                findings.append(_finding("PR_HEAD_MISMATCH", f"{w}: PR head differs from tested head", w))
            if isinstance(pr_heads, dict) and pr_heads.get(h.get("pr")) and pr_heads[h["pr"]] != pr_head:
                findings.append(_finding("PR_HEAD_STALE", f"{w}: PR advanced after evidence", w))
            if not _text(h.get("summary")) or not _text(h.get("selection_notes")):
                findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{w}: missing summary/selection notes", w))
            tests = h.get("tests")
            if not isinstance(tests, list) or not tests:
                findings.append(_finding("TEST_EVIDENCE_MISSING", f"{w}: no tests actually run", w))
            elif any(not isinstance(t, dict) or not _text(t.get("command")) or t.get("result") not in {"pass", "fail"} for t in tests):
                findings.append(_finding("TEST_EVIDENCE_INVALID", f"{w}: malformed test evidence", w))
            for d in c.get("dependencies", []):
                if isinstance(d, dict) and d.get("blocking") is True and d.get("status") == "pending":
                    findings.append(_finding("BLOCKING_DEPENDENCY_UNRESOLVED", f"{w}: pending blocker from {d.get('producer_worker')}", w))

        for field in ("risks", "assumptions", "blockers", "dependencies_consumed", "out_of_scope_findings"):
            if not isinstance(h.get(field), list):
                findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{w}: missing list field {field}", w))
        for item in h.get("out_of_scope_findings", []):
            if not isinstance(item, dict) or not all(_text(item.get(k)) for k in ("finding", "owner", "durable_ref")) or item.get("status") not in {"posted", "acknowledged"}:
                findings.append(_finding("OUT_OF_SCOPE_HANDOFF_INVALID", f"{w}: out-of-scope finding lacks durable receipt", w))
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
    c3 = _claim()
    state: dict[str, Any] = {
        "current_main_sha": BASE,
        "claims": [c3],
        "handoffs": [],
        "current_branch_heads": {"validation/03-work": HEAD},
        "current_pr_heads": {"#40": HEAD},
    }
    if case_id == "overlapping-write-set-rejected":
        state["claims"].append(_claim("08", write_set=["work/03/fix.txt"]))
    elif case_id == "silent-scope-expansion-rejected":
        state["handoffs"] = [_handoff(changed_files=["work/03/fix.txt", "runtime/engine.py"])]
    elif case_id == "shared-hotspot-without-sequencing-rejected":
        c3["hotspots"] = [{"name": "evals", "mode": "sequenced", "coordinated_with": []}]
        state["claims"].append(_claim("01", hotspots=[{"name": "evals", "mode": "sequenced", "coordinated_with": []}]))
    elif case_id == "sequenced-shared-hotspot-accepted":
        c3["hotspots"] = [{"name": "evals", "mode": "sequenced", "coordinated_with": ["01"]}]
        c3["dependencies"] = [{"producer_worker": "01", "consumer_worker": "03", "interface": "eval contract", "blocking": False, "status": "ready"}]
        state["claims"].append(_claim("01", hotspots=[{"name": "evals", "mode": "sequenced", "coordinated_with": ["03"]}]))
    elif case_id == "unfinished-blocking-dependency-rejected":
        c3["dependencies"] = [{"producer_worker": "01", "consumer_worker": "03", "interface": "required artifact", "blocking": True, "status": "pending"}]
        state["handoffs"] = [_handoff()]
    elif case_id == "stale-base-without-disposition-rejected":
        c3["base_sha"] = "b" * 40
    elif case_id == "advanced-pr-state-rejected":
        state["handoffs"] = [_handoff()]
        state["current_pr_heads"]["#40"] = "4" * 40
    elif case_id == "global-mission-mismatch-rejected":
        state["handoffs"] = [_handoff(mission_id="local-only")]
    elif case_id == "lost-out-of-scope-finding-rejected":
        state["handoffs"] = [_handoff(out_of_scope_findings=[{"finding": "adjacent defect", "owner": "04", "status": "local_only"}])]
    elif case_id == "completion-without-test-evidence-rejected":
        state["handoffs"] = [_handoff(tests=[])]
    elif case_id == "insufficient-integrator-selection-packet-rejected":
        state["handoffs"] = [_handoff(selection_notes="")]
    elif case_id == "complete-handoff-accepted":
        state["handoffs"] = [_handoff(out_of_scope_findings=[{"finding": "adjacent defect", "owner": "04", "durable_ref": "issue-27#comment", "status": "acknowledged"}])]
    else:
        raise ValueError(f"unknown coordination scenario {case_id!r}")
    return state


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(case.get("state")) if isinstance(case.get("state"), dict) else scenario(case["case_id"])
    findings = lint_state(state)
    codes = sorted({f["code"] for f in findings})
    expected = case.get("expected", {})
    passed = (not findings) == expected.get("valid") and all(code in codes for code in expected.get("codes", []))
    return {"case_id": case["case_id"], "passed": passed, "actual_valid": not findings, "codes": codes, "findings": findings}


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("coordination eval file must contain a list of objects")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run structural parallel-coordination evals.")
    parser.add_argument("case_file", nargs="?", default="evals/coordination/cases.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = [evaluate_case(case) for case in load_cases(Path(args.case_file))]
    failed = [r for r in results if not r["passed"]]
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for r in results:
            print(f"{'PASS' if r['passed'] else 'FAIL'} {r['case_id']}")
        print(f"{len(results) - len(failed)}/{len(results)} coordination eval cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
