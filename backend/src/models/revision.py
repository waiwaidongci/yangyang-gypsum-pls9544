"""Compatibility aliases for correction/version persistence models."""
from .correction import Correction, FoldArtifact, FoldCheckpoint  # noqa: F401

Revision = Correction
DatasetRevision = Correction
FoldResult = FoldCheckpoint
FoldCache = FoldArtifact
