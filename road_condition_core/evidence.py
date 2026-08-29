from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .geometry import project_to_trajectory


_RGBD_HEADER = struct.Struct("<4sIII")
_RGBD_RECORD_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
        ("a", "u1"),
    ]
)
_RCEV_HEADER = struct.Struct("<4sIII6f6I")
_RCEV_RECORD_DTYPE = np.dtype(
    [
        ("qx", "<u2"),
        ("qy", "<u2"),
        ("qz", "<u2"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
        ("defect_class", "u1"),
        ("defect_index", "<u2"),
    ]
)
_NO_DEFECT = np.iinfo(np.uint16).max
_DEFECT_CLASS = {"pothole": 1, "rutting": 2, "bump": 3}
_MAX_SOURCE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class EvidenceConfig:
    max_points_per_tile: int = 60_000
    surface_band_tolerance_m: float = 0.15
    tile_context_m: float = 1.0
    context_point_stage: str = "clean"

    def validate(self) -> None:
        if self.max_points_per_tile < 1_000:
            raise ValueError("max_points_per_tile must be at least 1,000")
        if not np.isfinite(self.surface_band_tolerance_m) or not (
            0.02 <= self.surface_band_tolerance_m <= 0.50
        ):
            raise ValueError("surface_band_tolerance_m must be within [0.02, 0.50]")
        if not np.isfinite(self.tile_context_m) or not (0.0 <= self.tile_context_m <= 5.0):
            raise ValueError("tile_context_m must be within [0, 5]")
        if self.context_point_stage not in {"raw", "clean"}:
            raise ValueError("context_point_stage must be raw or clean")


@dataclass(frozen=True)
class EvidenceTile:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    defect_class: np.ndarray
    defect_index: np.ndarray
    bbox_min_enu_m: np.ndarray
    quantization_scale_m: np.ndarray


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _read_trajectory(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trajectory = np.asarray(payload.get("fused"), dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory.json fused must have shape (N, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        raise ValueError("trajectory.json must contain at least two finite points")
    return trajectory


def read_rgbd_browser_points(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    size = resolved.stat().st_size
    if size < _RGBD_HEADER.size or size > _MAX_SOURCE_BYTES:
        raise ValueError("RGBD browser sample size is outside the allowed range")
    with resolved.open("rb") as stream:
        magic, version, count, stride = _RGBD_HEADER.unpack(stream.read(_RGBD_HEADER.size))
        if magic != b"RGBD" or version != 1 or stride != _RGBD_RECORD_DTYPE.itemsize:
            raise ValueError("unsupported RGBD browser point format")
        expected = _RGBD_HEADER.size + int(count) * int(stride)
        if expected != size:
            raise ValueError("RGBD browser point payload size mismatch")
        records = np.fromfile(stream, dtype=_RGBD_RECORD_DTYPE, count=count)
    points = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float32,
        copy=False,
    )
    colors = np.column_stack((records["r"], records["g"], records["b"])).astype(
        np.uint8,
        copy=False,
    )
    return points, colors


def write_evidence_tile(
    path: str | Path,
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    defect_class: np.ndarray,
    defect_index: np.ndarray,
) -> dict[str, Any]:
    points = np.asarray(points_enu_m, dtype=np.float64)
    colors = np.asarray(colors_rgb, dtype=np.uint8)
    classes = np.asarray(defect_class, dtype=np.uint8)
    indices = np.asarray(defect_index, dtype=np.uint16)
    count = len(points)
    if points.shape != (count, 3) or colors.shape != (count, 3):
        raise ValueError("evidence points/colors must have aligned shape (N, 3)")
    if classes.shape != (count,) or indices.shape != (count,):
        raise ValueError("evidence defect arrays must align with points")
    if count == 0 or not np.all(np.isfinite(points)):
        raise ValueError("evidence points must be non-empty and finite")
    bbox_min = np.min(points, axis=0)
    bbox_max = np.max(points, axis=0)
    extent = bbox_max - bbox_min
    scale = np.where(extent > 0.0, extent / 65535.0, 1e-6)
    quantized = np.rint((points - bbox_min) / scale).clip(0, 65535).astype(np.uint16)
    records = np.empty(count, dtype=_RCEV_RECORD_DTYPE)
    records["qx"], records["qy"], records["qz"] = quantized.T
    records["r"], records["g"], records["b"] = colors.T
    records["defect_class"] = classes
    records["defect_index"] = indices
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(resolved.name + ".tmp")
    header = _RCEV_HEADER.pack(
        b"RCEV",
        1,
        count,
        _RCEV_RECORD_DTYPE.itemsize,
        *bbox_min.astype(np.float32).tolist(),
        *scale.astype(np.float32).tolist(),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    with temporary.open("wb") as stream:
        stream.write(header)
        records.tofile(stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(resolved)
    reconstructed = bbox_min + quantized.astype(np.float64) * scale
    maximum_error = float(np.max(np.abs(reconstructed - points)))
    return {
        "format": "RCEV",
        "format_version": 1,
        "point_count": count,
        "byte_size": resolved.stat().st_size,
        "bbox_min_enu_m": bbox_min.tolist(),
        "bbox_max_enu_m": bbox_max.tolist(),
        "quantization_scale_m": scale.tolist(),
        "maximum_quantization_error_m": maximum_error,
        "masked_point_count": int(np.count_nonzero(indices != _NO_DEFECT)),
        "sha256": _sha256(resolved),
    }


def read_evidence_tile(path: str | Path) -> EvidenceTile:
    resolved = Path(path).expanduser().resolve()
    with resolved.open("rb") as stream:
        header = stream.read(_RCEV_HEADER.size)
        if len(header) != _RCEV_HEADER.size:
            raise ValueError("RCEV header is incomplete")
        values = _RCEV_HEADER.unpack(header)
        magic, version, count, stride = values[:4]
        if magic != b"RCEV" or version != 1 or stride != _RCEV_RECORD_DTYPE.itemsize:
            raise ValueError("unsupported RCEV evidence format")
        expected = _RCEV_HEADER.size + int(count) * int(stride)
        if resolved.stat().st_size != expected:
            raise ValueError("RCEV payload size mismatch")
        records = np.fromfile(stream, dtype=_RCEV_RECORD_DTYPE, count=count)
    bbox_min = np.asarray(values[4:7], dtype=np.float64)
    scale = np.asarray(values[7:10], dtype=np.float64)
    quantized = np.column_stack((records["qx"], records["qy"], records["qz"]))
    points = bbox_min + quantized.astype(np.float64) * scale
    colors = np.column_stack((records["r"], records["g"], records["b"])).astype(np.uint8)
    return EvidenceTile(
        points_enu_m=points,
        colors_rgb=colors,
        defect_class=np.asarray(records["defect_class"], dtype=np.uint8),
        defect_index=np.asarray(records["defect_index"], dtype=np.uint16),
        bbox_min_enu_m=bbox_min,
        quantization_scale_m=scale,
    )


def _point_in_polygon(s: np.ndarray, t: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    ring = np.asarray(polygon, dtype=np.float64)
    if ring.ndim != 2 or ring.shape[1:] != (2,) or len(ring) < 3:
        return np.zeros(len(s), dtype=bool)
    inside = np.zeros(len(s), dtype=bool)
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > t) != (y2 > t)
        x_intersection = (x2 - x1) * (t - y1) / (y2 - y1 + 1e-15) + x1
        inside ^= crosses & (s <= x_intersection)
        previous = current
    return inside


def _assign_defects(
    s: np.ndarray,
    t: np.ndarray,
    defects: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    classes = np.zeros(len(s), dtype=np.uint8)
    indices = np.full(len(s), _NO_DEFECT, dtype=np.uint16)
    metadata: list[dict[str, Any]] = []
    if len(defects) >= _NO_DEFECT:
        raise ValueError("a tile cannot contain 65,535 defects")
    for index, defect in enumerate(defects):
        polygon = np.asarray(defect.get("local_polygon_st_m") or [], dtype=np.float64)
        matched = _point_in_polygon(s, t, polygon) & (indices == _NO_DEFECT)
        defect_type = str(defect.get("defect_type") or "unknown")
        classes[matched] = _DEFECT_CLASS.get(defect_type, 4)
        indices[matched] = index
        metadata.append(
            {
                "index": index,
                "defect_id": str(defect.get("defect_id") or ""),
                "defect_type": defect_type,
                "severity": str(defect.get("severity") or "unknown"),
                "point_count": int(np.count_nonzero(matched)),
            }
        )
    return classes, indices, metadata


def _bounded_sample_indices(defect_index: np.ndarray, maximum: int) -> np.ndarray:
    count = len(defect_index)
    if count <= maximum:
        return np.arange(count, dtype=np.int64)
    masked = np.flatnonzero(defect_index != _NO_DEFECT)
    context = np.flatnonzero(defect_index == _NO_DEFECT)
    masked_limit = min(len(masked), maximum // 2)
    if len(masked) > masked_limit:
        masked = masked[np.linspace(0, len(masked) - 1, masked_limit, dtype=np.int64)]
    context_limit = maximum - len(masked)
    if len(context) > context_limit:
        context = context[np.linspace(0, len(context) - 1, context_limit, dtype=np.int64)]
    return np.sort(np.concatenate((masked, context)))


def _surface_band_mask(
    s: np.ndarray,
    t: np.ndarray,
    local_up: np.ndarray,
    surface_path: Path,
    tolerance_m: float,
) -> np.ndarray:
    with np.load(surface_path, allow_pickle=False) as archive:
        s_values = np.asarray(archive["s_values_m"], dtype=np.float64)
        t_values = np.asarray(archive["t_values_m"], dtype=np.float64)
        observed = np.asarray(archive["observed_local_up_m"], dtype=np.float64)
        supported = np.asarray(archive["supported_mask"], dtype=bool)
    if len(s_values) < 1 or len(t_values) < 1:
        return np.zeros(len(s), dtype=bool)
    grid_s = float(np.median(np.diff(s_values))) if len(s_values) > 1 else 0.1
    grid_t = float(np.median(np.diff(t_values))) if len(t_values) > 1 else 0.1
    s_index = np.rint((s - s_values[0]) / grid_s).astype(np.int64)
    t_index = np.rint((t - t_values[0]) / grid_t).astype(np.int64)
    bounds = (
        (s_index >= 0)
        & (s_index < len(s_values))
        & (t_index >= 0)
        & (t_index < len(t_values))
    )
    output = np.zeros(len(s), dtype=bool)
    selected = np.flatnonzero(bounds)
    cell_supported = supported[s_index[selected], t_index[selected]]
    selected = selected[cell_supported]
    if len(selected):
        surface = observed[s_index[selected], t_index[selected]]
        output[selected] = np.abs(local_up[selected] - surface) <= tolerance_m
    return output


def build_route_evidence(
    mapping_output: str | Path,
    route_output: str | Path,
    *,
    config: EvidenceConfig | None = None,
) -> dict[str, Any]:
    resolved_config = config or EvidenceConfig()
    resolved_config.validate()
    mapping = Path(mapping_output).expanduser().resolve()
    route = Path(route_output).expanduser().resolve()
    requested_source_path = mapping / "data" / f"points_{resolved_config.context_point_stage}.bin"
    source_stage = resolved_config.context_point_stage
    if not requested_source_path.is_file() and source_stage == "clean":
        source_stage = "raw"
        requested_source_path = mapping / "data" / "points_raw.bin"
    source_path = requested_source_path
    trajectory_path = mapping / "data" / "trajectory.json"
    route_manifest_path = route / "route_manifest.json"
    if not route_manifest_path.is_file():
        raise FileNotFoundError(route_manifest_path)
    points, colors = read_rgbd_browser_points(source_path)
    trajectory = _read_trajectory(trajectory_path)
    coordinates = project_to_trajectory(points, trajectory)
    local_up = points[:, 2].astype(np.float64) - coordinates.trajectory_z_m
    route_manifest = json.loads(route_manifest_path.read_text(encoding="utf-8"))
    output_root = route / "evidence"
    tiles: list[dict[str, Any]] = []
    for tile in route_manifest.get("tiles", []):
        tile_id = str(tile.get("tile_id") or "")
        if tile.get("state") != "completed" or not tile_id.startswith("tile-"):
            tiles.append({"tile_id": tile_id, "state": "unavailable"})
            continue
        result_root = route / "tiles" / tile_id / "result"
        defects = json.loads((result_root / "defects.json").read_text(encoding="utf-8"))
        core_start = float(tile["core_start_m"])
        core_end = float(tile["core_end_m"])
        spatial = (
            np.isfinite(coordinates.along_track_m)
            & np.isfinite(coordinates.signed_cross_track_m)
            & np.isfinite(local_up)
            & (coordinates.along_track_m >= core_start - resolved_config.tile_context_m)
            & (coordinates.along_track_m <= core_end + resolved_config.tile_context_m)
        )
        spatial_indices = np.flatnonzero(spatial)
        surface = _surface_band_mask(
            coordinates.along_track_m[spatial_indices],
            coordinates.signed_cross_track_m[spatial_indices],
            local_up[spatial_indices],
            result_root / "surface.npz",
            resolved_config.surface_band_tolerance_m,
        )
        selected = spatial_indices[surface]
        if len(selected) == 0:
            tiles.append({"tile_id": tile_id, "state": "empty"})
            continue
        classes, defect_indices, defect_metadata = _assign_defects(
            coordinates.along_track_m[selected],
            coordinates.signed_cross_track_m[selected],
            defects,
        )
        keep = _bounded_sample_indices(defect_indices, resolved_config.max_points_per_tile)
        selected = selected[keep]
        classes = classes[keep]
        defect_indices = defect_indices[keep]
        tile_path = output_root / "tiles" / f"{tile_id}.rcev"
        encoded = write_evidence_tile(
            tile_path,
            points[selected],
            colors[selected],
            classes,
            defect_indices,
        )
        retained_counts = np.bincount(
            defect_indices[defect_indices != _NO_DEFECT],
            minlength=len(defect_metadata),
        )
        for item in defect_metadata:
            item["point_count"] = int(retained_counts[item["index"]])
        tiles.append(
            {
                "tile_id": tile_id,
                "state": "completed",
                "core_start_m": core_start,
                "core_end_m": core_end,
                "artifact": f"tiles/{tile_id}.rcev",
                "defects": defect_metadata,
                **encoded,
            }
        )
    mapping_summary = json.loads((mapping / "data" / "summary.json").read_text(encoding="utf-8"))
    manifest = {
        "format_version": 1,
        "evidence_contract": "road-condition-rcev-v1",
        "coordinate_system": "local_ENU_metres",
        "source": {
            "point_stage": f"{source_stage}_browser_spatial_sample",
            "point_count": int(len(points)),
            "sha256": _sha256(source_path),
        },
        "origin": mapping_summary.get("origin"),
        "config": asdict(resolved_config),
        "tile_count": len(tiles),
        "completed_tile_count": sum(item.get("state") == "completed" for item in tiles),
        "tiles": tiles,
        "limitations": [
            "This is a spatially sampled RGB-D point evidence layer, not the full PLY.",
            "Damage masks visualize geometry candidates and do not confirm surveyed distress.",
        ],
    }
    _atomic_json(output_root / "manifest.json", manifest)
    return manifest
