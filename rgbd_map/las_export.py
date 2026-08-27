from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pyproj import CRS, Transformer

from .geodesy import LocalENU


STAGE_FILENAMES = {
    "raw": "cloud_raw_enu.ply",
    "clean": "cloud_clean_enu.ply",
    "removed": "cloud_removed_enu.ply",
}


@dataclass(frozen=True)
class LasExportPlan:
    input_ply: Path
    output_las: Path
    pipeline_json: Path
    report_json: Path
    stage: str
    source_point_count: int
    origin: LocalENU
    target_crs: CRS
    scale_m: float
    offset_xyz: tuple[float, float, float]


def infer_utm_crs(longitude_deg: float, latitude_deg: float) -> CRS:
    """Return the WGS 84 UTM CRS containing the supplied longitude/latitude."""

    if not -180.0 <= longitude_deg <= 180.0:
        raise ValueError("origin longitude must be in [-180, 180]")
    if not -80.0 <= latitude_deg <= 84.0:
        raise ValueError("automatic UTM selection requires latitude in [-80, 84]")
    zone = min(60, max(1, math.floor((longitude_deg + 180.0) / 6.0) + 1))
    epsg = (32600 if latitude_deg >= 0.0 else 32700) + zone
    return CRS.from_epsg(epsg)


def read_ply_vertex_count(path: Path) -> int:
    """Read the vertex count without loading a potentially huge PLY body."""

    with path.open("rb") as stream:
        first = stream.readline()
        if first.strip() != b"ply":
            raise ValueError(f"not a PLY file: {path}")
        for _ in range(10_000):
            line = stream.readline()
            if not line:
                break
            if line.startswith(b"element vertex "):
                return int(line.split()[2])
            if line.strip() == b"end_header":
                break
    raise ValueError(f"PLY header has no vertex count: {path}")


def _origin_from_summary(summary: Mapping[str, Any]) -> LocalENU:
    value = summary.get("origin")
    if not isinstance(value, Mapping):
        raise ValueError("summary.json does not contain an origin object")
    return LocalENU(
        origin_longitude_deg=float(value["longitude_deg"]),
        origin_latitude_deg=float(value["latitude_deg"]),
        origin_ellipsoid_height_m=float(value["ellipsoid_height_m"]),
    )


def make_export_plan(
    output_dir: str | Path,
    *,
    stage: str = "clean",
    output_las: str | Path | None = None,
    target_crs: str | CRS | None = None,
    scale_m: float = 0.001,
) -> LasExportPlan:
    output_path = Path(output_dir).expanduser().resolve()
    if stage not in STAGE_FILENAMES:
        raise ValueError(f"stage must be one of: {', '.join(STAGE_FILENAMES)}")
    if not math.isfinite(scale_m) or scale_m <= 0.0:
        raise ValueError("scale_m must be a positive finite number")

    data_dir = output_path / "data"
    summary_path = data_dir / "summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"mapping summary not found: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    origin = _origin_from_summary(summary)
    input_ply = data_dir / STAGE_FILENAMES[stage]
    if not input_ply.is_file():
        raise FileNotFoundError(f"{stage} point cloud not found: {input_ply}")

    crs = (
        infer_utm_crs(origin.origin_longitude_deg, origin.origin_latitude_deg)
        if target_crs is None
        else CRS.from_user_input(target_crs)
    )
    if not crs.is_projected:
        raise ValueError("target_crs must be a projected CRS with metre-scale coordinates")
    axis_units = {axis.unit_name.lower() for axis in crs.axis_info[:2]}
    if not axis_units or not all("metre" in unit or "meter" in unit for unit in axis_units):
        raise ValueError("target_crs horizontal axes must use metres")

    target = (
        Path(output_las).expanduser().resolve()
        if output_las is not None
        else data_dir / f"cloud_{stage}_epsg{crs.to_epsg() or 'custom'}.las"
    )
    pipeline_json = target.with_suffix(".pdal.json")
    report_json = target.with_suffix(".report.json")

    to_projected = Transformer.from_crs("EPSG:4979", crs, always_xy=True)
    offset = to_projected.transform(
        origin.origin_longitude_deg,
        origin.origin_latitude_deg,
        origin.origin_ellipsoid_height_m,
    )
    return LasExportPlan(
        input_ply=input_ply,
        output_las=target,
        pipeline_json=pipeline_json,
        report_json=report_json,
        stage=stage,
        source_point_count=read_ply_vertex_count(input_ply),
        origin=origin,
        target_crs=crs,
        scale_m=scale_m,
        offset_xyz=(float(offset[0]), float(offset[1]), float(offset[2])),
    )


