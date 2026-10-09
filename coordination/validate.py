#!/usr/bin/env python3
"""Fail-closed structural checks for durable parallel-worker coordination."""
from __future__ import annotations

import argparse, copy, json, re
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
ACTIVE = {"declared", "active", "blocked", "ready_for_handoff"}
CLAIM_STATUS = ACTIVE | {"superseded", "withdrawn"}
DEP_STATUS = {"pending", "ready", "consumed", "superseded"}
HOTSPOT_MODE = {"exclusive", "sequenced", "integrator_only", "intentional_verification"}
STALE_DISPOSITION = {"unrelated_reviewed", "rebase_required", "integrator_sequence", "superseded"}
HANDOFF_STATUS = {"blocked", "no_change", "ready_for_integration"}


def _text(v: Any) -> bool: return isinstance(v, str) and bool(v.strip())
def _strings(v: Any, nonempty: bool = False) -> bool: return isinstance(v, list) and (not nonempty or bool(v)) and all(_text(x) for x in v)
def _finding(code: str, message: str, *workers: str) -> dict[str, Any]: return {"code": code, "message": message, "workers": list(workers)}

def _path(spec: str) -> tuple[str, bool]:
    value = spec.strip().lstrip("./")
    prefix = value.endswith("/") or value.endswith("/**")
    if value.endswith("/**"): value = value[:-3]
    return value.rstrip("/"), prefix

def _overlap(a: str, b: str) -> bool:
    ap, apre = _path(a); bp, bpre = _path(b)
    return not ap or not bp or ap == bp or (apre and bp.startswith(ap + "/")) or (bpre and ap.startswith(bp + "/"))

def _covered(path: str, specs: list[str]) -> bool:
    target, _ = _path(path)
    return any(target == base or (prefix and target.startswith(base + "/")) for base, prefix in map(_path, specs))

def _deps_link(a: dict[str, Any], b: dict[str, Any]) -> bool:
    pair = {a.get("worker_id"), b.get("worker_id")}
    return any(isinstance(d, dict) and {d.get("producer_worker"), d.get("consumer_worker")} == pair for c in (a, b) for d in c.get("dependencies", []))


