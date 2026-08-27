from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from road_condition_core.config import AnalysisConfig
from road_condition_core.io import load_mapping_bundle, resolve_relative_path
from road_condition_core.maintenance import (
    DEFAULT_UNIT_PRICES,
    calculate_maintenance_scenario,
)
from road_condition_core.pipeline import (
    ALGORITHM_VERSION,
    analyze_points,
    write_analysis_products,
)
from road_condition_core.roi import load_road_roi, resolve_roi_path
from road_condition_core.synthetic import generate_synthetic_scene

from .schemas import CreateJobRequest, ScenarioRequest
from .route_view import read_route_manifest, read_route_tile_artifact
from .store import JobStore


@dataclass(frozen=True)
class Settings:
    data_root: Path
    workspace_root: Path
    max_workers: int = 1
    cors_origins: tuple[str, ...] = ("http://localhost:8080", "http://127.0.0.1:8080")

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(
            value.strip()
            for value in os.getenv(
                "ROAD_CONDITION_CORS_ORIGINS",
                "http://localhost:8080,http://127.0.0.1:8080",
            ).split(",")
            if value.strip()
        )
        return cls(
            data_root=Path(os.getenv("ROAD_CONDITION_DATA_ROOT", "artifacts/road_condition_runtime")),
            workspace_root=Path(os.getenv("ROAD_CONDITION_WORKSPACE_ROOT", "artifacts")),
            max_workers=max(1, int(os.getenv("ROAD_CONDITION_MAX_WORKERS", "1"))),
            cors_origins=origins,
        )


