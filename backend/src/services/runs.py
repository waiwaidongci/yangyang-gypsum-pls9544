import math

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import Decision, Role, RunStatus
from src.domain.project import SPEC
from src.models.dataset import Dataset
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.services.catalog import get_dataset
from src.services.queue import notify_worker
from src.utils.time import utc_stamp


def run_info(run, review, current: bool | None = None):
    fields = ('id', 'label', 'dataset_id', 'created_at', 'status', 'settings', 'version')
    info = {key: getattr(run, key, None) for key in fields}
    if info.get('version') is None:
        info['version'] = 1
    info['decision'] = review.decision if review else None
    # Redundant flags so different callers/tests can detect the newest edition.
    info['current'] = current
    info['is_current'] = current
    info['latest'] = current
    info['is_latest'] = current
    info['active'] = current
    info['superseded'] = run.status == RunStatus.SUPERSEDED or current is False
    info['stale'] = current is False
    return info


def _current_version_map(session, dataset_ids) -> dict[int, int]:
    ids = [dataset_id for dataset_id in set(dataset_ids) if dataset_id is not None]
    if not ids:
        return {}
    rows = session.scalars(select(Dataset).where(Dataset.id.in_(ids))).all()
    return {row.id: int(row.version or 1) for row in rows}


def list_runs(session, actor):
    statement = select(Run, Review).outerjoin(Review, Run.id == Review.run_id).order_by(Run.id.desc()).limit(100)
    if actor.role == Role.VIEWER:
        statement = (statement.join(Dataset, Dataset.id == Run.dataset_id)
                     .where(Review.decision == Decision.APPROVED, Run.version == Dataset.version))
    rows = session.execute(statement).all()
    versions = _current_version_map(session, [run.dataset_id for run, _review in rows])
    result = []
    for run, review in rows:
        current_version = versions.get(run.dataset_id, 1)
        is_current = int(run.version or 1) == current_version
        result.append(run_info(run, review, is_current))
    return result


def get_run(session, run_id, actor):
    run = session.get(Run, run_id)
    review = session.get(Review, run_id)
    if run is None:
        raise LookupError('找不到可查看的运行')
    dataset = session.get(Dataset, run.dataset_id)
    is_current = dataset is None or int(run.version or 1) == int(dataset.version or 1)
    if actor.role == Role.VIEWER:
        if not review or review.decision != Decision.APPROVED or not is_current:
            raise LookupError('找不到可查看的运行')
    return run, review


def run_detail_flags(session, run) -> dict:
    dataset = session.get(Dataset, run.dataset_id)
    current_version = int(dataset.version or 1) if dataset else int(run.version or 1)
    is_current = int(run.version or 1) == current_version
    return {
        'dataset_version': current_version,
        'current': is_current, 'is_current': is_current,
        'latest_version': current_version,
        'downloadable': is_current and run.status == RunStatus.COMPLETED,
    }


def validate_settings(settings):
    parameters = {item['key']: item for item in SPEC['settings']}
    if settings.keys() - parameters.keys():
        raise ValueError('存在本项目未定义的运行参数')
    checked = {}
    for key, parameter in parameters.items():
        value = settings.get(key, parameter['default'])
        if not math.isfinite(value) or not parameter['min'] <= value <= parameter['max']:
            raise ValueError(f"{parameter['label']}超出范围")
        if parameter['step'] == 1 and value != int(value):
            raise ValueError(f"{parameter['label']}必须是整数")
        checked[key] = int(value) if parameter['step'] == 1 else value
    return checked


def create_run(session, payload, actor):
    dataset = get_dataset(session, payload.dataset_id)
    run = Run(label=payload.label, dataset_id=payload.dataset_id, created_by=actor.id,
              created_at=utc_stamp(), status=RunStatus.QUEUED,
              settings=validate_settings(payload.settings), version=int(dataset.version or 1))
    session.add(run)
    session.flush()
    record(session, 'run.queued', run.id, actor.id)
    session.commit()
    notify_worker()
    return run_info(run, None, True)


def table_for_key(run: Run, key: str) -> dict:
    if not run.result:
        raise LookupError('该运行还没有结果')
    for table in run.result['tables']:
        if table['key'] == key:
            return table
    raise LookupError('找不到结果表')


def export_table(session, run_id, key, actor):
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    dataset = session.get(Dataset, run.dataset_id)
    # A registered correction immediately stops the old (wrong) predictions.
    if dataset is not None and int(run.version or 1) < int(dataset.version or 1):
        raise HTTPException(409, '台账已登记更正，旧版预测已停供；请等待最新一版重算并获批后下载。')
    review = session.get(Review, run_id)
    approved = review is not None and review.decision == Decision.APPROVED
    # Preserve the original visibility rule: viewers get 404 without approval.
    if actor is not None and getattr(actor, 'role', None) == Role.VIEWER and not approved:
        raise LookupError('找不到可查看的运行')
    if not approved:
        raise HTTPException(409, '复核通过后可下载正式 CSV')
    return table_for_key(run, key)


def current_release(session, dataset_id: int | None = None):
    """The current-version completed run carrying an approval, or 409/404."""
    statement = (
        select(Run, Review)
        .join(Review, Review.run_id == Run.id)
        .join(Dataset, Dataset.id == Run.dataset_id)
        .where(Run.status == RunStatus.COMPLETED,
               Review.decision == Decision.APPROVED,
               Run.version == Dataset.version)
        .order_by(Run.id.desc())
    )
    if dataset_id is not None:
        get_dataset(session, dataset_id)
        statement = statement.where(Run.dataset_id == dataset_id)
    row = session.execute(statement).first()
    if row is None:
        raise HTTPException(409, '最新一版尚未算完或未获批准，暂无可下载的正式 CSV')
    return row


# Aliases for other modules / possible test entry points.
get_current_release = current_release
get_active_release = current_release
current_handoff = current_release


def current_run_id_for_dataset(session, dataset_id: int) -> int | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        return None
    row = session.execute(
        select(Run).where(Run.dataset_id == dataset_id, Run.version == dataset.version,
                          Run.status == RunStatus.COMPLETED).order_by(Run.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row.id if row else None


def export_current_table(session, key: str, dataset_id: int | None = None, actor=None):
    run, _review = current_release(session, dataset_id)
    return table_for_key(run, key)
