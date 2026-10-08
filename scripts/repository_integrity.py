#!/usr/bin/env python3
"""Public-repository integrity checks.

This script is repository tooling only; it does not select Manager's runtime language.
It intentionally uses high-confidence checks and does not claim to detect every secret or privacy leak.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

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

TEXT_LIMIT = 1_000_000


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def is_public_safe_email(address: str) -> bool:
    lower = address.lower()
    return lower in SAFE_EMAIL_EXACT or lower.endswith(SAFE_EMAIL_SUFFIXES)


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
