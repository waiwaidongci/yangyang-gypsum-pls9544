import hashlib
import json

from sqlalchemy import select

from src.domain.project import normalize_rows, SPEC
from src.models.dataset import Dataset
from src.services.audit import record
from src.utils.csv_files import read_rows


def overview(dataset):
    return {'id': dataset.id, 'name': dataset.name, 'checksum': dataset.checksum,
            'count': len(dataset.rows), 'version': int(getattr(dataset, 'version', 1) or 1)}


def list_datasets(session):
    return [overview(dataset) for dataset in session.scalars(select(Dataset).order_by(Dataset.id.desc()))]


def get_dataset(session, dataset_id):
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise LookupError('找不到来源台账')
    return dataset


def add_dataset(session, name, rows, owner):
    normalized = normalize_rows(rows)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    dataset = Dataset(name=name, rows=normalized, checksum=hashlib.sha256(encoded).hexdigest(), created_by=owner)
    session.add(dataset)
    session.flush()
    return dataset


def import_dataset(session, payload, owner):
    dataset = add_dataset(session, payload.name, read_rows(payload.csv, SPEC['columns']), owner)
    record(session, 'dataset.imported', dataset.id, owner)
    session.commit()
    return overview(dataset)


def correct_row(session, dataset_id, row_id, flexural_mpa, owner, **kwargs):
    """Compatibility facade: register a measurement correction via catalog."""
    from src.services.corrections import register_correction
    actor_id = owner if isinstance(owner, int) else getattr(owner, 'id', owner)
    correction, run = register_correction(
        session, dataset_id=dataset_id, row_id=row_id, flexural_mpa=flexural_mpa,
        actor_id=actor_id, **kwargs,
    )
    return correction, run


def revise_dataset(session, dataset_id, csv_text, owner, **kwargs):
    """Compatibility facade: register a corrected CSV edition."""
    from src.services.corrections import register_csv_revision
    actor_id = owner if isinstance(owner, int) else getattr(owner, 'id', owner)
    return register_csv_revision(
        session, dataset_id=dataset_id, csv_text=csv_text, actor_id=actor_id, **kwargs,
    )
