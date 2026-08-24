from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal

import numpy as np
import scipy
from scipy.spatial import cKDTree

from .postprocess_config import PostprocessConfig


NeighborBackend = Literal["open3d", "scipy"]
GroundBackend = Literal["local", "pdal", "off"]


@dataclass(frozen=True)
class DependencyInfo:
    open3d_available: bool
    open3d_version: str | None
    scipy_available: bool
    scipy_version: str
    pdal_available: bool
    pdal_path: str | None
    pdal_version: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NeighborFilterResult:
    radius_outlier_mask: np.ndarray
    statistical_outlier_mask: np.ndarray
    neighbor_count: np.ndarray
    mean_neighbor_distance_m: np.ndarray
    core_evaluation_count: np.ndarray
    statistical_evaluation_count: np.ndarray
    distance_proxy_m: np.ndarray
    distance_proxy_source: str
    backend: str
    tile_count: int
    radius_seconds: float = 0.0
    statistical_seconds: float = 0.0


@dataclass(frozen=True)
class _TileLayout:
    points: np.ndarray
    tile_size_m: float
    overlap_m: float
    groups: dict[tuple[int, int], np.ndarray]

    def queries(self) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        reach = int(np.ceil(self.overlap_m / self.tile_size_m))
        for key in sorted(self.groups):
            core_indices = self.groups[key]
            candidate_groups: list[np.ndarray] = []
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    candidate = self.groups.get((key[0] + dx, key[1] + dy))
                    if candidate is not None:
                        candidate_groups.append(candidate)
            query_indices = np.sort(np.concatenate(candidate_groups))
            lower = np.asarray(key, dtype=np.float64) * self.tile_size_m
            upper = lower + self.tile_size_m
            xy = self.points[query_indices, :2]
            within_overlap = np.all(
                (xy >= lower - self.overlap_m)
                & (xy <= upper + self.overlap_m),
                axis=1,
            )
            yield core_indices, query_indices[within_overlap]


@lru_cache(maxsize=1)
def _open3d_module_and_version() -> tuple[object | None, str | None]:
    try:
        module = importlib.import_module("open3d")
    except Exception:
        return None, None
    return module, str(getattr(module, "__version__", "unknown"))


@lru_cache(maxsize=1)
def _pdal_info() -> tuple[str | None, str | None]:
    executable = shutil.which("pdal")
    if executable is None:
        return None, None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return executable, None
    version_text = (completed.stdout or completed.stderr).strip()
    return executable, version_text or None


def inspect_dependencies() -> DependencyInfo:
    _, open3d_version = _open3d_module_and_version()
    pdal_path, pdal_version = _pdal_info()
    return DependencyInfo(
        open3d_available=open3d_version is not None,
        open3d_version=open3d_version,
        scipy_available=True,
        scipy_version=str(scipy.__version__),
        pdal_available=pdal_path is not None,
        pdal_path=pdal_path,
        pdal_version=pdal_version,
    )


def resolve_neighbor_backend(requested: str) -> NeighborBackend:
    choice = str(requested).lower()
    if choice not in {"auto", "open3d", "scipy"}:
        raise ValueError("neighbor backend must be one of: auto, open3d, scipy")
    module, _ = _open3d_module_and_version()
    if choice == "auto":
        return "open3d" if module is not None else "scipy"
    if choice == "open3d" and module is None:
        raise RuntimeError(
            "neighbor backend 'open3d' was requested, but Open3D could not be imported"
        )
    return choice  # type: ignore[return-value]


def resolve_ground_backend(requested: str) -> GroundBackend:
    choice = str(requested).lower()
    if choice not in {"auto", "local", "pdal", "off"}:
        raise ValueError("ground backend must be one of: auto, local, pdal, off")
    if choice == "auto":
        # Local is deterministic across hosts. PDAL, when present, is comparison-only.
        return "local"
    if choice == "pdal" and _pdal_info()[0] is None:
        raise RuntimeError(
            "ground backend 'pdal' was requested, but the pdal executable was not found"
        )
    return choice  # type: ignore[return-value]


def make_pdal_pipeline(
    input_ply: str | Path,
    output_ply: str | Path,
    config: PostprocessConfig,
) -> dict[str, object]:
    """Return a conservative ELM/SMRF/HAG comparison pipeline for a PDAL CLI."""

    return {
        "pipeline": [
            {"type": "readers.ply", "filename": str(input_ply)},
            {"type": "filters.assign", "assignment": "Classification[:]=0"},
            {"type": "filters.elm", "cell": 0.30, "threshold": 0.20},
            {
                "type": "filters.smrf",
                "where": "!(Classification == 7)",
                "cell": min(0.50, config.ground_grid_size_m),
                "scalar": 1.2,
                "slope": 0.15,
                "threshold": config.below_ground_tolerance_m,
                "window": 8.0,
            },
            {"type": "filters.hag_nn"},
            {
                "type": "filters.expression",
                "expression": (
                    "Classification != 7 && HeightAboveGround >= "
                    f"{-config.below_ground_tolerance_m:.9g}"
                ),
            },
            {"type": "writers.ply", "filename": str(output_ply)},
        ]
    }


