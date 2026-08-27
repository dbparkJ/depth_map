from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import Settings, create_app
from app.rimms_store import RimmsContractStore


def _payload(**overrides) -> dict:
    payload = {
        "contract_version": "road-condition-rimms-request-v1",
        "expected_result_contract_version": "road-condition-rimms-result-v1",
        "external_job_id": "rimms-2026-001",
        "survey_id": "survey-20260827-001",
        "route_id": "route-a",
        "lane_id": None,
        "mapping_bundle_uri": "s3://example-bucket/survey-001/mapping/",
        "raw_dataset_uri": "s3://example-bucket/survey-001/raw/",
        "road_roi_uri": "s3://example-bucket/survey-001/road_roi.geojson",
        "config_profile_id": "internal-geometry-mvp-v1",
        "callback_url": None,
    }
    payload.update(overrides)
    return payload


def test_store_replay_is_idempotent_and_key_is_hashed_at_rest(tmp_path) -> None:
    store = RimmsContractStore(tmp_path)
    first = store.create(_payload(), "rimms-retry-key-001")
    replay = store.create(_payload(), "rimms-retry-key-001")
    assert first["external_job_id"] == replay["external_job_id"]
    assert first["request_sha256"] == replay["request_sha256"]
    assert first["idempotency_replayed"] is False
    assert replay["idempotency_replayed"] is True
    assert first["state"] == "awaiting_connector_configuration"
    assert first["execution"]["started"] is False
    assert first["callback_delivery"] == {
        "enabled": False,
        "attempt_count": 0,
        "status": "N/A_polling_only",
    }
    assert first["result"]["result_manifest_uri"] is None
    assert first["result"]["summary_uri"] is None
    assert first["result"]["report_uri"] is None
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "integrations" / "rimms").rglob("*.json")
    )
    assert "rimms-retry-key-001" not in persisted
    index = json.loads(
        (tmp_path / "integrations" / "rimms" / "idempotency_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(index) == 1


def test_api_is_disabled_by_default_and_contract_gate_is_fail_closed(tmp_path) -> None:
    disabled_app = create_app(
        Settings(data_root=tmp_path / "disabled", workspace_root=tmp_path / "workspace")
    )
    with TestClient(disabled_app) as client:
        capabilities = client.get("/api/v1/capabilities").json()
        integration = capabilities["roadinventory_mms_integration"]
        assert integration["ingress_enabled"] is False
        assert integration["callback"] == "disabled_fail_closed"
        response = client.post(
            "/api/v1/integrations/rimms/jobs",
            headers={"Idempotency-Key": "rimms-retry-key-001"},
            json=_payload(),
        )
        assert response.status_code == 503


def test_enabled_contract_supports_polling_and_rejects_conflicts(tmp_path) -> None:
    app = create_app(
        Settings(
            data_root=tmp_path / "enabled",
            workspace_root=tmp_path / "workspace",
            rimms_contract_ingress_enabled=True,
        )
    )
    endpoint = "/api/v1/integrations/rimms/jobs"
    with TestClient(app) as client:
        assert client.post(endpoint, json=_payload()).status_code == 428
        created = client.post(
            endpoint,
            headers={"Idempotency-Key": "rimms-retry-key-001"},
            json=_payload(),
        )
        assert created.status_code == 202
        assert created.json()["state"] == "awaiting_connector_configuration"
        replay = client.post(
            endpoint,
            headers={"Idempotency-Key": "rimms-retry-key-001"},
            json=_payload(),
        )
        assert replay.status_code == 202
        assert replay.json()["idempotency_replayed"] is True
        conflict = client.post(
            endpoint,
            headers={"Idempotency-Key": "rimms-retry-key-001"},
            json=_payload(external_job_id="rimms-2026-002"),
        )
        assert conflict.status_code == 409
        external_id_conflict = client.post(
            endpoint,
            headers={"Idempotency-Key": "rimms-retry-key-002"},
            json=_payload(),
        )
        assert external_id_conflict.status_code == 409
        polled = client.get(f"{endpoint}/rimms-2026-001")
        assert polled.status_code == 200
        assert polled.json()["result"]["result_manifest_uri"] is None
        assert polled.json()["source_of_truth"]["reviewed_defect_sync"] == (
            "N/A_direction_not_agreed"
        )
        assert len(client.get(endpoint).json()["jobs"]) == 1


def test_version_uri_and_callback_mismatch_are_explicit_422(tmp_path) -> None:
    app = create_app(
        Settings(
            data_root=tmp_path / "data",
            workspace_root=tmp_path / "workspace",
            rimms_contract_ingress_enabled=True,
        )
    )
    endpoint = "/api/v1/integrations/rimms/jobs"
    headers = {"Idempotency-Key": "rimms-validation-key-001"}
    cases = [
        (_payload(contract_version="road-condition-rimms-request-v999"), "contract_version"),
        (
            _payload(expected_result_contract_version="road-condition-rimms-result-v999"),
            "expected_result_contract_version",
        ),
        (_payload(mapping_bundle_uri="file:///etc/passwd"), "URI scheme"),
        (_payload(raw_dataset_uri="https://user:secret@example.invalid/raw"), "userinfo"),
        (
            _payload(callback_url="https://rimms.example.invalid/callback"),
            "callback mode is disabled",
        ),
    ]
    with TestClient(app) as client:
        for payload, expected in cases:
            response = client.post(endpoint, headers=headers, json=payload)
            assert response.status_code == 422
            assert expected in response.text
