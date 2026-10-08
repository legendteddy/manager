from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]


def load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build_rc = load_script("manager_build_release_candidate", "scripts/build_release_candidate.py")
verify_rc = load_script("manager_verify_release_candidate", "scripts/verify_release_candidate.py")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseCandidateSecurityTests(unittest.TestCase):
    def make_candidate(self, root: Path) -> tuple[Path, dict[str, str]]:
        candidate = root / "candidate"
        artifacts = candidate / "artifacts"
        dependencies = candidate / "dependencies"
        artifacts.mkdir(parents=True)
        dependencies.mkdir()

        wheel = artifacts / "manager_reference_runtime-0.10.0-py3-none-any.whl"
        sdist = artifacts / "manager_reference_runtime-0.10.0.tar.gz"
        dep = dependencies / "example_dep-1.2.3-py3-none-any.whl"
        wheel.write_bytes(b"wheel-bytes")
        sdist.write_bytes(b"sdist-bytes")
        dep.write_bytes(b"dependency-wheel")

        artifact_hashes = {
            f"artifacts/{wheel.name}": digest(wheel),
            f"artifacts/{sdist.name}": digest(sdist),
        }
        (candidate / "SHA256SUMS").write_text(
            "".join(f"{value}  {path}\n" for path, value in sorted(artifact_hashes.items())),
            encoding="utf-8",
        )

        lock = {
            "schema_version": "1.0",
            "resolver_scope": "release-python-platform-wheelhouse",
            "roots": [{"name": "example-dep", "version": "1.2.3"}],
            "packages": [
                {
                    "name": "example-dep",
                    "version": "1.2.3",
                    "filename": dep.name,
                    "sha256": digest(dep),
                }
            ],
        }
        (candidate / "dependencies.lock.json").write_text(
            json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        lock_hash = digest(candidate / "dependencies.lock.json")
        (candidate / "requirements.lock").write_text(
            f"example-dep==1.2.3 --hash=sha256:{digest(dep)}\n", encoding="utf-8"
        )

        sbom = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "library",
                    "name": "manager-reference-runtime",
                    "version": "0.10.0",
                    "properties": [
                        {"name": "manager:commit-sha", "value": "a" * 40},
                        {"name": "manager:dependency-lock-sha256", "value": lock_hash},
                    ],
                }
            },
            "components": [],
        }
        (candidate / "sbom.cdx.json").write_text(
            json.dumps(sbom, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        sbom_hash = digest(candidate / "sbom.cdx.json")

        provenance = {
            "schema_version": "2.0",
            "project": "manager-reference-runtime",
            "version": "0.10.0",
            "commit_sha": "a" * 40,
            "source_tree_clean": True,
            "dependency_lock_sha256": lock_hash,
            "sbom_sha256": sbom_hash,
            "artifacts": [
                {"path": path, "sha256": value, "verification_policy": "test"}
                for path, value in sorted(artifact_hashes.items())
            ],
        }
        (candidate / "provenance.json").write_text(
            json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )

        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": path, "digest": {"sha256": value}}
                for path, value in sorted(artifact_hashes.items())
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
        (candidate / "provenance.intoto.jsonl").write_text(
            json.dumps(statement, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )

        payload = {}
        for path in candidate.rglob("*"):
            if path.is_file() and path.name != "candidate-manifest.json":
                payload[path.relative_to(candidate).as_posix()] = digest(path)
        manifest = {
            "schema_version": "1.0",
            "project": "manager-reference-runtime",
            "version": "0.10.0",
            "commit_sha": "a" * 40,
            "dependency_lock_sha256": lock_hash,
            "sbom_sha256": sbom_hash,
            "files": [{"path": path, "sha256": value} for path, value in sorted(payload.items())],
        }
        (candidate / "candidate-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        return candidate, {
            "manifest": digest(candidate / "candidate-manifest.json"),
            "wheel": artifact_hashes[f"artifacts/{wheel.name}"],
            "sdist": artifact_hashes[f"artifacts/{sdist.name}"],
        }

    def test_valid_candidate_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, evidence = self.make_candidate(Path(tmp))
            observed = verify_rc.verify_candidate(
                candidate,
                expected_commit="a" * 40,
                expected_version="0.10.0",
                expected_manifest_sha256=evidence["manifest"],
            )
            self.assertEqual(observed["candidate_manifest_sha256"], evidence["manifest"])

    def test_modified_wheel_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            wheel = next((candidate / "artifacts").glob("*.whl"))
            wheel.write_bytes(b"tampered-wheel")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_rc.verify_candidate(candidate)

    def test_modified_sdist_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            sdist = next((candidate / "artifacts").glob("*.tar.gz"))
            sdist.write_bytes(b"tampered-sdist")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_rc.verify_candidate(candidate)

    def test_missing_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            next((candidate / "artifacts").glob("*.whl")).unlink()
            with self.assertRaisesRegex(ValueError, "artifact set mismatch"):
                verify_rc.verify_candidate(candidate)

    def test_wrong_commit_and_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            with self.assertRaisesRegex(ValueError, "commit mismatch"):
                verify_rc.verify_candidate(candidate, expected_commit="b" * 40)
            with self.assertRaisesRegex(ValueError, "version mismatch"):
                verify_rc.verify_candidate(candidate, expected_version="9.9.9")

    def test_dependency_substitution_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            dep = next((candidate / "dependencies").glob("*.whl"))
            dep.write_bytes(b"substituted")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_rc.verify_candidate(candidate)

    def test_missing_dependency_hash_is_rejected_even_if_manifest_is_rebound(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, _ = self.make_candidate(Path(tmp))
            lock_path = candidate / "dependencies.lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"][0].pop("sha256")
            lock_path.write_text(json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            manifest_path = candidate / "candidate-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["path"] == "dependencies.lock.json":
                    entry["sha256"] = digest(lock_path)
            manifest["dependency_lock_sha256"] = digest(lock_path)
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "dependency hash"):
                verify_rc.verify_candidate(candidate)

    def test_changed_candidate_invalidates_prior_approval_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate, evidence = self.make_candidate(Path(tmp))
            manifest_path = candidate / "candidate-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "0.10.1"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "candidate manifest digest mismatch"):
                verify_rc.verify_candidate(candidate, expected_manifest_sha256=evidence["manifest"])

    def test_dirty_source_tree_is_rejected(self):
        with mock.patch.object(build_rc, "run", return_value=" M runtime/python/pyproject.toml"):
            with self.assertRaisesRegex(SystemExit, "dirty source tree"):
                build_rc.ensure_clean_source_tree()

    def test_compare_candidates_detects_reproducibility_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, _ = self.make_candidate(root / "first")
            second, _ = self.make_candidate(root / "second")
            sdist = next((second / "artifacts").glob("*.tar.gz"))
            sdist.write_bytes(b"different-sdist")
            (second / "SHA256SUMS").write_text(
                "".join(
                    f"{digest(path)}  artifacts/{path.name}\n"
                    for path in sorted((second / "artifacts").iterdir())
                ),
                encoding="utf-8",
            )
            self.assertFalse(build_rc.compare_candidates(first, second))

    def test_wheel_identity_ignores_nested_vendored_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "setuptools-84.0.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "setuptools-84.0.0.dist-info/METADATA",
                    "Name: setuptools\nVersion: 84.0.0\n",
                )
                archive.writestr(
                    "setuptools/_vendor/example-1.0.dist-info/METADATA",
                    "Name: example\nVersion: 1.0\n",
                )
            self.assertEqual(build_rc.read_wheel_identity(wheel), ("setuptools", "84.0.0"))

    def test_sdist_normalization_removes_archive_metadata_variance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            for path, mtime, uid in ((first, 123, 1000), (second, 999, 2000)):
                with tarfile.open(path, "w:gz") as archive:
                    info = tarfile.TarInfo("pkg/file.txt")
                    payload = b"same-content"
                    info.size = len(payload)
                    info.mtime = mtime
                    info.uid = uid
                    info.gid = uid
                    info.uname = "user"
                    info.gname = "group"
                    archive.addfile(info, io.BytesIO(payload))
            build_rc.normalize_sdist(first, 100)
            build_rc.normalize_sdist(second, 100)
            self.assertEqual(digest(first), digest(second))


if __name__ == "__main__":
    unittest.main()
