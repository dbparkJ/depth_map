from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import Settings, create_app


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
    _write_json(result / "summary.json", {"format_version": 1, "tile": "zero"})
    _write_json(result / "defects.json", [{"defect_id": "pothole-0001"}])
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
