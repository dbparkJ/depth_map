from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np

from .contracts import attach_prediction_audit
from .metrics import connected_components


def accumulate_bev_probability(
    frames: Iterable[Mapping[str, Any]],
    *,
    s_min_m: float,
    s_max_m: float,
    t_min_m: float,
    t_max_m: float,
    grid_size_m: float,
) -> dict[str, np.ndarray]:
    if grid_size_m <= 0 or s_max_m <= s_min_m or t_max_m <= t_min_m:
        raise ValueError("invalid BEV grid bounds")
    s_count = int(math.ceil((s_max_m - s_min_m) / grid_size_m))
    t_count = int(math.ceil((t_max_m - t_min_m) / grid_size_m))
    probability_sum = np.zeros((s_count, t_count), dtype=np.float64)
    observation_count = np.zeros((s_count, t_count), dtype=np.uint32)
    accepted_frames = 0
    rejected_pose_frames = 0
    for frame in frames:
        if not bool(frame.get("pose_valid", False)):
            rejected_pose_frames += 1
            continue
        probability = np.asarray(frame["probability"], dtype=np.float64)
        s_coordinates = np.asarray(frame["s_m"], dtype=np.float64)
        t_coordinates = np.asarray(frame["t_m"], dtype=np.float64)
        road_mask = np.asarray(frame["road_mask"], dtype=bool)
        depth_valid = np.asarray(frame["depth_valid"], dtype=bool)
        shapes = {
            probability.shape,
            s_coordinates.shape,
            t_coordinates.shape,
            road_mask.shape,
            depth_valid.shape,
        }
        if len(shapes) != 1:
            raise ValueError("frame probability, projection, and masks must share a shape")
        valid = (
            road_mask
            & depth_valid
            & np.isfinite(probability)
            & np.isfinite(s_coordinates)
            & np.isfinite(t_coordinates)
            & (probability >= 0.0)
            & (probability <= 1.0)
        )
        s_index = np.floor((s_coordinates[valid] - s_min_m) / grid_size_m).astype(int)
        t_index = np.floor((t_coordinates[valid] - t_min_m) / grid_size_m).astype(int)
        inside = (
            (s_index >= 0)
            & (s_index < s_count)
            & (t_index >= 0)
            & (t_index < t_count)
        )
        np.add.at(
            probability_sum,
            (s_index[inside], t_index[inside]),
            probability[valid][inside],
        )
        np.add.at(observation_count, (s_index[inside], t_index[inside]), 1)
        accepted_frames += 1
    probability = np.full_like(probability_sum, np.nan)
    supported = observation_count > 0
    probability[supported] = probability_sum[supported] / observation_count[supported]
    return {
        "probability": probability,
        "observation_count": observation_count,
        "s_values_m": s_min_m + (np.arange(s_count) + 0.5) * grid_size_m,
        "t_values_m": t_min_m + (np.arange(t_count) + 0.5) * grid_size_m,
        "accepted_frame_count": np.asarray(accepted_frames),
        "rejected_pose_frame_count": np.asarray(rejected_pose_frames),
    }


def _thin(mask: np.ndarray, maximum_iterations: int = 256) -> np.ndarray:
    skeleton = np.asarray(mask, dtype=bool).copy()
    for _ in range(maximum_iterations):
        changed = False
        for step in (0, 1):
            padded = np.pad(skeleton, 1)
            p2 = padded[:-2, 1:-1]
            p3 = padded[:-2, 2:]
            p4 = padded[1:-1, 2:]
            p5 = padded[2:, 2:]
            p6 = padded[2:, 1:-1]
            p7 = padded[2:, :-2]
            p8 = padded[1:-1, :-2]
            p9 = padded[:-2, :-2]
            neighbors = [p2, p3, p4, p5, p6, p7, p8, p9]
            count = sum(value.astype(np.uint8) for value in neighbors)
            transitions = sum(
                (~left & right).astype(np.uint8)
                for left, right in zip(neighbors, [*neighbors[1:], neighbors[0]], strict=True)
            )
            if step == 0:
                connectivity = ~(p2 & p4 & p6) & ~(p4 & p6 & p8)
            else:
                connectivity = ~(p2 & p4 & p8) & ~(p2 & p6 & p8)
            remove = skeleton & (count >= 2) & (count <= 6) & (transitions == 1) & connectivity
            if np.any(remove):
                skeleton[remove] = False
                changed = True
        if not changed:
            break
    return skeleton


def _skeleton_length_m(skeleton: np.ndarray, grid_size_m: float) -> float:
    horizontal = np.count_nonzero(skeleton[:, :-1] & skeleton[:, 1:])
    vertical = np.count_nonzero(skeleton[:-1, :] & skeleton[1:, :])
    diagonal = np.count_nonzero(skeleton[:-1, :-1] & skeleton[1:, 1:])
    diagonal += np.count_nonzero(skeleton[:-1, 1:] & skeleton[1:, :-1])
    length = grid_size_m * (horizontal + vertical + math.sqrt(2.0) * diagonal)
    return max(float(length), grid_size_m if np.any(skeleton) else 0.0)


