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
