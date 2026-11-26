"""Compatibility alias (singular module name)."""
from .revision import *  # noqa: F401,F403
from .revision import (  # noqa: F401
    IncrementalResult, analyze_incremental, apply_correction,
    select_components_incremental,
)
