"""Manager Python reference runtime.

This package is a reference implementation of the public Manager contracts.
It is not the canonical architecture definition and does not imply production readiness.
"""

from .engine import run
from .orchestrator import run_with_model

__all__ = ["run", "run_with_model"]
