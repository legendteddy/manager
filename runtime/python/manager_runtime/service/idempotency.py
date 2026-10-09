from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class IdempotencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    disposition: Literal["new", "replay", "in_progress", "ambiguous", "conflict"]
    status_code: int | None = None
    response: dict[str, Any] | None = None
    run_id: str | None = None


def canonical_fingerprint(method: str, path: str, payload: Any) -> str:
    encoded = json.dumps(
        {"method": method, "path": path, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class SQLiteIdempotencyStore:
    """Durable network request identity with conservative crash recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_api_idempotency (
                    subject TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('in_progress','completed','ambiguous')),
                    response_status INTEGER,
                    response_json TEXT,
                    run_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(subject, idempotency_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_api_run_owners (
                    run_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def ready(self) -> bool | tuple[bool, str]:
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT 1").fetchone()
                if row is None:
                    return False, "idempotency database unavailable"
        except sqlite3.Error as exc:
            return False, f"idempotency database unavailable ({type(exc).__name__})"
        return True

    def recover_orphans(self) -> int:
        """Turn prior-process in-flight work into ambiguity, never automatic retry."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE manager_api_idempotency
                   SET status = 'ambiguous', updated_at = CURRENT_TIMESTAMP
                 WHERE status = 'in_progress'
                """
            )
            connection.commit()
            return int(cursor.rowcount)

    def claim(
        self,
        *,
        subject: str,
        key: str,
        method: str,
        path: str,
        request_fingerprint: str,
        run_id: str | None,
    ) -> IdempotencyClaim:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT method, path, request_fingerprint, status,
                       response_status, response_json, run_id
                  FROM manager_api_idempotency
                 WHERE subject = ? AND idempotency_key = ?
                """,
                (subject, key),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO manager_api_idempotency(
                        subject, idempotency_key, method, path, request_fingerprint,
                        status, run_id
                    ) VALUES (?, ?, ?, ?, ?, 'in_progress', ?)
                    """,
                    (subject, key, method, path, request_fingerprint, run_id),
                )
                connection.commit()
                return IdempotencyClaim("new", run_id=run_id)

            same_identity = (
                row["method"] == method
                and row["path"] == path
                and row["request_fingerprint"] == request_fingerprint
                and row["run_id"] == run_id
            )
            if not same_identity:
                connection.rollback()
                return IdempotencyClaim("conflict", run_id=row["run_id"])

            status = row["status"]
            if status == "completed":
                payload = None
                if row["response_status"] is None or row["response_json"] is None:
                    connection.rollback()
                    raise IdempotencyError("persisted completed idempotency response is incomplete")
                try:
                    parsed = json.loads(row["response_json"])
                except json.JSONDecodeError as exc:
                    connection.rollback()
                    raise IdempotencyError("persisted idempotency response is corrupted") from exc
                if not isinstance(parsed, dict):
                    connection.rollback()
                    raise IdempotencyError("persisted idempotency response is malformed")
                payload = parsed
                connection.rollback()
                return IdempotencyClaim(
                    "replay",
                    status_code=int(row["response_status"]),
                    response=payload,
                    run_id=row["run_id"],
                )
            connection.rollback()
            if status == "in_progress":
                return IdempotencyClaim("in_progress", run_id=row["run_id"])
            if status == "ambiguous":
                return IdempotencyClaim("ambiguous", run_id=row["run_id"])
            raise IdempotencyError("persisted idempotency status is invalid")

    def complete(
        self,
        *,
        subject: str,
        key: str,
        status_code: int,
        response: dict[str, Any],
    ) -> bool:
        encoded = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE manager_api_idempotency
                   SET status = 'completed', response_status = ?, response_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE subject = ? AND idempotency_key = ? AND status = 'in_progress'
                """,
                (status_code, encoded, subject, key),
            )
            connection.commit()
            return cursor.rowcount == 1

    def mark_ambiguous(self, *, subject: str, key: str) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE manager_api_idempotency
                   SET status = 'ambiguous', updated_at = CURRENT_TIMESTAMP
                 WHERE subject = ? AND idempotency_key = ? AND status = 'in_progress'
                """,
                (subject, key),
            )
            connection.commit()
            return cursor.rowcount == 1

    def abandon(self, *, subject: str, key: str) -> bool:
        """Delete a claim only before its backend operation is safe to retry."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM manager_api_idempotency
                 WHERE subject = ? AND idempotency_key = ? AND status = 'in_progress'
                """,
                (subject, key),
            )
            connection.commit()
            return cursor.rowcount == 1

    def bind_run(self, *, subject: str, run_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT subject FROM manager_api_run_owners WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO manager_api_run_owners(run_id, subject) VALUES (?, ?)",
                    (run_id, subject),
                )
                connection.commit()
                return
            if row["subject"] != subject:
                connection.rollback()
                raise IdempotencyError("run identity is already bound to another service subject")
            connection.rollback()

    def subject_owns_run(self, *, subject: str, run_id: str) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT subject FROM manager_api_run_owners WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise IdempotencyError("run ownership lookup failed") from exc
        return row is not None and row["subject"] == subject
