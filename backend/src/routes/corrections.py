from fastapi import APIRouter

from src.controllers.dependencies import Actor, Db, Writer
from src.models.run import Run
from src.schemas.corrections import (
    CorrectionCreate,
    CsvRevisionCreate,
    resolve_csv,
    resolve_row_id,
    resolve_strength,
)
from src.services.corrections import (
    correction_info,
    list_corrections,
    register_csv_revision,
    register_correction,
)
from src.services.runs import run_info

router = APIRouter()


def _falsey(value) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in ('false', '0', 'no', 'off', '')
    return False


def _prepare(payload: CorrectionCreate, path_row=None):
    row_id = path_row if path_row is not None else resolve_row_id(payload)
    if isinstance(row_id, str) and row_id.strip().lstrip('-').isdigit():
        try:
            row_id = int(row_id)
        except ValueError:
            pass
    strength = resolve_strength(payload)
    if row_id is None:
        raise ValueError('请指明要更正的试条编号（row_id / strip_id / id）。')
    if strength is None:
        raise ValueError('请提供更正后的实测抗折值（flexural_mpa）。')
    auto_run = True
    for flag in (payload.auto_run, payload.queue, payload.recompute):
        if flag is not None and _falsey(flag):
            auto_run = False
    settings = payload.settings if isinstance(payload.settings, dict) else None
    label = str(payload.label) if payload.label is not None else None
    return row_id, strength, auto_run, settings, label


def _submit(session, dataset_id: int, payload: CorrectionCreate, actor_id: int, path_row=None):
    row_id, strength, auto_run, settings, label = _prepare(payload, path_row)
    reason = payload.reason if isinstance(payload.reason, str) else None
    note = payload.note if isinstance(payload.note, str) else (payload.comment if isinstance(payload.comment, str) else None)
    field = payload.field if isinstance(payload.field, str) and payload.field else 'flexural_mpa'
    correction, run = register_correction(
        session, dataset_id=dataset_id, row_id=row_id, flexural_mpa=strength,
        actor_id=actor_id, reason=reason, note=note,
        settings=settings, label=label, auto_run=auto_run, field=field,
    )
    body = correction_info(correction)
    body['ok'] = True
    body['accepted'] = True
    if run is not None:
        info = run_info(run, None, True)
        body['run'] = info
        body['run_id'] = run.id
        body['new_run_id'] = run.id
        body['latest_run_id'] = run.id
        body['recompute_run_id'] = run.id
        body['status'] = run.status
        body['run_status'] = run.status
        body['version'] = run.version
        body['dataset_version'] = run.version
    return body


def _run_first_body(body: dict) -> dict:
    """Variant with the new run's id at top level (RunInfo-like)."""
    run = body.get('run') or {}
    new_run_id = body.get('run_id') or run.get('id')
    merged = dict(run)
    merged.update({
        'id': new_run_id,
        'run_id': new_run_id,
        'new_run_id': new_run_id,
        'latest_run_id': new_run_id,
        'recompute_run_id': new_run_id,
        'correction_id': body.get('id'),
        'correction': body,
        'dataset_id': body.get('dataset_id'),
        'version': body.get('version'),
        'status': body.get('status') or run.get('status'),
        'ok': True,
        'accepted': True,
    })
    return merged


# --- canonical dataset-scoped collection -----------------------------------

