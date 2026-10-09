#!/usr/bin/env python3
"""Independently verify a Manager release-candidate directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

NAME_RE = re.compile(r"[-_.]+")
BUILD_TYPE = "https://github.com/legendteddy/manager/release-candidate/v1"
BUILDER_ID = "https://github.com/legendteddy/manager/scripts/build_release_candidate.py"


def normalize_name(name: str) -> str:
    return NAME_RE.sub("-", name).lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def require_hex_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def load_source_version(pyproject: Path) -> tuple[str, str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data["project"]
    return str(project["name"]), str(project["version"])


def parse_sha256sums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            raise ValueError(f"malformed SHA256SUMS line: {line}")
        digest, relative = line.split("  ", 1)
        require_hex_sha256(digest, "SHA256SUMS digest")
        if relative in result:
            raise ValueError(f"duplicate SHA256SUMS path: {relative}")
        result[relative] = digest
    return result


def expected_requirements(lock: dict[str, object]) -> str:
    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("dependency lock packages must be a non-empty array")
    lines: list[str] = []
    seen_names: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("dependency lock package must be an object")
        name = package.get("name")
        version = package.get("version")
        filename = package.get("filename")
        digest = require_hex_sha256(package.get("sha256"), "dependency hash")
        if not all(isinstance(value, str) and value for value in (name, version, filename)):
            raise ValueError("dependency lock package fields must be non-empty strings")
        if name != normalize_name(name):
            raise ValueError(f"dependency lock project name is not normalized: {name}")
        if name in seen_names:
            raise ValueError(f"duplicate dependency project in lock: {name}")
        seen_names.add(name)
        if Path(filename).name != filename or not filename.endswith(".whl"):
            raise ValueError(f"unsafe or non-wheel dependency filename: {filename}")
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    return "\n".join(lines) + "\n"


def expected_dependency_records(lock: dict[str, object]) -> dict[str, tuple[str, str, str]]:
    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("dependency lock packages must be a non-empty array")
    result: dict[str, tuple[str, str, str]] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("dependency lock package must be an object")
        name = package.get("name")
        version = package.get("version")
        filename = package.get("filename")
        digest = require_hex_sha256(package.get("sha256"), "dependency hash")
        if not all(isinstance(value, str) and value for value in (name, version, filename)):
            raise ValueError("dependency lock package fields must be non-empty strings")
        if name in result:
            raise ValueError(f"duplicate dependency project in lock: {name}")
        result[name] = (version, filename, digest)
    return result


def verify_candidate(
    candidate: Path,
    *,
    expected_commit: str | None = None,
    expected_version: str | None = None,
    expected_manifest_sha256: str | None = None,
    pyproject: Path | None = None,
    changelog: Path | None = None,
    expected_changelog_sha256: str | None = None,
) -> dict[str, str]:
    if not candidate.is_dir():
        raise ValueError(f"candidate directory does not exist: {candidate}")
    for path in candidate.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"candidate may not contain symlinks: {path.relative_to(candidate)}")

    manifest_path = candidate / "candidate-manifest.json"
    manifest_digest = sha256(manifest_path)
    if expected_manifest_sha256 is not None and manifest_digest != expected_manifest_sha256:
        raise ValueError(
            f"candidate manifest digest mismatch: expected {expected_manifest_sha256}, observed {manifest_digest}"
        )
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "1.0":
        raise ValueError("unsupported candidate-manifest schema_version")

    commit = manifest.get("commit_sha")
    version = manifest.get("version")
    project = manifest.get("project")
    if not all(isinstance(value, str) and value for value in (commit, version, project)):
        raise ValueError("candidate manifest project/version/commit must be non-empty strings")
    if expected_commit is not None and commit != expected_commit:
        raise ValueError(f"commit mismatch: expected {expected_commit}, observed {commit}")
    if expected_version is not None and version != expected_version:
        raise ValueError(f"version mismatch: expected {expected_version}, observed {version}")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("candidate manifest files must be a non-empty array")
    expected_files: dict[str, str] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("candidate manifest file entry must be an object")
        relative = entry.get("path")
        digest = require_hex_sha256(entry.get("sha256"), "candidate file hash")
        if not isinstance(relative, str) or not relative:
            raise ValueError("candidate manifest path must be a non-empty string")
        pure = Path(relative)
        if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative:
            raise ValueError(f"unsafe candidate manifest path: {relative}")
        if relative in expected_files:
            raise ValueError(f"duplicate candidate manifest path: {relative}")
        expected_files[relative] = digest

    actual_files = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file() and path.name != "candidate-manifest.json"
    }
    if actual_files != set(expected_files):
        missing = sorted(set(expected_files) - actual_files)
        extra = sorted(actual_files - set(expected_files))
        raise ValueError(f"candidate artifact set mismatch: missing={missing}, extra={extra}")
    for relative, digest in sorted(expected_files.items()):
        observed = sha256(candidate / relative)
        if observed != digest:
            raise ValueError(f"checksum mismatch for {relative}: expected {digest}, observed {observed}")

    sums = parse_sha256sums(candidate / "SHA256SUMS")
    artifact_entries = {path: digest for path, digest in expected_files.items() if path.startswith("artifacts/")}
    if sums != artifact_entries:
        raise ValueError("SHA256SUMS does not exactly match candidate package artifacts")
    wheels = [path for path in artifact_entries if path.endswith(".whl")]
    sdists = [path for path in artifact_entries if path.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("candidate must contain exactly one wheel and one .tar.gz sdist")

    lock_path = candidate / "dependencies.lock.json"
    lock_digest = sha256(lock_path)
    if lock_digest != manifest.get("dependency_lock_sha256"):
        raise ValueError("candidate manifest dependency lock identity mismatch")
    lock = load_json(lock_path)
    if lock.get("schema_version") != "1.0":
        raise ValueError("unsupported dependency lock schema_version")
    if lock.get("resolver_scope") != "release-python-platform-wheelhouse":
        raise ValueError("unexpected dependency lock resolver scope")

    packages = lock.get("packages")
    if not isinstance(packages, list):
        raise ValueError("dependency lock packages must be an array")
    dependency_files: set[str] = set()
    dependency_versions: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("dependency lock package must be an object")
        filename = package.get("filename")
        name = package.get("name")
        version_value = package.get("version")
        digest = require_hex_sha256(package.get("sha256"), "dependency hash")
        if not all(isinstance(value, str) and value for value in (filename, name, version_value)):
            raise ValueError("dependency lock package fields must be non-empty strings")
        relative = f"dependencies/{filename}"
        dependency_files.add(relative)
        dependency_versions[name] = version_value
        if expected_files.get(relative) != digest:
            raise ValueError(f"dependency manifest hash mismatch for {filename}")

    manifest_dependency_files = {path for path in expected_files if path.startswith("dependencies/")}
    if manifest_dependency_files != dependency_files:
        missing = sorted(dependency_files - manifest_dependency_files)
        extra = sorted(manifest_dependency_files - dependency_files)
        raise ValueError(
            f"dependency wheelhouse does not exactly match dependency lock: missing={missing}, extra={extra}"
        )

    roots = lock.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("dependency lock roots must be a non-empty array")
    for root in roots:
        if not isinstance(root, dict):
            raise ValueError("dependency lock root must be an object")
        name = root.get("name")
        root_version = root.get("version")
        if not isinstance(name, str) or not isinstance(root_version, str):
            raise ValueError("dependency lock root fields must be strings")
        if dependency_versions.get(name) != root_version:
            raise ValueError(f"dependency root is missing or substituted: {name}=={root_version}")

    if (candidate / "requirements.lock").read_text(encoding="utf-8") != expected_requirements(lock):
        raise ValueError("requirements.lock is not the deterministic rendering of dependencies.lock.json")

    sbom_path = candidate / "sbom.cdx.json"
    sbom_digest = sha256(sbom_path)
    if sbom_digest != manifest.get("sbom_sha256"):
        raise ValueError("candidate manifest SBOM identity mismatch")
    sbom = load_json(sbom_path)
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.6":
        raise ValueError("SBOM is not CycloneDX 1.6 JSON")
    metadata = sbom.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("component"), dict):
        raise ValueError("SBOM metadata.component is missing")
    sbom_project = metadata["component"]
    if sbom_project.get("name") != project or sbom_project.get("version") != version:
        raise ValueError("SBOM project identity mismatch")
    expected_project_purl = f"pkg:pypi/{normalize_name(project)}@{version}"
    if sbom_project.get("purl") not in {None, expected_project_purl}:
        raise ValueError("SBOM project purl mismatch")
    properties = sbom_project.get("properties")
    if not isinstance(properties, list):
        raise ValueError("SBOM component properties are missing")
    property_map = {
        item.get("name"): item.get("value")
        for item in properties
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if property_map.get("manager:commit-sha") != commit:
        raise ValueError("SBOM commit identity mismatch")
    if property_map.get("manager:dependency-lock-sha256") != lock_digest:
        raise ValueError("SBOM dependency lock identity mismatch")

    components = sbom.get("components")
    if not isinstance(components, list):
        raise ValueError("SBOM components must be an array")
    observed_sbom_dependencies: dict[str, tuple[str, str, str]] = {}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("SBOM dependency component must be an object")
        name = component.get("name")
        component_version = component.get("version")
        if not isinstance(name, str) or not isinstance(component_version, str):
            raise ValueError("SBOM dependency name/version must be strings")
        if name in observed_sbom_dependencies:
            raise ValueError(f"duplicate dependency component in SBOM: {name}")
        expected_purl = f"pkg:pypi/{name}@{component_version}"
        if component.get("purl") != expected_purl or component.get("bom-ref") != expected_purl:
            raise ValueError(f"SBOM dependency purl identity mismatch: {name}")
        hashes = component.get("hashes")
        if not isinstance(hashes, list):
            raise ValueError(f"SBOM dependency hashes missing: {name}")
        sha_values = [
            item.get("content")
            for item in hashes
            if isinstance(item, dict) and item.get("alg") == "SHA-256"
        ]
        if len(sha_values) != 1:
            raise ValueError(f"SBOM dependency must contain exactly one SHA-256 hash: {name}")
        digest_value = require_hex_sha256(sha_values[0], f"SBOM dependency hash for {name}")
        component_properties = component.get("properties")
        if not isinstance(component_properties, list):
            raise ValueError(f"SBOM dependency properties missing: {name}")
        component_property_map = {
            item.get("name"): item.get("value")
            for item in component_properties
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        filename = component_property_map.get("manager:wheel-filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"SBOM dependency wheel filename missing: {name}")
        observed_sbom_dependencies[name] = (component_version, filename, digest_value)

    expected_sbom_dependencies = expected_dependency_records(lock)
    if observed_sbom_dependencies != expected_sbom_dependencies:
        raise ValueError("SBOM dependency components do not exactly match dependency lock")

    provenance = load_json(candidate / "provenance.json")
    if provenance.get("schema_version") != "2.0":
        raise ValueError("unsupported provenance schema_version")
    if provenance.get("project") != project:
        raise ValueError("provenance project does not match candidate manifest")
    if provenance.get("commit_sha") != commit or provenance.get("version") != version:
        raise ValueError("provenance commit/version does not match candidate manifest")
    if provenance.get("source_tree_clean") is not True:
        raise ValueError("provenance does not attest a clean source tree")
    if provenance.get("dependency_lock_sha256") != lock_digest:
        raise ValueError("provenance dependency lock identity mismatch")
    if provenance.get("sbom_sha256") != sbom_digest:
        raise ValueError("provenance SBOM identity mismatch")
    provenance_artifacts = provenance.get("artifacts")
    if not isinstance(provenance_artifacts, list):
        raise ValueError("provenance artifacts must be an array")
    observed_provenance = {
        item.get("path"): item.get("sha256")
        for item in provenance_artifacts
        if isinstance(item, dict)
    }
    if observed_provenance != artifact_entries:
        raise ValueError("provenance artifact identities do not match SHA256SUMS")

    statement = load_json(candidate / "provenance.intoto.jsonl")
    if statement.get("_type") != "https://in-toto.io/Statement/v1":
        raise ValueError("unexpected in-toto statement type")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ValueError("unexpected provenance predicate type")
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        raise ValueError("in-toto statement subject must be an array")
    statement_subjects = {}
    for subject in subjects:
        if not isinstance(subject, dict) or not isinstance(subject.get("digest"), dict):
            raise ValueError("invalid in-toto subject")
        statement_subjects[subject.get("name")] = subject["digest"].get("sha256")
    if statement_subjects != artifact_entries:
        raise ValueError("in-toto statement subjects do not match candidate package artifacts")

    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise ValueError("in-toto statement predicate must be an object")
    build_definition = predicate.get("buildDefinition")
    if not isinstance(build_definition, dict):
        raise ValueError("in-toto buildDefinition is missing")
    if build_definition.get("buildType") != BUILD_TYPE:
        raise ValueError("unexpected in-toto build type")
    external_parameters = build_definition.get("externalParameters")
    expected_external_parameters = {
        "project": project,
        "version": version,
        "commit_sha": commit,
        "dependency_lock_sha256": lock_digest,
    }
    if external_parameters != expected_external_parameters:
        raise ValueError("in-toto external parameters do not match candidate identities")
    resolved_dependencies = build_definition.get("resolvedDependencies")
    expected_resolved_dependencies = [
        {
            "uri": f"git+https://github.com/legendteddy/manager@{commit}",
            "digest": {"gitCommit": commit},
        },
        {
            "uri": "file:dependencies.lock.json",
            "digest": {"sha256": lock_digest},
        },
    ]
    if resolved_dependencies != expected_resolved_dependencies:
        raise ValueError("in-toto resolved dependencies do not match source commit and dependency lock")
    run_details = predicate.get("runDetails")
    if not isinstance(run_details, dict):
        raise ValueError("in-toto runDetails is missing")
    if run_details.get("builder") != {"id": BUILDER_ID}:
        raise ValueError("unexpected in-toto builder identity")
    if run_details.get("metadata") != {"invocationId": commit}:
        raise ValueError("in-toto invocation identity mismatch")
    if run_details.get("byproducts") != [
        {"name": "sbom.cdx.json", "digest": {"sha256": sbom_digest}}
    ]:
        raise ValueError("in-toto SBOM byproduct identity mismatch")

    if pyproject is not None:
        source_project, source_version = load_source_version(pyproject)
        if source_project != project or source_version != version:
            raise ValueError(
                f"source package metadata mismatch: candidate={project} {version}, source={source_project} {source_version}"
            )

    if expected_changelog_sha256 is not None:
        if changelog is None:
            raise ValueError("expected changelog digest requires --changelog")
        observed_changelog = sha256(changelog)
        if observed_changelog != expected_changelog_sha256:
            raise ValueError(
                f"changelog digest mismatch: expected {expected_changelog_sha256}, observed {observed_changelog}"
            )

    return {
        "project": project,
        "version": version,
        "commit_sha": commit,
        "candidate_manifest_sha256": manifest_digest,
        "dependency_lock_sha256": lock_digest,
        "sbom_sha256": sbom_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--pyproject", type=Path)
    parser.add_argument("--changelog", type=Path)
    parser.add_argument("--expected-changelog-sha256")
    args = parser.parse_args()

    try:
        evidence = verify_candidate(
            args.candidate.resolve(),
            expected_commit=args.expected_commit,
            expected_version=args.expected_version,
            expected_manifest_sha256=args.expected_manifest_sha256,
            pyproject=args.pyproject.resolve() if args.pyproject else None,
            changelog=args.changelog.resolve() if args.changelog else None,
            expected_changelog_sha256=args.expected_changelog_sha256,
        )
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"release candidate verification failed: {exc}", file=sys.stderr)
        return 1

    print("release candidate verification passed")
    for key, value in evidence.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
