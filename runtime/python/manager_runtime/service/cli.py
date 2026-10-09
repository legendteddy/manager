from __future__ import annotations

import sys

from ..deployment import ConfigError
from .gateway import GatewayConfigError, run_service


def main() -> int:
    try:
        run_service()
    except (ConfigError, GatewayConfigError) as exc:
        print(f"manager-service startup rejected configuration: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Application-owned backend/provider/MCP initialization may include
        # sensitive exception text. Preserve the exception class for operators
        # without echoing the raw message or chained transport/credential data.
        print(
            f"manager-service startup failed safely ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
