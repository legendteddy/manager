#!/usr/bin/env python3
"""Build and verify a local Manager release candidate without publishing it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "runtime" / "python"
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
BASELINE = PACKAGE_ROOT / "constraints" / "known-good.txt"


def run(*args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(args, cwd=ROOT, env=env, text=True).strip()


def git_value(format_string: str) -> str:
    return run("git", "show", "-s", f"--format={format_string}", "HEAD")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_package_metadata() -> tuple[str, str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    return str(project["name"]), str(project["version"])


def load_baseline() -> dict[str, str]:
    baseline: dict[str, str] = {}
    for raw_line in BASELINE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise SystemExit(f"unsupported baseline entry: {line}")
        name, version = line.split("==", 1)
        baseline[name.strip().lower()] = version.strip()
    return baseline


def verify_baseline() -> dict[str, str]:
    expected = load_baseline()
    observed: dict[str, str] = {}
    for distribution, version in expected.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise SystemExit(f"required baseline distribution is not installed: {distribution}") from exc
        observed[distribution] = actual
        if actual != version:
            raise SystemExit(
                f"baseline mismatch for {distribution}: expected {version}, observed {actual}"
            )
    return observed


def artifact_policy(name: str) -> str:
    if name.endswith(".whl"):
        return "byte_reproducibility_required"
    if name.endswith(".tar.gz"):
        return "checksum_integrity_only"
    raise SystemExit(f"unsupported release-candidate artifact: {name}")


def build(output: Path) -> dict[str, str]:
    commit = run("git", "rev-parse", "HEAD")
    source_date_epoch = git_value("%ct")
    package_name, package_version = load_package_metadata()
    tool_versions = verify_baseline()

    if output.exists():
        shutil.rmtree(output)
    artifacts_dir = output / "artifacts"
    artifacts_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = source_date_epoch
    env["PYTHONHASHSEED"] = "0"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--sdist",
            "--outdir",
            str(artifacts_dir),
            str(PACKAGE_ROOT),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )

    artifact_paths = sorted(
        path for path in artifacts_dir.iterdir() if path.is_file() and not path.name.startswith(".")
    )
    if len(artifact_paths) != 2:
        raise SystemExit(f"expected exactly one wheel and one sdist, found {len(artifact_paths)}")
    if sum(path.suffix == ".whl" for path in artifact_paths) != 1:
        raise SystemExit("release candidate must contain exactly one wheel")
    if sum(path.name.endswith(".tar.gz") for path in artifact_paths) != 1:
        raise SystemExit("release candidate must contain exactly one source distribution")

    hashes = {path.name: sha256(path) for path in artifact_paths}
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  artifacts/{name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )

    provenance = {
        "schema_version": "1.0",
        "project": package_name,
        "version": package_version,
        "commit_sha": commit,
        "source_date_epoch": int(source_date_epoch),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "build_tools": tool_versions,
        "artifacts": [
            {
                "path": f"artifacts/{name}",
                "sha256": digest,
                "verification_policy": artifact_policy(name),
            }
            for name, digest in sorted(hashes.items())
        ],
        "note": (
            "Local release-candidate provenance record. The wheel is required to be "
            "byte-reproducible in the same verified build environment; the source distribution "
            "is checksum-recorded but is not currently claimed byte-reproducible. This record is "
            "not a cryptographic attestation or publication authorization."
        ),
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return hashes


def load_hashes(output: Path) -> dict[str, str]:
    sums = output / "SHA256SUMS"
    if not sums.is_file():
        raise SystemExit(f"comparison candidate lacks SHA256SUMS: {output}")
    hashes: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        hashes[Path(relative).name] = digest
    return hashes


def wheel_hash(hashes: dict[str, str]) -> tuple[str, str]:
    wheels = [(name, digest) for name, digest in hashes.items() if name.endswith(".whl")]
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel hash, found {len(wheels)}")
    return wheels[0]


def compare_candidates(expected: dict[str, str], observed: dict[str, str]) -> bool:
    if set(expected) != set(observed):
        print("release-candidate artifact filenames changed between builds", file=sys.stderr)
        print(f"expected: {sorted(expected)}", file=sys.stderr)
        print(f"observed: {sorted(observed)}", file=sys.stderr)
        return False

    expected_wheel_name, expected_wheel_hash = wheel_hash(expected)
    observed_wheel_name, observed_wheel_hash = wheel_hash(observed)
    if expected_wheel_name != observed_wheel_name or expected_wheel_hash != observed_wheel_hash:
        print("release-candidate wheel is not byte-reproducible", file=sys.stderr)
        print(f"expected: {expected_wheel_name} {expected_wheel_hash}", file=sys.stderr)
        print(f"observed: {observed_wheel_name} {observed_wheel_hash}", file=sys.stderr)
        return False

    print("release-candidate wheel hash is byte-reproducible")
    for name in sorted(expected):
        if name.endswith(".tar.gz") and expected[name] != observed[name]:
            print(
                "NOTE: source distribution hash changed between builds; "
                "sdist is checksum-recorded but not claimed byte-reproducible"
            )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-to", type=Path)
    args = parser.parse_args()

    hashes = build(args.output.resolve())
    if args.compare_to is not None:
        expected = load_hashes(args.compare_to.resolve())
        if not compare_candidates(expected, hashes):
            return 1
    else:
        print("release candidate built")

    for name, digest in sorted(hashes.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
