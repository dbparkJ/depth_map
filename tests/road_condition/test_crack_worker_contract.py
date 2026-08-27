from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from services.road_condition_crack_worker.app.bev import (
    accumulate_bev_probability,
    extract_crack_candidates,
)
from services.road_condition_crack_worker.app.contracts import (
    HIGHER_IS_BETTER,
    REQUIRED_HOLDOUT_METRICS,
    append_review_revision,
    attach_prediction_audit,
    canonical_sha256,
    validate_model_manifest,
    verify_model_bundle,
)
from services.road_condition_crack_worker.app.metrics import evaluate_holdout


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "services" / "road_condition_crack_worker"


def _approved_manifest(weights_path: str, checksum: str) -> dict:
    return {
        "format_version": 1,
        "dataset": {
            "dataset_id": "fixture-v1",
            "split_manifest_sha256": "1" * 64,
            "label_format": "binary-mask-png",
            "classes": ["crack"],
            "minimum_detection_width_mm": 3.0,
            "rgb_resolution_px": [1920, 1080],
            "ground_sampling_distance_mm_per_px": 1.5,
            "conditions": {"wet": True, "shadow": True, "night": False},
        },
        "model": {
            "name": "fixture-segmenter",
            "version": "1.0.0",
            "weights_path": weights_path,
            "weights_sha256": checksum,
            "runtime": "fixture-cpu",
            "input_resolution_px": [512, 512],
        },
        "environment": {
            "training_gpu": "none-fixture-cpu",
            "inference_gpu": "none-fixture-cpu",
            "framework": "fixture",
            "framework_version": "1.0",
        },
        "holdout_evaluation": {
            "protocol_id": "road-condition-crack-holdout-v1",
            "metrics": {name: 0.8 for name in REQUIRED_HOLDOUT_METRICS},
            "acceptance_thresholds": {
                name: {
                    "operator": ">=" if name in HIGHER_IS_BETTER else "<=",
                    "value": 0.0 if name in HIGHER_IS_BETTER else 1.0,
                }
                for name in REQUIRED_HOLDOUT_METRICS
            },
        },
        "approval": {
            "state": "approved",
            "approved_by": "fixture-reviewer",
            "approved_at": "2026-08-27T00:00:00Z",
            "holdout_protocol_sha256": "2" * 64,
        },
    }


def test_holdout_protocol_is_frozen_but_cannot_auto_approve() -> None:
    protocol = json.loads(
        (WORKER / "config" / "holdout_protocol_v1.json").read_text(encoding="utf-8")
    )
    assert set(protocol["required_metrics"]) == REQUIRED_HOLDOUT_METRICS
    assert protocol["required_subsets"] == ["wet", "shadow"]
    assert protocol["acceptance_thresholds"] is None
    assert protocol["automatic_approval_enabled"] is False
    assert protocol["split_policy"]["unit"] == "route-and-survey-date"

    template = json.loads(
        (ROOT / "docs/road_condition/crack_model_manifest.example.json").read_text(
            encoding="utf-8"
        )
    )
    errors = validate_model_manifest(template)
    assert "model is not explicitly approved for inference" in errors
    assert any("dataset.label_format" in error for error in errors)


