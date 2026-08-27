from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["synthetic", "mapping_bundle"] = "synthetic"
    synthetic_profile: Literal["flat", "potholes", "rutting", "mixed"] = "mixed"
    mapping_output_path: str | None = None
    road_roi_path: str | None = None
    point_cloud_stage: Literal["raw", "clean", "removed"] = "raw"
    scoring_profile_id: str = Field(
        default="internal-geometry-mvp-v1",
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "CreateJobRequest":
        if self.source_type == "mapping_bundle" and not self.mapping_output_path:
            raise ValueError("mapping_output_path is required for mapping_bundle")
        if self.source_type == "synthetic" and self.mapping_output_path:
            raise ValueError("mapping_output_path is only valid for mapping_bundle")
        if self.source_type == "synthetic" and self.road_roi_path:
            raise ValueError("road_roi_path is only valid for mapping_bundle")
        return self


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_types: list[Literal["pothole", "rutting", "bump"]] = Field(
        default_factory=lambda: ["pothole", "rutting", "bump"]
    )
    unit_prices: dict[str, float] = Field(default_factory=dict)
    rainfall_mm: float = Field(default=30.0, ge=0.0, le=1000.0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=128)
    action: Literal[
        "accepted",
        "modified",
        "rejected",
        "needs_recollection",
    ]
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    after: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_after(self) -> "ReviewRequest":
        if self.action == "modified" and self.after is None:
            raise ValueError("modified review requires after")
        if self.action != "modified" and self.after is not None:
            raise ValueError("after is only valid for modified review")
        return self
