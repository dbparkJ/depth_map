from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from road_condition_core.release_gate import (
    REQUIRED_ARTIFACTS,
    REQUIRED_DATASETS,
    REQUIRED_METRICS,
    REQUIRED_PROHIBITIONS,
    evaluate_release_readiness,
    load_release_readiness,
)


ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "release_readiness" / "road-condition-v1.yaml"


def test_repository_v1_manifest_is_blocked_with_all_required_dimensions() -> None:
    payload = load_release_readiness(MANIFEST)
    assert set(payload["dataset_policy"]["subsets"]) == REQUIRED_DATASETS
    assert set(payload["metrics"]) == REQUIRED_METRICS
    assert set(payload["release_prohibitions"]) == REQUIRED_PROHIBITIONS
    assert set(payload["release_artifacts"]) == REQUIRED_ARTIFACTS
    result = evaluate_release_readiness(payload)
    assert result["status"] == "BLOCKED"
    assert result["release_allowed"] is False
    assert result["blocker_count"] >= 40
    blocker_paths = {item["path"] for item in result["blockers"]}
    assert "dataset_policy.subsets.holdout" in blocker_paths
    assert "metrics.peak_rss_mb" in blocker_paths
    assert "release_prohibitions.full_dataset_no_oom" in blocker_paths
    assert "release_artifacts.sbom" in blocker_paths
    assert "approval" in blocker_paths


def test_cli_returns_two_and_machine_readable_blockers() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/road_condition_release_gate.py", str(MANIFEST)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["status"] == "BLOCKED"
    assert result["manifest_sha256"]


def test_available_artifact_hash_is_verified_inside_root() -> None:
    payload = copy.deepcopy(load_release_readiness(MANIFEST))
    payload["release_artifacts"]["known_limitations"]["sha256"] = "0" * 64
    result = evaluate_release_readiness(payload, artifact_root=ROOT)
    assert any(
        item["code"] == "ARTIFACT_INVALID"
        and item["path"] == "release_artifacts.known_limitations"
        and "mismatch" in item["message"]
        for item in result["blockers"]
    )
