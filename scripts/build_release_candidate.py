#!/usr/bin/env python3
"""Build a verifiable Manager release candidate without publishing it."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "runtime" / "python"
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
BASELINE = PACKAGE_ROOT / "constraints" / "known-good.txt"

NAME_RE = re.compile(r"[-_.]+")


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


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def normalize_name(name: str) -> str:
    return NAME_RE.sub("-", name).lower()


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
        if "==" not in line or any(marker in line for marker in ("--hash", ";", "@")):
            raise SystemExit(f"unsupported baseline entry: {line}")
        name, version = line.split("==", 1)
        normalized = normalize_name(name.strip())
        if normalized in baseline:
            raise SystemExit(f"duplicate baseline distribution: {normalized}")
        baseline[normalized] = version.strip()
    if not baseline:
        raise SystemExit("known-good baseline is empty")
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


def ensure_clean_source_tree() -> None:
    status = run("git", "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        preview = "\n".join(status.splitlines()[:10])
        raise SystemExit(f"refusing release-candidate build from dirty source tree:\n{preview}")


def normalize_sdist(path: Path, source_date_epoch: int) -> None:
    """Rewrite a gzip tar sdist with deterministic archive metadata and ordering."""

    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for member in source.getmembers():
            payload: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise SystemExit(f"unable to read sdist member: {member.name}")
                payload = extracted.read()
            cloned = tarfile.TarInfo(member.name)
            cloned.size = len(payload) if payload is not None else 0
            cloned.mode = member.mode
            cloned.type = member.type
            cloned.linkname = member.linkname
            cloned.mtime = source_date_epoch
            cloned.uid = 0
            cloned.gid = 0
            cloned.uname = ""
            cloned.gname = ""
            cloned.devmajor = member.devmajor
            cloned.devminor = member.devminor
            cloned.pax_headers = {}
            members.append((cloned, payload))

    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=source_date_epoch) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as target:
                for member, payload in sorted(members, key=lambda item: item[0].name):
                    target.addfile(member, io.BytesIO(payload) if payload is not None else None)
    temp.replace(path)


def read_wheel_identity(path: Path) -> tuple[str, str]:
    if not path.name.endswith(".whl"):
        raise SystemExit(f"dependency bundle contains non-wheel file: {path.name}")
    metadata_members: list[str] = []
    with zipfile.ZipFile(path) as archive:
        metadata_members = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_members) != 1:
            raise SystemExit(f"wheel must contain exactly one METADATA file: {path.name}")
        metadata = archive.read(metadata_members[0]).decode("utf-8", errors="strict")
    name = None
    version = None
    for line in metadata.splitlines():
        if line.startswith("Name: "):
            name = line[6:].strip()
        elif line.startswith("Version: "):
            version = line[9:].strip()
        if name and version:
            break
    if not name or not version:
        raise SystemExit(f"wheel metadata lacks Name/Version: {path.name}")
    return normalize_name(name), version


def copy_and_lock_dependencies(dependency_dir: Path, output: Path) -> tuple[dict[str, object], str]:
    baseline = load_baseline()
    source_wheels = sorted(path for path in dependency_dir.iterdir() if path.is_file())
    if not source_wheels:
        raise SystemExit(f"dependency directory contains no wheels: {dependency_dir}")

    dependencies_dir = output / "dependencies"
    dependencies_dir.mkdir(parents=True)
    packages: list[dict[str, str]] = []
    seen_names: dict[str, str] = {}
    for source in source_wheels:
        if not source.name.endswith(".whl"):
            raise SystemExit(f"dependency directory must contain wheels only: {source.name}")
        name, version = read_wheel_identity(source)
        if name in seen_names:
            raise SystemExit(f"dependency bundle contains duplicate project {name}: {source.name}")
        seen_names[name] = version
        destination = dependencies_dir / source.name
        shutil.copyfile(source, destination)
        packages.append(
            {
                "name": name,
                "version": version,
                "filename": source.name,
                "sha256": sha256(destination),
            }
        )

    missing_roots = sorted(name for name, version in baseline.items() if seen_names.get(name) != version)
    if missing_roots:
        raise SystemExit(
            "dependency bundle does not contain every exact known-good root: " + ", ".join(missing_roots)
        )

    lock = {
        "schema_version": "1.0",
        "resolver_scope": "release-python-platform-wheelhouse",
        "roots": [{"name": name, "version": version} for name, version in sorted(baseline.items())],
        "packages": sorted(packages, key=lambda item: (item["name"], item["filename"])),
    }
    lock_bytes = canonical_json_bytes(lock)
    (output / "dependencies.lock.json").write_bytes(lock_bytes)
    lock_hash = hashlib.sha256(lock_bytes).hexdigest()

    requirements_lines = []
    for package in lock["packages"]:
        requirements_lines.append(
            f"{package['name']}=={package['version']} --hash=sha256:{package['sha256']}"
        )
    (output / "requirements.lock").write_text("\n".join(requirements_lines) + "\n", encoding="utf-8")
    return lock, lock_hash


def artifact_policy(name: str) -> str:
    if name.endswith(".whl") or name.endswith(".tar.gz"):
        return "same_environment_byte_reproducibility_required"
    raise SystemExit(f"unsupported release-candidate artifact: {name}")


def build_sbom(
    package_name: str,
    package_version: str,
    commit: str,
    source_date_epoch: int,
    artifact_hashes: dict[str, str],
    lock: dict[str, object],
    lock_hash: str,
) -> dict[str, object]:
    timestamp = dt.datetime.fromtimestamp(source_date_epoch, tz=dt.timezone.utc).replace(microsecond=0).isoformat()
    components = []
    for package in lock["packages"]:
        components.append(
            {
                "type": "library",
                "bom-ref": f"pkg:pypi/{package['name']}@{package['version']}",
                "name": package["name"],
                "version": package["version"],
                "purl": f"pkg:pypi/{package['name']}@{package['version']}",
                "hashes": [{"alg": "SHA-256", "content": package["sha256"]}],
                "properties": [
                    {"name": "manager:wheel-filename", "value": package["filename"]},
                ],
            }
        )

    artifact_properties = [
        {"name": f"manager:artifact-sha256:{name}", "value": digest}
        for name, digest in sorted(artifact_hashes.items())
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "type": "library",
                "bom-ref": f"pkg:pypi/{normalize_name(package_name)}@{package_version}",
                "name": package_name,
                "version": package_version,
                "purl": f"pkg:pypi/{normalize_name(package_name)}@{package_version}",
                "properties": [
                    {"name": "manager:commit-sha", "value": commit},
                    {"name": "manager:dependency-lock-sha256", "value": lock_hash},
                    *artifact_properties,
                ],
            },
        },
        "components": components,
    }


def write_intoto_statement(
    output: Path,
    artifact_hashes: dict[str, str],
    commit: str,
    package_name: str,
    package_version: str,
    source_date_epoch: int,
    lock_hash: str,
    sbom_hash: str,
) -> str:
    subjects = [
        {"name": f"artifacts/{name}", "digest": {"sha256": digest}}
        for name, digest in sorted(artifact_hashes.items())
    ]
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/legendteddy/manager/release-candidate/v1",
                "externalParameters": {
                    "project": package_name,
                    "version": package_version,
                    "commit_sha": commit,
                    "dependency_lock_sha256": lock_hash,
                },
                "internalParameters": {"source_date_epoch": source_date_epoch},
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/legendteddy/manager@{commit}",
                        "digest": {"gitCommit": commit},
                    },
                    {
                        "uri": "file:dependencies.lock.json",
                        "digest": {"sha256": lock_hash},
                    },
                ],
            },
            "runDetails": {
                "builder": {"id": "https://github.com/legendteddy/manager/scripts/build_release_candidate.py"},
                "metadata": {"invocationId": commit},
                "byproducts": [
                    {"name": "sbom.cdx.json", "digest": {"sha256": sbom_hash}},
                ],
            },
        },
    }
    path = output / "provenance.intoto.jsonl"
    path.write_bytes(canonical_json_bytes(statement))
    return sha256(path)


def collect_payload_hashes(output: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "candidate-manifest.json":
            continue
        relative = path.relative_to(output).as_posix()
        hashes[relative] = sha256(path)
    return hashes


def write_candidate_manifest(
    output: Path,
    commit: str,
    package_name: str,
    package_version: str,
    lock_hash: str,
    sbom_hash: str,
) -> str:
    manifest = {
        "schema_version": "1.0",
        "project": package_name,
        "version": package_version,
        "commit_sha": commit,
        "dependency_lock_sha256": lock_hash,
        "sbom_sha256": sbom_hash,
        "files": [
            {"path": path, "sha256": digest}
            for path, digest in sorted(collect_payload_hashes(output).items())
        ],
    }
    path = output / "candidate-manifest.json"
    path.write_bytes(canonical_json_bytes(manifest))
    return sha256(path)


def build(output: Path, dependency_dir: Path) -> dict[str, str]:
    ensure_clean_source_tree()
    commit = run("git", "rev-parse", "HEAD")
    source_date_epoch = int(git_value("%ct"))
    package_name, package_version = load_package_metadata()
    tool_versions = verify_baseline()

    if output.exists():
        shutil.rmtree(output)
    artifacts_dir = output / "artifacts"
    artifacts_dir.mkdir(parents=True)

    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
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
    wheels = [path for path in artifact_paths if path.suffix == ".whl"]
    sdists = [path for path in artifact_paths if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("release candidate must contain exactly one wheel and one .tar.gz sdist")

    normalize_sdist(sdists[0], source_date_epoch)
    hashes = {path.name: sha256(path) for path in artifact_paths}
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  artifacts/{name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )

    lock, lock_hash = copy_and_lock_dependencies(dependency_dir, output)
    sbom = build_sbom(
        package_name,
        package_version,
        commit,
        source_date_epoch,
        hashes,
        lock,
        lock_hash,
    )
    sbom_path = output / "sbom.cdx.json"
    sbom_path.write_bytes(canonical_json_bytes(sbom))
    sbom_hash = sha256(sbom_path)

    provenance = {
        "schema_version": "2.0",
        "project": package_name,
        "version": package_version,
        "commit_sha": commit,
        "source_tree_clean": True,
        "source_date_epoch": source_date_epoch,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "build_tools": tool_versions,
        "dependency_lock_sha256": lock_hash,
        "sbom_sha256": sbom_hash,
        "verification_policy": {
            "candidate_manifest": "all payload files must match exact SHA-256 entries",
            "dependencies": "offline wheelhouse reconstructed with --require-hashes",
            "artifacts": "wheel and normalized sdist must reproduce byte-for-byte in verified environment",
            "publication": "not authorized by candidate construction",
        },
        "artifacts": [
            {
                "path": f"artifacts/{name}",
                "sha256": digest,
                "verification_policy": artifact_policy(name),
            }
            for name, digest in sorted(hashes.items())
        ],
    }
    provenance_path = output / "provenance.json"
    provenance_path.write_bytes(canonical_json_bytes(provenance))

    write_intoto_statement(
        output,
        hashes,
        commit,
        package_name,
        package_version,
        source_date_epoch,
        lock_hash,
        sbom_hash,
    )
    manifest_hash = write_candidate_manifest(
        output,
        commit,
        package_name,
        package_version,
        lock_hash,
        sbom_hash,
    )
    print(f"candidate-manifest-sha256: {manifest_hash}")
    return hashes


def load_hashes(output: Path) -> dict[str, str]:
    sums = output / "SHA256SUMS"
    if not sums.is_file():
        raise SystemExit(f"comparison candidate lacks SHA256SUMS: {output}")
    hashes: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            raise SystemExit(f"malformed SHA256SUMS line: {line}")
        digest, relative = line.split("  ", 1)
        hashes[Path(relative).name] = digest
    return hashes


def compare_candidates(expected_dir: Path, observed_dir: Path) -> bool:
    expected = load_hashes(expected_dir)
    observed = load_hashes(observed_dir)
    if expected != observed:
        print("release-candidate package artifacts are not byte-reproducible", file=sys.stderr)
        print(f"expected: {json.dumps(expected, sort_keys=True)}", file=sys.stderr)
        print(f"observed: {json.dumps(observed, sort_keys=True)}", file=sys.stderr)
        return False

    for relative in (
        "dependencies.lock.json",
        "requirements.lock",
        "sbom.cdx.json",
        "provenance.json",
        "provenance.intoto.jsonl",
        "candidate-manifest.json",
    ):
        expected_path = expected_dir / relative
        observed_path = observed_dir / relative
        if sha256(expected_path) != sha256(observed_path):
            print(f"release-candidate metadata is not reproducible: {relative}", file=sys.stderr)
            return False

    expected_deps = {
        path.relative_to(expected_dir).as_posix(): sha256(path)
        for path in sorted((expected_dir / "dependencies").iterdir())
        if path.is_file()
    }
    observed_deps = {
        path.relative_to(observed_dir).as_posix(): sha256(path)
        for path in sorted((observed_dir / "dependencies").iterdir())
        if path.is_file()
    }
    if expected_deps != observed_deps:
        print("release-candidate dependency wheelhouse changed between builds", file=sys.stderr)
        return False

    print("release candidate is byte-reproducible in the verified environment")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-dir", type=Path, required=True)
    parser.add_argument("--compare-to", type=Path)
    args = parser.parse_args()

    output = args.output.resolve()
    dependency_dir = args.dependency_dir.resolve()
    hashes = build(output, dependency_dir)
    if args.compare_to is not None:
        if not compare_candidates(args.compare_to.resolve(), output):
            return 1
    else:
        print("release candidate built")

    for name, digest in sorted(hashes.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
