from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from manager_runtime.deployment.sqlite_ops import (
    BackupError,
    create_sqlite_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)


class SQLiteBackupTests(unittest.TestCase):
    def _database(self, path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE manager_runs(run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state_json TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
                ("run-synthetic-1", 1, json.dumps({"status": "running"})),
            )
            connection.commit()

    def test_backup_verify_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            restored = root / "restored.sqlite3"
            self._database(source)

            created = create_sqlite_backup(source, backup)
            verified = verify_sqlite_backup(backup)
            restored_metadata = restore_sqlite_backup(backup, restored)

            self.assertEqual(created["sha256"], verified["sha256"])
            self.assertEqual(verified["schema"], restored_metadata["schema"])
            with sqlite3.connect(restored) as connection:
                row = connection.execute("SELECT run_id, revision FROM manager_runs").fetchone()
            self.assertEqual(row, ("run-synthetic-1", 1))

    def test_corrupted_backup_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            self._database(source)
            create_sqlite_backup(source, backup)
            with backup.open("ab") as handle:
                handle.write(b"corruption")
            with self.assertRaisesRegex(BackupError, "size does not match|checksum"):
                verify_sqlite_backup(backup)

    def test_manifest_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            self._database(source)
            create_sqlite_backup(source, backup)
            manifest = backup.with_suffix(".sqlite3.manifest.json")
            payload = json.loads(manifest.read_text())
            payload["sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload))
            with self.assertRaisesRegex(BackupError, "checksum"):
                verify_sqlite_backup(backup)

    def test_restore_will_not_overwrite_without_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            destination = root / "existing.sqlite3"
            self._database(source)
            self._database(destination)
            create_sqlite_backup(source, backup)
            with self.assertRaisesRegex(BackupError, "destination exists"):
                restore_sqlite_backup(backup, destination)

    def test_online_backup_excludes_uncommitted_writer_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            self._database(source)
            writer = sqlite3.connect(source)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
                    ("run-uncommitted", 1, "{}"),
                )
                create_sqlite_backup(source, backup)
            finally:
                writer.rollback()
                writer.close()
            verify_sqlite_backup(backup)
            with sqlite3.connect(backup) as connection:
                rows = connection.execute("SELECT run_id FROM manager_runs ORDER BY run_id").fetchall()
            self.assertEqual(rows, [("run-synthetic-1",)])

    def test_invalid_source_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "not-sqlite.sqlite3"
            source.write_bytes(b"not a sqlite database")
            with self.assertRaises(BackupError):
                create_sqlite_backup(source, root / "backup.sqlite3")

    def test_restore_replace_is_explicit_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            destination = root / "existing.sqlite3"
            self._database(source)
            with sqlite3.connect(source) as connection:
                connection.execute(
                    "INSERT INTO manager_runs(run_id, revision, state_json) VALUES (?, ?, ?)",
                    ("run-synthetic-2", 1, "{}"),
                )
            self._database(destination)
            create_sqlite_backup(source, backup)
            restore_sqlite_backup(backup, destination, allow_replace=True)
            with sqlite3.connect(destination) as connection:
                count = connection.execute("SELECT COUNT(*) FROM manager_runs").fetchone()[0]
            self.assertEqual(count, 2)
