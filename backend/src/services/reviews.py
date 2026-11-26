from fastapi import HTTPException
from sqlalchemy import select

from src.constants.workflow import RunStatus
from src.models.dataset import Dataset
from src.models.review import Review
from src.models.run import Run
from src.services.audit import record
from src.utils.time import utc_stamp


def review_info(review):
    return {'run_id': review.run_id, 'decision': review.decision, 'note': review.note, 'created_at': review.created_at}


def submit(session, run_id, payload, actor):
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run:
        raise LookupError('找不到运行')
    dataset = session.get(Dataset, run.dataset_id)
    if dataset is not None and int(run.version or 1) < int(dataset.version or 1):
        raise HTTPException(409, '该运行属于已被更正取代的旧版本；请复核并批准最新一版。')
    if run.status != RunStatus.COMPLETED or session.get(Review, run_id):
        raise HTTPException(409, '仅可复核一次，且计算必须成功完成')
    review = Review(run_id=run_id, reviewer_id=actor.id, decision=payload.decision,
                    note=payload.note, created_at=utc_stamp())
    session.add(review)
    record(session, 'review.' + payload.decision, run_id, actor.id)
    session.commit()
    return review_info(review)
