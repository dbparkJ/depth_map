from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from .diagnostics import (
    DebugProjectionContext,
    prepare_debug_projection_context,
    write_debug_stage_diagnostics,
)
from .exporters import write_browser_points_arrays, write_ply
from .geodesy import LocalENU
from .postprocess import PostprocessStage, RemovalReason
from .postprocess_io import atomic_json_dump, validate_browser_binary


_MASK_SCAN_CHUNK_POINTS = 1_000_000
_STAGE_REASON_BITS: Mapping[str, tuple[RemovalReason, ...]] = {
    "raw": (),
    "non_finite": (
        RemovalReason.NON_FINITE,
        RemovalReason.OUTSIDE_VALID_BOUNDS,
    ),
    "high_position_spread": (RemovalReason.HIGH_POSITION_SPREAD,),
    "radius_outlier": (RemovalReason.RADIUS_OUTLIER,),
    "statistical_outlier": (RemovalReason.STATISTICAL_OUTLIER,),
    "local_surface": (RemovalReason.BELOW_LOCAL_SURFACE,),
    "low_support_bright_combined": (
        RemovalReason.LOW_MULTI_FRAME_SUPPORT,
        RemovalReason.BRIGHT_LOW_SUPPORT,
    ),
}
_EXPECTED_STAGE_ORDER = tuple(_STAGE_REASON_BITS)
_SAMPLE_SELECTION = "deterministic_rank_over_raw_voxel_order"


@dataclass(frozen=True)
class _StageMasks:
    index: int
    stage: PostprocessStage
    before: np.ndarray
    after: np.ndarray
    removed_delta: np.ndarray
    reason_bits: int
    reason_names: tuple[str, ...]


