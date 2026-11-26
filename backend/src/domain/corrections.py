"""Compatibility alias for the correction/incremental recomputation domain."""

from .revision import (  # noqa: F401
    IncrementalResult,
    analyze_incremental,
    analyze_revision,
    apply_correction,
    cached_selection,
    compute_fold_artifact,
    correct_rows,
    correct_strength,
    fold_signature,
    fold_training_key,
    incremental_select,
    reanalyze,
    recompute_analysis,
    register_correction_rows,
    reselect_components,
    revise_strength,
    select_components_cached,
    select_components_incremental,
    training_signature,
    update_actual,
)

__all__ = [
    'IncrementalResult', 'analyze_incremental', 'analyze_revision', 'apply_correction',
    'cached_selection', 'compute_fold_artifact', 'correct_rows', 'correct_strength',
    'fold_signature', 'fold_training_key', 'incremental_select', 'reanalyze',
    'recompute_analysis', 'register_correction_rows', 'reselect_components',
    'revise_strength', 'select_components_cached', 'select_components_incremental',
    'training_signature', 'update_actual',
]
