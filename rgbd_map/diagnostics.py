from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import cv2
import numpy as np

from .postprocess import (
    REMOVAL_REASON_COLORS_RGB,
    RemovalReason,
    diagnostic_removal_colors,
    primary_removal_reasons,
)


_DIAGNOSTIC_CHUNK_POINTS = 250_000
_DEFAULT_REASON_NAMES: dict[int, str] = {
    int(reason): reason.name.lower()
    for reason in RemovalReason
    if reason != RemovalReason.NONE
}
_REASON_COLORS_BY_CODE: dict[int, tuple[int, int, int]] = {
    int(reason): color for reason, color in REMOVAL_REASON_COLORS_RGB.items()
}
_PANEL_BACKGROUND_RGB = np.array([8, 13, 20], dtype=np.uint8)
_PANEL_BORDER_RGB = (54, 72, 92)


@dataclass(frozen=True)
class DebugProjectionContext:
    """Fixed projection geometry shared by every debug-stage image."""

    top_bounds: tuple[tuple[float, float], tuple[float, float]]
    side_bounds: tuple[tuple[float, float], tuple[float, float]]
    side_origin_xy: tuple[float, float]
    side_axis_xy: tuple[float, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "top_bounds": [list(bounds) for bounds in self.top_bounds],
            "side_bounds": [list(bounds) for bounds in self.side_bounds],
            "side_origin_xy": list(self.side_origin_xy),
            "side_axis_xy": list(self.side_axis_xy),
        }


def _as_points(value: np.ndarray | None, name: str) -> np.ndarray:
    if value is None:
        return np.empty((0, 3), dtype=np.float32)
    points = np.asarray(value)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError(f"{name} must have shape (N, 3)")
    if (
        points.dtype == object
        or not np.issubdtype(points.dtype, np.number)
        or np.iscomplexobj(points)
    ):
        raise ValueError(f"{name} must be a real numeric array")
    return points


def _as_colors(
    value: np.ndarray | None,
    count: int,
    name: str,
) -> np.ndarray | None:
    if value is None:
        return None
    colors = np.asarray(value)
    if colors.shape != (count, 3):
        raise ValueError(f"{name} must have shape ({count}, 3)")
    if (
        colors.dtype == object
        or not np.issubdtype(colors.dtype, np.number)
        or np.iscomplexobj(colors)
    ):
        raise ValueError(f"{name} must be a real numeric array")
    return colors


def _as_reason_array(
    value: np.ndarray | None,
    count: int,
    name: str,
) -> np.ndarray | None:
    if value is None:
        return None
    reasons = np.asarray(value)
    if reasons.shape != (count,):
        raise ValueError(f"{name} must have shape ({count},)")
    if (
        reasons.dtype == object
        or not np.issubdtype(reasons.dtype, np.integer)
        or np.iscomplexobj(reasons)
    ):
        raise ValueError(f"{name} must be an integer array")
    return reasons


def _as_selection_mask(
    value: np.ndarray | None,
    count: int,
    name: str,
) -> np.ndarray | None:
    if value is None:
        return None
    mask = np.asarray(value)
    if mask.shape != (count,):
        raise ValueError(f"{name} must have shape ({count},)")
    return mask.astype(bool, copy=False)


def _iter_finite_point_chunks(
    points: np.ndarray,
    selection_mask: np.ndarray | None = None,
) -> Iterator[tuple[int, int, np.ndarray, np.ndarray | None]]:
    """Yield bounded selected/finite chunks and their companion-array mask."""

    selected = _as_selection_mask(selection_mask, len(points), "selection_mask")

    for begin in range(0, len(points), _DIAGNOSTIC_CHUNK_POINTS):
        end = min(begin + _DIAGNOSTIC_CHUNK_POINTS, len(points))
        chunk = points[begin:end]
        finite = np.isfinite(chunk).all(axis=1)
        if selected is not None:
            finite &= selected[begin:end]
        if not bool(np.any(finite)):
            continue
        if bool(np.all(finite)):
            yield begin, end, chunk, None
        else:
            yield begin, end, chunk[finite], finite


def _select_companion_chunk(
    values: np.ndarray,
    begin: int,
    end: int,
    finite_mask: np.ndarray | None,
) -> np.ndarray:
    chunk = values[begin:end]
    return chunk if finite_mask is None else chunk[finite_mask]


