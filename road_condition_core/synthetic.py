from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SyntheticScene:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    trajectory_enu_m: np.ndarray
    truth: dict[str, Any]
    source_origin: dict[str, float]


def _elliptical_shape(
    s: np.ndarray,
    t: np.ndarray,
    *,
    center_s: float,
    center_t: float,
    radius_s: float,
    radius_t: float,
    amplitude_m: float,
) -> np.ndarray:
    radius_squared = (
        ((s - center_s) / radius_s) ** 2 + ((t - center_t) / radius_t) ** 2
    )
    inside = radius_squared < 1.0
    value = np.zeros_like(s, dtype=np.float64)
    value[inside] = amplitude_m * (1.0 - radius_squared[inside]) ** 2
    return value


def _smooth_window(s: np.ndarray, start_m: float, end_m: float, edge_m: float = 2.0) -> np.ndarray:
    left = np.clip((s - start_m) / max(edge_m, 1e-9), 0.0, 1.0)
    right = np.clip((end_m - s) / max(edge_m, 1e-9), 0.0, 1.0)
    return np.minimum(0.5 - 0.5 * np.cos(np.pi * left), 0.5 - 0.5 * np.cos(np.pi * right))


def generate_synthetic_scene(
    profile: str = "mixed",
    *,
    length_m: float = 60.0,
    half_width_m: float = 3.5,
    resolution_m: float = 0.10,
    observations_per_cell: int = 4,
    seed: int = 7,
) -> SyntheticScene:
    """Generate a deterministic road surface for Docker and regression smoke tests."""

    if profile not in {"flat", "potholes", "rutting", "mixed"}:
        raise ValueError("profile must be flat, potholes, rutting, or mixed")
    if length_m <= 5 or half_width_m <= 1 or resolution_m <= 0:
        raise ValueError("synthetic scene dimensions are invalid")
    if observations_per_cell < 1:
        raise ValueError("observations_per_cell must be positive")

    rng = np.random.default_rng(seed)
    s_axis = np.arange(0.5 * resolution_m, length_m, resolution_m)
    t_axis = np.arange(-half_width_m + 0.5 * resolution_m, half_width_m, resolution_m)
    s_grid, t_grid = np.meshgrid(s_axis, t_axis, indexing="ij")

    center_z = 100.0 + 0.003 * s_grid + 0.004 * np.sin(s_grid / 13.0)
    road_z = center_z + 0.018 * t_grid + 0.0012 * t_grid * t_grid
    defect_delta = np.zeros_like(road_z)
    truth_defects: list[dict[str, Any]] = []

    if profile in {"potholes", "mixed"}:
        potholes = [
            {
                "center_s_m": 12.0,
                "center_t_m": -0.45,
                "radius_s_m": 0.85,
                "radius_t_m": 0.62,
                "depth_m": 0.115,
            },
            {
                "center_s_m": 44.0,
                "center_t_m": 1.25,
                "radius_s_m": 0.68,
                "radius_t_m": 0.48,
                "depth_m": 0.072,
            },
        ]
        for pothole in potholes:
            defect_delta -= _elliptical_shape(
                s_grid,
                t_grid,
                center_s=pothole["center_s_m"],
                center_t=pothole["center_t_m"],
                radius_s=pothole["radius_s_m"],
                radius_t=pothole["radius_t_m"],
                amplitude_m=pothole["depth_m"],
            )
            truth_defects.append({"type": "pothole", **pothole})

    if profile in {"rutting", "mixed"}:
        window = _smooth_window(s_grid, 20.0, 52.0, edge_m=3.0)
        for center_t, depth in ((-0.90, 0.034), (0.90, 0.041)):
            rut = depth * np.exp(-0.5 * ((t_grid - center_t) / 0.18) ** 2) * window
            defect_delta -= rut
            truth_defects.append(
                {
                    "type": "rutting",
                    "center_t_m": center_t,
                    "start_s_m": 20.0,
                    "end_s_m": 52.0,
                    "depth_m": depth,
                }
            )

    if profile == "mixed":
        bump = {
            "center_s_m": 33.0,
            "center_t_m": 0.15,
            "radius_s_m": 1.15,
            "radius_t_m": 0.78,
            "height_m": 0.068,
        }
        defect_delta += _elliptical_shape(
            s_grid,
            t_grid,
            center_s=bump["center_s_m"],
            center_t=bump["center_t_m"],
            radius_s=bump["radius_s_m"],
            radius_t=bump["radius_t_m"],
            amplitude_m=bump["height_m"],
        )
        truth_defects.append({"type": "bump", **bump})

    surface_z = road_z + defect_delta
    base_s = np.repeat(s_grid.ravel(), observations_per_cell)
    base_t = np.repeat(t_grid.ravel(), observations_per_cell)
    base_z = np.repeat(surface_z.ravel(), observations_per_cell)
    jitter_xy = min(0.20 * resolution_m, 0.01)
    points = np.column_stack(
        (
            base_s + rng.normal(0.0, jitter_xy, size=len(base_s)),
            base_t + rng.normal(0.0, jitter_xy, size=len(base_t)),
            base_z + rng.normal(0.0, 0.0025, size=len(base_z)),
        )
    ).astype(np.float32)

    asphalt = np.array([82, 84, 88], dtype=np.float64)
    texture = rng.normal(0.0, 8.0, size=(len(points), 1))
    colors = np.clip(asphalt[None, :] + texture, 25, 150).astype(np.uint8)

    trajectory_s = np.arange(0.0, length_m + 0.25, 0.25)
    trajectory_center_z = 100.0 + 0.003 * trajectory_s + 0.004 * np.sin(trajectory_s / 13.0)
    trajectory = np.column_stack(
        (
            trajectory_s,
            np.zeros_like(trajectory_s),
            trajectory_center_z + 1.50,
        )
    ).astype(np.float64)

    return SyntheticScene(
        points_enu_m=points,
        colors_rgb=colors,
        trajectory_enu_m=trajectory,
        truth={
            "profile": profile,
            "length_m": length_m,
            "half_width_m": half_width_m,
            "resolution_m": resolution_m,
            "defects": truth_defects,
        },
        source_origin={
            "longitude_deg": 127.000000,
            "latitude_deg": 37.000000,
            "ellipsoid_height_m": 50.0,
        },
    )
