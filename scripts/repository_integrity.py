#!/usr/bin/env python3
"""Public-repository integrity checks.

This script is repository tooling only; it does not select Manager's runtime language.
It intentionally uses high-confidence checks and does not claim to detect every secret,
privacy leak, or semantic schema violation.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
CONTRACTS_DIR = ROOT / "contracts"
EVAL_CASES_DIR = ROOT / "evals" / "cases"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
SUPPLY_CHAIN_WORKFLOWS = tuple(
    sorted({*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")})
)

RISKY_FILENAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
RISKY_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}

SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SAFE_EMAIL_SUFFIXES = (
    "@users.noreply.github.com",
    "@example.com",
    "@example.org",
    "@example.net",
)
SAFE_EMAIL_EXACT = {"noreply@github.com"}
ACTION_SHA_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")

EVAL_CATEGORIES = {
    "routing",
    "approval",
    "security",
    "reconciliation",
    "governance",
    "delegation",
    "evidence",
    "evolution",
    "recovery",
}
EVAL_SUBJECTS = {"trace", "result", "approval", "reconciliation"}
EVAL_OPERATORS = {"equals", "not_equals", "contains", "not_contains", "exists", "absent"}

TEXT_LIMIT = 1_000_000


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def is_public_safe_email(address: str) -> bool:
    lower = address.lower()
    return lower in SAFE_EMAIL_EXACT or lower.endswith(SAFE_EMAIL_SUFFIXES)


def validate_contracts(failures: list[str]) -> None:
    if not CONTRACTS_DIR.exists():
        return

    for path in sorted(CONTRACTS_DIR.glob("*.schema.json")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            failures.append(f"invalid contract JSON {relative}: {exc}")
            continue

        if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            failures.append(f"contract must declare JSON Schema Draft 2020-12: {relative}")
        if not isinstance(data.get("$id"), str) or not data["$id"].startswith(
            "https://github.com/legendteddy/manager/contracts/"
        ):
            failures.append(f"contract has missing or unexpected $id: {relative}")
        if data.get("type") != "object":
            failures.append(f"contract root type must be object: {relative}")


def validate_eval_cases(failures: list[str]) -> None:
    if not EVAL_CASES_DIR.exists():
        return

    required_fields = {
        "schema_version",
        "case_id",
        "title",
        "category",
        "input",
        "expected",
        "deterministic_assertions",
    }

    for path in sorted(EVAL_CASES_DIR.glob("*.json")):
        relative = path.relative_to(ROOT).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            failures.append(f"invalid eval JSON {relative}: {exc}")
            continue

        missing = sorted(required_fields - set(data))
        if missing:
            failures.append(f"eval case missing required fields {missing}: {relative}")
            continue

        if data.get("schema_version") != "1.0":
            failures.append(f"eval case must use schema_version 1.0: {relative}")
        if data.get("case_id") != path.stem:
            failures.append(f"eval case_id must match filename: {relative}")
        if data.get("category") not in EVAL_CATEGORIES:
            failures.append(f"eval case has unsupported category: {relative}")

        input_data = data.get("input")
        if not isinstance(input_data, dict) or not isinstance(input_data.get("task"), dict):
            failures.append(f"eval case input.task must be an object: {relative}")

        expected = data.get("expected")
        if not isinstance(expected, dict):
            failures.append(f"eval case expected must be an object: {relative}")
        else:
            for field in ("required_behaviors", "forbidden_behaviors"):
                value = expected.get(field)
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    failures.append(f"eval case expected.{field} must be a string array: {relative}")

        assertions = data.get("deterministic_assertions")
        if not isinstance(assertions, list):
            failures.append(f"eval case deterministic_assertions must be an array: {relative}")
            continue

        for index, assertion in enumerate(assertions):
            if not isinstance(assertion, dict):
                failures.append(f"eval assertion {index} must be an object: {relative}")
                continue
            if assertion.get("subject") not in EVAL_SUBJECTS:
                failures.append(f"eval assertion {index} has unsupported subject: {relative}")
            if not isinstance(assertion.get("path"), str) or not assertion["path"]:
                failures.append(f"eval assertion {index} requires a non-empty path: {relative}")
            if assertion.get("operator") not in EVAL_OPERATORS:
                failures.append(f"eval assertion {index} has unsupported operator: {relative}")
            if assertion.get("operator") not in {"exists", "absent"} and "value" not in assertion:
                failures.append(f"eval assertion {index} requires value for its operator: {relative}")


def validate_supply_chain_workflow(path: Path, failures: list[str]) -> None:
    """Enforce high-confidence workflow invariants without needing a YAML dependency."""

    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        failures.append(f"supply-chain workflow is not UTF-8 {path}: {exc}")
        return

    label = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("uses: "):
            action = stripped.removeprefix("uses: ").split(" #", 1)[0].strip()
            if not ACTION_SHA_RE.fullmatch(action):
                failures.append(
                    f"supply-chain workflow action must be pinned to a 40-hex commit: {label}:{index + 1}"
                )

        if stripped not in {"run: |", "run: >", "run: |-", "run: >-"}:
            continue
        base_indent = len(line) - len(line.lstrip())
        block_lines: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                indent = len(candidate) - len(candidate.lstrip())
                if indent <= base_indent:
                    break
            block_lines.append(candidate)
        if "${{ inputs." in "\n".join(block_lines):
            failures.append(
                f"workflow_dispatch inputs must enter shell through env, not expression interpolation: {label}:{index + 1}"
            )


def validate_supply_chain_workflows(failures: list[str]) -> None:
    for path in SUPPLY_CHAIN_WORKFLOWS:
        validate_supply_chain_workflow(path, failures)


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        lower_name = path.name.lower()

        if lower_name in RISKY_FILENAMES or path.suffix.lower() in RISKY_SUFFIXES or lower_name.startswith(".env."):
            failures.append(f"risky tracked filename: {relative}")

        if path == SELF or not path.is_file() or path.stat().st_size > TEXT_LIMIT:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"possible {label}: {relative}")

        for email in EMAIL_PATTERN.findall(text):
            if not is_public_safe_email(email):
                failures.append(f"non-placeholder email address in public file {relative}")
                break

    validate_contracts(failures)
    validate_eval_cases(failures)
    validate_supply_chain_workflows(failures)

    try:
        log_emails = subprocess.check_output(
            ["git", "log", "--format=%ae%n%ce"], cwd=ROOT, text=True
        ).splitlines()
    except subprocess.CalledProcessError:
        log_emails = []

    for email in sorted(set(e.strip() for e in log_emails if e.strip())):
        if not is_public_safe_email(email):
            warnings.append(
                "commit history contains a non-no-reply email; verify it was intentionally public"
            )
            break

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print("repository integrity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
