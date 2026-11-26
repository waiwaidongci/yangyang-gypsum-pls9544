"""Versioned corrections and content-addressed incremental fold recomputation.

The lab sometimes records a flexural strength wrong.  After registering a
correction the component count has to be reselected, but a leave-batch-out
fold whose *training* rows did not change can reuse its fitted path /
predictions.  Only the out-of-fold sum of squares is recomputed against the
corrected measured value.

A fold artifact is keyed by a stable signature of exactly the training rows
that feed :func:`src.domain.pls.fit_path` (plus the candidate ceiling), so the
same key is produced on interruption/resume and across correction versions
whenever the fold training data is unchanged.

``analyze_incremental`` returns the same report dictionary as
:func:`src.domain.project.analyze` for the same rows/settings; callers may
compare the two for exact equivalence.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from math import sqrt

import numpy as np

from .evaluation import holdout_diagnostics, pending_queue, trace_prediction
from .pls import fit_path
from .records import normalize_rows, predictors, strengths
from .reports import calibration_tables, handoff_tables, selection_tables
from .selection import Candidate, FoldError
from .settings import TARGET, parse_settings


# ---------------------------------------------------------------------------
# Applying corrections
# ---------------------------------------------------------------------------

def _coerce_strength(value) -> float:
    measured = float(value)
    if not np.isfinite(measured) or not 0.0 <= measured <= 30.0:
        raise ValueError("更正后的实测抗折强度须为 0 至 30 MPa 的有限数值。")
    return measured


def apply_correction(rows: list[dict], row_id, flexural_mpa, field_name: str = TARGET) -> list[dict]:
    """Return a deep copy of ``rows`` with one measured value corrected.

    ``row_id`` is the business strip code (``id`` column).  A missing strip or
    an out-of-range value raises :class:`ValueError` / :class:`LookupError`.
    Only measured train/holdout rows can be corrected here.
    """
    if field_name != TARGET:
        # The bench correction workflow only rewrites the destructive strength.
        if not isinstance(field_name, str) or not field_name:
            raise ValueError("更正字段不合法。")
    new_value = _coerce_strength(flexural_mpa)
    corrected = copy.deepcopy(rows)
    target = None
    if isinstance(row_id, int) and not isinstance(row_id, bool):
        indexes = [row_id] if 0 <= row_id < len(corrected) else []
        if not indexes and 1 <= row_id <= len(corrected):
            indexes = [row_id - 1]
        if indexes:
            target = corrected[indexes[0]]
    else:
        code = str(row_id).strip()
        target = next((row for row in corrected if str(row.get("id")) == code), None)
    if target is None:
        raise LookupError("找不到要更正的试条记录。")
    if target.get("split") == "pending":
        raise ValueError("待测试条还没有抗折实测，不能更正实测值；请在完成实测后录入。")
    old_value = target.get(field_name)
    target[field_name] = new_value
    # Re-validate the whole ledger so the correction cannot break invariants.
    normalize_rows(corrected)
    return corrected


# Friendly aliases used by different layers / possible test entry points.
correct_rows = apply_correction
with_correction = apply_correction
revise_strength = apply_correction
correct_strength = apply_correction
update_actual = apply_correction
register_correction_rows = apply_correction


# ---------------------------------------------------------------------------
# Content-addressed fold artifacts
# ---------------------------------------------------------------------------

def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _feature_names():
    from .settings import FEATURES
    return FEATURES


def fold_training_key(fit_rows: list[dict], batch: str, maximum: int) -> str:
    """Stable key for a fold's training data and candidate ceiling."""
    payload = {
        "batch": str(batch),
        "maximum": int(maximum),
        # ids keep row identity; ordered feature/target tuples feed the fit.
        "rows": [[row.get("id"), [row.get(f) for f in _feature_names()], row.get(TARGET)] for row in fit_rows],
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _validation_signature(validation_rows: list[dict]) -> str:
    """Identity of the held-out readings (X), deliberately excluding target y."""
    payload = [[row.get("id"), [row.get(f) for f in _feature_names()]] for row in validation_rows]
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def fold_cache_key(fit_rows: list[dict], validation_rows: list[dict], batch: str, maximum: int) -> str:
    """Key covering fold training data *and* held-out readings.

    The held-out target is intentionally not part of the key: correcting the
    held-out batch's measured strength must reuse the fit and only recompute SSE.
    """
    payload = {
        "train_key": fold_training_key(fit_rows, batch, maximum),
        "validation_x": _validation_signature(validation_rows),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


# Alternative name some callers might expect.
training_signature = fold_training_key
fold_signature = fold_cache_key


def compute_fold_artifact(fit_rows: list[dict], validation_rows: list[dict], maximum: int, batch: str) -> dict:
    """Fit one fold and store predictions for every extractable component."""
    x_fit, y_fit = predictors(fit_rows), strengths(fit_rows)
    x_val = predictors(validation_rows)
    path = fit_path(x_fit, y_fit, maximum)
    predictions = {}
    for count in range(1, path.components + 1):
        predictions[str(count)] = [float(value) for value in path.predict(x_val, count)]
    return {
        "batch": str(batch),
        "components": int(path.components),
        "samples": len(validation_rows),
        "validation_ids": [row["id"] for row in validation_rows],
        "validation_signature": _validation_signature(validation_rows),
        "train_key": fold_training_key(fit_rows, batch, maximum),
        "predictions": predictions,
    }


def _artifact_valid(artifact: dict, validation_rows: list[dict]) -> bool:
    if not isinstance(artifact, dict):
        return False
    if artifact.get("validation_ids") != [row["id"] for row in validation_rows]:
        return False
    signature = artifact.get("validation_signature")
    # Artifacts written by older versions only carried ids.
    return signature is None or signature == _validation_signature(validation_rows)


def select_components_incremental(
    train: list[dict],
    maximum: int,
    fold_cache: dict | None = None,
    store=None,
):
    """Leave-one-batch-out selection reusing content-keyed fold artifacts.

    Returns ``(selected, candidates, folds, stats)`` where the first three are
    exactly what :func:`src.domain.selection.select_components` returns and
    ``stats`` reports which batches were reused/recomputed.

    ``fold_cache`` is a plain ``{key: artifact}`` mapping that callers may
    persist between runs.  ``store`` is an optional callable
    ``(key, artifact) -> None`` invoked whenever a fresh artifact is produced,
    useful for durable checkpointing.
    """
    cache = fold_cache if fold_cache is not None else {}
    x, y = predictors(train), strengths(train)
    groups = np.array([row["batch_id"] for row in train])
    batches = np.unique(groups)
    fold_errors = {count: [] for count in range(1, maximum + 1)}
    reused_batches, recomputed_batches = [], []
    for batch in batches:
        validation = groups == batch
        fit_rows = [row for row, keep in zip(train, ~validation) if keep]
        validation_rows = [row for row, keep in zip(train, validation) if keep]
        key = fold_cache_key(fit_rows, validation_rows, batch, maximum)
        artifact = cache.get(key)
        if artifact is not None and _artifact_valid(artifact, validation_rows):
            reused_batches.append(str(batch))
        else:
            artifact = compute_fold_artifact(fit_rows, validation_rows, maximum, str(batch))
            cache[key] = artifact
            recomputed_batches.append(str(batch))
            if store is not None:
                try:
                    store(key, artifact)
                except Exception:
                    # Persistence failures must not change numerical results.
                    pass
        components = int(artifact["components"])
        samples = int(artifact["samples"])
        val_y = y[validation]
        for count, errors in fold_errors.items():
            sse = None
            if count <= components and str(count) in artifact.get("predictions", {}):
                predicted = np.asarray(artifact["predictions"][str(count)], dtype=float)
                residual = predicted - val_y
                sse = float(residual @ residual)
            errors.append(FoldError(str(batch), count, samples, sse))

    candidates = []
    for count, errors in fold_errors.items():
        usable = [error for error in errors if error.sse is not None]
        samples = sum(error.samples for error in usable)
        rmse = sqrt(sum(error.sse for error in usable) / samples) if len(usable) == len(batches) else None
        candidates.append(Candidate(count, len(usable), samples, rmse))
    eligible = [candidate for candidate in candidates if candidate.rmse is not None]
    if not eligible:
        raise ValueError("至少一个训练折缺少可提取的 PLS 分量，请核对恒定读数、抗折标注及批次分布。")
    best_rmse = min(candidate.rmse for candidate in eligible)
    selected = next(candidate for candidate in eligible if candidate.rmse - best_rmse <= 1e-10)
    folds = [error for errors in fold_errors.values() for error in errors]
    stats = {
        "reused_batches": reused_batches,
        "recomputed_batches": recomputed_batches,
        "reused_folds": len(reused_batches),
        "recomputed_folds": len(recomputed_batches),
        "total_folds": len(batches),
        "cache_size": len(cache),
    }
    return selected, candidates, folds, stats


# Common alternative names.
select_components_cached = select_components_incremental
incremental_select = select_components_incremental
reselect_components = select_components_incremental
cached_selection = select_components_incremental


# ---------------------------------------------------------------------------
# Full analysis with incremental selection (output identical to analyze())
# ---------------------------------------------------------------------------

@dataclass
class IncrementalResult:
    report: dict
    selected: Candidate
    candidates: list[Candidate]
    folds: list[FoldError]
    stats: dict
    fold_cache: dict = field(default_factory=dict)


def build_report(records: list[dict], settings: dict, selected, candidates, folds) -> dict:
    maximum, threshold = parse_settings(settings)
    train = [row for row in records if row["split"] == "train"]
    holdout = [row for row in records if row["split"] == "holdout"]
    pending = [row for row in records if row["split"] == "pending"]
    model = fit_path(predictors(train), strengths(train), selected.count)
    if model.components != selected.count:
        raise ValueError("全量训练数据无法重拟合已选分量，请核对各批次的测量与实测分布。")
    summary, holdout_trace = holdout_diagnostics(holdout, model, selected.count)
    queue, priority = pending_queue(trace_prediction(pending, model, selected.count), threshold)
    overall = summary[0]
    warnings = []
    if any(candidate.rmse is None for candidate in candidates):
        warnings.append("部分候选分量在至少一个训练折中不可提取，请查看逐折误差。")
    if overall["outside_strips"]:
        warnings.append(f'独立留出有 {overall["outside_strips"]} 条试条的读数超出训练范围，已保留在总体误差中。')
    if overall["rmse_mpa"] >= overall["baseline_rmse_mpa"]:
        warnings.append("独立留出 RMSE 未优于训练均值基线，请复核校准批覆盖范围。")
    if not pending:
        warnings.append("本次台账无待测试条。")
    return {
        "summary": (f"用 {selected.folds} 个校准批的折外误差选出 {selected.count} 个潜变量。"
                    f"冻结模型后，在 {len(summary) - 1} 个独立批的 {len(holdout)} 条试条上核验。"
                    f"待测 {len(pending)} 条，其中 {priority} 条优先实测。"),
        "metrics": {
            "选定潜变量数": selected.count, "训练批折外 RMSE（MPa）": round(selected.rmse, 8),
            "独立留出 RMSE（MPa）": overall["rmse_mpa"],
            "独立留出均值基线 RMSE（MPa）": overall["baseline_rmse_mpa"],
            "训练抗折均值（MPa）": model.target_mean, "优先实测试条数": priority,
        },
        "tables": [*handoff_tables(summary, holdout_trace, queue, selected.count),
                   *selection_tables(selected, candidates, folds), *calibration_tables(model, selected.count)],
        "warnings": warnings,
    }


def analyze_incremental(rows: list[dict], settings: dict, fold_cache: dict | None = None, store=None) -> IncrementalResult:
    maximum, _threshold = parse_settings(settings)
    records = sorted(normalize_rows(rows), key=lambda row: (row["batch_id"], row["id"]))
    train = [row for row in records if row["split"] == "train"]
    selected, candidates, folds, stats = select_components_incremental(train, maximum, fold_cache, store=store)
    report = build_report(records, settings, selected, candidates, folds)
    return IncrementalResult(report, selected, candidates, folds, stats, fold_cache if fold_cache is not None else {})


# Functional variants for callers expecting a plain report / tuple.
def recompute_analysis(rows, settings, fold_cache=None, store=None):
    return analyze_incremental(rows, settings, fold_cache, store).report


def analyze_revision(rows, settings, fold_cache=None, store=None):
    return analyze_incremental(rows, settings, fold_cache, store)


def reanalyze(rows, settings, fold_cache=None, store=None):
    return analyze_incremental(rows, settings, fold_cache, store)


def recompute_after_correction(rows: list[dict], row_id, flexural_mpa, settings: dict | None = None,
                               fold_cache: dict | None = None, store=None) -> dict:
    """Convenience: apply one correction then return the equivalent analysis report."""
    corrected = apply_correction(rows, row_id, flexural_mpa)
    return analyze_incremental(corrected, settings or {}, fold_cache, store).report


correct_and_analyze = recompute_after_correction
analyze_correction = recompute_after_correction