def _color_scale(colors: np.ndarray | None) -> float:
    if colors is None or not np.issubdtype(colors.dtype, np.floating) or colors.size == 0:
        return 1.0
    with np.errstate(all="ignore"):
        maximum = float(np.nanmax(colors, initial=0.0))
    return 255.0 if maximum <= 1.0 else 1.0


def _normalized_color_chunk(colors: np.ndarray, scale: float) -> np.ndarray:
    if colors.dtype == np.uint8 and scale == 1.0:
        return colors
    return np.nan_to_num(
        colors * scale,
        nan=0.0,
        posinf=255.0,
        neginf=0.0,
    ).clip(0, 255).astype(np.uint8, copy=False)


def _padded_bounds(low: float, high: float) -> tuple[float, float]:
    if not np.isfinite(low) or not np.isfinite(high):
        return -0.5, 0.5
    if high - low < 1e-6:
        padding = max(0.5, abs(low) * 0.01)
        return low - padding, high + padding
    padding = (high - low) * 0.025
    return low - padding, high + padding


def _project_chunk(
    points: np.ndarray,
    projection: str,
    origin_xy: np.ndarray | None,
    axis_xy: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    if projection == "top":
        return points[:, 0], points[:, 1]
    if projection != "side" or origin_xy is None or axis_xy is None:
        raise ValueError(f"unsupported projection: {projection}")
    along = (
        points[:, 0] * axis_xy[0]
        + points[:, 1] * axis_xy[1]
        - float(origin_xy @ axis_xy)
    )
    return along, points[:, 2]


def _projected_bounds(
    point_sets: tuple[np.ndarray, ...],
    projection: str,
    origin_xy: np.ndarray | None = None,
    axis_xy: np.ndarray | None = None,
    selection_masks: tuple[np.ndarray | None, ...] | None = None,
) -> tuple[tuple[float, float], tuple[float, float]]:
    x_min = np.inf
    x_max = -np.inf
    y_min = np.inf
    y_max = -np.inf
    masks = (
        tuple(None for _ in point_sets)
        if selection_masks is None
        else selection_masks
    )
    if len(masks) != len(point_sets):
        raise ValueError("selection_masks must align with point_sets")
    for points, selection_mask in zip(point_sets, masks, strict=True):
        for _, _, finite_points, _ in _iter_finite_point_chunks(
            points, selection_mask
        ):
            x, y = _project_chunk(finite_points, projection, origin_xy, axis_xy)
            x_min = min(x_min, float(np.min(x)))
            x_max = max(x_max, float(np.max(x)))
            y_min = min(y_min, float(np.min(y)))
            y_max = max(y_max, float(np.max(y)))
    return _padded_bounds(x_min, x_max), _padded_bounds(y_min, y_max)


def _xy_moments(
    points: np.ndarray,
    selection_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    count = 0
    sum_x = 0.0
    sum_y = 0.0
    sum_xx = 0.0
    sum_xy = 0.0
    sum_yy = 0.0
    first: np.ndarray | None = None
    last: np.ndarray | None = None
    for _, _, finite_points, _ in _iter_finite_point_chunks(points, selection_mask):
        x = finite_points[:, 0].astype(np.float64, copy=False)
        y = finite_points[:, 1].astype(np.float64, copy=False)
        if first is None:
            first = np.array([x[0], y[0]], dtype=np.float64)
        last = np.array([x[-1], y[-1]], dtype=np.float64)
        count += len(finite_points)
        sum_x += float(np.sum(x, dtype=np.float64))
        sum_y += float(np.sum(y, dtype=np.float64))
        sum_xx += float(x @ x)
        sum_xy += float(x @ y)
        sum_yy += float(y @ y)
    return {
        "count": count,
        "sum_x": sum_x,
        "sum_y": sum_y,
        "sum_xx": sum_xx,
        "sum_xy": sum_xy,
        "sum_yy": sum_yy,
        "first": first,
        "last": last,
    }


def _axis_from_moments(moments: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    count = int(moments["count"])
    if count == 0:
        return np.zeros(2, dtype=np.float64), np.array([1.0, 0.0], dtype=np.float64)
    center = np.array(
        [float(moments["sum_x"]) / count, float(moments["sum_y"]) / count],
        dtype=np.float64,
    )
    if count < 2:
        return center, np.array([1.0, 0.0], dtype=np.float64)
    covariance = np.array(
        [
            [float(moments["sum_xx"]) / count - center[0] ** 2,
             float(moments["sum_xy"]) / count - center[0] * center[1]],
            [float(moments["sum_xy"]) / count - center[0] * center[1],
             float(moments["sum_yy"]) / count - center[1] ** 2],
        ],
        dtype=np.float64,
    )
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    axis = eigenvectors[:, int(np.argmax(eigenvalues))]
    norm = float(np.linalg.norm(axis))
    if not np.isfinite(axis).all() or norm < 1e-12:
        axis = np.array([1.0, 0.0], dtype=np.float64)
    else:
        axis = axis / norm
    if axis[0] < 0 or (abs(axis[0]) < 1e-12 and axis[1] < 0):
        axis = -axis
    return center, axis


def _side_projection_basis(
    trajectory: np.ndarray,
    raw: np.ndarray,
    clean: np.ndarray,
    removed: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    trajectory_moments = _xy_moments(trajectory)
    if int(trajectory_moments["count"]) >= 2:
        center, pca_axis = _axis_from_moments(trajectory_moments)
        direction = trajectory_moments["last"] - trajectory_moments["first"]
        norm = float(np.linalg.norm(direction))
        return center, direction / norm if norm > 1e-9 else pca_axis
    for points in (raw, clean, removed):
        moments = _xy_moments(points)
        if int(moments["count"]):
            return _axis_from_moments(moments)
    return np.zeros(2, dtype=np.float64), np.array([1.0, 0.0], dtype=np.float64)


def _reason_chunk(
    begin: int,
    end: int,
    finite_mask: np.ndarray | None,
    primary_reason: np.ndarray | None,
    reason_bits: np.ndarray | None,
) -> np.ndarray:
    if primary_reason is not None:
        return _select_companion_chunk(primary_reason, begin, end, finite_mask).astype(
            np.uint16,
            copy=False,
        )
    if reason_bits is not None:
        bits = _select_companion_chunk(reason_bits, begin, end, finite_mask)
        return primary_removal_reasons(bits)
    count = (
        end - begin
        if finite_mask is None
        else int(np.count_nonzero(finite_mask))
    )
    return np.zeros(count, dtype=np.uint16)


def _rasterize_points(
    points: np.ndarray,
    colors_rgb: np.ndarray | None,
    bounds: tuple[tuple[float, float], tuple[float, float]],
    width: int,
    height: int,
    default_rgb: tuple[int, int, int],
    *,
    projection: str,
    origin_xy: np.ndarray | None = None,
    axis_xy: np.ndarray | None = None,
    primary_reason: np.ndarray | None = None,
    reason_bits: np.ndarray | None = None,
    selection_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    pixel_count = width * height
    counts = np.zeros(pixel_count, dtype=np.int64)
    use_reason_colors = primary_reason is not None or reason_bits is not None
    color_sums = (
        np.zeros((pixel_count, 3), dtype=np.float64)
        if colors_rgb is not None or use_reason_colors
        else None
    )
    color_scale = _color_scale(colors_rgb)
    visible_count = 0
    (x_min, x_max), (y_min, y_max) = bounds
    x_scale = (width - 1) / max(x_max - x_min, 1e-9)
    y_scale = (height - 1) / max(y_max - y_min, 1e-9)

    for begin, end, finite_points, finite_mask in _iter_finite_point_chunks(
        points, selection_mask
    ):
        x, y = _project_chunk(finite_points, projection, origin_xy, axis_xy)
        px = np.floor((x - x_min) * x_scale).astype(np.int64)
        py = np.floor((y_max - y) * y_scale).astype(np.int64)
        np.clip(px, 0, width - 1, out=px)
        np.clip(py, 0, height - 1, out=py)
        flat = py * width + px
        counts += np.bincount(flat, minlength=pixel_count)
        visible_count += len(finite_points)

        if color_sums is None:
            continue
        if use_reason_colors:
            primary = _reason_chunk(
                begin,
                end,
                finite_mask,
                primary_reason,
                reason_bits,
            )
            color_chunk = diagnostic_removal_colors(primary)
        else:
            selected = _select_companion_chunk(colors_rgb, begin, end, finite_mask)
            color_chunk = _normalized_color_chunk(selected, color_scale)
        for channel in range(3):
            color_sums[:, channel] += np.bincount(
                flat,
                weights=color_chunk[:, channel],
                minlength=pixel_count,
            )

    occupied = counts > 0
    maximum = int(np.max(counts, initial=0))
    density = np.zeros(pixel_count, dtype=np.float64)
    if maximum:
        density[occupied] = 0.35 + 0.65 * (
            np.log1p(counts[occupied]) / np.log1p(maximum)
        )
    rendered = np.broadcast_to(_PANEL_BACKGROUND_RGB, (pixel_count, 3)).astype(
        np.float64,
    ).copy()
    if color_sums is None:
        base = np.asarray(default_rgb, dtype=np.float64)
        rendered[occupied] = base * density[occupied, None]
    else:
        rendered[occupied] = (
            color_sums[occupied]
            / counts[occupied, None]
            * density[occupied, None]
        )
    return rendered.clip(0, 255).astype(np.uint8).reshape(height, width, 3), visible_count


def _label_panel(image_rgb: np.ndarray, title: str, point_count: int) -> np.ndarray:
    image = image_rgb.copy()
    cv2.rectangle(
        image,
        (0, 0),
        (image.shape[1] - 1, image.shape[0] - 1),
        _PANEL_BORDER_RGB,
        1,
    )
    cv2.rectangle(image, (0, 0), (image.shape[1], 42), (8, 13, 20), -1)
    cv2.putText(
        image,
        title,
        (13, 19),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (235, 242, 250),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"{point_count:,} points",
        (13, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.40,
        (145, 164, 184),
        1,
        cv2.LINE_AA,
    )
    if point_count == 0:
        cv2.putText(
            image,
            "No points",
            (max(12, image.shape[1] // 2 - 37), image.shape[0] // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (145, 164, 184),
            1,
            cv2.LINE_AA,
        )
    return image


def _write_comparison_image(
    path: Path,
    point_sets: tuple[np.ndarray, np.ndarray, np.ndarray],
    colors: tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None],
    labels: tuple[str, str, str],
    bounds: tuple[tuple[float, float], tuple[float, float]],
    panel_size: tuple[int, int],
    *,
    projection: str,
    origin_xy: np.ndarray | None = None,
    axis_xy: np.ndarray | None = None,
    selection_masks: tuple[
        np.ndarray | None,
        np.ndarray | None,
        np.ndarray | None,
    ] = (None, None, None),
) -> None:
    panel_width, panel_height = panel_size
    defaults = ((88, 184, 255), (100, 235, 165), (255, 120, 70))
    panels: list[np.ndarray] = []
    for points, color, label, default, selection_mask in zip(
        point_sets,
        colors,
        labels,
        defaults,
        selection_masks,
        strict=True,
    ):
        image, visible_count = _rasterize_points(
            points,
            color,
            bounds,
            panel_width,
            panel_height,
            default,
            projection=projection,
            origin_xy=origin_xy,
            axis_xy=axis_xy,
            selection_mask=selection_mask,
        )
        panels.append(_label_panel(image, label, visible_count))
    canvas = np.concatenate(panels, axis=1)
    if not cv2.imwrite(str(path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)):
        raise OSError(f"failed to write diagnostic image: {path}")


def prepare_debug_projection_context(
    raw_points: np.ndarray,
    trajectory_points: np.ndarray | None = None,
) -> DebugProjectionContext:
    """Resolve one top/side frame to reuse across every cleanup stage."""

    raw = _as_points(raw_points, "raw_points")
    trajectory = _as_points(trajectory_points, "trajectory_points")
    empty = np.empty((0, 3), dtype=np.float32)
    top_bounds = _projected_bounds((raw,), "top")
    origin_xy, axis_xy = _side_projection_basis(trajectory, raw, empty, empty)
    side_bounds = _projected_bounds(
        (raw,),
        "side",
        origin_xy,
        axis_xy,
    )
    return DebugProjectionContext(
        top_bounds=top_bounds,
        side_bounds=side_bounds,
        side_origin_xy=(float(origin_xy[0]), float(origin_xy[1])),
        side_axis_xy=(float(axis_xy[0]), float(axis_xy[1])),
    )


def write_debug_stage_diagnostics(
    output_dir: str | Path,
    raw_points: np.ndarray,
    raw_colors: np.ndarray,
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    removed_delta_mask: np.ndarray,
    *,
    stage_name: str,
    projection: DebugProjectionContext,
    image_size: tuple[int, int] = (560, 520),
) -> dict[str, Path]:
    """Write input/output/new-removal panels without materializing stage clouds.

    All three panels address the same raw arrays through boolean masks. The caller
    supplies a projection computed once for the full run, which prevents per-stage
    auto-scaling from making a destructive filter look deceptively unchanged.
    """

    output_path = Path(output_dir)
    if len(image_size) != 2 or min(image_size) < 128:
        raise ValueError("image_size must contain two dimensions of at least 128 pixels")
    output_path.mkdir(parents=True, exist_ok=True)
    raw = _as_points(raw_points, "raw_points")
    colors = _as_colors(raw_colors, len(raw), "raw_colors")
    assert colors is not None
    before = _as_selection_mask(before_mask, len(raw), "before_mask")
    after = _as_selection_mask(after_mask, len(raw), "after_mask")
    removed = _as_selection_mask(
        removed_delta_mask,
        len(raw),
        "removed_delta_mask",
    )
    assert before is not None and after is not None and removed is not None
    if np.any(after & ~before):
        raise ValueError("after_mask must be a subset of before_mask")
    if np.any(removed & ~before) or np.any(removed & after):
        raise ValueError("removed_delta_mask must be the disjoint before/after delta")
    if not np.array_equal(before, after | removed):
        raise ValueError("before_mask must equal after_mask union removed_delta_mask")

    display_name = stage_name.replace("_", " ")
    paths = {
        "top": output_path / "top_input_output_removed.png",
        "side": output_path / "side_input_output_removed.png",
    }
    point_sets = (raw, raw, raw)
    color_sets = (colors, colors, None)
    labels = (
        f"Input: {display_name}",
        f"Output: {display_name}",
        f"Removed in: {display_name}",
    )
    masks = (before, after, removed)
    _write_comparison_image(
        paths["top"],
        point_sets,
        color_sets,
        labels,
        projection.top_bounds,
        image_size,
        projection="top",
        selection_masks=masks,
    )
    origin_xy = np.asarray(projection.side_origin_xy, dtype=np.float64)
    axis_xy = np.asarray(projection.side_axis_xy, dtype=np.float64)
    _write_comparison_image(
        paths["side"],
        point_sets,
        color_sets,
        labels,
        projection.side_bounds,
        image_size,
        projection="side",
        origin_xy=origin_xy,
        axis_xy=axis_xy,
        selection_masks=masks,
    )
    return paths


def _tile_count_map(points: np.ndarray, tile_size_m: float) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for _, _, finite_points, _ in _iter_finite_point_chunks(points):
        tile_xy = np.floor(finite_points[:, :2] / tile_size_m).astype(np.int64)
        keys, chunk_counts = np.unique(tile_xy, axis=0, return_counts=True)
        for key, count in zip(keys, chunk_counts, strict=True):
            tile = (int(key[0]), int(key[1]))
            counts[tile] = counts.get(tile, 0) + int(count)
    return counts


def _join_tile_counts(
    raw_points: np.ndarray,
    clean_points: np.ndarray,
    removed_points: np.ndarray,
    tile_size_m: float,
) -> list[dict[str, Any]]:
    raw_counts = _tile_count_map(raw_points, tile_size_m)
    clean_counts = _tile_count_map(clean_points, tile_size_m)
    removed_counts = _tile_count_map(removed_points, tile_size_m)
    tiles = raw_counts.keys() | clean_counts.keys() | removed_counts.keys()
    records = []
    for tile_x, tile_y in tiles:
        tile = (tile_x, tile_y)
        raw = raw_counts.get(tile, 0)
        clean = clean_counts.get(tile, 0)
        removed = removed_counts.get(tile, 0)
        records.append(
            {
                "tile_x": tile_x,
                "tile_y": tile_y,
                "bounds_xy_m": [
                    float(tile_x * tile_size_m),
                    float(tile_y * tile_size_m),
                    float((tile_x + 1) * tile_size_m),
                    float((tile_y + 1) * tile_size_m),
                ],
                "raw_point_count": raw,
                "clean_point_count": clean,
                "removed_point_count": removed,
                "removal_ratio": float(removed / raw) if raw else 0.0,
            }
        )
    records.sort(
        key=lambda record: (
            -record["removed_point_count"],
            -record["removal_ratio"],
            record["tile_x"],
            record["tile_y"],
        )
    )
    return records


def _write_representative_tiles(
    path: Path,
    raw_points: np.ndarray,
    clean_points: np.ndarray,
    removed_points: np.ndarray,
    tile_size_m: float,
    representative_tile_count: int,
) -> None:
    tiles = _join_tile_counts(raw_points, clean_points, removed_points, tile_size_m)
    payload = {
        "format_version": 1,
        "coordinate_system": "local_ENU_m",
        "tile_size_m": float(tile_size_m),
        "tile_count": len(tiles),
        "selection": "highest_removed_point_count_then_removal_ratio",
        "representative_tiles": tiles[:representative_tile_count],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _reason_counts(
    points: np.ndarray,
    primary_reason: np.ndarray | None,
    reason_bits: np.ndarray | None,
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for begin, end, _, finite_mask in _iter_finite_point_chunks(points):
        primary = _reason_chunk(
            begin,
            end,
            finite_mask,
            primary_reason,
            reason_bits,
        )
        values, chunk_counts = np.unique(primary, return_counts=True)
        for value, count in zip(values, chunk_counts, strict=True):
            code = int(value)
            counts[code] = counts.get(code, 0) + int(count)
    return counts


def _format_metric(value: Any, *, percentage: bool = False) -> str:
    if value is None:
        return "not_evaluable"
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(number):
        return "not_evaluable"
    return f"{number * 100.0:.2f}%" if percentage else f"{number:.6g}"


def _write_run_summary(
    path: Path,
    raw_count: int,
    clean_count: int,
    removed_count: int,
    report: Mapping[str, Any] | None,
) -> None:
    report = report or {}
    output_value = report.get("output", {})
    guards_value = report.get("quality_guards", {})
    output = output_value if isinstance(output_value, Mapping) else {}
    guards = guards_value if isinstance(guards_value, Mapping) else {}
    ratio = output.get("removal_ratio")
    if ratio is None:
        ratio = removed_count / raw_count if raw_count else 0.0
    lines = [
        "Point cloud postprocess run summary",
        f"raw_point_count: {raw_count}",
        f"clean_point_count: {clean_count}",
        f"removed_point_count: {removed_count}",
        f"removal_ratio: {_format_metric(ratio, percentage=True)}",
        f"selected_result: {report.get('selected_result', 'not_recorded')}",
        f"quality_guards_passed: {guards.get('passed', 'not_evaluable')}",
        "xy_coverage_retention: "
        f"{_format_metric(guards.get('xy_coverage_retention'), percentage=True)}",
        "trajectory_corridor_coverage_retention: "
        f"{_format_metric(guards.get('trajectory_corridor_coverage_retention'), percentage=True)}",
        "high_structure_retention: "
        f"{_format_metric(guards.get('high_structure_retention'), percentage=True)}",
        "below_surface_reduction: "
        f"{_format_metric(guards.get('below_surface_reduction'), percentage=True)}",
        "bright_isolated_reduction: "
        f"{_format_metric(guards.get('bright_isolated_reduction'), percentage=True)}",
    ]
    warnings = guards.get("warnings", [])
    if warnings:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_postprocess_diagnostics(
    output_dir: str | Path,
    raw_points: np.ndarray,
    clean_points: np.ndarray,
    removed_points: np.ndarray,
    removed_reason_bits: np.ndarray | None = None,
    *,
    raw_colors: np.ndarray | None = None,
    clean_colors: np.ndarray | None = None,
    removed_colors: np.ndarray | None = None,
    trajectory_points: np.ndarray | None = None,
    primary_reason: np.ndarray | None = None,
    reason_names: Mapping[int, str] | None = None,
    report: Mapping[str, Any] | None = None,
    tile_size_m: float = 20.0,
    image_size: tuple[int, int] = (560, 520),
    representative_tile_count: int = 12,
) -> dict[str, Path]:
    """Write bounded-memory postprocess diagnostics from local ENU arrays.

    ``output_dir`` is the diagnostics directory itself. Point projection,
    rasterization, reason coloring, and tile binning scan bounded chunks instead
    of allocating point-count-sized float64 or int64 work arrays. Resident work
    memory is therefore proportional to the configured chunk plus raster and
    occupied-tile counts, not to the full raw point count.
    """

    output_path = Path(output_dir)
    if tile_size_m <= 0:
        raise ValueError("tile_size_m must be positive")
    if len(image_size) != 2 or min(image_size) < 128:
        raise ValueError("image_size must contain two dimensions of at least 128 pixels")
    if representative_tile_count < 0:
        raise ValueError("representative_tile_count must be non-negative")
    output_path.mkdir(parents=True, exist_ok=True)

    raw = _as_points(raw_points, "raw_points")
    clean = _as_points(clean_points, "clean_points")
    removed = _as_points(removed_points, "removed_points")
    trajectory = _as_points(trajectory_points, "trajectory_points")
    raw_rgb = _as_colors(raw_colors, len(raw), "raw_colors")
    clean_rgb = _as_colors(clean_colors, len(clean), "clean_colors")
    removed_rgb = _as_colors(removed_colors, len(removed), "removed_colors")
    reason_bits = _as_reason_array(
        removed_reason_bits,
        len(removed),
        "removed_reason_bits",
    )
    primary = _as_reason_array(primary_reason, len(removed), "primary_reason")

    paths = {
        "top_before_after": output_path / "top_before_after.png",
        "side_before_after": output_path / "side_before_after.png",
        "removed_reason_top": output_path / "removed_reason_top.png",
        "representative_tiles": output_path / "representative_tiles.json",
        "run_summary": output_path / "run_summary.txt",
    }
    point_sets = (raw, clean, removed)
    color_sets = (raw_rgb, clean_rgb, removed_rgb)

    top_bounds = _projected_bounds(point_sets, "top")
    _write_comparison_image(
        paths["top_before_after"],
        point_sets,
        color_sets,
        ("Raw top view", "Clean top view", "Removed top view"),
        top_bounds,
        image_size,
        projection="top",
    )

    origin_xy, axis_xy = _side_projection_basis(trajectory, raw, clean, removed)
    side_bounds = _projected_bounds(
        point_sets,
        "side",
        origin_xy,
        axis_xy,
    )
    _write_comparison_image(
        paths["side_before_after"],
        point_sets,
        color_sets,
        ("Raw side view", "Clean side view", "Removed side view"),
        side_bounds,
        image_size,
        projection="side",
        origin_xy=origin_xy,
        axis_xy=axis_xy,
    )

    reason_image, removed_finite_count = _rasterize_points(
        removed,
        None,
        top_bounds,
        image_size[0] * 2,
        image_size[1],
        (255, 120, 70),
        projection="top",
        primary_reason=primary,
        reason_bits=reason_bits,
    )
    reason_panel = _label_panel(
        reason_image,
        "Removed points by primary reason",
        removed_finite_count,
    )
    reason_labels = dict(_DEFAULT_REASON_NAMES)
    if reason_names:
        reason_labels.update({int(code): str(label) for code, label in reason_names.items()})
    legend_y = 60
    for code, count in sorted(_reason_counts(removed, primary, reason_bits).items()):
        color = _REASON_COLORS_BY_CODE.get(code, (170, 175, 185))
        cv2.rectangle(reason_panel, (14, legend_y - 9), (25, legend_y + 2), color, -1)
        cv2.putText(
            reason_panel,
            f"{reason_labels.get(code, f'code_{code}')} ({count:,})",
            (32, legend_y + 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (235, 242, 250),
            1,
            cv2.LINE_AA,
        )
        legend_y += 18
    if not cv2.imwrite(
        str(paths["removed_reason_top"]),
        cv2.cvtColor(reason_panel, cv2.COLOR_RGB2BGR),
    ):
        raise OSError(f"failed to write diagnostic image: {paths['removed_reason_top']}")

    _write_representative_tiles(
        paths["representative_tiles"],
        raw,
        clean,
        removed,
        tile_size_m,
        representative_tile_count,
    )
    _write_run_summary(paths["run_summary"], len(raw), len(clean), len(removed), report)
    return paths


generate_postprocess_diagnostics = write_postprocess_diagnostics


__all__ = [
    "DebugProjectionContext",
    "generate_postprocess_diagnostics",
    "prepare_debug_projection_context",
    "write_debug_stage_diagnostics",
    "write_postprocess_diagnostics",
]