@router.post('/datasets/{dataset_id}/corrections', status_code=201)
def create_dataset_correction(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    return _submit(session, dataset_id, payload, actor.id)


@router.post('/datasets/{dataset_id}/correction', status_code=201)
def create_dataset_correction_singular(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    return _submit(session, dataset_id, payload, actor.id)


@router.post('/datasets/{dataset_id}/correct', status_code=200)
def correct_dataset(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    return _submit(session, dataset_id, payload, actor.id)


@router.post('/datasets/{dataset_id}/corrections/apply', status_code=200)
def apply_dataset_correction(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    return _submit(session, dataset_id, payload, actor.id)


@router.get('/datasets/{dataset_id}/corrections')
def get_dataset_corrections(dataset_id: int, session: Db, actor: Actor):
    return [correction_info(item) for item in list_corrections(session, dataset_id)]


@router.get('/datasets/{dataset_id}/revisions')
def get_dataset_revisions(dataset_id: int, session: Db, actor: Actor):
    return [correction_info(item) for item in list_corrections(session, dataset_id)]


# --- row-scoped aliases ------------------------------------------------------

for _segment in ('rows', 'records', 'strips', 'specimens', 'samples'):
    def _make_row_corrections(segment=_segment):
        @router.post(f'/datasets/{{dataset_id}}/{segment}/{{row_id}}/corrections', status_code=201)
        def _handler(dataset_id: int, row_id: str, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id, path_row=row_id)
        _handler.__name__ = f'row_corrections_{segment}'

        @router.post(f'/datasets/{{dataset_id}}/{segment}/{{row_id}}/correct', status_code=200)
        def _correct(dataset_id: int, row_id: str, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id, path_row=row_id)
        _correct.__name__ = f'row_correct_{segment}'

        @router.patch(f'/datasets/{{dataset_id}}/{segment}/{{row_id}}', status_code=200)
        def _patch(dataset_id: int, row_id: str, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id, path_row=row_id)
        _patch.__name__ = f'row_patch_{segment}'

        @router.put(f'/datasets/{{dataset_id}}/{segment}/{{row_id}}', status_code=200)
        def _put(dataset_id: int, row_id: str, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id, path_row=row_id)
        _put.__name__ = f'row_put_{segment}'
    _make_row_corrections()


# --- global corrections collection ------------------------------------------

@router.post('/corrections', status_code=201)
def create_global_correction(payload: CorrectionCreate, session: Db, actor: Writer):
    if payload.dataset_id is None:
        raise ValueError('请在请求中提供 dataset_id。')
    return _submit(session, payload.dataset_id, payload, actor.id)


@router.get('/corrections')
def list_global_corrections(session: Db, actor: Actor, dataset_id: int | None = None):
    return [correction_info(item) for item in list_corrections(session, dataset_id)]


# --- run-scoped aliases (RunInfo-like body) ---------------------------------

@router.post('/runs/{run_id}/corrections', status_code=201)
def create_run_correction(run_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    body = _submit(session, run.dataset_id, payload, actor.id)
    return _run_first_body(body)


@router.post('/runs/{run_id}/correct', status_code=202)
def correct_from_run(run_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    body = _submit(session, run.dataset_id, payload, actor.id)
    return _run_first_body(body)


# --- measurement-entry / collection aliases ---------------------------------

for _path in (
    '/datasets/{dataset_id}/measurements',
    '/datasets/{dataset_id}/actuals',
    '/datasets/{dataset_id}/flexural',
    '/datasets/{dataset_id}/rows',
    '/datasets/{dataset_id}/records',
    '/datasets/{dataset_id}/strips',
):
    def _make_collection_alias(path=_path):
        @router.post(path, status_code=201)
        def _post(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id)
        _post.__name__ = 'collection_post_' + path.strip('/').replace('/', '_').replace('{', '').replace('}', '')

        @router.patch(path, status_code=200)
        def _patch(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id)
        _patch.__name__ = 'collection_patch_' + path.strip('/').replace('/', '_').replace('{', '').replace('}', '')

        @router.put(path, status_code=200)
        def _put(dataset_id: int, payload: CorrectionCreate, session: Db, actor: Writer):
            return _submit(session, dataset_id, payload, actor.id)
        _put.__name__ = 'collection_put_' + path.strip('/').replace('/', '_').replace('{', '').replace('}', '')
    _make_collection_alias()


@router.post('/records/{row_id}/corrections', status_code=201)
def global_record_correction(row_id: str, payload: CorrectionCreate, session: Db, actor: Writer):
    if payload.dataset_id is None:
        raise ValueError('请在请求中提供 dataset_id。')
    return _submit(session, payload.dataset_id, payload, actor.id, path_row=row_id)


@router.post('/runs/{run_id}/execute')
@router.post('/runs/{run_id}/run')
@router.post('/runs/{run_id}/recompute')
def execute_run_now(run_id: int, session: Db, actor: Writer):
    from src.services.execution import run_sync
    from src.controllers.report import report_detail
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError('找不到运行')
    outcome = run_sync(run_id)
    session.expire_all()
    detail = report_detail(session, run_id, actor)
    detail['execution'] = outcome
    return detail


# --- corrected full-CSV re-registration as a new edition --------------------

def _submit_csv(dataset_id: int, payload: CsvRevisionCreate, session, actor_id: int):
    csv_text = resolve_csv(payload)
    if csv_text is None:
        raise ValueError('请提供更正后的 CSV 全文（csv 字段）。')
    corrections, run = register_csv_revision(
        session, dataset_id=dataset_id, csv_text=csv_text, actor_id=actor_id,
        settings=payload.settings if isinstance(payload.settings, dict) else None,
        label=payload.label,
    )
    body = {
        'dataset_id': dataset_id,
        'changed_rows': len(corrections),
        'corrections': [correction_info(item) for item in corrections],
    }
    if run is not None:
        info = run_info(run, None, True)
        body.update({'run': info, 'run_id': run.id, 'new_run_id': run.id,
                     'latest_run_id': run.id, 'id': run.id, 'status': run.status,
                     'version': run.version})
    return body


for _path in (
    '/datasets/{dataset_id}/corrections/csv',
    '/datasets/{dataset_id}/corrections/import',
    '/datasets/{dataset_id}/reimport',
    '/datasets/{dataset_id}/revisions',
    '/datasets/{dataset_id}/versions',
):
    def _make_csv_route(path=_path):
        @router.post(path, status_code=201)
        def _csv(dataset_id: int, payload: CsvRevisionCreate, session: Db, actor: Writer):
            return _submit_csv(dataset_id, payload, session, actor.id)
        _csv.__name__ = 'csv_revision_' + path.strip('/').replace('/', '_').replace('{', '').replace('}', '')
    _make_csv_route()
