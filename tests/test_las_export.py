import json
from pathlib import Path

import numpy as np
from pyproj import Transformer

from rgbd_map.geodesy import LocalENU
from rgbd_map.las_export import (
    infer_utm_crs,
    make_export_plan,
    make_pdal_las_pipeline,
    read_ply_vertex_count,
)


def _write_bundle(root: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True)
    (data / "summary.json").write_text(
        json.dumps(
            {
                "origin": {
                    "longitude_deg": 126.84949888910248,
                    "latitude_deg": 37.717401871308695,
                    "ellipsoid_height_m": 44.5,
                }
            }
        ),
        encoding="utf-8",
    )
    (data / "cloud_clean_enu.ply").write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 3\n"
        b"property float x\nproperty float y\nproperty float z\nend_header\n"
    )


def test_enu_to_ecef_affine_matches_local_enu_internal_transform():
    origin = LocalENU(126.8495, 37.7174, 44.5)
    points = np.array([[0.0, 0.0, 0.0], [10.0, 20.0, 3.0], [-4.0, 8.0, -2.0]])
    homogeneous = np.column_stack((points, np.ones(len(points))))
    transformed = (origin.enu_to_ecef_affine_matrix() @ homogeneous.T).T[:, :3]
    expected = origin._origin_ecef + points @ origin._ecef_to_enu
    np.testing.assert_allclose(transformed, expected, atol=1e-9)


def test_infer_utm_crs_for_korean_origin():
    assert infer_utm_crs(126.8495, 37.7174).to_epsg() == 32652


def test_read_ply_vertex_count_and_pipeline(tmp_path):
    bundle = tmp_path / "result"
    _write_bundle(bundle)
    plan = make_export_plan(bundle)
    assert read_ply_vertex_count(plan.input_ply) == 3
    assert plan.target_crs.to_epsg() == 32652
    assert plan.output_las.name == "cloud_clean_epsg32652.las"

    pipeline = make_pdal_las_pipeline(plan)["pipeline"]
    assert [stage["type"] for stage in pipeline] == [
        "readers.ply",
        "filters.transformation",
        "filters.reprojection",
        "writers.las",
    ]
    assert pipeline[2]["in_srs"] == "EPSG:4978"
    assert pipeline[2]["out_srs"] == "EPSG:32652"
    assert pipeline[-1]["dataformat_id"] == 7
    assert pipeline[-1]["scale_x"] == 0.001

    projected = Transformer.from_crs("EPSG:4979", "EPSG:32652", always_xy=True)
    expected = projected.transform(126.84949888910248, 37.717401871308695, 44.5)
    np.testing.assert_allclose(plan.offset_xyz, expected, atol=1e-9)
