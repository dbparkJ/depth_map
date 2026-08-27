from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


_PLY_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
    ]
)

_STAGE_FILENAMES = {
    "raw": "cloud_raw_enu.ply",
    "clean": "cloud_clean_enu.ply",
    "removed": "cloud_removed_enu.ply",
}


@dataclass(frozen=True)
class MappingBundle:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    trajectory_enu_m: np.ndarray
    point_metadata: dict[str, np.ndarray]
    summary: dict[str, Any]
    source_path: Path


def _read_ply_header(path: Path) -> tuple[int, int, list[str]]:
    lines: list[str] = []
    count: int | None = None
    with path.open("rb") as stream:
        while True:
            raw = stream.readline()
            if not raw:
                raise ValueError(f"PLY header is incomplete: {path}")
            try:
                line = raw.decode("ascii").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise ValueError(f"PLY header is not ASCII: {path}") from exc
            lines.append(line)
            if line.startswith("element vertex "):
                count = int(line.split()[-1])
            if line == "end_header":
                offset = stream.tell()
                break
    if not lines or lines[0] != "ply":
        raise ValueError(f"not a PLY file: {path}")
    if "format binary_little_endian 1.0" not in lines:
        raise ValueError("only binary_little_endian PLY 1.0 is supported")
    expected_properties = [
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
    ]
    for property_line in expected_properties:
        if property_line not in lines:
            raise ValueError(f"PLY is missing {property_line!r}")
    if count is None or count < 0:
        raise ValueError("PLY vertex count is missing or invalid")
    expected_size = offset + count * _PLY_DTYPE.itemsize
    actual_size = path.stat().st_size
    if expected_size != actual_size:
        raise ValueError(
            f"PLY size mismatch for {path}: expected {expected_size}, got {actual_size}"
        )
    return count, offset, lines


def read_depth_map_ply(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read the fixed XYZ/RGB PLY layout produced by ``depth_map``.

    The function intentionally rejects arbitrary PLY variants instead of guessing
    property order. This protects large offline jobs from silently interpreting a
    different binary layout as valid XYZ/RGB data.
    """

    resolved = Path(path).expanduser().resolve()
    count, offset, _lines = _read_ply_header(resolved)
    if count == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.uint8),
        )
    records = np.memmap(
        resolved,
        mode="r",
        dtype=_PLY_DTYPE,
        offset=offset,
        shape=(count,),
    )
    points = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float32,
        copy=False,
    )
    colors = np.column_stack((records["r"], records["g"], records["b"])).astype(
        np.uint8,
        copy=False,
    )
    del records
    return np.asarray(points), np.asarray(colors)


def _load_numeric_npz(path: Path, expected_count: int) -> dict[str, np.ndarray]:
    if not path.is_file():
        return {}
    result: dict[str, np.ndarray] = {}
    with np.load(path, allow_pickle=False) as archive:
        for name in archive.files:
            value = np.asarray(archive[name])
            if value.dtype.hasobject:
                raise ValueError(f"metadata array {name!r} uses object dtype")
            if value.shape != (expected_count,):
                # Diagnostic-only arrays that are not point aligned are ignored.
                continue
            result[name] = value
    return result


def load_mapping_bundle(
    output_dir: str | Path,
    *,
    stage: str = "raw",
) -> MappingBundle:
    resolved = Path(output_dir).expanduser().resolve()
    data_dir = resolved / "data"
    if stage not in _STAGE_FILENAMES:
        raise ValueError(f"stage must be one of: {', '.join(_STAGE_FILENAMES)}")
    required = {
        "point cloud": data_dir / _STAGE_FILENAMES[stage],
        "trajectory": data_dir / "trajectory.json",
        "summary": data_dir / "summary.json",
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("mapping bundle is incomplete; missing " + ", ".join(missing))

    points, colors = read_depth_map_ply(required["point cloud"])
    trajectory_payload = json.loads(required["trajectory"].read_text(encoding="utf-8"))
    trajectory = np.asarray(trajectory_payload.get("fused"), dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,) or len(trajectory) < 2:
        raise ValueError("trajectory.json fused must contain at least two XYZ points")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        raise ValueError("trajectory has fewer than two finite points")

    summary = json.loads(required["summary"].read_text(encoding="utf-8"))
    metadata_path = data_dir / "cloud_raw_metadata.npz"
    metadata = _load_numeric_npz(metadata_path, len(points)) if stage == "raw" else {}
    return MappingBundle(
        points_enu_m=points,
        colors_rgb=colors,
        trajectory_enu_m=trajectory,
        point_metadata=metadata,
        summary=summary,
        source_path=resolved,
    )


def resolve_relative_path(root: str | Path, user_path: str) -> Path:
    """Resolve a user-provided relative path under a configured read-only root."""

    if not user_path or user_path.strip() in {".", "./"}:
        candidate = Path(root).expanduser().resolve()
    else:
        raw = Path(user_path)
        if raw.is_absolute():
            raise ValueError("mapping_output_path must be relative to the workspace root")
        candidate = (Path(root).expanduser().resolve() / raw).resolve()
    root_resolved = Path(root).expanduser().resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("mapping_output_path escapes the workspace root") from exc
    return candidate
