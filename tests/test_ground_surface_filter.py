from __future__ import annotations

import numpy as np

from rgbd_map.ground_surface import estimate_local_ground_surface
from rgbd_map.postprocess_config import resolve_postprocess_config


def test_local_surface_preserves_slope_and_outside_corridor_but_removes_low_tail():
    road: list[list[float]] = []
    below: list[list[float]] = []
    poles: list[list[float]] = []
    for ix in range(-2, 3):
        for iy in range(-1, 2):
            x = (ix + 0.5) * 0.5
            y = (iy + 0.5) * 0.5
            surface_z = 0.05 * x
            for sample in range(12):
                road.append(
                    [
                        x + ((sample % 3) - 1) * 0.01,
                        y + (((sample // 3) % 3) - 1) * 0.01,
                        surface_z + ((sample % 2) - 0.5) * 0.006,
                    ]
                )
            below.append([x, y, surface_z - 0.50])
            poles.append([x, y, surface_z + 1.0])
    outside_low = [[-0.25, 5.0, -1.0], [0.25, 5.0, -1.1]]
    points = np.asarray(road + below + poles + outside_low, dtype=np.float64)
    trajectory_x = np.linspace(-2.0, 2.0, 25)
    trajectory = np.column_stack(
        (trajectory_x, np.zeros_like(trajectory_x), 1.5 + 0.05 * trajectory_x)
    )
    config = resolve_postprocess_config(
        "road-map",
        0.05,
        {
            "road_corridor_half_width_m": 2.0,
            "ground_grid_size_m": 0.5,
            "ground_z_bin_m": 0.05,
            "ground_min_cell_points": 5,
            "ground_min_neighbor_cells": 3,
            "below_ground_tolerance_m": 0.20,
        },
    )

    result = estimate_local_ground_surface(points, trajectory, config)
    road_end = len(road)
    below_end = road_end + len(below)
    poles_end = below_end + len(poles)

    assert not np.any(result.below_surface_mask[:road_end])
    assert np.all(result.below_surface_mask[road_end:below_end])
    assert not np.any(result.below_surface_mask[below_end:poles_end])
    assert not np.any(result.below_surface_mask[poles_end:])
    assert result.stats.valid_surface_cell_count > 0
    assert result.stats.below_surface_removed_count == len(below)
