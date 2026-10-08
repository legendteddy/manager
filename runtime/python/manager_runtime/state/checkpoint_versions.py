from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .base import RunStateError

CURRENT_AGENT_LOOP_CHECKPOINT_VERSION = 1
CheckpointMigration = Callable[[dict[str, Any]], dict[str, Any]]

# Migrations are deliberately explicit. Stage 8 introduced version 1, so there
# is no legitimate older public checkpoint format to invent a migration for.
# A future breaking checkpoint revision must add a reviewed migration here or
# fail closed rather than guessing how persisted state should be interpreted.
AGENT_LOOP_CHECKPOINT_MIGRATIONS: dict[int, CheckpointMigration] = {}


def migrate_agent_loop_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        raise RunStateError("durable agent loop checkpoint must be an object")
    value = deepcopy(checkpoint)
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RunStateError("durable agent loop checkpoint has invalid version")
    if version > CURRENT_AGENT_LOOP_CHECKPOINT_VERSION:
        raise RunStateError(
            f"durable agent loop checkpoint version {version} is newer than this runtime"
        )

    while version < CURRENT_AGENT_LOOP_CHECKPOINT_VERSION:
        migration = AGENT_LOOP_CHECKPOINT_MIGRATIONS.get(version)
        if migration is None:
            raise RunStateError(
                f"no reviewed migration exists for durable agent loop checkpoint version {version}"
            )
        value = migration(value)
        next_version = value.get("version")
        if not isinstance(next_version, int) or next_version <= version:
            raise RunStateError("checkpoint migration did not advance its version")
        version = next_version

    return value
