"""Compatibility alias: dataset edition/correction services."""
from .catalog import *  # noqa: F401,F403
from .corrections import *  # noqa: F401,F403
from .corrections import (  # noqa: F401
    correction_overview, latest_approved_run, list_corrections,
    register_correction, register_csv_revision,
)
