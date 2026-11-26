"""Compatibility alias for recompute entry points."""
from .revision import (  # noqa: F401
    analyze_incremental, apply_correction, recompute_analysis, reanalyze,
    select_components_incremental,
)

recompute = recompute_analysis
