from __future__ import annotations

import numpy as np
import pytest

from rgbd_map.depth_quality import DepthQualityPolicy, evaluate_depth_quality


def _policy(**overrides) -> DepthQualityPolicy:
    values = {
        "min_depth_m": 0.5,
        "max_depth_m": 30.0,
        "far_depth_policy": "off",
        "far_depth_soft_start_m": 20.0,
        "far_depth_hard_m": 28.8,
    }
    values.update(overrides)
    return DepthQualityPolicy(**values)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        ("higher-is-better", [[False, True]]),
        ("lower-is-better", [[True, False]]),
    ],
)
def test_confidence_direction_is_explicit_and_mask_is_applied(order, expected):
    depth = np.array([[1000, 1000]], dtype=np.uint16)
    confidence = np.array([[10, 200]], dtype=np.uint8)

    result = evaluate_depth_quality(
        depth,
        _policy(confidence_threshold=100, confidence_order=order),
        confidence,
    )

    np.testing.assert_array_equal(result.valid_mask, expected)
    assert result.report["confidence_status"] == "applied"
    assert result.report["confidence_removed_count"] == 1


def test_missing_confidence_is_reported_and_shape_mismatch_is_rejected():
    depth = np.full((2, 2), 1000, dtype=np.uint16)
    policy = _policy(confidence_threshold=100)

    missing = evaluate_depth_quality(depth, policy)

    assert np.all(missing.valid_mask)
    assert missing.report["confidence_status"] == "missing_fallback"
    with pytest.raises(ValueError, match="confidence map shape"):
        evaluate_depth_quality(depth, policy, np.ones((1, 2), dtype=np.uint8))


def test_adaptive_far_peak_flags_fragmented_spike_but_preserves_coherent_plane():
    height = width = 200
    background = np.linspace(20_100, 28_500, height * width, dtype=np.uint16).reshape(
        height, width
    )
    fragmented = background.copy()
    fragmented[::3, ::3] = 29_300
    policy = _policy(
        far_depth_policy="adaptive",
        adaptive_peak_ratio=5.0,
    )

    spike = evaluate_depth_quality(fragmented, policy)
    plane = evaluate_depth_quality(
        np.full((height, width), 29_300, dtype=np.uint16), policy
    )

    assert spike.detected_far_peaks_m
    assert spike.resolved_hard_depth_m is not None
    assert plane.detected_far_peaks_m == ()
    assert plane.resolved_hard_depth_m is None
    assert plane.report["coherent_far_surface_peaks_m"]
