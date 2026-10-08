from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError, load_deployment_config, validate_runtime_paths
from .sqlite_ops import BackupError, create_sqlite_backup, restore_sqlite_backup, verify_sqlite_backup


def _write_json(payload: object) -> None:
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


def _config(args: argparse.Namespace):
    config = load_deployment_config(config_file=args.config)
    if not args.skip_path_checks:
        validate_runtime_paths(config)
    return config


def _probe(url: str, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1024 * 1024)
            if not 200 <= response.status < 300:
                raise RuntimeError(f"probe returned HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"probe failed: {exc}") from exc
    if body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError("probe endpoint reported ok=false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manager-deployment")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("validate-config", "print-effective-config"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--config", type=Path)
        sub.add_argument("--skip-path-checks", action="store_true")

    backup = subparsers.add_parser("backup")
    backup.add_argument("database", type=Path)
    backup.add_argument("output", type=Path)
    backup.add_argument("--manifest", type=Path)

    verify = subparsers.add_parser("verify-backup")
    verify.add_argument("backup", type=Path)
    verify.add_argument("--manifest", type=Path)

    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--manifest", type=Path)
    restore.add_argument("--replace", action="store_true")

    probe = subparsers.add_parser("probe")
    probe.add_argument("url")
    probe.add_argument("--timeout", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            config = _config(args)
            _write_json({"ok": True, "environment": config.environment})
        elif args.command == "print-effective-config":
            config = _config(args)
            _write_json(config.effective_dict())
        elif args.command == "backup":
            _write_json(
                create_sqlite_backup(args.database, args.output, manifest=args.manifest)
            )
        elif args.command == "verify-backup":
            _write_json(verify_sqlite_backup(args.backup, manifest=args.manifest))
        elif args.command == "restore":
            _write_json(
                restore_sqlite_backup(
                    args.backup,
                    args.destination,
                    manifest=args.manifest,
                    allow_replace=args.replace,
                )
            )
        elif args.command == "probe":
            if args.timeout <= 0:
                parser.error("--timeout must be positive")
            _probe(args.url, args.timeout)
            _write_json({"ok": True, "url": args.url})
        else:
            parser.error("unknown command")
    except (BackupError, ConfigError, RuntimeError) as exc:
        print(f"manager-deployment: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