def _resolve_current_claims(claims: list[dict[str, Any]], findings: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    histories: dict[str, list[dict[str, Any]]] = {}; invalid: set[str] = set()
    for c in claims:
        w = c.get("worker_id")
        if not _text(w): continue
        w = str(w); histories.setdefault(w, []).append(c)
        r = c.get("claim_revision")
        if not isinstance(r, int) or isinstance(r, bool) or r < 1 or c.get("status") not in CLAIM_STATUS: invalid.add(w)
    current: dict[str, dict[str, Any]] = {}
    for w, history in histories.items():
        seen: set[int] = set(); revisions: list[int] = []
        for c in history:
            r = c.get("claim_revision")
            if not isinstance(r, int) or isinstance(r, bool) or r < 1: continue
            if r in seen:
                findings.append(_finding("CLAIM_REVISION_DUPLICATE", f"{w}: duplicate claim revision {r}", w)); invalid.add(w)
            seen.add(r); revisions.append(r)
        live = [c for c in history if c.get("status") in ACTIVE]
        if len(live) > 1:
            findings.append(_finding("MULTIPLE_CURRENT_CLAIMS", f"{w}: multiple simultaneous current claims", w)); invalid.add(w)
        elif len(live) == 1 and revisions and live[0].get("claim_revision") != max(revisions):
            findings.append(_finding("CLAIM_CURRENT_NOT_HIGHEST", f"{w}: current claim is not highest revision", w)); invalid.add(w)
        if w not in invalid and len(live) == 1: current[w] = live[0]
    return current, histories


def _validate_claim(c: dict[str, Any], current_main: Any, findings: list[dict[str, Any]]) -> None:
    w = c.get("worker_id") if _text(c.get("worker_id")) else "<unknown>"
    for f in ("worker_id", "branch", "mission_id", "mission_summary", "integrator"):
        if not _text(c.get(f)): findings.append(_finding("CLAIM_FIELD_MISSING", f"{w}: missing {f}", w))
    r = c.get("claim_revision")
    if not isinstance(r, int) or isinstance(r, bool) or r < 1: findings.append(_finding("CLAIM_REVISION_INVALID", f"{w}: invalid claim_revision", w))
    base = c.get("base_sha")
    if not isinstance(base, str) or not SHA.fullmatch(base): findings.append(_finding("BASE_SHA_INVALID", f"{w}: base_sha must be exact", w))
    for f in ("success_criteria", "scope_keys", "scope", "exclusions"):
        if not _strings(c.get(f), True): findings.append(_finding("CLAIM_FIELD_MISSING", f"{w}: {f} must be a non-empty string list", w))
    if not _strings(c.get("write_set", [])): findings.append(_finding("WRITE_SET_INVALID", f"{w}: write_set must be a string list", w))
    if c.get("status") not in CLAIM_STATUS: findings.append(_finding("CLAIM_STATUS_INVALID", f"{w}: invalid claim status", w))
    if current_main and c.get("status") in ACTIVE and base != current_main and c.get("stale_base_disposition") not in STALE_DISPOSITION:
        findings.append(_finding("STALE_BASE_UNRECONCILED", f"{w}: stale base has no disposition", w))
    for h in c.get("hotspots", []):
        if not (isinstance(h, dict) and _text(h.get("name")) and h.get("mode") in HOTSPOT_MODE and _strings(h.get("coordinated_with", []))):
            findings.append(_finding("HOTSPOT_DECLARATION_INVALID", f"{w}: invalid hotspot declaration", w))
    for d in c.get("dependencies", []):
        valid = isinstance(d, dict) and all(_text(d.get(k)) for k in ("producer_worker", "consumer_worker", "interface")) and d.get("status") in DEP_STATUS and isinstance(d.get("blocking"), bool)
        if not valid:
            findings.append(_finding("DEPENDENCY_INVALID", f"{w}: invalid dependency", w)); continue
        if d["consumer_worker"] != w: findings.append(_finding("DEPENDENCY_CONSUMER_MISMATCH", f"{w}: dependency consumer mismatch", w))
        if d["producer_worker"] == w: findings.append(_finding("DEPENDENCY_SELF_REFERENCE", f"{w}: dependency producer cannot be self", w))
        ph = d.get("producer_head_sha")
        if ph is not None and (not isinstance(ph, str) or not SHA.fullmatch(ph)): findings.append(_finding("DEPENDENCY_HEAD_INVALID", f"{w}: invalid producer_head_sha", w))


def _validate_cross_claims(claims: list[dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    active = [c for c in claims if c.get("status") in ACTIVE]
    for i, a in enumerate(active):
        aw = str(a.get("worker_id"))
        for b in active[i + 1:]:
            bw = str(b.get("worker_id"))
            if aw == bw: continue
            pair = tuple(sorted((aw, bw)))
            if a.get("branch") == b.get("branch"): findings.append(_finding("BRANCH_COLLISION", f"{pair}: same branch", *pair))
            shared = sorted(set(a.get("scope_keys", [])) & set(b.get("scope_keys", [])))
            if shared: findings.append(_finding("SCOPE_COLLISION", f"{pair}: duplicate scope keys {shared}", *pair))
            for x in a.get("write_set", []):
                for y in b.get("write_set", []):
                    if _overlap(x, y): findings.append(_finding("WRITE_SET_CONFLICT", f"{pair}: {x!r} overlaps {y!r}", *pair))
            ah = {x["name"]: x for x in a.get("hotspots", []) if isinstance(x, dict) and _text(x.get("name"))}; bh = {x["name"]: x for x in b.get("hotspots", []) if isinstance(x, dict) and _text(x.get("name"))}
            for name in set(ah) & set(bh):
                if ah[name].get("mode") == bh[name].get("mode") == "intentional_verification": continue
                mutual = bw in ah[name].get("coordinated_with", []) and aw in bh[name].get("coordinated_with", [])
                if not (mutual and ah[name].get("mode") == bh[name].get("mode") == "sequenced" and _deps_link(a, b)):
                    findings.append(_finding("HOTSPOT_UNCOORDINATED", f"{pair}: hotspot {name!r} lacks reciprocal sequencing", *pair))


def _validate_handoff(h: dict[str, Any], c: dict[str, Any], branch_heads: Any, pr_heads: Any, current: dict[str, dict[str, Any]], findings: list[dict[str, Any]]) -> None:
    w = str(h.get("worker_id")); status = h.get("status")
    if status not in HANDOFF_STATUS: findings.append(_finding("HANDOFF_STATUS_INVALID", f"{w}: invalid handoff status", w))
    if status == "ready_for_integration" and c.get("status") != "ready_for_handoff": findings.append(_finding("CLAIM_NOT_READY_FOR_HANDOFF", f"{w}: current claim is not ready_for_handoff", w))
    if h.get("mission_id") != c.get("mission_id"): findings.append(_finding("MISSION_MISMATCH", f"{w}: mission differs from claim", w))
    if h.get("branch") != c.get("branch"): findings.append(_finding("HANDOFF_BRANCH_MISMATCH", f"{w}: branch differs from claim", w))
    if h.get("claim_revision") != c.get("claim_revision"): findings.append(_finding("HANDOFF_CLAIM_STALE", f"{w}: stale claim revision", w))
    head = h.get("head_sha"); branch = c.get("branch")
    if not isinstance(head, str) or not SHA.fullmatch(head): findings.append(_finding("HANDOFF_HEAD_INVALID", f"{w}: invalid handoff head", w))
    if status == "ready_for_integration":
        if not isinstance(branch_heads, dict) or branch not in branch_heads: findings.append(_finding("BRANCH_HEAD_UNVERIFIED", f"{w}: live branch head observation missing", w))
        elif branch_heads[branch] != head: findings.append(_finding("BRANCH_HEAD_STALE", f"{w}: branch advanced after evidence", w))
    elif isinstance(branch_heads, dict) and branch_heads.get(branch) not in (None, head): findings.append(_finding("BRANCH_HEAD_STALE", f"{w}: branch advanced after evidence", w))
    changed = h.get("changed_files", [])
    if not _strings(changed): findings.append(_finding("CHANGED_FILES_INVALID", f"{w}: changed_files must be a string list", w))
    else:
        for path in changed:
            if not _covered(path, c.get("write_set", [])): findings.append(_finding("OUT_OF_SCOPE_CHANGE", f"{w}: {path!r} outside write_set", w))
    if status == "ready_for_integration":
        pr = h.get("pr"); pr_head = h.get("pr_head_sha")
        if not _text(pr): findings.append(_finding("PR_REFERENCE_MISSING", f"{w}: PR reference missing", w))
        if not isinstance(pr_head, str) or not SHA.fullmatch(pr_head): findings.append(_finding("PR_HEAD_INVALID", f"{w}: invalid PR head", w))
        elif pr_head != head: findings.append(_finding("PR_HEAD_MISMATCH", f"{w}: PR head differs from tested head", w))
        if not isinstance(pr_heads, dict) or pr not in pr_heads: findings.append(_finding("PR_HEAD_UNVERIFIED", f"{w}: live PR head observation missing", w))
        elif pr_heads[pr] != pr_head: findings.append(_finding("PR_HEAD_STALE", f"{w}: PR advanced after evidence", w))
        if not _text(h.get("summary")) or not _text(h.get("selection_notes")): findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{w}: missing summary/selection notes", w))
        tests = h.get("tests")
        if not isinstance(tests, list) or not tests: findings.append(_finding("TEST_EVIDENCE_MISSING", f"{w}: no tests actually run", w))
        elif any(not isinstance(t, dict) or not _text(t.get("command")) or t.get("result") not in {"pass", "fail"} for t in tests): findings.append(_finding("TEST_EVIDENCE_INVALID", f"{w}: malformed test evidence", w))
        elif any(t.get("result") == "fail" for t in tests): findings.append(_finding("TEST_EVIDENCE_FAILED", f"{w}: ready handoff reports a failed test", w))
        blockers = h.get("blockers")
        if isinstance(blockers, list) and blockers: findings.append(_finding("READY_HANDOFF_HAS_BLOCKERS", f"{w}: ready handoff still declares blockers", w))
        for d in c.get("dependencies", []):
            if not isinstance(d, dict) or d.get("blocking") is not True: continue
            pw = str(d.get("producer_worker"))
            if d.get("status") not in {"ready", "consumed"}:
                findings.append(_finding("BLOCKING_DEPENDENCY_UNRESOLVED", f"{w}: blocking dependency on {pw} is not ready", w)); continue
            p = current.get(pw)
            if p is None:
                findings.append(_finding("DEPENDENCY_PRODUCER_UNAVAILABLE", f"{w}: producer {pw} has no unambiguous current claim", w)); continue
            if p.get("status") != "ready_for_handoff": findings.append(_finding("DEPENDENCY_PRODUCER_NOT_READY", f"{w}: producer {pw} is not ready_for_handoff", w))
            ph = d.get("producer_head_sha")
            if not isinstance(ph, str) or not SHA.fullmatch(ph): findings.append(_finding("DEPENDENCY_HEAD_EVIDENCE_MISSING", f"{w}: blocking dependency lacks exact producer head", w))
            else:
                pb = p.get("branch")
                if not isinstance(branch_heads, dict) or pb not in branch_heads: findings.append(_finding("DEPENDENCY_HEAD_UNVERIFIED", f"{w}: producer branch head was not observed", w))
                elif branch_heads[pb] != ph: findings.append(_finding("DEPENDENCY_HEAD_STALE", f"{w}: producer branch advanced after dependency evidence", w))
    for f in ("risks", "assumptions", "blockers", "dependencies_consumed", "out_of_scope_findings"):
        if not isinstance(h.get(f), list): findings.append(_finding("INTEGRATOR_PACKET_INCOMPLETE", f"{w}: missing list field {f}", w))
    for item in h.get("out_of_scope_findings", []):
        if not (isinstance(item, dict) and all(_text(item.get(k)) for k in ("finding", "owner", "durable_ref")) and item.get("status") in {"posted", "acknowledged"}): findings.append(_finding("OUT_OF_SCOPE_HANDOFF_INVALID", f"{w}: out-of-scope finding lacks durable receipt", w))


def lint_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []; claims = state.get("claims", []); handoffs = state.get("handoffs", [])
    if not isinstance(claims, list): return [_finding("CLAIMS_INVALID", "claims must be a list")]
    if not isinstance(handoffs, list): return [_finding("HANDOFFS_INVALID", "handoffs must be a list")]
    current_main = state.get("current_main_sha")
    if current_main is not None and (not isinstance(current_main, str) or not SHA.fullmatch(current_main)): findings.append(_finding("CURRENT_MAIN_SHA_INVALID", "current_main_sha must be an exact SHA"))
    valid = [c for c in claims if isinstance(c, dict)]
    if len(valid) != len(claims): findings.append(_finding("CLAIM_INVALID", "every claim must be an object"))
    for c in valid: _validate_claim(c, current_main, findings)
    current, histories = _resolve_current_claims(valid, findings); _validate_cross_claims(valid, findings)
    branch_heads, pr_heads = state.get("current_branch_heads", {}), state.get("current_pr_heads", {})
    for h in handoffs:
        if not isinstance(h, dict): findings.append(_finding("HANDOFF_INVALID", "every handoff must be an object")); continue
        w = str(h.get("worker_id")); c = current.get(w)
        if c is None:
            findings.append(_finding("HANDOFF_WITHOUT_CURRENT_CLAIM" if w in histories else "HANDOFF_WITHOUT_CLAIM", f"{w}: no unambiguous current claim", w)); continue
        _validate_handoff(h, c, branch_heads, pr_heads, current, findings)
    return findings

BASE, HEAD = "a" * 40, "3" * 40

def _claim(worker: str = "03", **changes: Any) -> dict[str, Any]:
    v = {"worker_id": worker, "claim_revision": 1, "branch": f"validation/{worker}-work", "base_sha": BASE, "mission_id": "issue-27", "mission_summary": "Validate safe ten-chat parallel engineering.", "success_criteria": ["non-overlap", "durable handoffs"], "integrator": "10", "scope_keys": [f"scope-{worker}"], "scope": [f"owned-{worker}"], "exclusions": ["other-worker-domains"], "write_set": [f"work/{worker}/"], "hotspots": [], "dependencies": [], "status": "ready_for_handoff"}
    v.update(changes); return v

def _handoff(**changes: Any) -> dict[str, Any]:
    v = {"worker_id": "03", "claim_revision": 1, "mission_id": "issue-27", "branch": "validation/03-work", "head_sha": HEAD, "status": "ready_for_integration", "pr": "#40", "pr_head_sha": HEAD, "summary": "Bounded coordination fix.", "selection_notes": "Isolated implementation for integrator selection.", "changed_files": ["work/03/fix.txt"], "tests": [{"command": "coordination evals", "result": "pass"}], "risks": [], "assumptions": [], "blockers": [], "dependencies_consumed": [], "out_of_scope_findings": []}
    v.update(changes); return v

def scenario(case_id: str) -> dict[str, Any]:
    c = _claim(); s: dict[str, Any] = {"current_main_sha": BASE, "claims": [c], "handoffs": [], "current_branch_heads": {"validation/03-work": HEAD}, "current_pr_heads": {"#40": HEAD}}
    if case_id == "overlapping-write-set-rejected": s["claims"].append(_claim("08", write_set=["work/03/fix.txt"]))
    elif case_id == "silent-scope-expansion-rejected": s["handoffs"] = [_handoff(changed_files=["work/03/fix.txt", "runtime/engine.py"])]
    elif case_id == "shared-hotspot-without-sequencing-rejected": c["hotspots"] = [{"name":"evals","mode":"sequenced","coordinated_with":[]}]; s["claims"].append(_claim("01", hotspots=[{"name":"evals","mode":"sequenced","coordinated_with":[]}]))
    elif case_id == "sequenced-shared-hotspot-accepted": c["hotspots"]=[{"name":"evals","mode":"sequenced","coordinated_with":["01"]}]; c["dependencies"]=[{"producer_worker":"01","consumer_worker":"03","interface":"eval contract","blocking":False,"status":"ready"}]; s["claims"].append(_claim("01", hotspots=[{"name":"evals","mode":"sequenced","coordinated_with":["03"]}]))
    elif case_id == "unfinished-blocking-dependency-rejected": c["dependencies"]=[{"producer_worker":"01","consumer_worker":"03","interface":"required artifact","blocking":True,"status":"pending"}]; s["handoffs"]=[_handoff()]
    elif case_id == "stale-base-without-disposition-rejected": c["base_sha"]="b"*40
    elif case_id == "advanced-pr-state-rejected": s["handoffs"]=[_handoff()]; s["current_pr_heads"]["#40"]="4"*40
    elif case_id == "global-mission-mismatch-rejected": s["handoffs"]=[_handoff(mission_id="local-only")]
    elif case_id == "lost-out-of-scope-finding-rejected": s["handoffs"]=[_handoff(out_of_scope_findings=[{"finding":"adjacent defect","owner":"04","status":"local_only"}])]
    elif case_id == "completion-without-test-evidence-rejected": s["handoffs"]=[_handoff(tests=[])]
    elif case_id == "completion-with-failed-test-rejected": s["handoffs"]=[_handoff(tests=[{"command":"full suite","result":"fail"}])]
    elif case_id == "completion-without-live-branch-head-rejected": s["handoffs"]=[_handoff()]; s["current_branch_heads"]={}
    elif case_id == "completion-without-live-pr-head-rejected": s["handoffs"]=[_handoff()]; s["current_pr_heads"]={}
    elif case_id == "ready-handoff-from-blocked-claim-rejected": c["status"]="blocked"; s["handoffs"]=[_handoff()]
    elif case_id == "ready-handoff-with-blockers-rejected": s["handoffs"]=[_handoff(blockers=["CI pending"])]
    elif case_id == "blocking-dependency-unknown-producer-rejected": c["dependencies"]=[{"producer_worker":"01","consumer_worker":"03","interface":"artifact","blocking":True,"status":"ready","producer_head_sha":"4"*40}]; s["handoffs"]=[_handoff()]
    elif case_id in {"blocking-dependency-producer-advanced-rejected","blocking-dependency-ready-accepted"}:
        ph="4"*40; c["dependencies"]=[{"producer_worker":"01","consumer_worker":"03","interface":"artifact","blocking":True,"status":"ready","producer_head_sha":ph}]; p=_claim("01"); s["claims"].append(p); s["current_branch_heads"][p["branch"]]="5"*40 if case_id.endswith("advanced-rejected") else ph; s["handoffs"]=[_handoff()]
    elif case_id == "insufficient-integrator-selection-packet-rejected": s["handoffs"]=[_handoff(selection_notes="")]
    elif case_id in {"superseded-history-before-current-accepted","superseded-history-after-current-accepted"}:
        old=_claim(claim_revision=1,status="superseded"); cur=_claim(claim_revision=2,status="ready_for_handoff"); s["claims"]=[old,cur] if case_id.endswith("before-current-accepted") else [cur,old]; s["handoffs"]=[_handoff(claim_revision=2)]
    elif case_id == "duplicate-claim-revision-rejected": s["claims"]=[_claim(claim_revision=1,status="superseded"),_claim(claim_revision=1,status="active")]
    elif case_id == "multiple-current-claims-rejected": s["claims"]=[_claim(claim_revision=1,status="active"),_claim(claim_revision=2,status="ready_for_handoff")]
    elif case_id == "current-claim-not-highest-revision-rejected": s["claims"]=[_claim(claim_revision=1,status="active"),_claim(claim_revision=2,status="superseded")]
    elif case_id == "complete-handoff-accepted": s["handoffs"]=[_handoff(out_of_scope_findings=[{"finding":"adjacent defect","owner":"04","durable_ref":"issue-27#comment","status":"acknowledged"}])]
    else: raise ValueError(f"unknown coordination scenario {case_id!r}")
    return s


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(case.get("state")) if isinstance(case.get("state"), dict) else scenario(case["case_id"])
    findings = lint_state(state); codes = sorted({f["code"] for f in findings}); expected = case.get("expected", {})
    passed = (not findings) == expected.get("valid") and all(code in codes for code in expected.get("codes", []))
    return {"case_id": case["case_id"], "passed": passed, "actual_valid": not findings, "codes": codes, "findings": findings}

def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data): raise ValueError("coordination eval file must contain a list of objects")
    return data

def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description="Run structural parallel-coordination evals."); p.add_argument("case_file",nargs="?",default="evals/coordination/cases.json"); p.add_argument("--json",action="store_true"); a=p.parse_args(argv)
    results=[evaluate_case(c) for c in load_cases(Path(a.case_file))]; failed=[r for r in results if not r["passed"]]
    if a.json: print(json.dumps(results,indent=2,sort_keys=True))
    else:
        for r in results: print(f"{'PASS' if r['passed'] else 'FAIL'} {r['case_id']}")
        print(f"{len(results)-len(failed)}/{len(results)} coordination eval cases passed")
    return 1 if failed else 0

if __name__ == "__main__": raise SystemExit(main())
