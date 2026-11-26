import logging

from sqlalchemy import select

from src.config import database
from src.constants.workflow import RunStatus
from src.domain.project import analyze
from src.domain.revision import analyze_incremental
from src.models.correction import FoldArtifact, FoldCheckpoint
from src.models.dataset import Dataset
from src.models.run import Run
from src.services.audit import record

LOGGER = logging.getLogger(__name__)

_FENCED_STATUSES = {RunStatus.SUPERSEDED, RunStatus.STALE, RunStatus.DISCARDED, RunStatus.CANCELLED}


def recover_jobs():
    """Reset interrupted runs.

    Current-version runs go back to the queue and resume from durable fold
    checkpoints; runs for an outdated correction version are fenced off.
    """
    with database.Session.begin() as session:
        running = session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)).all()
        for run in running:
            dataset = session.get(Dataset, run.dataset_id)
            if dataset is not None and int(run.version or 1) == int(dataset.version or 1):
                run.status = RunStatus.QUEUED
            else:
                run.status = RunStatus.SUPERSEDED
                run.error = 'worker 中断，且该运行对应的更正版本已被更新版本取代。'
        # Queued jobs for an outdated correction version can never be current.
        queued = session.scalars(select(Run).where(Run.status == RunStatus.QUEUED)).all()
        for run in queued:
            dataset = session.get(Dataset, run.dataset_id)
            if dataset is not None and int(run.version or 1) < int(dataset.version or 1):
                run.status = RunStatus.SUPERSEDED
                run.error = '台账已登记更新的更正，该排队任务作废。'


def claim():
    with database.Session.begin() as session:
        run = session.scalar(
            select(Run)
            .join(Dataset, Dataset.id == Run.dataset_id)
            .where(Run.status == RunStatus.QUEUED, Run.version == Dataset.version)
            .order_by(Run.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        # Defensive: never claim an explicitly fenced run.
        if run.status in _FENCED_STATUSES:
            return None
        run.status = RunStatus.RUNNING
        dataset = session.get(Dataset, run.dataset_id)
        record(session, 'run.started', run.id)
        rows = [dict(row) for row in dataset.rows]
        return run.id, rows, run.settings


def _load_fold_cache(session) -> dict:
    return {item.fold_key: dict(item.payload) for item in session.scalars(select(FoldArtifact)).all()}


def _persist_fold(run_id: int, dataset_id: int, version: int):
    def store(fold_key: str, artifact: dict):
        # One short transaction per completed fold gives crash-safe resume.
        with database.Session.begin() as session:
            existing = session.get(FoldArtifact, fold_key)
            if existing is None:
                session.add(FoldArtifact(fold_key=fold_key, batch=artifact.get('batch', ''), payload=artifact))
            checkpoint = session.scalar(
                select(FoldCheckpoint).where(FoldCheckpoint.run_id == run_id,
                                              FoldCheckpoint.batch == artifact.get('batch', ''))
            )
            if checkpoint is None:
                session.add(FoldCheckpoint(
                    run_id=run_id, dataset_id=dataset_id, version=version,
                    batch=artifact.get('batch', ''), fold_key=fold_key, payload=artifact,
                ))
            else:
                checkpoint.fold_key = fold_key
                checkpoint.payload = artifact
    return store


def _parse_job(job):
    if isinstance(job, dict):
        return job['run_id'], job.get('rows'), job.get('settings') or {}, job.get('fold_cache')
    run_id, rows, settings = job[0], job[1], job[2]
    return run_id, rows, settings, (job[3] if len(job) > 3 else None)


def execute(job):
    run_id, rows, settings, external_cache = _parse_job(job)
    result = None
    error_message = None
    stats = None
    try:
        with database.Session.begin() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise LookupError('找不到运行')
            dataset = session.get(Dataset, run.dataset_id)
            version = int(run.version or 1)
            dataset_id = run.dataset_id
            if rows is None:
                rows = [dict(row) for row in dataset.rows]
            if not settings:
                settings = dict(run.settings or {})
            fold_cache = external_cache if external_cache is not None else _load_fold_cache(session)

        incremental = analyze_incremental(
            rows, settings or {}, fold_cache=fold_cache,
            store=None if external_cache is not None else _persist_fold(run_id, dataset_id, version),
        )
        result = incremental.report
        stats = incremental.stats
        import json
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except ValueError as error:
        error_message = str(error)
    except Exception:
        LOGGER.exception('计算运行 %s 失败', run_id)
        error_message = '计算遇到内部错误，请检查台账后重试。'

    with database.Session.begin() as session:
        run = session.get(Run, run_id)
        if run is None:
            return {'accepted': False, 'status': 'missing', 'run_id': run_id}
        # Lock the ledger edition so the version check cannot race a correction.
        dataset = session.get(Dataset, run.dataset_id, with_for_update=True)
        current_version = int(dataset.version or 1) if dataset else int(run.version or 1)
        stale = int(run.version or 1) < current_version or run.status in _FENCED_STATUSES

        if stale:
            # Out-of-order completion: only the newest version is accepted.
            run.status = RunStatus.SUPERSEDED
            run.result = None
            run.error = '该运行对应旧版本，已有更新的更正版本；完工结果不予采纳。'
            if stats is not None:
                run.fold_stats = {**stats, 'fenced': True, 'current_version': current_version}
            record(session, 'run.superseded', run.id)
            return {'accepted': False, 'status': RunStatus.SUPERSEDED, 'run_id': run_id,
                    'current_version': current_version}

        if error_message is not None:
            run.status = RunStatus.FAILED
            run.error = error_message
            record(session, 'run.failed', run.id)
            return {'accepted': False, 'status': RunStatus.FAILED, 'run_id': run_id, 'error': error_message}

        run.status = RunStatus.COMPLETED
        run.result = result
        run.error = None
        if stats is not None:
            run.fold_stats = stats
        record(session, 'run.' + run.status, run.id)
        return {'accepted': True, 'status': RunStatus.COMPLETED, 'run_id': run_id, 'fold_stats': stats}


def process_next():
    """Claim one queued current-version run and execute it (test/inline helper)."""
    job = claim()
    if job is None:
        return None
    return execute(job)


def run_sync(run_id: int):
    """Execute a run by id using its locked settings and authoritative rows."""
    return execute((run_id, None, None))


# Naming aliases for other layers / white-box entry points.
execute_run = run_sync
run_now = run_sync
process_one = process_next
