from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from road_condition_core.evidence import (
    read_evidence_tile,
    read_rgbd_browser_points,
    write_evidence_tile,
)


def test_rcev_round_trip_preserves_rgb_mask_and_centimetre_accuracy(tmp_path: Path) -> None:
    points = np.array(
        [
            [123.125, -45.25, 7.75],
            [132.875, -41.0, 8.125],
            [128.5, -43.5, 7.95],
        ],
        dtype=np.float64,
    )
    colors = np.array([[10, 20, 30], [240, 120, 60], [90, 100, 110]], dtype=np.uint8)
    classes = np.array([0, 1, 3], dtype=np.uint8)
    indices = np.array([65535, 0, 2], dtype=np.uint16)
    path = tmp_path / "tile.rcev"
    report = write_evidence_tile(path, points, colors, classes, indices)
    decoded = read_evidence_tile(path)
    assert report["byte_size"] == 64 + len(points) * 12
    assert report["maximum_quantization_error_m"] < 0.01
    np.testing.assert_allclose(decoded.points_enu_m, points, atol=0.01)
    np.testing.assert_array_equal(decoded.colors_rgb, colors)
    np.testing.assert_array_equal(decoded.defect_class, classes)
    np.testing.assert_array_equal(decoded.defect_index, indices)


def test_rcev_rejects_empty_points(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        write_evidence_tile(
            tmp_path / "empty.rcev",
            np.empty((0, 3)),
            np.empty((0, 3), dtype=np.uint8),
            np.empty(0, dtype=np.uint8),
            np.empty(0, dtype=np.uint16),
        )


def test_rgbd_browser_sample_contract_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "points_raw.bin"
    records = np.zeros(
        2,
        dtype=np.dtype(
            [
                ("x", "<f4"),
                ("y", "<f4"),
                ("z", "<f4"),
                ("r", "u1"),
                ("g", "u1"),
                ("b", "u1"),
                ("a", "u1"),
            ]
        ),
    )
    records["x"] = [1.0, 2.0]
    records["r"] = [30, 40]
    with path.open("wb") as stream:
        stream.write(struct.pack("<4sIII", b"RGBD", 1, 2, 16))
        records.tofile(stream)
    points, colors = read_rgbd_browser_points(path)
    assert points.shape == (2, 3)
    assert colors[:, 0].tolist() == [30, 40]

    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="payload size"):
        read_rgbd_browser_points(path)
