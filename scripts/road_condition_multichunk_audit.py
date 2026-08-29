#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import queue
import resource
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from road_condition_core.audit import plan_audit_windows, summarize_audit_tile
from road_condition_core.config import AnalysisConfig
from road_condition_core.detectors import detect_potholes
from road_condition_core.geometry import project_to_trajectory
from road_condition_core.io import load_mapping_bundle
from road_condition_core.pipeline import ALGORITHM_VERSION, analyze_points


FORMAT_VERSION = 1
AUDIT_CONTRACT = "road-condition-multichunk-audit-v1"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
    temporary.replace(path)


def _origin(summary: dict[str, Any]) -> dict[str, float] | None:
    value = summary.get("origin")
    if not isinstance(value, dict):
        return None
    try:
        return {
            "longitude_deg": float(value["longitude_deg"]),
            "latitude_deg": float(value["latitude_deg"]),
            "ellipsoid_height_m": float(value["ellipsoid_height_m"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _audit_chunk(mapping_output: Path) -> dict[str, Any]:
    started = time.monotonic()
    bundle = load_mapping_bundle(
        mapping_output,
        stage="raw",
        metadata_fields={"position_std_m", "independent_view_count"},
    )
    load_seconds = time.monotonic() - started
    projection_started = time.monotonic()
    coordinates = project_to_trajectory(bundle.points_enu_m, bundle.trajectory_enu_m)
    projection_seconds = time.monotonic() - projection_started
    route_length = float(coordinates.trajectory_cumulative_m[-1])
    windows = plan_audit_windows(route_length)
    config = AnalysisConfig()
    literature_detection = replace(
        config.detection,
        pothole_min_depth_m=0.025,
        pothole_min_area_m2=0.020,
    )
    finite = np.isfinite(coordinates.along_track_m)
    tile_results: list[dict[str, Any]] = []
    metadata = bundle.point_metadata
    for window in windows:
        selected = (
            finite
            & (coordinates.along_track_m >= window.halo_start_m)
            & (coordinates.along_track_m <= window.halo_end_m)
        )
        selected_count = int(np.count_nonzero(selected))
        tile_started = time.monotonic()
        try:
            tile_metadata = {
                name: np.asarray(values)[selected]
                for name, values in metadata.items()
                if np.asarray(values).shape == (len(bundle.points_enu_m),)
            }
            products = analyze_points(
                bundle.points_enu_m[selected],
                bundle.colors_rgb[selected],
                bundle.trajectory_enu_m,
                config=config,
                point_metadata=tile_metadata,
                source={
                    "type": "mapping_bundle",
                    "chunk_id": mapping_output.name,
                    "audit_window": window.label,
                    "point_cloud_stage": "raw",
                },
                source_origin=_origin(bundle.summary),
                quality_context=bundle.analysis_quality,
            )
            comparison = detect_potholes(products.surface, literature_detection)
            summary = summarize_audit_tile(
                products,
                window,
                alternative_potholes=comparison,
            )
            summary.update(
                {
                    "state": "completed",
                    "selected_raw_point_count": selected_count,
                    "wall_time_seconds": time.monotonic() - tile_started,
                }
            )
        except Exception as exc:  # noqa: BLE001 - one control tile must not stop the audit
            summary = {
                "window": window.to_dict(),
                "state": "failed",
                "selected_raw_point_count": selected_count,
                "wall_time_seconds": time.monotonic() - tile_started,
                "error": f"{type(exc).__name__}: {exc}",
            }
        tile_results.append(summary)
    return {
        "chunk_id": mapping_output.name,
        "state": (
            "completed"
            if tile_results and all(item["state"] == "completed" for item in tile_results)
            else "partial"
        ),
        "mapping_output": str(mapping_output),
        "raw_cloud_bytes": (mapping_output / "data" / "cloud_raw_enu.ply").stat().st_size,
        "raw_point_count": int(len(bundle.points_enu_m)),
        "trajectory_point_count": int(len(bundle.trajectory_enu_m)),
        "trajectory_length_m": route_length,
        "window_policy": "q25_q50_q75_or_one_central_full_for_short_control",
        "load_wall_time_seconds": load_seconds,
        "projection_wall_time_seconds": projection_seconds,
        "wall_time_seconds": time.monotonic() - started,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
        "tiles": tile_results,
    }


def _worker(mapping_output: str, result_queue: Any) -> None:
    try:
        result_queue.put(_audit_chunk(Path(mapping_output)))
    except Exception as exc:  # noqa: BLE001 - process boundary reports durable failure
        result_queue.put(
            {
                "chunk_id": Path(mapping_output).name,
                "state": "failed",
                "mapping_output": mapping_output,
                "error": f"{type(exc).__name__}: {exc}",
                "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                / 1024.0,
            }
        )


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    tiles = [
        tile
        for chunk in results
        for tile in chunk.get("tiles", [])
        if tile.get("state") == "completed"
    ]

    def distribution(path: tuple[str, ...]) -> dict[str, float | None]:
        values: list[float] = []
        for tile in tiles:
            value: Any = tile
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if value is not None and np.isfinite(float(value)):
                values.append(float(value))
        if not values:
            return {"min": None, "p25": None, "p50": None, "p75": None, "max": None}
        quantiles = np.quantile(np.asarray(values), [0.0, 0.25, 0.5, 0.75, 1.0])
        return {
            name: float(value)
            for name, value in zip(("min", "p25", "p50", "p75", "max"), quantiles, strict=True)
        }

    return {
        "chunk_count": len(results),
        "completed_chunk_count": sum(item.get("state") == "completed" for item in results),
        "partial_chunk_count": sum(item.get("state") == "partial" for item in results),
        "failed_chunk_count": sum(item.get("state") == "failed" for item in results),
        "completed_tile_count": len(tiles),
        "failed_tile_count": sum(
            tile.get("state") == "failed"
            for chunk in results
            for tile in chunk.get("tiles", [])
        ),
        "raw_point_count": sum(int(item.get("raw_point_count", 0)) for item in results),
        "trajectory_length_m": sum(float(item.get("trajectory_length_m", 0.0)) for item in results),
        "maximum_peak_rss_mib": max(
            (float(item.get("peak_rss_mib", 0.0)) for item in results), default=0.0
        ),
        "coverage_distributions": {
            "supported": distribution(("coverage", "supported_coverage_ratio")),
            "usable": distribution(("coverage", "valid_coverage_ratio")),
            "plausibility_excluded_supported": distribution(
                ("coverage", "plausibility_excluded_supported_ratio")
            ),
        },
        "candidate_distributions": {
            "count": distribution(("defects", "count")),
            "edge_ratio": distribution(("defects", "edge_candidate_ratio")),
            "max_pothole_depth_m": distribution(("defects", "max_pothole_depth_m")),
            "max_rutting_depth_m": distribution(("defects", "max_rutting_depth_m")),
            "max_bump_height_m": distribution(("defects", "max_bump_height_m")),
            "literature_pothole_count_delta": distribution(
                ("literature_comparison", "count_delta_vs_current")
            ),
            "bump_boundary_guard_removed_components": distribution(
                ("quality", "bump_boundary_guard_removed_component_count")
            ),
        },
    }


def _write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "chunk_id",
        "chunk_state",
        "window",
        "tile_state",
        "route_length_m",
        "selected_raw_point_count",
        "supported_coverage_ratio",
        "valid_coverage_ratio",
        "plausibility_excluded_supported_ratio",
        "bump_boundary_guard_removed_components",
        "defect_count",
        "pothole_count",
        "rutting_count",
        "bump_count",
        "edge_candidate_ratio",
        "max_pothole_depth_m",
        "max_rutting_depth_m",
        "max_bump_height_m",
        "literature_pothole_count",
        "literature_pothole_count_delta",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for chunk in results:
            for tile in chunk.get("tiles", []) or [{"state": chunk.get("state")}]:
                coverage = tile.get("coverage") or {}
                defects = tile.get("defects") or {}
                by_type = defects.get("by_type") or {}
                literature = tile.get("literature_comparison") or {}
                quality = tile.get("quality") or {}
                writer.writerow(
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "chunk_state": chunk.get("state"),
                        "window": (tile.get("window") or {}).get("label"),
                        "tile_state": tile.get("state"),
                        "route_length_m": chunk.get("trajectory_length_m"),
                        "selected_raw_point_count": tile.get("selected_raw_point_count"),
                        "supported_coverage_ratio": coverage.get("supported_coverage_ratio"),
                        "valid_coverage_ratio": coverage.get("valid_coverage_ratio"),
                        "plausibility_excluded_supported_ratio": coverage.get("plausibility_excluded_supported_ratio"),
                        "bump_boundary_guard_removed_components": quality.get(
                            "bump_boundary_guard_removed_component_count"
                        ),
                        "defect_count": defects.get("count"),
                        "pothole_count": by_type.get("pothole", 0),
                        "rutting_count": by_type.get("rutting", 0),
                        "bump_count": by_type.get("bump", 0),
                        "edge_candidate_ratio": defects.get("edge_candidate_ratio"),
                        "max_pothole_depth_m": defects.get("max_pothole_depth_m"),
                        "max_rutting_depth_m": defects.get("max_rutting_depth_m"),
                        "max_bump_height_m": defects.get("max_bump_height_m"),
                        "literature_pothole_count": literature.get("pothole_count"),
                        "literature_pothole_count_delta": literature.get("count_delta_vs_current"),
                        "error": tile.get("error", chunk.get("error")),
                    }
                )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit q25/q50/q75 10 m windows across mapping chunks in isolated processes."
    )
    parser.add_argument("mapping_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--max-chunks", type=int, default=None)
    parser.add_argument("--chunk-id", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    mapping_root = args.mapping_root.expanduser().resolve()
    chunks = sorted(
        path
        for path in mapping_root.glob("chunk_[0-9][0-9][0-9][0-9]")
        if path.is_dir()
    )
    if args.chunk_id:
        requested = set(args.chunk_id)
        chunks = [path for path in chunks if path.name in requested]
        missing = requested - {path.name for path in chunks}
        if missing:
            raise SystemExit("requested chunks not found: " + ", ".join(sorted(missing)))
    if args.max_chunks is not None:
        chunks = chunks[: max(0, args.max_chunks)]
    if not chunks:
        raise SystemExit("no mapping chunk directories found")
    output = args.output.expanduser().resolve()
    previous: dict[str, Any] = {}
    if output.is_file() and not args.force:
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("audit_contract") != AUDIT_CONTRACT:
            raise SystemExit("existing output uses a different audit contract")
    result_by_chunk = {
        str(item.get("chunk_id")): item
        for item in previous.get("chunks", [])
        if isinstance(item, dict)
    }
    context = mp.get_context("spawn")
    run_started = time.monotonic()
    for index, chunk in enumerate(chunks, start=1):
        prior = result_by_chunk.get(chunk.name)
        if prior and prior.get("state") == "completed" and not args.force:
            print(f"[{index}/{len(chunks)}] {chunk.name}: resume skip", flush=True)
            continue
        result_queue = context.Queue(maxsize=1)
        process = context.Process(target=_worker, args=(str(chunk), result_queue))
        process.start()
        process.join(timeout=max(1, args.timeout_seconds))
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
            result = {
                "chunk_id": chunk.name,
                "state": "failed",
                "mapping_output": str(chunk),
                "error": f"TimeoutError: exceeded {args.timeout_seconds} seconds",
            }
        else:
            try:
                result = result_queue.get(timeout=5)
            except queue.Empty:
                result = {
                    "chunk_id": chunk.name,
                    "state": "failed",
                    "mapping_output": str(chunk),
                    "error": f"worker exited {process.exitcode} without a result",
                }
        result_queue.close()
        result_by_chunk[chunk.name] = result
        ordered = [result_by_chunk[path.name] for path in chunks if path.name in result_by_chunk]
        payload = {
            "format_version": FORMAT_VERSION,
            "audit_contract": AUDIT_CONTRACT,
            "state": "running",
            "mapping_root": str(mapping_root),
            "parameters": {
                "algorithm_version": ALGORITHM_VERSION,
                "point_cloud_stage": "raw",
                "core_length_m": 10.0,
                "halo_m": 3.0,
                "normal_windows": [0.25, 0.50, 0.75],
                "short_chunk_policy": "one central or full window",
                "current_pothole_threshold": {"depth_m": 0.035, "area_m2": 0.035},
                "literature_comparison_only": {"depth_m": 0.025, "area_m2": 0.020},
                "per_chunk_timeout_seconds": args.timeout_seconds,
                "peak_rss_stop_mib": 10240,
            },
            "summary": _summary(ordered),
            "chunks": ordered,
            "controller_wall_time_seconds": time.monotonic() - run_started,
        }
        _atomic_json(output, payload)
        _write_csv(output.with_suffix(".csv"), ordered)
        print(
            f"[{index}/{len(chunks)}] {chunk.name}: {result.get('state')} "
            f"{float(result.get('wall_time_seconds', 0.0)):.1f}s "
            f"{float(result.get('peak_rss_mib', 0.0)):.1f}MiB",
            flush=True,
        )
        if float(result.get("peak_rss_mib", 0.0)) > 10240:
            print("stopped: peak RSS exceeded 10 GiB", flush=True)
            break
    final_results = [result_by_chunk[path.name] for path in chunks if path.name in result_by_chunk]
    final_summary = _summary(final_results)
    final_state = (
        "completed"
        if len(final_results) == len(chunks)
        and final_summary["failed_chunk_count"] == 0
        and final_summary["partial_chunk_count"] == 0
        else "partial"
    )
    payload = {
        "format_version": FORMAT_VERSION,
        "audit_contract": AUDIT_CONTRACT,
        "state": final_state,
        "mapping_root": str(mapping_root),
        "parameters": {
            "algorithm_version": ALGORITHM_VERSION,
            "point_cloud_stage": "raw",
            "core_length_m": 10.0,
            "halo_m": 3.0,
            "normal_windows": [0.25, 0.50, 0.75],
            "short_chunk_policy": "one central or full window",
            "current_pothole_threshold": {"depth_m": 0.035, "area_m2": 0.035},
            "literature_comparison_only": {"depth_m": 0.025, "area_m2": 0.020},
            "per_chunk_timeout_seconds": args.timeout_seconds,
            "peak_rss_stop_mib": 10240,
        },
        "summary": final_summary,
        "chunks": final_results,
        "controller_wall_time_seconds": time.monotonic() - run_started,
    }
    _atomic_json(output, payload)
    _write_csv(output.with_suffix(".csv"), final_results)
    print(json.dumps({"state": final_state, **final_summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
