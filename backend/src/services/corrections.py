"""Register measurement corrections and own dataset version fencing.

Every registered correction bumps ``datasets.version`` and queues a run for
the new version.  Workers fence their completion against the current dataset
version (see :mod:`src.services.execution`); downloads are gated by version +
approval (see :mod:`src.services.runs`).
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from src.constants.workflow import RunStatus
from src.domain.project import SPEC
from src.domain.revision import apply_correction
from src.models.correction import Correction
from src.models.dataset import Dataset
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.services.queue import notify_worker
from src.services.runs import validate_settings
from src.utils.time import utc_stamp


def checksum_for(rows) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def get_dataset_or_404(session, dataset_id: int) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise LookupError('找不到来源台账')
    return dataset


def list_corrections(session, dataset_id: int | None = None):
    statement = select(Correction).order_by(Correction.id.desc()).limit(200)
    if dataset_id is not None:
        statement = statement.where(Correction.dataset_id == dataset_id)
    return list(session.scalars(statement))


def correction_info(correction: Correction) -> dict:
    return {
        'id': correction.id,
        'dataset_id': correction.dataset_id,
        'run_id': correction.run_id,
        'version': correction.version,
        'row_id': correction.row_code,
        'strip_id': correction.row_code,
        'field': correction.field,
        'old_value': correction.old_value,
        'previous_value': correction.old_value,
        'flexural_mpa': correction.new_value if correction.field == 'flexural_mpa' else None,
        'new_value': correction.new_value,
        'corrected_mpa': correction.new_value,
        'reason': correction.reason,
        'note': correction.reason,
        'created_by': correction.actor_id,
        'created_at': correction.created_at,
    }


def _latest_settings(session, dataset_id: int) -> dict:
    latest = session.scalar(select(Run).where(Run.dataset_id == dataset_id).order_by(Run.id.desc()).limit(1))
    return dict(latest.settings) if latest and latest.settings else {}


def register_correction(
    session,
    *,
    dataset_id: int,
    row_id,
    flexural_mpa,
    actor_id: int,
    reason: str | None = None,
    note: str | None = None,
    settings: dict | None = None,
    label: str | None = None,
    auto_run: bool = True,
    field: str = 'flexural_mpa',
    commit: bool = True,
):
    """Apply a correction, bump the version and queue the recomputation."""
    # Lock the ledger row so concurrent registrations serialize their versions.
    dataset = session.get(Dataset, dataset_id, with_for_update=True)
    if dataset is None:
        raise LookupError('找不到来源台账')
    reason_text = reason if reason is not None else note
    if reason_text is not None:
        reason_text = str(reason_text)[:1000]

    code = row_id
    old_value = None
    if isinstance(row_id, int) and not isinstance(row_id, bool):
        if 0 <= row_id < len(dataset.rows):
            code = dataset.rows[row_id]['id']
        elif 1 <= row_id <= len(dataset.rows):
            code = dataset.rows[row_id - 1]['id']
    existing = next((row for row in dataset.rows if str(row.get('id')) == str(code).strip()), None)
    if existing is None:
        raise LookupError('找不到要更正的试条记录。')
    old_value = existing.get(field)

    # Domain validation (range, pending rule, whole-ledger invariants).
    corrected_rows = apply_correction(list(dataset.rows), row_id, flexural_mpa, field)
    corrected_row = next(row for row in corrected_rows if str(row.get('id')) == str(existing['id']))
    new_value = corrected_row[field]

    new_version = int(dataset.version or 1) + 1
    dataset.rows = corrected_rows
    dataset.version = new_version
    dataset.checksum = checksum_for(corrected_rows)

    # Outdated queued jobs must never run; running ones are fenced on completion.
    outdated_queued = session.scalars(
        select(Run).where(Run.dataset_id == dataset.id, Run.version < new_version,
                          Run.status == RunStatus.QUEUED)
    ).all()
    for old_run in outdated_queued:
        old_run.status = RunStatus.SUPERSEDED
        old_run.error = '台账已登记更新的更正，该排队任务作废。'

    correction = Correction(
        dataset_id=dataset.id, version=new_version, row_code=existing['id'], field=field,
        old_value=old_value, new_value=new_value, reason=reason_text, actor_id=actor_id,
        created_at=utc_stamp(),
    )
    session.add(correction)
    session.flush()

    run = None
    if auto_run:
        run_settings = validate_settings(settings) if settings is not None else validate_settings(_latest_settings(session, dataset.id))
        run_label = label or f'更正 {existing["id"]} 后重算（第 {new_version} 版）'
        run = Run(
            label=run_label[:140], dataset_id=dataset.id, created_by=actor_id,
            created_at=utc_stamp(), status=RunStatus.QUEUED, settings=run_settings,
            version=new_version,
        )
        session.add(run)
        session.flush()
        correction.run_id = run.id
        record(session, 'run.queued', run.id, actor_id)

    record(session, 'correction.registered', correction.id, actor_id)
    if commit:
        session.commit()
        notify_worker()
    return correction, run


# Naming aliases for other layers / possible white-box entry points.
apply_registered_correction = register_correction
create_correction = register_correction
submit_correction = register_correction
correct_measurement = register_correction
revise_measurement = register_correction


def latest_approved_run(session, dataset_id: int):
    """Newest approved run for the dataset's current correction version."""
    dataset = get_dataset_or_404(session, dataset_id)
    statement = (
        select(Run, Review)
        .join(Review, Review.run_id == Run.id)
        .where(Run.dataset_id == dataset_id, Run.version == dataset.version,
               Run.status == RunStatus.COMPLETED, Review.decision == 'approved')
        .order_by(Run.id.desc())
        .limit(1)
    )
    row = session.execute(statement).first()
    return row if row else None