def test_model_bundle_is_workspace_confined_and_checksum_verified(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    weights = workspace / "weights.bin"
    weights.write_bytes(b"approved-fixture-weights")
    checksum = hashlib.sha256(weights.read_bytes()).hexdigest()
    manifest = _approved_manifest("weights.bin", checksum)
    (workspace / "model.json").write_text(json.dumps(manifest), encoding="utf-8")

    verified = verify_model_bundle(workspace, "model.json")

    assert verified["approved_for_inference"] is True
    assert verified["weights_sha256"] == checksum
    assert verified["weights_path"] == "weights.bin"

    with pytest.raises(ValueError, match="holdout protocol SHA-256 mismatch"):
        verify_model_bundle(
            workspace,
            "model.json",
            expected_holdout_protocol_sha256="3" * 64,
        )

    manifest["model"]["weights_sha256"] = "0" * 64
    (workspace / "bad.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_model_bundle(workspace, "bad.json")
    with pytest.raises(ValueError, match="escapes /workspace"):
        verify_model_bundle(workspace, "../model.json")


def test_bev_accumulation_applies_pose_road_and_depth_masks() -> None:
    probability = np.asarray([[0.9, 0.8], [0.7, 0.6]])
    coordinates = np.asarray([[0.25, 0.75], [0.25, 0.75]])
    frames = [
        {
            "probability": probability,
            "s_m": coordinates,
            "t_m": coordinates.T,
            "road_mask": np.asarray([[True, True], [False, True]]),
            "depth_valid": np.asarray([[True, False], [True, True]]),
            "pose_valid": True,
        },
        {
            "probability": probability,
            "s_m": coordinates,
            "t_m": coordinates.T,
            "road_mask": np.ones((2, 2), dtype=bool),
            "depth_valid": np.ones((2, 2), dtype=bool),
            "pose_valid": False,
        },
    ]

    result = accumulate_bev_probability(
        frames,
        s_min_m=0.0,
        s_max_m=1.0,
        t_min_m=0.0,
        t_max_m=1.0,
        grid_size_m=0.5,
    )

    assert result["accepted_frame_count"].item() == 1
    assert result["rejected_pose_frame_count"].item() == 1
    assert result["observation_count"].sum() == 2
    assert result["probability"][0, 0] == pytest.approx(0.9)
    assert result["probability"][1, 1] == pytest.approx(0.6)
    assert np.isnan(result["probability"][1, 0])


def test_probability_postprocessing_emits_metric_and_audit_contract() -> None:
    probability = np.zeros((30, 20), dtype=float)
    probability[4:25, 8:11] = 0.9
    model = {
        "name": "fixture-segmenter",
        "version": "1.0.0",
        "weights_sha256": "a" * 64,
    }

    defects = extract_crack_candidates(
        probability,
        s_min_m=0.0,
        t_min_m=-1.0,
        grid_size_m=0.02,
        model=model,
    )

    assert len(defects) == 1
    defect = defects[0]
    assert defect["defect_type"] == "crack"
    assert defect["source"] == "rgb_ai"
    assert defect["model"] == model
    assert defect["metrics"]["length_m"] > 0.30
    assert defect["metrics"]["mean_width_mm"] > 0
    assert defect["metrics"]["max_width_mm"] >= defect["metrics"]["mean_width_mm"]
    assert defect["original_prediction"]["defect_id"] == "rgb-crack-000001"
    assert canonical_sha256(defect["original_prediction"]) == defect[
        "original_prediction_sha256"
    ]


def test_holdout_metrics_include_wet_shadow_instances_length_and_route_rate() -> None:
    truth = np.zeros((5, 5), dtype=bool)
    truth[1, 1:3] = True
    probability = np.zeros((5, 5), dtype=float)
    probability[1, 1:3] = 0.9
    probability[4, 4] = 0.9
    sample = {
        "truth_mask": truth,
        "probability": probability,
        "route_length_m": 50.0,
        "conditions": {"wet": True, "shadow": True},
        "matched_lengths_m": [[2.0, 2.5]],
    }

    result = evaluate_holdout([sample])

    assert result["pixel_precision"] == pytest.approx(2 / 3)
    assert result["pixel_recall"] == pytest.approx(1.0)
    assert result["pixel_f1"] == pytest.approx(0.8)
    assert result["instance_recall"] == pytest.approx(1.0)
    assert result["mean_absolute_length_error_m"] == pytest.approx(0.5)
    assert result["false_positive_per_100m"] == pytest.approx(2.0)
    assert result["wet_pixel_f1"] == pytest.approx(0.8)
    assert result["shadow_pixel_f1"] == pytest.approx(0.8)


def test_review_revision_preserves_immutable_original_prediction() -> None:
    record = attach_prediction_audit(
        {"defect_id": "rgb-crack-1", "defect_type": "crack", "metrics": {"length_m": 1.0}}
    )
    updated = append_review_revision(
        record,
        actor="reviewer-1",
        timestamp="2026-08-27T10:00:00Z",
        action="correct",
        patch={"metrics": {"length_m": 0.8}},
        reason="manual endpoint correction",
    )

    assert updated["original_prediction"] == record["original_prediction"]
    assert updated["original_prediction_sha256"] == record["original_prediction_sha256"]
    assert updated["review"]["state"] == "corrected"
    assert updated["review"]["revisions"][0]["patch"]["metrics"]["length_m"] == 0.8
    assert record["review"]["revisions"] == []


def test_worker_capability_is_fail_closed_and_geometry_api_has_no_torch() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "services.road_condition_crack_worker.app.main",
            "capabilities",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    capabilities = json.loads(completed.stdout)
    assert capabilities["ready_for_neural_inference"] is False
    assert capabilities["automatic_approval_enabled"] is False
    assert "trained_weights" in capabilities["not_implemented"]

    api_requirements = (ROOT / "services/road_condition_api/requirements.txt").read_text()
    api_dockerfile = (ROOT / "services/road_condition_api/Dockerfile").read_text()
    assert "torch" not in api_requirements.lower()
    assert "torch" not in api_dockerfile.lower()
