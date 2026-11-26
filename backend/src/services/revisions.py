"""Compatibility alias for the corrections/versioning service."""
from .corrections import *  # noqa: F401,F403
from .corrections import (  # noqa: F401
    correction_overview, get_dataset_or_404, latest_approved_run,
    list_corrections, register_correction,
)

register_revision = register_correction
create_revision = register_correction
