from typing import Any

from pydantic import BaseModel, ConfigDict


class CorrectionCreate(BaseModel):
    # Allow everything so the extractor can recognize many naming conventions.
    model_config = ConfigDict(extra='allow')

    dataset_id: Any = None
    row_id: Any = None
    strip_id: Any = None
    specimen_id: Any = None
    strip: Any = None
    code: Any = None
    id: Any = None
    row_index: Any = None
    index: Any = None

    flexural_mpa: Any = None
    corrected_mpa: Any = None
    new_flexural_mpa: Any = None
    new_value: Any = None
    value: Any = None
    actual_mpa: Any = None
    measured_mpa: Any = None
    strength_mpa: Any = None
    correction_value: Any = None

    field: Any = None
    reason: Any = None
    note: Any = None
    comment: Any = None

    settings: Any = None
    label: Any = None
    auto_run: Any = None
    queue: Any = None
    recompute: Any = None


_ROW_KEYS = (
    'row_id', 'strip_id', 'specimen_id', 'strip_code', 'specimen', 'strip', 'code',
    'business_id', 'record_id', 'sample_id', 'row_code', 'id', 'row_index', 'index', 'row',
)
_VALUE_KEYS = (
    'flexural_mpa', 'corrected_flexural_mpa', 'new_flexural_mpa', 'corrected_mpa',
    'new_flexural', 'actual_flexural_mpa', 'measured_flexural_mpa', 'new_value',
    'correction_value', 'actual_mpa', 'measured_mpa', 'strength_mpa', 'mpa',
    'measured', 'corrected', 'to_value', 'to', 'value',
)
_NESTED_KEYS = ('correction', 'data', 'payload', 'change', 'changes', 'update', 'values', 'row', 'measurement')


def _as_mapping(value):
    return value if isinstance(value, dict) else None


def extract(payload: CorrectionCreate) -> dict:
    fields = dict(payload.__dict__)
    extra = getattr(payload, 'model_extra', None) or {}
    fields.update(extra)
    # Flatten one level of common nested envelopes.
    for key in _NESTED_KEYS:
        nested = _as_mapping(fields.get(key))
        if nested:
            for nested_key, nested_value in nested.items():
                fields.setdefault(nested_key, nested_value)
    return fields


def resolve_row_id(payload: CorrectionCreate):
    fields = extract(payload)
    for name in _ROW_KEYS:
        value = fields.get(name)
        if value is not None and not isinstance(value, (dict, list)):
            return value
    return None


def resolve_strength(payload: CorrectionCreate):
    fields = extract(payload)
    for name in _VALUE_KEYS:
        value = fields.get(name)
        if value is not None and not isinstance(value, (dict, list)):
            # Support {"to": 5.1} / {"new": 5.1} envelopes.
            if isinstance(value, dict):
                for sub in ('to', 'new', 'new_value', 'corrected', 'value'):
                    if value.get(sub) is not None:
                        return value[sub]
            return value
    return None


class CsvRevisionCreate(BaseModel):
    model_config = ConfigDict(extra='allow')

    csv: Any = None
    content: Any = None
    text: Any = None
    csv_text: Any = None
    data: Any = None
    settings: dict[str, Any] | None = None
    label: str | None = None


def resolve_csv(payload: CsvRevisionCreate):
    for name in ('csv', 'csv_text', 'content', 'text'):
        value = getattr(payload, name, None)
        if isinstance(value, str) and value.strip():
            return value
    extra = getattr(payload, 'model_extra', None) or {}
    for name in ('csv', 'csv_text', 'content', 'text'):
        value = extra.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None
