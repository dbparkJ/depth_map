from __future__ import annotations

import json

import numpy as np

from fastapi.testclient import TestClient

from app.main import Settings, create_app
from road_condition_core.evidence import write_evidence_tile


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_route_view_reads_only_selected_completed_tile(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    route = workspace / "route-a"
    _write_json(
        route / "route_manifest.json",
        {
            "format_version": 1,
            "state": "partial",
            "route_config": {"core_tile_length_m": 10.0, "halo_m": 3.0},
            "defect_count": 1,
            "segment_count": 1,
            "tiles": [
                {
                    "tile_id": "tile-000000",
                    "state": "completed",
                    "core_start_m": 0.0,
                    "core_end_m": 10.0,
                    "artifacts": {"private": "/host/path"},
                },
                {
                    "tile_id": "tile-000001",
                    "state": "failed",
                    "error": "fixture",
                },
            ],
        },
    )
    result = route / "tiles" / "tile-000000" / "result"
    _write_json(
        result / "summary.json",
        {"format_version": 1, "tile": "zero", "scores": {"geometry_score": 80.0}},
    )
    _write_json(
        result / "defects.json",
        [
            {
                "defect_id": "pothole-0001",
                "defect_type": "pothole",
                "severity": "high",
                "metrics": {"area_m2": 0.1, "volume_m3": 0.004, "max_depth_m": 0.08},
            }
        ],
    )
    evidence_path = route / "evidence" / "tiles" / "tile-000000.rcev"
    evidence_report = write_evidence_tile(
        evidence_path,
        np.array([[1.0, 2.0, 3.0], [1.2, 2.1, 3.02]]),
        np.array([[100, 110, 120], [200, 70, 40]], dtype=np.uint8),
        np.array([0, 1], dtype=np.uint8),
        np.array([65535, 0], dtype=np.uint16),
    )
    _write_json(
        route / "evidence" / "manifest.json",
        {
            "format_version": 1,
            "evidence_contract": "road-condition-rcev-v1",
            "coordinate_system": "local_ENU_metres",
            "source": {"sha256": "private-source-hash"},
            "tiles": [
                {
                    "tile_id": "tile-000000",
                    "state": "completed",
                    "artifact": "tiles/tile-000000.rcev",
                    **evidence_report,
                }
            ],
        },
    )
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=workspace,
            max_workers=1,
            cors_origins=(),
        )
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/route-datasets/manifest",
            params={"path": "route-a"},
        )
        assert response.status_code == 200
        manifest = response.json()
        assert manifest["tile_count"] == 2
        assert manifest["completed_tile_count"] == 1
        assert manifest["viewer_contract"]["full_point_cloud_served"] is False
        assert manifest["viewer_contract"]["point_evidence"]["available"] is True
        assert manifest["tiles"][0]["evidence"]["point_count"] == 2
        assert "artifacts" not in manifest["tiles"][0]

        response = client.get(
            "/api/v1/route-datasets/tile",
            params={
                "path": "route-a",
                "tile_id": "tile-000000",
                "artifact": "defects",
            },
        )
        assert response.status_code == 200
        assert response.json()[0]["defect_id"] == "pothole-0001"

        failed = client.get(
            "/api/v1/route-datasets/tile",
            params={
                "path": "route-a",
                "tile_id": "tile-000001",
                "artifact": "defects",
            },
        )
        assert failed.status_code == 409

        evidence_manifest = client.get(
            "/api/v1/route-datasets/evidence/manifest",
            params={"path": "route-a"},
        )
        assert evidence_manifest.status_code == 200
        assert "source" not in evidence_manifest.json()
        evidence = client.get(
            "/api/v1/route-datasets/evidence/tile",
            params={"path": "route-a", "tile_id": "tile-000000"},
        )
        assert evidence.status_code == 200
        assert evidence.headers["content-type"].startswith(
            "application/vnd.road-condition.rcev"
        )
        assert evidence.content[:4] == b"RCEV"
        budget = client.get(
            "/api/v1/route-datasets/budget-report",
            params={
                "path": "route-a",
                "tile_id": "tile-000000",
                "budget_krw": 1_000_000,
            },
        )
        assert budget.status_code == 200
        budget_payload = budget.json()
        assert budget_payload["catalog"]["catalog_version"] == "2026.2.0"
        assert budget_payload["budget_screening"]["priced_total_krw"] == 876
        assert budget_payload["budget_report"]["amount_range_krw"][
            "full_project_estimate"
        ] is None


def test_route_view_rejects_workspace_escape_and_artifact_traversal(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=workspace,
            max_workers=1,
            cors_origins=(),
        )
    )
    with TestClient(app) as client:
        escaped = client.get(
            "/api/v1/route-datasets/manifest",
            params={"path": "../outside"},
        )
        assert escaped.status_code == 422
        artifact = client.get(
            "/api/v1/route-datasets/tile",
            params={
                "path": "route-a",
                "tile_id": "tile-000000",
                "artifact": "../../secret",
            },
        )
        assert artifact.status_code in {404, 422}
        evidence = client.get(
            "/api/v1/route-datasets/evidence/tile",
            params={"path": "route-a", "tile_id": "../../secret"},
        )
        assert evidence.status_code in {404, 422}


def test_route_view_rejects_json_symlink_escape(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    route = workspace / "route-a"
    route.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"format_version": 1, "tiles": []}', encoding="utf-8")
    (route / "route_manifest.json").symlink_to(outside)
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=workspace,
            max_workers=1,
            cors_origins=(),
        )
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/route-datasets/manifest",
            params={"path": "route-a"},
        )
        assert response.status_code == 422
        assert "escapes route result" in response.json()["detail"]
