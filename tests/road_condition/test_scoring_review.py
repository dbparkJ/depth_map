from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from road_condition_core.config import AnalysisConfig
from road_condition_core.scoring import load_scoring_profile, merge_profile_config


ROOT = Path(__file__).resolve().parents[2]


def test_default_scoring_profile_matches_legacy_score_defaults() -> None:
    profile = load_scoring_profile(
        ROOT / "scoring_profiles",
        "internal-geometry-mvp-v1",
    )

    assert profile.version == "1.0.0"
    assert profile.approval_status == "experimental"
    assert profile.standard_naming_allowed is False
    assert profile.automatic_approval_confidence_threshold is None
    assert profile.segment_length_m == 20.0
    assert asdict(profile.score_config) == asdict(AnalysisConfig().score)

    merged, custom = merge_profile_config(
        profile,
        {"surface": {"grid_size_m": 0.2}},
    )
    assert custom is False
    assert merged["detection"]["segment_length_m"] == 20.0
    assert merged["surface"]["grid_size_m"] == 0.2
    AnalysisConfig.from_overrides(merged)

    overridden, custom = merge_profile_config(
        profile,
        {"score": {"pothole_weight": 0.5}},
    )
    assert custom is True
    assert overridden["score"]["pothole_weight"] == 0.5


def test_scoring_profile_rejects_path_escape_and_unvalidated_standard_name(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="invalid scoring profile id"):
        load_scoring_profile(ROOT / "scoring_profiles", "../escape")

    source = yaml.safe_load(
        (ROOT / "scoring_profiles/internal-geometry-mvp-v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    source["profile_id"] = "invalid-standard"
    source["standard_naming_allowed"] = True
    (tmp_path / "invalid-standard.yaml").write_text(
        yaml.safe_dump(source, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="standard naming requires"):
        load_scoring_profile(tmp_path, "invalid-standard")
