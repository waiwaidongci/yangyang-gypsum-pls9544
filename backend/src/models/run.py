from sqlalchemy import JSON, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class Run(Base):
    __tablename__ = 'analysis_runs'

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(140))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('accounts.id'))
    created_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    settings: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    # Dataset correction version this run was queued for; completion is fenced
    # against the dataset's current version so out-of-order workers cannot
    # overwrite the newest edition.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text('1'), index=True)
    fold_stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