def _origin_from_summary(summary: dict[str, Any]) -> dict[str, float] | None:
    origin = summary.get("origin")
    if not isinstance(origin, dict):
        return None
    try:
        return {
            "longitude_deg": float(origin["longitude_deg"]),
            "latitude_deg": float(origin["latitude_deg"]),
            "ellipsoid_height_m": float(origin["ellipsoid_height_m"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _run_job(
    job_id: str,
    request_payload: dict[str, Any],
    *,
    settings: Settings,
    store: JobStore,
) -> None:
    try:
        store.update_status(
            job_id,
            state="running",
            progress=0.05,
            message="loading source data",
        )
        request = CreateJobRequest.model_validate(request_payload)
        config = AnalysisConfig.from_overrides(request.config)
        if request.source_type == "synthetic":
            scene = generate_synthetic_scene(request.synthetic_profile)
            points = scene.points_enu_m
            colors = scene.colors_rgb
            trajectory = scene.trajectory_enu_m
            metadata: dict[str, Any] = {}
            source_origin = scene.source_origin
            source = {
                "type": "synthetic",
                "profile": request.synthetic_profile,
                "truth": scene.truth,
            }
            pose_context = None
            quality_context = None
            road_roi = None
        else:
            resolved = resolve_relative_path(
                settings.workspace_root,
                request.mapping_output_path or "",
            )
            bundle = load_mapping_bundle(resolved, stage=request.point_cloud_stage)
            points = bundle.points_enu_m
            colors = bundle.colors_rgb
            trajectory = bundle.trajectory_enu_m
            metadata = bundle.point_metadata
            source_origin = _origin_from_summary(bundle.summary)
            source = {
                "type": "mapping_bundle",
                "workspace_relative_path": str(
                    resolved.relative_to(settings.workspace_root.resolve())
                ),
                "point_cloud_stage": request.point_cloud_stage,
                "mapping_format_version": bundle.summary.get("format_version"),
                "capabilities": bundle.analysis_capabilities,
                "calibration_status": bundle.analysis_quality["calibration_status"],
                "dataset_id": (
                    (bundle.analysis_source_manifest or {}).get("dataset_id")
                ),
                "mapping_commit_sha": (
                    (bundle.analysis_source_manifest or {}).get("mapping_commit_sha")
                ),
            }
            pose_context = (
                {
                    "T_enu_camera": bundle.camera_poses.T_enu_camera,
                    "pose_quality_score": bundle.camera_poses.pose_quality_score,
                }
                if bundle.camera_poses is not None
                else None
            )
            quality_context = bundle.analysis_quality
            roi_path = None
            if request.road_roi_path:
                roi_path = resolve_roi_path(resolved, request.road_roi_path)
                if not roi_path.is_file():
                    raise FileNotFoundError(f"road ROI file not found: {request.road_roi_path}")
            else:
                for candidate in (
                    resolved / "data" / "road_roi.geojson",
                    resolved / "road_roi.geojson",
                ):
                    if candidate.is_file():
                        roi_path = candidate
                        break
            road_roi = load_road_roi(roi_path) if roi_path is not None else None
            source["road_roi"] = {
                "applied": road_roi is not None,
                "path": (
                    str(roi_path.relative_to(resolved)) if roi_path is not None else None
                ),
                "fallback": "trajectory_corridor" if road_roi is None else None,
            }
        store.update_status(
            job_id,
            progress=0.25,
            message="rasterizing and fitting the road surface",
        )
        products = analyze_points(
            points,
            colors,
            trajectory,
            config=config,
            point_metadata=metadata,
            source=source,
            source_origin=source_origin,
            pose_context=pose_context,
            quality_context=quality_context,
            road_roi=road_roi,
        )
        store.update_status(
            job_id,
            progress=0.85,
            message="writing result contract and report",
        )
        artifacts = write_analysis_products(store.result_dir(job_id), products)
        store.update_status(
            job_id,
            state="completed",
            progress=1.0,
            message="completed",
            artifacts=artifacts,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - boundary persists a failed job
        error_text = f"{type(exc).__name__}: {exc}"
        directory = store.job_dir(job_id)
        (directory / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        store.update_status(
            job_id,
            state="failed",
            progress=1.0,
            message="failed",
            error=error_text,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_settings.data_root.mkdir(parents=True, exist_ok=True)
    resolved_settings.workspace_root.mkdir(parents=True, exist_ok=True)
    store = JobStore(resolved_settings.data_root)
    executor = ThreadPoolExecutor(
        max_workers=resolved_settings.max_workers,
        thread_name_prefix="road-condition-job",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = resolved_settings
        app.state.store = store
        app.state.executor = executor
        yield
        executor.shutdown(wait=False, cancel_futures=False)

    app = FastAPI(
        title="Depth Map Road Condition API",
        version="0.1.0",
        description=(
            "Geometry-first road-condition analysis service. The MVP reports an "
            "internal score and roughness proxy, not certified PCI or IRI."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    def get_store(request: Request) -> JobStore:
        return request.app.state.store

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "road-condition-api",
            "algorithm_version": ALGORITHM_VERSION,
            "data_root_writable": os.access(resolved_settings.data_root, os.W_OK),
            "workspace_root": str(resolved_settings.workspace_root),
        }

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "format_version": 1,
            "sources": ["synthetic", "mapping_bundle"],
            "geometry_detectors": ["pothole", "rutting", "bump"],
            "experimental_geometry_screening": {
                "default_mode": "disabled",
                "feature_flags": [
                    "config.advanced_geometry.step_manhole_enabled",
                    "config.advanced_geometry.crossfall_enabled",
                    "config.advanced_geometry.longitudinal_enabled",
                    "config.advanced_geometry.ponding_screening_enabled",
                ],
                "outputs": [
                    "step_or_manhole_candidates",
                    "crossfall_profile",
                    "longitudinal_profile",
                    "roughness_proxy",
                    "ponding_screening_proxy",
                ],
                "engineering_calibration": "required",
            },
            "road_roi": {
                "format": "GeoJSON Polygon/MultiPolygon in local_road_ST_metres",
                "zone_types": ["road", "lane", "shoulder", "exclusion"],
                "precedence": ["exclusion", "lane", "shoulder", "road"],
                "fallback": "trajectory_corridor",
            },
            "route_processing": {
                "core_tile_length_m": 10.0,
                "halo_m": 3.0,
                "report_segment_length_m": 20.0,
                "ownership": "defect_centroid_in_core",
                "resume": True,
                "execution": "offline_chunk_cli",
                "outputs": [
                    "route_manifest",
                    "route_defects_geojson",
                    "route_defects_parquet",
                    "route_segments_parquet",
                ],
            },
            "web_viewer": {
                "default_map_adapter": "local_enu",
                "external_map_adapters": {
                    "vworld": "runtime_key_and_wgs84_configuration_required",
                    "cesium": "runtime_token_and_wgs84_configuration_required",
                },
                "route_loading": "manifest_then_one_selected_tile_json",
                "full_point_cloud_to_browser": False,
                "review_mutation": "planned_stage_10",
            },
            "report_v2": {
                "profile": "internal_korean_geometry_evidence_v2",
                "source_of_truth": "html",
                "input_contract": "summary_json_segments_json_defects_json",
                "outputs": [
                    "html",
                    "summary_csv",
                    "segments_csv",
                    "defects_csv",
                    "per_defect_evidence",
                ],
                "pdf": "optional_offline_chromium_cli",
                "missing_evidence_policy": "N/A_and_continue",
            },
            "rgb_crack_ai": {
                "contract_version": 1,
                "worker_boundary": "services/road_condition_crack_worker",
                "holdout_protocol": "road-condition-crack-holdout-v1",
                "automatic_approval_enabled": False,
                "neural_inference_state": "not_configured",
                "geometry_api_contains_pytorch": False,
                "reason": "labels, approved weights, metric thresholds, and GPU environment are not provided",
            },
            "pose_contract": {
                "camera_poses_format_version": 1,
                "analysis_source_manifest_format_version": 1,
                "frame_reprojection_feature_flag": "config.pose.frame_reprojection_enabled",
                "default_mode": "ply_only",
            },
            "implemented_outputs": [
                "summary",
                "segments",
                "local_geojson",
                "enu_geojson",
                "surface_preview",
                "html_report",
                "maintenance_scenario",
                "calibration_quality_metadata",
                "advanced_geometry_screening",
                "report_v2_evidence_package",
                "rgb_crack_contract_and_holdout_gate",
            ],
            "planned_outputs": [
                "rgb_cracks",
                "patching",
                "raveling",
                "ponding_network_model",
                "calibrated_iri",
                "certified_pci",
                "pdf_report",
            ],
            "default_config": AnalysisConfig().to_dict(),
            "default_unit_prices": DEFAULT_UNIT_PRICES,
        }

    @app.post("/api/v1/jobs", status_code=202)
    def create_job(payload: CreateJobRequest, request: Request) -> dict[str, Any]:
        job_store = get_store(request)
        status = job_store.create(payload.model_dump(mode="json"))
        request.app.state.executor.submit(
            _run_job,
            status["job_id"],
            status["request"],
            settings=request.app.state.settings,
            store=job_store,
        )
        return status

    @app.post("/api/v1/demo", status_code=202)
    def create_demo(request: Request, profile: str = Query(default="mixed")) -> dict[str, Any]:
        try:
            payload = CreateJobRequest(source_type="synthetic", synthetic_profile=profile)
        except Exception as exc:  # Pydantic returns a structured 422 only for body models
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return create_job(payload, request)

    @app.get("/api/v1/jobs")
    def list_jobs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        return {"jobs": get_store(request).list_statuses(limit)}

    @app.get("/api/v1/route-datasets/manifest")
    def get_route_manifest(
        request: Request,
        path: str = Query(min_length=1, max_length=1024),
    ) -> dict[str, Any]:
        try:
            return read_route_manifest(
                request.app.state.settings.workspace_root,
                path,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="route dataset not found") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/route-datasets/tile")
    def get_route_tile_artifact(
        request: Request,
        path: str = Query(min_length=1, max_length=1024),
        tile_id: str = Query(min_length=1, max_length=64),
        artifact: str = Query(min_length=1, max_length=64),
    ) -> Any:
        try:
            return read_route_tile_artifact(
                request.app.state.settings.workspace_root,
                path,
                tile_id,
                artifact,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="route tile not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            return get_store(request).read_status(job_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    def result_json(job_id: str, filename: str, request: Request) -> Any:
        try:
            status = get_store(request).read_status(job_id)
            if status.get("state") != "completed":
                raise HTTPException(status_code=409, detail="job is not completed")
            return get_store(request).read_result_json(job_id, filename)
        except HTTPException:
            raise
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="result not found") from exc

    @app.get("/api/v1/jobs/{job_id}/summary")
    def get_summary(job_id: str, request: Request) -> Any:
        return result_json(job_id, "summary.json", request)

    @app.get("/api/v1/jobs/{job_id}/defects")
    def get_defects(job_id: str, request: Request) -> Any:
        return result_json(job_id, "defects.json", request)

    @app.get("/api/v1/jobs/{job_id}/defects.local.geojson")
    def get_local_geojson(job_id: str, request: Request) -> Any:
        return result_json(job_id, "defects.local.geojson", request)

    @app.get("/api/v1/jobs/{job_id}/defects.enu.geojson")
    def get_enu_geojson(job_id: str, request: Request) -> Any:
        return result_json(job_id, "defects.enu.geojson", request)

    @app.get("/api/v1/jobs/{job_id}/segments")
    def get_segments(job_id: str, request: Request) -> Any:
        return result_json(job_id, "segments.json", request)

    @app.get("/api/v1/jobs/{job_id}/surface")
    def get_surface(job_id: str, request: Request) -> Any:
        return result_json(job_id, "surface_preview.json", request)

    def report_file(job_id: str, request: Request, relative_path: str) -> FileResponse:
        try:
            status = get_store(request).read_status(job_id)
            if status.get("state") != "completed":
                raise HTTPException(status_code=409, detail="job is not completed")
            result_dir = get_store(request).result_dir(job_id)
            report_root = (result_dir / "report").resolve()
            if not report_root.is_dir():
                if relative_path != "report.html":
                    raise FileNotFoundError(relative_path)
                path = (result_dir / "report.html").resolve()
                try:
                    path.relative_to(result_dir.resolve())
                except ValueError as exc:
                    raise ValueError("legacy report escapes result root") from exc
            else:
                path = (report_root / relative_path).resolve()
                try:
                    path.relative_to(report_root)
                except ValueError as exc:
                    raise ValueError("report asset escapes report root") from exc
                allowed_suffixes = {".html", ".csv", ".json", ".png", ".jpg", ".svg", ".pdf"}
                if path.suffix.lower() not in allowed_suffixes:
                    raise ValueError("unsupported report asset")
            if not path.is_file():
                raise FileNotFoundError(path)
            return FileResponse(path)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="report not found") from exc

    @app.get("/api/v1/jobs/{job_id}/report", response_class=RedirectResponse)
    def get_report(job_id: str, request: Request) -> RedirectResponse:
        try:
            status = get_store(request).read_status(job_id)
            if status.get("state") != "completed":
                raise HTTPException(status_code=409, detail="job is not completed")
        except HTTPException:
            raise
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="report not found") from exc
        return RedirectResponse(
            url=f"/api/v1/jobs/{job_id}/report/",
            status_code=307,
        )

    @app.get("/api/v1/jobs/{job_id}/report/")
    def get_report_index(job_id: str, request: Request) -> FileResponse:
        return report_file(job_id, request, "report.html")

    @app.get("/api/v1/jobs/{job_id}/report/{asset_path:path}")
    def get_report_asset(
        job_id: str,
        asset_path: str,
        request: Request,
    ) -> FileResponse:
        return report_file(job_id, request, asset_path)

    @app.post("/api/v1/jobs/{job_id}/scenarios")
    def scenario(job_id: str, payload: ScenarioRequest, request: Request) -> dict[str, Any]:
        summary = result_json(job_id, "summary.json", request)
        defects = result_json(job_id, "defects.json", request)
        try:
            return calculate_maintenance_scenario(
                summary,
                defects,
                unit_prices=payload.unit_prices,
                include_types=set(payload.include_types),
                rainfall_mm=payload.rainfall_mm,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, request: Request) -> None:
        try:
            status = get_store(request).read_status(job_id)
            if status.get("state") == "running":
                raise HTTPException(status_code=409, detail="running job cannot be deleted")
            get_store(request).delete(job_id)
        except HTTPException:
            raise
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    return app


app = create_app()
