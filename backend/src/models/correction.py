from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.utils.time import utc_stamp


class Correction(Base):
    """A registered correction to a recorded measurement (usually flexural).

    Registering a correction bumps the owning dataset's ``version`` and queues
    a fresh run for that version.  Old/new values are retained for audit.
    """

    __tablename__ = 'corrections'

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey('analysis_runs.id'), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text('1'), index=True)
    row_code: Mapped[str] = mapped_column(String(64), index=True)
    field: Mapped[str] = mapped_column(String(48), default='flexural_mpa', server_default=text("'flexural_mpa'"))
    old_value: Mapped[float | None] = mapped_column(nullable=True)
    new_value: Mapped[float | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('accounts.id'), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_stamp)


class FoldCheckpoint(Base):
    """One leave-batch-out fold durably completed for a specific run/version."""

    __tablename__ = 'fold_checkpoints'
    __table_args__ = (UniqueConstraint('run_id', 'batch', name='uq_fold_checkpoint_run_batch'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), index=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text('1'), index=True)
    batch: Mapped[str] = mapped_column(String(64), index=True)
    fold_key: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_stamp)


class FoldArtifact(Base):
    """Content-addressed fold fit/predictions reusable across versions/runs."""

    __tablename__ = 'fold_artifacts'

    fold_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(32), default=utc_stamp)