def run_pdal_comparison(
    input_ply: str | Path,
    diagnostics_dir: str | Path,
    config: PostprocessConfig,
) -> dict[str, object]:
    """Run the optional PDAL comparison without changing the local selection.

    PDAL's PLY writer does not guarantee preservation of a stable source index,
    so this path deliberately remains comparison-only unless a future indexed
    interchange format enables all local coverage guards to be reproduced.
    """

    executable, version = _pdal_info()
    if executable is None:
        return {"status": "not_run_unavailable", "selected": False}
    target_dir = Path(diagnostics_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output_ply = target_dir / "pdal_clean_comparison.ply"
    pipeline_path = target_dir / "pdal_postprocess_pipeline.json"
    temporary = pipeline_path.with_name(f"{pipeline_path.name}.tmp")
    temporary.write_text(
        json.dumps(
            make_pdal_pipeline(input_ply, output_ply, config),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(pipeline_path)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [executable, "pipeline", str(pipeline_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "failed",
            "selected": False,
            "seconds": time.perf_counter() - started,
            "error": str(exc),
            "pipeline": pipeline_path.name,
        }
    result: dict[str, object] = {
        "status": (
            "completed_comparison_not_selected"
            if completed.returncode == 0 and output_ply.is_file()
            else "failed"
        ),
        "selected": False,
        "seconds": time.perf_counter() - started,
        "returncode": int(completed.returncode),
        "version": version,
        "pipeline": pipeline_path.name,
        "output": output_ply.name if output_ply.is_file() else None,
        "selection_note": (
            "PDAL PLY lacks a guaranteed source index, so the full local quality "
            "guards cannot be reproduced; the deterministic local result remains selected."
        ),
    }
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        result["error"] = message[-2000:]
    return result


def _validate_points(points_enu_m: np.ndarray) -> np.ndarray:
    points = np.asarray(points_enu_m)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if not np.issubdtype(points.dtype, np.number):
        raise ValueError("points_enu_m must be numeric")
    if not np.all(np.isfinite(points)):
        raise ValueError("neighbor filtering requires finite points")
    return points


def _tile_layout(
    points: np.ndarray, tile_size_m: float, overlap_m: float
) -> _TileLayout:
    if len(points) == 0:
        return _TileLayout(points, tile_size_m, overlap_m, {})
    keys = np.floor(points[:, :2] / tile_size_m).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    sorted_keys = keys[order]
    starts = np.empty(len(points), dtype=bool)
    starts[0] = True
    starts[1:] = np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)
    boundaries = np.flatnonzero(starts)
    ends = np.append(boundaries[1:], len(points))
    groups = {
        (int(sorted_keys[start, 0]), int(sorted_keys[start, 1])): np.sort(
            order[start:end]
        )
        for start, end in zip(boundaries, ends, strict=True)
    }
    return _TileLayout(points, tile_size_m, overlap_m, groups)


def distance_to_trajectory_xy(
    points_enu_m: np.ndarray,
    trajectory_enu_m: np.ndarray | None,
    *,
    chunk_size: int = 1_000_000,
) -> np.ndarray:
    points = np.asarray(points_enu_m)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if trajectory_enu_m is None:
        return np.zeros(len(points), dtype=np.float32)
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory_enu_m must have shape (M, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) == 0:
        return np.zeros(len(points), dtype=np.float32)
    tree = cKDTree(trajectory[:, :2])
    result = np.empty(len(points), dtype=np.float32)
    for begin in range(0, len(points), chunk_size):
        end = min(begin + chunk_size, len(points))
        distances, _ = tree.query(points[begin:end, :2], k=1, workers=1)
        result[begin:end] = distances
    return result


def _distance_proxy(
    points: np.ndarray,
    mean_depth_m: np.ndarray | None,
    trajectory_enu_m: np.ndarray | None,
) -> tuple[np.ndarray, str]:
    if mean_depth_m is not None:
        depth = np.asarray(mean_depth_m)
        if depth.shape != (len(points),):
            raise ValueError("mean_depth_m must have shape (N,)")
        usable = np.isfinite(depth) & (depth >= 0.0)
        if np.all(usable):
            return depth.astype(np.float32, copy=False), "mean_depth_m"
        fallback = distance_to_trajectory_xy(points, trajectory_enu_m)
        fallback[usable] = depth[usable]
        return fallback, "mean_depth_m_with_trajectory_fallback"
    if trajectory_enu_m is not None:
        return distance_to_trajectory_xy(points, trajectory_enu_m), "trajectory_xy"
    return np.zeros(len(points), dtype=np.float32), "unavailable_assumed_near"


def _radius_parameters(
    distance_proxy_m: np.ndarray, config: PostprocessConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tier = np.zeros(len(distance_proxy_m), dtype=np.uint8)
    tier[distance_proxy_m >= 10.0] = 1
    tier[distance_proxy_m >= 20.0] = 2
    radius = np.full(len(tier), config.radius_outlier_radius_m, dtype=np.float32)
    radius[tier == 1] *= 1.25
    radius[tier == 2] *= 1.50
    minimum = np.full(
        len(tier), config.radius_outlier_min_neighbors, dtype=np.int32
    )
    minimum[tier == 2] = max(2, config.radius_outlier_min_neighbors - 1)
    return radius, minimum, tier


def _scipy_radius_tile(
    points: np.ndarray,
    query_indices: np.ndarray,
    core_indices: np.ndarray,
    radius: np.ndarray,
    minimum: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    tree = cKDTree(points[query_indices])
    counts_with_self = tree.query_ball_point(
        points[core_indices], r=radius[core_indices], return_length=True
    )
    counts = np.maximum(np.asarray(counts_with_self, dtype=np.int64) - 1, 0)
    return counts.astype(np.int32), counts < minimum[core_indices]


def _open3d_radius_tile(
    points: np.ndarray,
    query_indices: np.ndarray,
    core_indices: np.ndarray,
    tier: np.ndarray,
    radius: np.ndarray,
    minimum: np.ndarray,
) -> np.ndarray:
    module, _ = _open3d_module_and_version()
    if module is None:  # pragma: no cover - guarded by resolver
        raise RuntimeError("Open3D is unavailable")
    point_cloud = module.geometry.PointCloud()
    point_cloud.points = module.utility.Vector3dVector(points[query_indices])
    core_local = np.searchsorted(query_indices, core_indices)
    outlier = np.zeros(len(core_indices), dtype=bool)
    core_tier = tier[core_indices]
    for tier_value in np.unique(core_tier):
        selected_core = core_tier == tier_value
        representative = int(np.flatnonzero(selected_core)[0])
        _, inlier_local = point_cloud.remove_radius_outlier(
            nb_points=int(minimum[core_indices[representative]]) + 1,
            radius=float(radius[core_indices[representative]]),
        )
        inlier = np.zeros(len(query_indices), dtype=bool)
        inlier[np.asarray(inlier_local, dtype=np.int64)] = True
        outlier[selected_core] = ~inlier[core_local[selected_core]]
    return outlier


def _mean_knn_distances(
    points: np.ndarray, query_indices: np.ndarray, neighbor_count: int
) -> np.ndarray:
    count = len(query_indices)
    if count < 2:
        return np.full(count, np.nan, dtype=np.float64)
    k = min(int(neighbor_count) + 1, count)
    tree = cKDTree(points[query_indices])
    distances, _ = tree.query(points[query_indices], k=k, workers=1)
    if distances.ndim == 1:
        return np.full(count, np.nan, dtype=np.float64)
    return np.mean(distances[:, 1:], axis=1)


def _open3d_statistical_tile(
    points: np.ndarray,
    query_indices: np.ndarray,
    core_indices: np.ndarray,
    neighbor_count: int,
    std_ratio: float,
) -> np.ndarray:
    module, _ = _open3d_module_and_version()
    if module is None:  # pragma: no cover - guarded by resolver
        raise RuntimeError("Open3D is unavailable")
    point_cloud = module.geometry.PointCloud()
    point_cloud.points = module.utility.Vector3dVector(points[query_indices])
    _, inlier_local = point_cloud.remove_statistical_outlier(
        nb_neighbors=min(int(neighbor_count), len(query_indices) - 1),
        std_ratio=float(std_ratio),
    )
    inlier = np.zeros(len(query_indices), dtype=bool)
    inlier[np.asarray(inlier_local, dtype=np.int64)] = True
    core_local = np.searchsorted(query_indices, core_indices)
    return ~inlier[core_local]


def tiled_neighbor_filter(
    points_enu_m: np.ndarray,
    config: PostprocessConfig,
    *,
    backend: str = "auto",
    mean_depth_m: np.ndarray | None = None,
    trajectory_enu_m: np.ndarray | None = None,
) -> NeighborFilterResult:
    """Run radius then statistical filtering with one core decision per point.

    A per-tile KD-tree is built from the core plus overlap. No full-cloud 3-D
    neighbor index is constructed. ``neighbor_count`` excludes the query point
    itself for both backends.
    """

    points = _validate_points(points_enu_m)
    selected_backend = resolve_neighbor_backend(backend)
    count = len(points)
    empty_bool = np.zeros(count, dtype=bool)
    if count == 0 or not config.enabled:
        return NeighborFilterResult(
            radius_outlier_mask=empty_bool.copy(),
            statistical_outlier_mask=empty_bool.copy(),
            neighbor_count=np.zeros(count, dtype=np.int32),
            mean_neighbor_distance_m=np.full(count, np.nan, dtype=np.float32),
            core_evaluation_count=np.zeros(count, dtype=np.uint8),
            statistical_evaluation_count=np.zeros(count, dtype=np.uint8),
            distance_proxy_m=np.zeros(count, dtype=np.float32),
            distance_proxy_source="disabled",
            backend=selected_backend,
            tile_count=0,
        )

    radius_started = time.perf_counter()
    distance_proxy, distance_source = _distance_proxy(
        points, mean_depth_m, trajectory_enu_m
    )
    radius, minimum, tier = _radius_parameters(distance_proxy, config)
    layout = _tile_layout(points, config.tile_size_m, config.tile_overlap_m)
    radius_outlier = np.zeros(count, dtype=bool)
    statistical_outlier = np.zeros(count, dtype=bool)
    neighbor_counts = np.zeros(count, dtype=np.int32)
    mean_distances = np.full(count, np.nan, dtype=np.float32)
    core_evaluations = np.zeros(count, dtype=np.uint8)
    statistical_evaluations = np.zeros(count, dtype=np.uint8)

    # Radius pass. All points receive exactly one core decision.
    for core_indices, query_indices in layout.queries():
        core_evaluations[core_indices] += 1
        counts, scipy_outlier = _scipy_radius_tile(
            points, query_indices, core_indices, radius, minimum
        )
        neighbor_counts[core_indices] = counts
        enough_points = len(query_indices) > int(np.max(minimum[core_indices]))
        if not enough_points:
            continue
        if selected_backend == "open3d":
            radius_outlier[core_indices] = _open3d_radius_tile(
                points,
                query_indices,
                core_indices,
                tier,
                radius,
                minimum,
            )
        else:
            radius_outlier[core_indices] = scipy_outlier

    if np.any(core_evaluations != 1):
        raise RuntimeError("tile core partition did not evaluate every point exactly once")
    radius_seconds = time.perf_counter() - radius_started

    # Statistical pass only sees points that passed radius, including overlap.
    statistical_started = time.perf_counter()
    for core_indices, query_indices in layout.queries():
        core_pass = core_indices[~radius_outlier[core_indices]]
        if len(core_pass) == 0:
            continue
        query_pass = query_indices[~radius_outlier[query_indices]]
        statistical_evaluations[core_pass] += 1
        if len(query_pass) < 3:
            continue
        means = _mean_knn_distances(points, query_pass, config.statistical_neighbors)
        core_local = np.searchsorted(query_pass, core_pass)
        mean_distances[core_pass] = means[core_local]
        if selected_backend == "open3d":
            statistical_outlier[core_pass] = _open3d_statistical_tile(
                points,
                query_pass,
                core_pass,
                config.statistical_neighbors,
                config.statistical_std_ratio,
            )
        else:
            finite_means = means[np.isfinite(means)]
            if len(finite_means) < 2:
                continue
            threshold = float(np.mean(finite_means)) + config.statistical_std_ratio * float(
                np.std(finite_means)
            )
            statistical_outlier[core_pass] = means[core_local] > threshold

    if np.any(statistical_evaluations[~radius_outlier] != 1):
        raise RuntimeError("statistical core partition did not evaluate each survivor once")
    statistical_seconds = time.perf_counter() - statistical_started
    return NeighborFilterResult(
        radius_outlier_mask=radius_outlier,
        statistical_outlier_mask=statistical_outlier,
        neighbor_count=neighbor_counts,
        mean_neighbor_distance_m=mean_distances,
        core_evaluation_count=core_evaluations,
        statistical_evaluation_count=statistical_evaluations,
        distance_proxy_m=distance_proxy,
        distance_proxy_source=distance_source,
        backend=selected_backend,
        tile_count=len(layout.groups),
        radius_seconds=float(radius_seconds),
        statistical_seconds=float(statistical_seconds),
    )
