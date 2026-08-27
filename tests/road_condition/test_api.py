from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import Settings, create_app


def test_health_and_synthetic_job(tmp_path) -> None:
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=tmp_path / "workspace",
            max_workers=1,
            cors_origins=(),
        )
    )
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        response = client.post(
            "/api/v1/jobs",
            json={
                "source_type": "synthetic",
                "synthetic_profile": "potholes",
                "config": {
                    "surface": {
                        "grid_size_m": 0.20,
                        "reference_min_cells": 60,
                    }
                },
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/jobs/{job_id}").json()
            if status["state"] in {"completed", "failed"}:
                break
            time.sleep(0.10)
        assert status["state"] == "completed", status

        summary = client.get(f"/api/v1/jobs/{job_id}/summary")
        assert summary.status_code == 200
        assert summary.json()["results"]["pothole_count"] >= 1
        report = client.get(f"/api/v1/jobs/{job_id}/report")
        assert report.status_code == 200
        scenario = client.post(
            f"/api/v1/jobs/{job_id}/scenarios",
            json={"rainfall_mm": 50},
        )
        assert scenario.status_code == 200
        assert scenario.json()["costs_krw"]["total"] > 0


def test_mapping_path_must_be_relative(tmp_path) -> None:
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=tmp_path / "workspace",
            max_workers=1,
            cors_origins=(),
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "source_type": "mapping_bundle",
                "mapping_output_path": "/etc",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/jobs/{job_id}").json()
            if status["state"] in {"completed", "failed"}:
                break
            time.sleep(0.05)
        assert status["state"] == "failed"
        assert "relative" in status["error"]
