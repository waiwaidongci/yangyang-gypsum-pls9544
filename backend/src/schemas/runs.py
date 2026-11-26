from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.constants.workflow import RunStatus


class RunCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    label: str = Field(min_length=2, max_length=140)
    dataset_id: int = Field(gt=0)
    settings: dict[str, Any] = Field(default_factory=dict, max_length=20)


class RunInfo(BaseModel):
    model_config = ConfigDict(extra='ignore')

    id: int
    label: str
    dataset_id: int
    created_at: str
    status: str
    settings: dict
    decision: str | None
    version: int = 1
    current: bool | None = None
    is_current: bool | None = None
    latest: bool | None = None
    is_latest: bool | None = None
    active: bool | None = None
    superseded: bool | None = None
    stale: bool | None = None
