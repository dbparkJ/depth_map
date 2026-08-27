from __future__ import annotations

import json

import numpy as np

from road_condition_core.pipeline import analyze_points, write_analysis_products
from road_condition_core.roi import classify_st, parse_road_roi, resolve_roi_path
from road_condition_core.synthetic import generate_synthetic_scene


def _feature(zone_id, zone_type, ring, *, lane_id=None):
    properties = {
        "zone_id": zone_id,
        "zone_type": zone_type,
        "source": "manual",
        "confidence": 1.0,
        "chainage_start_m": 0.0,
        "chainage_end_m": 60.0,
    }
    if lane_id is not None:
        properties["lane_id"] = lane_id
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _roi():
    return parse_road_roi(
        {
            "type": "FeatureCollection",
            "format_version": 1,
            "coordinate_system": "local_road_ST_metres",
            "features": [
                _feature(
                    "road-0001",
                    "road",
                    [[0, -3.4], [60, -3.4], [60, 3.4], [0, 3.4], [0, -3.4]],
                ),
                _feature(
                    "lane-L1-0001",
                    "lane",
                    [[0, -3.0], [60, -3.0], [60, 0], [0, 0], [0, -3.0]],
                    lane_id="L1",
                ),
                _feature(
                    "lane-L2-0001",
                    "lane",
                    [[0, 0], [60, 0], [60, 3.0], [0, 3.0], [0, 0]],
                    lane_id="L2",
                ),
                _feature(
                    "shoulder-right-0001",
                    "shoulder",
                    [[0, 3.0], [60, 3.0], [60, 3.4], [0, 3.4], [0, 3.0]],
                ),
                _feature(
                    "exclusion-intersection-0001",
                    "exclusion",
                    [[10, -2.0], [14, -2.0], [14, 0.4], [10, 0.4], [10, -2.0]],
                ),
            ],
        }
    )


def test_exclusion_precedence_and_lane_ids() -> None:
    classified = classify_st(
        np.array([12.0, 20.0, 20.0, 20.0]),
        np.array([-0.5, -0.5, 0.5, 3.2]),
        _roi(),
    )
    assert classified.zone_type.tolist() == ["exclusion", "lane", "lane", "shoulder"]
    assert classified.lane_id.tolist() == ["", "L1", "L2", ""]
    assert classified.included_surface_mask.tolist() == [False, True, True, False]


def test_roi_filters_surface_and_preserves_lane_contract(tmp_path) -> None:
    scene = generate_synthetic_scene(
        "mixed",
        length_m=60.0,
        resolution_m=0.10,
        observations_per_cell=4,
        seed=7,
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        source={"type": "synthetic", "profile": "mixed"},
        road_roi=_roi(),
    )
    quality = products.summary["quality"]
    assert quality["roi_applied"] is True
    assert quality["roi_retained_point_count"] < quality["roi_input_point_count"]
    assert quality["roi_unknown_area_ratio"] > 0.0
    assert products.summary["results"]["pothole_count"] == 1
    potholes = [item for item in products.defects if item.defect_type == "pothole"]
    assert potholes[0].lane_id == "L2"
    assert potholes[0].road_zone == "lane"
    lane_segments = [item for item in products.segments if item.lane_id]
    assert {item.lane_id for item in lane_segments} == {"L1", "L2"}
    assert all(item.max_right_rut_depth_m == 0.0 for item in lane_segments if item.lane_id == "L1")
    assert all(item.max_left_rut_depth_m == 0.0 for item in lane_segments if item.lane_id == "L2")

    write_analysis_products(tmp_path, products)
    preview = json.loads((tmp_path / "surface_preview.json").read_text(encoding="utf-8"))
    assert preview["roi"]["applied"] is True
    assert preview["roi"]["lane_ids"] == ["L1", "L2"]
    defects = json.loads((tmp_path / "defects.json").read_text(encoding="utf-8"))
    assert defects[0]["road_zone"] in {"lane", "road"}


def test_roi_path_cannot_escape_mapping_bundle(tmp_path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    try:
        resolve_roi_path(bundle, "../outside.geojson")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("ROI path traversal was accepted")
