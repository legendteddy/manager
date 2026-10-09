from __future__ import annotations

from .gateway import run_service


def main() -> int:
    run_service()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