def _chamfer_distance(mask: np.ndarray) -> np.ndarray:
    values = np.pad(np.asarray(mask, dtype=bool), 1)
    distance = np.where(values, np.inf, 0.0)
    diagonal = math.sqrt(2.0)
    height, width = values.shape
    for row in range(height):
        for column in range(width):
            if not values[row, column]:
                continue
            candidates = [distance[row, column]]
            if row > 0:
                candidates.append(distance[row - 1, column] + 1.0)
                if column > 0:
                    candidates.append(distance[row - 1, column - 1] + diagonal)
                if column + 1 < width:
                    candidates.append(distance[row - 1, column + 1] + diagonal)
            if column > 0:
                candidates.append(distance[row, column - 1] + 1.0)
            distance[row, column] = min(candidates)
    for row in range(height - 1, -1, -1):
        for column in range(width - 1, -1, -1):
            if not values[row, column]:
                continue
            candidates = [distance[row, column]]
            if row + 1 < height:
                candidates.append(distance[row + 1, column] + 1.0)
                if column > 0:
                    candidates.append(distance[row + 1, column - 1] + diagonal)
                if column + 1 < width:
                    candidates.append(distance[row + 1, column + 1] + diagonal)
            if column + 1 < width:
                candidates.append(distance[row, column + 1] + 1.0)
            distance[row, column] = min(candidates)
    return distance[1:-1, 1:-1]


def extract_crack_candidates(
    probability: np.ndarray,
    *,
    s_min_m: float,
    t_min_m: float,
    grid_size_m: float,
    model: Mapping[str, Any],
    probability_threshold: float = 0.5,
    minimum_area_m2: float = 0.0004,
) -> list[dict[str, Any]]:
    values = np.asarray(probability, dtype=np.float64)
    if values.ndim != 2 or not 0 < probability_threshold < 1:
        raise ValueError("invalid probability grid or threshold")
    if grid_size_m <= 0 or minimum_area_m2 <= 0:
        raise ValueError("grid size and minimum area must be positive")
    binary = np.isfinite(values) & (values >= probability_threshold)
    minimum_cells = max(1, int(math.ceil(minimum_area_m2 / grid_size_m**2)))
    candidates: list[dict[str, Any]] = []
    for component in connected_components(binary):
        if len(component) < minimum_cells:
            continue
        component_mask = np.zeros_like(binary)
        component_mask[component[:, 0], component[:, 1]] = True
        skeleton = _thin(component_mask)
        length_m = _skeleton_length_m(skeleton, grid_size_m)
        distance = _chamfer_distance(component_mask)
        skeleton_width = (
            np.maximum(1.0, 2.0 * distance[skeleton] - 1.0)
            * grid_size_m
            * 1000.0
        )
        s_coordinates = s_min_m + (component[:, 0] + 0.5) * grid_size_m
        t_coordinates = t_min_m + (component[:, 1] + 0.5) * grid_size_m
        centered = np.column_stack(
            [s_coordinates - np.mean(s_coordinates), t_coordinates - np.mean(t_coordinates)]
        )
        if len(component) >= 2 and np.any(centered):
            covariance = centered.T @ centered / len(component)
            direction = np.linalg.eigh(covariance)[1][:, -1]
            orientation_deg = math.degrees(math.atan2(direction[1], direction[0])) % 180.0
        else:
            orientation_deg = 0.0
        half = 0.5 * grid_size_m
        polygon = [
            [float(np.min(s_coordinates) - half), float(np.min(t_coordinates) - half)],
            [float(np.max(s_coordinates) + half), float(np.min(t_coordinates) - half)],
            [float(np.max(s_coordinates) + half), float(np.max(t_coordinates) + half)],
            [float(np.min(s_coordinates) - half), float(np.max(t_coordinates) + half)],
        ]
        component_probability = values[component[:, 0], component[:, 1]]
        defect = {
            "defect_type": "crack",
            "crack_class": "unclassified",
            "source": "rgb_ai",
            "severity": "candidate",
            "confidence": float(np.mean(component_probability)),
            "chainage_m": float(np.mean(s_coordinates)),
            "lateral_offset_m": float(np.mean(t_coordinates)),
            "local_polygon_st_m": polygon,
            "metrics": {
                "length_m": length_m,
                "mean_width_mm": float(np.mean(skeleton_width)),
                "max_width_mm": float(np.max(skeleton_width)),
                "area_m2": float(len(component) * grid_size_m**2),
                "orientation_deg": float(orientation_deg),
            },
            "model": {
                "name": model.get("name"),
                "version": model.get("version"),
                "weights_sha256": model.get("weights_sha256"),
            },
            "quality_flags": [
                "rgb_ai_experimental_unvalidated",
                "manual_review_required",
            ],
        }
        candidates.append(defect)
    candidates.sort(key=lambda item: (item["chainage_m"], item["lateral_offset_m"]))
    result: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate["defect_id"] = f"rgb-crack-{index:06d}"
        result.append(attach_prediction_audit(candidate))
    return result
