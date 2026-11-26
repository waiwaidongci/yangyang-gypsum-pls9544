"""Compatibility alias for content-addressed fold reuse."""
from .revision import (  # noqa: F401
    compute_fold_artifact, fold_cache_key, fold_training_key,
    select_components_incremental as select_components_cached,
)
