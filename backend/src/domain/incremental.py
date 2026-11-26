"""Compatibility alias: incremental fold reuse / recomputation."""
from .revision import *  # noqa: F401,F403
from .revision import (  # noqa: F401
    IncrementalResult, analyze_incremental, analyze_revision, reanalyze,
    recompute_analysis, select_components_cached, select_components_incremental,
)

incremental_analyze = analyze_incremental
run_incremental = analyze_incremental
