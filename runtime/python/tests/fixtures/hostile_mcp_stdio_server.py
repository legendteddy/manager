from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MODE_ENV = "MANAGER_HOSTILE_MCP_MODE"
PID_ENV = "MANAGER_HOSTILE_MCP_PID"
SECRET = "synthetic-hostile-secret-do-not-log"


def write_pid() -> None:
    path = os.environ.get(PID_ENV)
    if path:
        Path(path).write_text(str(os.getpid()), encoding="utf-8")


def main() -> None:
    write_pid()
    mode = os.environ.get(MODE_ENV, "crash")

    if mode == "malformed":
        # Deliberately violate stdio JSON-RPC framing, then stay alive long
        # enough for the client to observe the malformed frame rather than EOF.
        sys.stdout.write('{"jsonrpc":"2.0","id":1,"result":')
        sys.stdout.flush()
        time.sleep(60)
        return

    if mode == "stderr_flood":
        # Include a synthetic credential-shaped value. Manager must not relay
        # this child stderr into parent logs or normalized tool errors.
        sys.stderr.write("AUTHORIZATION=Bearer " + SECRET + "\n")
        sys.stderr.write("x" * (2 * 1024 * 1024))
        sys.stderr.flush()
        time.sleep(60)
        return

    if mode == "hang":
        time.sleep(60)
        return

    if mode == "crash":
        sys.stderr.write("crash detail: " + SECRET + "\n")
        sys.stderr.flush()
        raise SystemExit(70)

    raise SystemExit(71)


if __name__ == "__main__":
    main()
