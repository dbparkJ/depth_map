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
        capabilities = client.get("/api/v1/capabilities").json()
        screening = capabilities["experimental_geometry_screening"]
        assert screening["default_mode"] == "disabled"
        assert len(screening["feature_flags"]) == 4
        viewer = capabilities["web_viewer"]
        assert viewer["default_map_adapter"] == "local_enu"
        assert viewer["full_point_cloud_to_browser"] is False
        assert capabilities["report_v2"]["missing_evidence_policy"] == "N/A_and_continue"
        crack = capabilities["rgb_crack_ai"]
        assert crack["neural_inference_state"] == "not_configured"
        assert crack["geometry_api_contains_pytorch"] is False
        maintenance_v2 = capabilities["maintenance_scenario_v2"]
        assert maintenance_v2["default_catalog"]["approval_status"] == "experimental"
        assert maintenance_v2["deterioration_rate"] == "N/A_no_repeated_survey"

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
        assert summary.json()["scoring_profile"]["profile_version"] == "1.0.0"
        assert summary.json()["scoring_profile"]["approval_status"] == "experimental"
        raw_defects = client.get(f"/api/v1/jobs/{job_id}/defects").json()
        reviews = client.get(f"/api/v1/jobs/{job_id}/reviews")
        assert reviews.status_code == 200
        review_bundle = reviews.json()
        assert review_bundle["automatic_approval_enabled"] is False
        assert review_bundle["scoring_profile"]["profile_version"] == "1.0.0"
        defect_id = raw_defects[0]["defect_id"]
        accepted = client.post(
            f"/api/v1/jobs/{job_id}/reviews/{defect_id}",
            json={
                "actor": "fixture-reviewer",
                "action": "accepted",
                "reason": "geometry evidence checked",
                "expected_version": 0,
            },
        )
        assert accepted.status_code == 200
        assert accepted.json()["record"]["state"] == "accepted"
        conflict = client.post(
            f"/api/v1/jobs/{job_id}/reviews/{defect_id}",
            json={
                "actor": "fixture-reviewer",
                "action": "rejected",
                "reason": "stale browser tab",
                "expected_version": 0,
            },
        )
        assert conflict.status_code == 409
        corrected = dict(raw_defects[0])
        corrected["severity"] = "reviewed-fixture"
        modified = client.post(
            f"/api/v1/jobs/{job_id}/reviews/{defect_id}",
            json={
                "actor": "fixture-reviewer",
                "action": "modified",
                "reason": "severity corrected from evidence",
                "expected_version": 1,
                "after": corrected,
            },
        )
        assert modified.status_code == 200
        assert modified.json()["record"]["state"] == "modified"
        assert modified.json()["event"]["before"] == raw_defects[0]
        assert modified.json()["event"]["after"]["severity"] == "reviewed-fixture"
        assert client.get(f"/api/v1/jobs/{job_id}/defects").json() == raw_defects
        report = client.get(f"/api/v1/jobs/{job_id}/report")
        assert report.status_code == 200
        assert "INTERNAL ROAD GEOMETRY EVIDENCE" in report.text
        summary_csv = client.get(f"/api/v1/jobs/{job_id}/report/summary.csv")
        assert summary_csv.status_code == 200
        assert "internal_geometry_score" in summary_csv.text
        escaped_report = client.get(
            f"/api/v1/jobs/{job_id}/report/%2E%2E/summary.json",
            follow_redirects=False,
        )
        assert escaped_report.status_code in {404, 422}
        scenario = client.post(
            f"/api/v1/jobs/{job_id}/scenarios",
            json={"rainfall_mm": 50},
        )
        assert scenario.status_code == 200
        assert scenario.json()["costs_krw"]["total"] > 0
        scenario_v2 = client.post(
            f"/api/v1/jobs/{job_id}/scenarios/v2",
            json={"budget_krw": 600000, "comparison_budgets_krw": [500000, 1000000]},
        )
        assert scenario_v2.status_code == 200
        scenario_v2_payload = scenario_v2.json()
        assert scenario_v2_payload["catalog"]["catalog_version"] == "1.0.0"
        assert scenario_v2_payload["budget_screening"]["priced_total_krw"] <= 600000
        assert scenario_v2_payload["budget_screening"]["full_total_krw"] is None
        assert scenario_v2_payload["deterioration"]["annual_rate"] is None


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
