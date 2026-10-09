from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .engine import run
from .precedence import run_instruction_precedence

SUBJECTS = {"trace", "result", "approval", "reconciliation"}


def _resolve(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _flatten_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_flatten_strings(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key))
            found.extend(_flatten_strings(item))
    return found


def _contains(container: Any, expected: Any) -> bool:
    if isinstance(container, str):
        return str(expected) in container
    if isinstance(container, list):
        if expected in container:
            return True
        return str(expected) in _flatten_strings(container)
    if isinstance(container, dict):
        if expected in container:
            return True
        return str(expected) in _flatten_strings(container)
    return False


def check_assertion(outputs: dict[str, Any], assertion: dict[str, Any]) -> tuple[bool, str]:
    subject_name = assertion["subject"]
    if subject_name not in SUBJECTS:
        return False, f"unsupported subject {subject_name!r}"

    subject = outputs.get(subject_name)
    operator = assertion["operator"]
    path = assertion["path"]
    exists, actual = _resolve(subject, path) if subject is not None else (False, None)
    expected = assertion.get("value")

    if operator == "exists":
        passed = exists
    elif operator == "absent":
        passed = not exists
    elif operator == "equals":
        passed = exists and actual == expected
    elif operator == "not_equals":
        passed = exists and actual != expected
    elif operator == "contains":
        passed = exists and _contains(actual, expected)
    elif operator == "not_contains":
        passed = (not exists) or not _contains(actual, expected)
    else:
        return False, f"unsupported operator {operator!r}"

    if passed:
        return True, f"{subject_name}.{path} {operator}"
    return (
        False,
        f"{subject_name}.{path} {operator} failed: actual={actual!r} expected={expected!r}",
    )


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    case_input = case["input"]
    task = case_input["task"]
    prior_state = case_input.get("prior_state") or {}

    # Baseline Manager controls always run first. The precedence oracle is only
    # eligible on an otherwise-completed public direct path; it cannot replace
    # approval, specialist/reconciliation routing, injection handling, or privacy.
    outputs = run(case_input)
    baseline_trace = outputs["trace"]
    precedence_eligible = (
        baseline_trace["status"] == "completed"
        and baseline_trace["workflow"] == "direct"
        and not case_input.get("untrusted_content")
        and task["classification"].get("sensitivity", "unknown") == "public"
    )
    if precedence_eligible:
        precedence_outputs = run_instruction_precedence(task, prior_state)
        if precedence_outputs is not None:
            outputs = precedence_outputs

    checks = [check_assertion(outputs, item) for item in case["deterministic_assertions"]]
    return {
        "case_id": case["case_id"],
        "passed": all(item[0] for item in checks),
        "checks": [{"passed": passed, "message": message} for passed, message in checks],
        "outputs": outputs,
    }


def load_cases(case_dir: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(case_dir.glob("*.json"))
    ]


def run_suite(case_dir: Path) -> list[dict[str, Any]]:
    return [run_case(case) for case in load_cases(case_dir)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Manager deterministic reference evals.")
    parser.add_argument(
        "case_dir",
        nargs="?",
        default="evals/cases",
        help="Directory containing eval case JSON files.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    results = run_suite(Path(args.case_dir))
    failed = [result for result in results if not result["passed"]]

    if args.as_json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            marker = "PASS" if result["passed"] else "FAIL"
            print(f"{marker} {result['case_id']}")
            if not result["passed"]:
                for check in result["checks"]:
                    if not check["passed"]:
                        print(f"  - {check['message']}")
        print(f"{len(results) - len(failed)}/{len(results)} deterministic eval cases passed")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
