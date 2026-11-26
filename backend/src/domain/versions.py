"""Compatibility alias: versioned correction analysis."""
from .revision import *  # noqa: F401,F403
from .revision import analyze_revision as analyze_version  # noqa: F401
from .revision import analyze_incremental, apply_correction  # noqa: F401

analyze_versioned = analyze_incremental
versioned_analysis = analyze_incremental