def make_pdal_las_pipeline(plan: LasExportPlan) -> dict[str, Any]:
    """Build a streamable ENU -> ECEF -> projected LAS pipeline."""

    matrix = plan.origin.enu_to_ecef_affine_matrix()
    matrix_text = " ".join(f"{value:.17g}" for value in matrix.ravel())
    target_srs = plan.target_crs.to_string()
    return {
        "pipeline": [
            {"type": "readers.ply", "filename": str(plan.input_ply)},
            {
                "type": "filters.transformation",
                "matrix": matrix_text,
                "override_srs": "EPSG:4978",
            },
            {
                "type": "filters.reprojection",
                "in_srs": "EPSG:4978",
                "out_srs": target_srs,
                "error_on_failure": True,
            },
            {
                "type": "writers.las",
                "filename": str(plan.output_las),
                "a_srs": target_srs,
                "major_version": 1,
                "minor_version": 4,
                "dataformat_id": 7,
                "scale_x": plan.scale_m,
                "scale_y": plan.scale_m,
                "scale_z": plan.scale_m,
                "offset_x": plan.offset_xyz[0],
                "offset_y": plan.offset_xyz[1],
                "offset_z": plan.offset_xyz[2],
                "enhanced_srs_vlrs": True,
            },
        ]
    }


def _run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return json.loads(completed.stdout)


def export_las(
    plan: LasExportPlan,
    *,
    pdal_executable: str = "pdal",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Execute the export and return a verification report."""

    pdal_path = shutil.which(pdal_executable)
    if pdal_path is None:
        raise FileNotFoundError(
            f"PDAL executable not found: {pdal_executable!r}; run this command in "
            "the depth-map-postprocess Conda environment"
        )
    if plan.output_las.exists() and not overwrite:
        raise FileExistsError(
            f"output already exists: {plan.output_las}; pass --overwrite to replace it"
        )
    plan.output_las.parent.mkdir(parents=True, exist_ok=True)

    pipeline = make_pdal_las_pipeline(plan)
    plan.pipeline_json.write_text(
        json.dumps(pipeline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    try:
        subprocess.run(
            [pdal_path, "pipeline", "--stream", str(plan.pipeline_json)], check=True
        )
        summary = _run_json([pdal_path, "info", "--summary", str(plan.output_las)])
        metadata = _run_json([pdal_path, "info", "--metadata", str(plan.output_las)])
    except Exception:
        # A failed writer can leave a valid-looking but incomplete LAS file.
        if plan.output_las.exists():
            plan.output_las.unlink()
        raise

    las_summary = summary.get("summary", {})
    las_metadata = metadata.get("metadata", {})
    output_count = int(las_summary.get("num_points", -1))
    if output_count != plan.source_point_count:
        plan.output_las.unlink(missing_ok=True)
        raise RuntimeError(
            f"LAS point count mismatch: source={plan.source_point_count}, output={output_count}"
        )

    report: dict[str, Any] = {
        "input_ply": str(plan.input_ply),
        "output_las": str(plan.output_las),
        "stage": plan.stage,
        "source_point_count": plan.source_point_count,
        "output_point_count": output_count,
        "output_size_bytes": plan.output_las.stat().st_size,
        "source_coordinates": "local ENU metres",
        "target_crs": plan.target_crs.to_string(),
        "target_crs_name": plan.target_crs.name,
        "vertical_coordinate": "WGS 84 ellipsoid height in metres",
        "scale_m": plan.scale_m,
        "offset_xyz": list(plan.offset_xyz),
        "origin": {
            "longitude_deg": plan.origin.origin_longitude_deg,
            "latitude_deg": plan.origin.origin_latitude_deg,
            "ellipsoid_height_m": plan.origin.origin_ellipsoid_height_m,
        },
        "bounds": las_summary.get("bounds"),
        "dimensions": las_summary.get("dimensions"),
        "las_header": {
            key: las_metadata.get(key)
            for key in (
                "major_version",
                "minor_version",
                "dataformat_id",
                "global_encoding",
                "count",
                "scale_x",
                "scale_y",
                "scale_z",
                "offset_x",
                "offset_y",
                "offset_z",
                "minx",
                "miny",
                "minz",
                "maxx",
                "maxy",
                "maxz",
                "software_id",
            )
        },
        "crs_wkt_embedded": bool(las_metadata.get("spatialreference")),
        "pipeline_json": str(plan.pipeline_json),
    }
    temporary = plan.report_json.with_suffix(plan.report_json.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, plan.report_json)
    return report