def _validated_arrays(
    raw_points: np.ndarray,
    raw_colors: np.ndarray,
    removal_reason_bits: np.ndarray,
    keep_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    points = np.asarray(raw_points)
    colors = np.asarray(raw_colors)
    reasons = np.asarray(removal_reason_bits, dtype=np.uint16)
    keep = np.asarray(keep_mask, dtype=bool)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("raw_points must have shape (N, 3)")
    if colors.shape != points.shape:
        raise ValueError("raw_colors must have shape (N, 3)")
    if reasons.shape != (len(points),):
        raise ValueError("removal_reason_bits must have shape (N,)")
    if keep.shape != (len(points),):
        raise ValueError("keep_mask must have shape (N,)")
    if not np.array_equal(keep, reasons == 0):
        raise RuntimeError("keep_mask must equal removal_reason_bits == 0")
    return points, colors.astype(np.uint8, copy=False), reasons, keep


def _reason_code(reasons: Sequence[RemovalReason]) -> int:
    result = 0
    for reason in reasons:
        result |= int(reason)
    return result


def _iter_stage_masks(
    reason_bits: np.ndarray,
    stages: Sequence[PostprocessStage],
) -> Iterator[_StageMasks]:
    actual_order = tuple(stage.stage for stage in stages)
    if actual_order != _EXPECTED_STAGE_ORDER:
        raise RuntimeError(
            "debug-stage reconstruction requires the documented stage order; "
            f"expected {_EXPECTED_STAGE_ORDER}, got {actual_order}"
        )
    previous = np.ones(len(reason_bits), dtype=bool)
    for index, stage in enumerate(stages):
        reasons = _STAGE_REASON_BITS[stage.stage]
        code = _reason_code(reasons)
        if code:
            removed_delta = previous & ((reason_bits & code) != 0)
            after = previous.copy()
            after[removed_delta] = False
        else:
            removed_delta = np.zeros(len(reason_bits), dtype=bool)
            after = previous.copy()
        input_count = int(np.count_nonzero(previous))
        output_count = int(np.count_nonzero(after))
        removed_count = int(np.count_nonzero(removed_delta))
        expected = (stage.input_count, stage.output_count, stage.removed_count)
        actual = (input_count, output_count, removed_count)
        if actual != expected:
            raise RuntimeError(
                f"debug-stage accounting mismatch for {stage.stage}: "
                f"expected {expected}, reconstructed {actual}"
            )
        yield _StageMasks(
            index=index,
            stage=stage,
            before=previous,
            after=after,
            removed_delta=removed_delta,
            reason_bits=code,
            reason_names=tuple(reason.name.lower() for reason in reasons),
        )
        previous = after


def _validate_stage_accounting(
    reason_bits: np.ndarray,
    keep_mask: np.ndarray,
    stages: Sequence[PostprocessStage],
) -> dict[str, object]:
    removed_seen = np.zeros(len(reason_bits), dtype=bool)
    final_after = np.ones(len(reason_bits), dtype=bool)
    delta_sum = 0
    for stage_masks in _iter_stage_masks(reason_bits, stages):
        delta = stage_masks.removed_delta
        if np.any(removed_seen & delta):
            raise RuntimeError("debug-stage removal deltas overlap")
        removed_seen |= delta
        delta_sum += int(np.count_nonzero(delta))
        final_after = stage_masks.after
    final_removed = ~keep_mask
    if not np.array_equal(final_after, keep_mask):
        raise RuntimeError("last reconstructed stage does not equal the final keep mask")
    if not np.array_equal(removed_seen, final_removed):
        raise RuntimeError("debug-stage removal deltas do not cover final removed points")
    removed_count = int(np.count_nonzero(final_removed))
    if delta_sum != removed_count:
        raise RuntimeError("debug-stage removal delta sum does not match final removed count")
    return {
        "postprocess_stage_counts_match": True,
        "removal_deltas_disjoint": True,
        "removal_delta_sum": delta_sum,
        "final_removed_point_count": removed_count,
        "delta_union_equals_final_removed": True,
        "last_stage_equals_final_clean": True,
    }


def _sample_mask_indices(mask: np.ndarray, max_points: int) -> np.ndarray:
    """Sample boolean-mask ranks without materializing every selected index."""

    if max_points <= 0:
        raise ValueError("max_points must be positive")
    selected_count = int(np.count_nonzero(mask))
    sample_count = min(selected_count, int(max_points))
    if sample_count == 0:
        return np.empty(0, dtype=np.int64)
    target_ranks = (
        np.arange(sample_count, dtype=np.int64) * selected_count // sample_count
    )
    sampled = np.empty(sample_count, dtype=np.int64)
    rank_offset = 0
    target_offset = 0
    for begin in range(0, len(mask), _MASK_SCAN_CHUNK_POINTS):
        end = min(begin + _MASK_SCAN_CHUNK_POINTS, len(mask))
        local_indices = np.flatnonzero(mask[begin:end])
        next_rank_offset = rank_offset + len(local_indices)
        target_end = int(
            np.searchsorted(target_ranks, next_rank_offset, side="left")
        )
        if target_end > target_offset:
            local_ranks = target_ranks[target_offset:target_end] - rank_offset
            sampled[target_offset:target_end] = begin + local_indices[local_ranks]
        target_offset = target_end
        rank_offset = next_rank_offset
    if target_offset != sample_count or rank_offset != selected_count:
        raise RuntimeError("debug-stage rank sampling accounting failed")
    if len(sampled) > 1 and np.any(sampled[1:] <= sampled[:-1]):
        raise RuntimeError("debug-stage sampled indices must be unique and ordered")
    return sampled


def _relative(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def _write_sample_products(
    stage_dir: Path,
    *,
    stem: str,
    points: np.ndarray,
    colors: np.ndarray,
    sampled_indices: np.ndarray,
    full_count: int,
    origin: LocalENU,
    stage_name: str,
) -> dict[str, object]:
    sampled_points = points[sampled_indices]
    sampled_colors = colors[sampled_indices]
    ply_path = stage_dir / f"cloud_{stem}_sampled.ply"
    bin_path = stage_dir / f"points_{stem}_sampled.bin"
    write_ply(
        ply_path,
        sampled_points,
        sampled_colors,
        origin,
        comments={
            "pointcloud_stage": f"debug_{stage_name}_{stem}",
            "full_point_count": int(full_count),
            "sample_selection": _SAMPLE_SELECTION,
        },
    )
    browser_count = write_browser_points_arrays(
        bin_path,
        sampled_points,
        sampled_colors,
    )
    validate_browser_binary(bin_path, browser_count)
    del sampled_points, sampled_colors
    return {
        "full_point_count": int(full_count),
        "sample_point_count": int(len(sampled_indices)),
        "ply": ply_path,
        "browser_binary": bin_path,
        "browser_point_count": int(browser_count),
    }


def _diagnostic_sample_masks(
    point_count: int,
    survivor_indices: np.ndarray,
    removed_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    after = np.zeros(point_count, dtype=bool)
    removed = np.zeros(point_count, dtype=bool)
    after[survivor_indices] = True
    removed[removed_indices] = True
    before = after | removed
    return before, after, removed


def _stage_payload(
    *,
    output_dir: Path,
    stage_masks: _StageMasks,
    survivor_products: Mapping[str, object],
    removed_products: Mapping[str, object],
    diagnostic_paths: Mapping[str, Path],
    projection: DebugProjectionContext,
    cumulative_removed_count: int,
    ground_surface_stats: Mapping[str, Any] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_version": 1,
        "stage_index": stage_masks.index,
        "stage": stage_masks.stage.stage,
        "input_point_count": stage_masks.stage.input_count,
        "output_point_count": stage_masks.stage.output_count,
        "removed_this_stage_count": stage_masks.stage.removed_count,
        "cumulative_removed_point_count": cumulative_removed_count,
        "seconds": float(stage_masks.stage.seconds),
        "new_removal_reason_bits": stage_masks.reason_bits,
        "new_removal_reason_names": list(stage_masks.reason_names),
        "point_retention": (
            float(stage_masks.stage.output_count / stage_masks.stage.input_count)
            if stage_masks.stage.input_count
            else None
        ),
        "sample_selection": _SAMPLE_SELECTION,
        "projection": projection.to_dict(),
        "survivors": {
            **{
                key: value
                for key, value in survivor_products.items()
                if key not in {"ply", "browser_binary"}
            },
            "ply": _relative(Path(survivor_products["ply"]), output_dir),
            "browser_binary": _relative(
                Path(survivor_products["browser_binary"]), output_dir
            ),
        },
        "removed_this_stage": {
            **{
                key: value
                for key, value in removed_products.items()
                if key not in {"ply", "browser_binary"}
            },
            "ply": _relative(Path(removed_products["ply"]), output_dir),
            "browser_binary": _relative(
                Path(removed_products["browser_binary"]), output_dir
            ),
            "ply_color_semantics": "original_rgb",
            "diagnostic_panel_color": "fixed_red",
        },
        "diagnostics": {
            name: _relative(path, output_dir)
            for name, path in diagnostic_paths.items()
        },
    }
    if stage_masks.stage.stage == "local_surface":
        payload["local_surface"] = {
            "purpose": (
                "remove points below a supported road-local surface only inside "
                "the trajectory corridor"
            ),
            "removed_below_surface_count": stage_masks.stage.removed_count,
            "ground_surface_stats": (
                dict(ground_surface_stats)
                if ground_surface_stats is not None
                else None
            ),
        }
    return payload


def write_debug_stage_artifacts(
    output_dir: str | Path,
    *,
    raw_points: np.ndarray,
    raw_colors: np.ndarray,
    removal_reason_bits: np.ndarray,
    keep_mask: np.ndarray,
    stages: Sequence[PostprocessStage],
    origin: LocalENU,
    trajectory_points: np.ndarray | None,
    max_points: int,
    selected_preset: str,
    neighbor_backend: str,
    ground_backend: str,
    report: Mapping[str, Any] | None = None,
    image_size: tuple[int, int] = (560, 520),
) -> dict[str, Path]:
    """Persist capped, reproducible products for the selected cleanup result."""

    if max_points <= 0:
        raise ValueError("debug stage max_points must be positive")
    root = Path(output_dir).expanduser().resolve()
    points, colors, reasons, keep = _validated_arrays(
        raw_points,
        raw_colors,
        removal_reason_bits,
        keep_mask,
    )
    stage_sequence = tuple(stages)
    accounting = _validate_stage_accounting(reasons, keep, stage_sequence)
    projection = prepare_debug_projection_context(points, trajectory_points)
    data_root = root / "data" / "debug_stages"
    diagnostics_root = root / "diagnostics" / "debug_stages"
    data_root.mkdir(parents=True, exist_ok=True)
    diagnostics_root.mkdir(parents=True, exist_ok=True)

    report_mapping = report if isinstance(report, Mapping) else {}
    ground_value = report_mapping.get("ground_surface")
    ground_stats = ground_value if isinstance(ground_value, Mapping) else None
    records: list[dict[str, object]] = []
    cumulative_removed = 0
    last_after = np.ones(len(points), dtype=bool)
    for stage_masks in _iter_stage_masks(reasons, stage_sequence):
        stage_name = stage_masks.stage.stage
        stage_dir_name = f"{stage_masks.index:02d}_{stage_name}"
        stage_dir = data_root / stage_dir_name
        stage_diagnostics_dir = diagnostics_root / stage_dir_name
        stage_dir.mkdir(parents=True, exist_ok=True)
        survivor_indices = _sample_mask_indices(stage_masks.after, max_points)
        removed_indices = _sample_mask_indices(stage_masks.removed_delta, max_points)
        survivor_products = _write_sample_products(
            stage_dir,
            stem="survivors",
            points=points,
            colors=colors,
            sampled_indices=survivor_indices,
            full_count=stage_masks.stage.output_count,
            origin=origin,
            stage_name=stage_name,
        )
        removed_products = _write_sample_products(
            stage_dir,
            stem="removed_delta",
            points=points,
            colors=colors,
            sampled_indices=removed_indices,
            full_count=stage_masks.stage.removed_count,
            origin=origin,
            stage_name=stage_name,
        )
        diagnostic_before, diagnostic_after, diagnostic_removed = (
            _diagnostic_sample_masks(
                len(points),
                survivor_indices,
                removed_indices,
            )
        )
        diagnostic_paths = write_debug_stage_diagnostics(
            stage_diagnostics_dir,
            points,
            colors,
            diagnostic_before,
            diagnostic_after,
            diagnostic_removed,
            stage_name=stage_name,
            projection=projection,
            image_size=image_size,
        )
        del diagnostic_before, diagnostic_after, diagnostic_removed
        cumulative_removed += stage_masks.stage.removed_count
        payload = _stage_payload(
            output_dir=root,
            stage_masks=stage_masks,
            survivor_products=survivor_products,
            removed_products=removed_products,
            diagnostic_paths=diagnostic_paths,
            projection=projection,
            cumulative_removed_count=cumulative_removed,
            ground_surface_stats=ground_stats,
        )
        stage_json = stage_dir / "stage.json"
        atomic_json_dump(stage_json, payload)
        records.append(
            {
                "stage_index": stage_masks.index,
                "stage": stage_name,
                "stage_json": _relative(stage_json, root),
                "input_point_count": stage_masks.stage.input_count,
                "output_point_count": stage_masks.stage.output_count,
                "removed_this_stage_count": stage_masks.stage.removed_count,
            }
        )
        last_after = stage_masks.after
        del survivor_indices, removed_indices

    clean_index = len(stage_sequence)
    clean_name = "clean"
    clean_stage = PostprocessStage(
        stage=clean_name,
        input_count=int(np.count_nonzero(last_after)),
        output_count=int(np.count_nonzero(last_after)),
        removed_count=0,
        seconds=0.0,
    )
    clean_masks = _StageMasks(
        index=clean_index,
        stage=clean_stage,
        before=last_after,
        after=last_after,
        removed_delta=np.zeros(len(points), dtype=bool),
        reason_bits=0,
        reason_names=(),
    )
    clean_dir_name = f"{clean_index:02d}_{clean_name}"
    clean_dir = data_root / clean_dir_name
    clean_diagnostics_dir = diagnostics_root / clean_dir_name
    clean_dir.mkdir(parents=True, exist_ok=True)
    clean_indices = _sample_mask_indices(last_after, max_points)
    empty_indices = np.empty(0, dtype=np.int64)
    clean_products = _write_sample_products(
        clean_dir,
        stem="survivors",
        points=points,
        colors=colors,
        sampled_indices=clean_indices,
        full_count=clean_stage.output_count,
        origin=origin,
        stage_name=clean_name,
    )
    clean_removed_products = _write_sample_products(
        clean_dir,
        stem="removed_delta",
        points=points,
        colors=colors,
        sampled_indices=empty_indices,
        full_count=0,
        origin=origin,
        stage_name=clean_name,
    )
    clean_before, clean_after, clean_removed = _diagnostic_sample_masks(
        len(points), clean_indices, empty_indices
    )
    clean_diagnostic_paths = write_debug_stage_diagnostics(
        clean_diagnostics_dir,
        points,
        colors,
        clean_before,
        clean_after,
        clean_removed,
        stage_name=clean_name,
        projection=projection,
        image_size=image_size,
    )
    clean_payload = _stage_payload(
        output_dir=root,
        stage_masks=clean_masks,
        survivor_products=clean_products,
        removed_products=clean_removed_products,
        diagnostic_paths=clean_diagnostic_paths,
        projection=projection,
        cumulative_removed_count=cumulative_removed,
        ground_surface_stats=ground_stats,
    )
    clean_payload["canonical_equivalent"] = "data/cloud_clean_enu.ply"
    clean_json = clean_dir / "stage.json"
    atomic_json_dump(clean_json, clean_payload)
    records.append(
        {
            "stage_index": clean_index,
            "stage": clean_name,
            "stage_json": _relative(clean_json, root),
            "input_point_count": clean_stage.input_count,
            "output_point_count": clean_stage.output_count,
            "removed_this_stage_count": 0,
        }
    )

    index_path = data_root / "index.json"
    index_payload = {
        "format_version": 1,
        "selected_result_only": True,
        "selected_preset": selected_preset,
        "neighbor_backend": neighbor_backend,
        "ground_backend": ground_backend,
        "raw_point_count": int(len(points)),
        "clean_point_count": int(np.count_nonzero(keep)),
        "removed_point_count": int(np.count_nonzero(~keep)),
        "debug_stage_max_points": int(max_points),
        "sample_selection": _SAMPLE_SELECTION,
        "projection": projection.to_dict(),
        "accounting": accounting,
        "stages": records,
    }
    atomic_json_dump(index_path, index_payload)
    return {"debug_stage_index": index_path}


__all__ = ["write_debug_stage_artifacts"]