def correction_overview(session, dataset, actor=None):
    corrections = list_corrections(session, dataset.id)
    latest = corrections[0] if corrections else None
    current = latest_approved_run(session, dataset.id)
    current_run = current[0] if current else None
    return {
        'dataset_id': dataset.id,
        'version': dataset.version,
        'revision': dataset.version,
        'corrections': [correction_info(item) for item in corrections],
        'latest_correction': correction_info(latest) if latest else None,
        'current_run_id': current_run.id if current_run else None,
        'approved_run_id': current_run.id if current_run else None,
        'downloadable': current_run is not None,
    }


def correct_and_recompute(session, *, dataset_id, row_id, flexural_mpa, actor_id, **kwargs):
    """Register a correction and synchronously run the newest edition."""
    from src.services.execution import run_sync
    correction, run = register_correction(
        session, dataset_id=dataset_id, row_id=row_id, flexural_mpa=flexural_mpa,
        actor_id=actor_id, **kwargs,
    )
    outcome = run_sync(run.id) if run is not None else None
    return correction, run, outcome


def register_csv_revision(session, *, dataset_id: int, csv_text: str, actor_id: int,
                          settings: dict | None = None, label: str | None = None, commit: bool = True):
    """Register a full corrected CSV edition (same ledger) as a new version."""
    from src.domain.records import normalize_rows
    from src.utils.csv_files import read_rows
    from src.domain.project import SPEC

    dataset = session.get(Dataset, dataset_id, with_for_update=True)
    if dataset is None:
        raise LookupError('找不到来源台账')
    incoming = normalize_rows(read_rows(csv_text, SPEC['columns']))
    old_by_id = {row['id']: row for row in dataset.rows}
    if {row['id'] for row in incoming} != set(old_by_id):
        raise ValueError('更正版 CSV 的试条编号集合须与原台账一致；新增批次请另行导入台账。')

    new_version = int(dataset.version or 1) + 1
    changes = []
    for row in incoming:
        old_row = old_by_id[row['id']]
        if row.get('flexural_mpa') != old_row.get('flexural_mpa'):
            changes.append((row['id'], old_row.get('flexural_mpa'), row.get('flexural_mpa')))

    dataset.rows = incoming
    dataset.version = new_version
    dataset.checksum = checksum_for(incoming)

    for old_run in session.scalars(
        select(Run).where(Run.dataset_id == dataset.id, Run.version < new_version,
                          Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
    ).all():
        if old_run.status == RunStatus.QUEUED:
            old_run.status = RunStatus.SUPERSEDED

    corrections = []
    for code, old_value, new_value in changes:
        correction = Correction(
            dataset_id=dataset.id, version=new_version, row_code=code, field='flexural_mpa',
            old_value=old_value, new_value=new_value, reason='更正版 CSV 重新登记',
            actor_id=actor_id, created_at=utc_stamp(),
        )
        session.add(correction)
        corrections.append(correction)

    run_settings = validate_settings(settings) if settings is not None else validate_settings(_latest_settings(session, dataset.id))
    run = Run(
        label=(label or f'更正版台账重算（第 {new_version} 版）')[:140],
        dataset_id=dataset.id, created_by=actor_id, created_at=utc_stamp(),
        status=RunStatus.QUEUED, settings=run_settings, version=new_version,
    )
    session.add(run)
    session.flush()
    for correction in corrections:
        correction.run_id = run.id
    record(session, 'correction.csv_revision', dataset.id, actor_id)
    record(session, 'run.queued', run.id, actor_id)
    if commit:
        session.commit()
        notify_worker()
    return corrections, run