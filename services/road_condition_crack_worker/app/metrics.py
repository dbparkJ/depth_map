from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


def _divide(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return float(numerator / denominator)


def _pixel_counts(truth: np.ndarray, prediction: np.ndarray) -> tuple[int, int, int]:
    truth_mask = np.asarray(truth, dtype=bool)
    prediction_mask = np.asarray(prediction, dtype=bool)
    if truth_mask.shape != prediction_mask.shape:
        raise ValueError("truth and prediction masks must have the same shape")
    true_positive = int(np.count_nonzero(truth_mask & prediction_mask))
    false_positive = int(np.count_nonzero(~truth_mask & prediction_mask))
    false_negative = int(np.count_nonzero(truth_mask & ~prediction_mask))
    return true_positive, false_positive, false_negative


def pixel_metrics_from_counts(
    true_positive: int,
    false_positive: int,
    false_negative: int,
) -> dict[str, float | None]:
    precision = _divide(true_positive, true_positive + false_positive)
    recall = _divide(true_positive, true_positive + false_negative)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2.0 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def connected_components(mask: np.ndarray) -> list[np.ndarray]:
    values = np.asarray(mask, dtype=bool)
    if values.ndim != 2:
        raise ValueError("component mask must be two-dimensional")
    visited = np.zeros_like(values)
    components: list[np.ndarray] = []
    height, width = values.shape
    for row, column in np.argwhere(values):
        if visited[row, column]:
            continue
        queue = deque([(int(row), int(column))])
        visited[row, column] = True
        coordinates: list[tuple[int, int]] = []
        while queue:
            current_row, current_column = queue.popleft()
            coordinates.append((current_row, current_column))
            for row_delta in (-1, 0, 1):
                for column_delta in (-1, 0, 1):
                    if row_delta == 0 and column_delta == 0:
                        continue
                    neighbor_row = current_row + row_delta
                    neighbor_column = current_column + column_delta
                    if (
                        0 <= neighbor_row < height
                        and 0 <= neighbor_column < width
                        and values[neighbor_row, neighbor_column]
                        and not visited[neighbor_row, neighbor_column]
                    ):
                        visited[neighbor_row, neighbor_column] = True
                        queue.append((neighbor_row, neighbor_column))
        components.append(np.asarray(coordinates, dtype=np.int32))
    return components


def _component_iou(left: np.ndarray, right: np.ndarray) -> float:
    left_set = {tuple(value) for value in left.tolist()}
    right_set = {tuple(value) for value in right.tolist()}
    return len(left_set & right_set) / max(len(left_set | right_set), 1)


def match_instances(
    truth: np.ndarray,
    prediction: np.ndarray,
    *,
    minimum_iou: float,
) -> tuple[int, int, int]:
    truth_components = connected_components(truth)
    prediction_components = connected_components(prediction)
    candidates: list[tuple[float, int, int]] = []
    for truth_index, truth_component in enumerate(truth_components):
        for prediction_index, prediction_component in enumerate(prediction_components):
            iou = _component_iou(truth_component, prediction_component)
            if iou >= minimum_iou:
                candidates.append((iou, truth_index, prediction_index))
    matched_truth: set[int] = set()
    matched_prediction: set[int] = set()
    for _, truth_index, prediction_index in sorted(candidates, reverse=True):
        if truth_index in matched_truth or prediction_index in matched_prediction:
            continue
        matched_truth.add(truth_index)
        matched_prediction.add(prediction_index)
    return len(matched_truth), len(truth_components), len(prediction_components) - len(
        matched_prediction
    )


def _subset_pixel_metrics(
    samples: Sequence[Mapping[str, Any]],
    condition: str,
    probability_threshold: float,
) -> dict[str, float | None]:
    counts = [0, 0, 0]
    selected = 0
    for sample in samples:
        if not bool((sample.get("conditions") or {}).get(condition, False)):
            continue
        selected += 1
        truth = np.asarray(sample["truth_mask"], dtype=bool)
        prediction = np.asarray(sample["probability"], dtype=float) >= probability_threshold
        for index, value in enumerate(_pixel_counts(truth, prediction)):
            counts[index] += value
    metrics = pixel_metrics_from_counts(*counts) if selected else {
        "precision": None,
        "recall": None,
        "f1": None,
    }
    return {**metrics, "sample_count": selected}


def evaluate_holdout(
    samples: Iterable[Mapping[str, Any]],
    *,
    probability_threshold: float = 0.5,
    instance_iou_threshold: float = 0.1,
) -> dict[str, Any]:
    materialized = list(samples)
    pixel_counts = [0, 0, 0]
    matched_instances = 0
    truth_instances = 0
    false_positive_instances = 0
    route_length_m = 0.0
    absolute_length_errors: list[float] = []
    for sample in materialized:
        truth = np.asarray(sample["truth_mask"], dtype=bool)
        probability = np.asarray(sample["probability"], dtype=float)
        if not np.all(np.isfinite(probability)):
            raise ValueError("probability contains non-finite values")
        prediction = probability >= probability_threshold
        for index, value in enumerate(_pixel_counts(truth, prediction)):
            pixel_counts[index] += value
        matched, truth_count, false_positive = match_instances(
            truth,
            prediction,
            minimum_iou=instance_iou_threshold,
        )
        matched_instances += matched
        truth_instances += truth_count
        false_positive_instances += false_positive
        route_length = float(sample.get("route_length_m", 0.0))
        if route_length < 0:
            raise ValueError("route_length_m cannot be negative")
        route_length_m += route_length
        for pair in sample.get("matched_lengths_m") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("matched_lengths_m entries must be [truth_m, prediction_m]")
            absolute_length_errors.append(abs(float(pair[0]) - float(pair[1])))

    pixel = pixel_metrics_from_counts(*pixel_counts)
    wet = _subset_pixel_metrics(materialized, "wet", probability_threshold)
    shadow = _subset_pixel_metrics(materialized, "shadow", probability_threshold)
    return {
        "protocol_metric_names": sorted(
            {
                "pixel_precision",
                "pixel_recall",
                "pixel_f1",
                "instance_recall",
                "mean_absolute_length_error_m",
                "false_positive_per_100m",
                "wet_pixel_f1",
                "shadow_pixel_f1",
            }
        ),
        "sample_count": len(materialized),
        "pixel_precision": pixel["precision"],
        "pixel_recall": pixel["recall"],
        "pixel_f1": pixel["f1"],
        "instance_recall": _divide(matched_instances, truth_instances),
        "mean_absolute_length_error_m": (
            float(np.mean(absolute_length_errors)) if absolute_length_errors else None
        ),
        "false_positive_per_100m": _divide(
            false_positive_instances * 100.0,
            route_length_m,
        ),
        "wet_pixel_f1": wet["f1"],
        "shadow_pixel_f1": shadow["f1"],
        "subsets": {"wet": wet, "shadow": shadow},
        "counts": {
            "pixel_true_positive": pixel_counts[0],
            "pixel_false_positive": pixel_counts[1],
            "pixel_false_negative": pixel_counts[2],
            "matched_instances": matched_instances,
            "truth_instances": truth_instances,
            "false_positive_instances": false_positive_instances,
            "route_length_m": route_length_m,
            "matched_length_count": len(absolute_length_errors),
        },
    }
