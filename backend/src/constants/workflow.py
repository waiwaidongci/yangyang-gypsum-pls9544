from enum import StrEnum


class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'


class RunStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    # A late worker completion for an outdated dataset version.  Such runs
    # never carry an accepted result and can never be approved/downloaded.
    SUPERSEDED = 'superseded'
    # Compatibility aliases for the same fenced state.
    STALE = 'stale'
    DISCARDED = 'discarded'
    CANCELLED = 'cancelled'


class Decision(StrEnum):
    APPROVED = 'approved'
    REJECTED = 'rejected'
