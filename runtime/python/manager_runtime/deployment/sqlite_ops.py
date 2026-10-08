from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


class BackupError(RuntimeError):
    """Raised when backup verification or restore fails closed."""


BACKUP_FORMAT = "manager-sqlite-backup-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30.0)


def _integrity_check(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA integrity_check").fetchall()
    if rows != [("ok",)]:
        details = "; ".join(str(row[0]) for row in rows[:5])
        raise BackupError(f"SQLite integrity_check failed: {details}")


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name, tbl_name, sql
        """
    ).fetchall()
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_summary(connection: sqlite3.Connection) -> dict[str, object]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    return {
        "fingerprint_sha256": _schema_fingerprint(connection),
        "user_version": user_version,
        "tables": tables,
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def create_sqlite_backup(
    source: str | Path,
    destination: str | Path,
    *,
    manifest: str | Path | None = None,
) -> dict[str, object]:
    """Create a consistent SQLite backup plus integrity manifest.

    Python's SQLite backup API snapshots a live database without requiring a raw
    filesystem copy of the main/WAL files. The resulting backup is integrity-checked
    before it is atomically promoted into place.
    """

    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.exists() or not source_path.is_file():
        raise BackupError(f"source database does not exist: {source_path}")
    if source_path.resolve() == destination_path.resolve():
        raise BackupError("backup destination must differ from source database")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(manifest) if manifest is not None else destination_path.with_suffix(
        destination_path.suffix + ".manifest.json"
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with _open_read_only(source_path) as source_db:
            _integrity_check(source_db)
            source_schema = _schema_summary(source_db)
            target_db = sqlite3.connect(temp_path)
            try:
                source_db.backup(target_db)
                target_db.commit()
                _integrity_check(target_db)
                target_schema = _schema_summary(target_db)
            finally:
                target_db.close()
        if target_schema != source_schema:
            raise BackupError("backup schema does not match the source snapshot")
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination_path)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite backup failed: {exc}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    metadata: dict[str, object] = {
        "format": BACKUP_FORMAT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_file": destination_path.name,
        "size_bytes": destination_path.stat().st_size,
        "sha256": _sha256(destination_path),
        "schema": source_schema,
    }
    _atomic_write_json(manifest_path, metadata)
    return metadata


def verify_sqlite_backup(
    backup: str | Path,
    *,
    manifest: str | Path | None = None,
) -> dict[str, object]:
    backup_path = Path(backup)
    manifest_path = Path(manifest) if manifest is not None else backup_path.with_suffix(
        backup_path.suffix + ".manifest.json"
    )
    if not backup_path.exists() or not backup_path.is_file():
        raise BackupError(f"backup database does not exist: {backup_path}")
    try:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupError(f"backup manifest does not exist: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"backup manifest is unreadable or invalid: {manifest_path}") from exc
    if not isinstance(metadata, dict) or metadata.get("format") != BACKUP_FORMAT:
        raise BackupError("backup manifest format is unsupported")
    if metadata.get("size_bytes") != backup_path.stat().st_size:
        raise BackupError("backup size does not match manifest")
    if metadata.get("sha256") != _sha256(backup_path):
        raise BackupError("backup checksum does not match manifest")

    try:
        with _open_read_only(backup_path) as connection:
            _integrity_check(connection)
            schema = _schema_summary(connection)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"backup is not a valid SQLite database: {exc}") from exc
    if metadata.get("schema") != schema:
        raise BackupError("backup schema does not match manifest")
    return metadata


def restore_sqlite_backup(
    backup: str | Path,
    destination: str | Path,
    *,
    manifest: str | Path | None = None,
    allow_replace: bool = False,
) -> dict[str, object]:
    """Restore a verified backup through a temporary database then atomically replace.

    Existing state is never overwritten unless ``allow_replace`` is explicit. The
    caller is responsible for stopping writers before restore; restoring underneath
    a live process is intentionally not supported.
    """

    backup_path = Path(backup)
    metadata = verify_sqlite_backup(backup_path, manifest=manifest)
    destination_path = Path(destination)
    if destination_path.exists() and not allow_replace:
        raise BackupError("destination exists; pass allow_replace only after stopping all writers")
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".restore.tmp", dir=destination_path.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with _open_read_only(backup_path) as source_db:
            target_db = sqlite3.connect(temp_path)
            try:
                source_db.backup(target_db)
                target_db.commit()
                _integrity_check(target_db)
                restored_schema = _schema_summary(target_db)
            finally:
                target_db.close()
        if restored_schema != metadata.get("schema"):
            raise BackupError("restored database schema differs from verified backup")
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination_path)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite restore failed: {exc}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with _open_read_only(destination_path) as restored_db:
        _integrity_check(restored_db)
        if _schema_summary(restored_db) != metadata.get("schema"):
            raise BackupError("post-restore schema verification failed")
    return metadata
