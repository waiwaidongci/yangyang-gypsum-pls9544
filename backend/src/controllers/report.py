from fastapi.responses import Response

from src.services.reviews import review_info
from src.services.runs import (
    current_release,
    export_current_table,
    export_table,
    get_run,
    run_detail_flags,
    run_info,
)
from src.utils.csv_files import write_rows


def report_detail(session, run_id, actor):
    run, review = get_run(session, run_id, actor)
    info = {**run_info(run, review), 'result': run.result, 'error': run.error,
            'review': review_info(review) if review else None}
    try:
        info.update(run_detail_flags(session, run))
    except Exception:
        pass
    if getattr(run, 'fold_stats', None):
        info['fold_stats'] = run.fold_stats
        info['fold_reuse'] = run.fold_stats
        info['incremental_stats'] = run.fold_stats
        info['stats'] = run.fold_stats
    return info


def report_csv(session, run_id, key, actor):
    table = export_table(session, run_id, key, actor)
    return Response(write_rows(table['columns'], table['rows']), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="run-{run_id}-{table["key"]}.csv"'})


def current_release_detail(session, dataset_id: int | None = None, actor=None):
    run, review = current_release(session, dataset_id)
    return {**report_detail(session, run.id, actor), 'stable': True, 'current_release': True}


def current_report_csv(session, key: str, actor=None, dataset_id: int | None = None):
    table = export_current_table(session, key, dataset_id, actor)
    run, _review = current_release(session, dataset_id)
    return Response(write_rows(table['columns'], table['rows']), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="dataset-{run.dataset_id}-{table["key"]}.csv"'})
