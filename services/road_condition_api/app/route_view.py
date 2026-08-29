from __future__ import annotations

import hashlib
import json
import re
import struct
from copy import deepcopy
from pathlib import Path
from typing import Any

from road_condition_core.io import resolve_relative_path


_TILE_ID = re.compile(r"^tile-[0-9]{6}$")
_ARTIFACTS = {
    "summary": "summary.json",
    "surface": "surface_preview.json",
    "defects": "defects.json",
    "segments": "segments.json",
    "defects_local_geojson": "defects.local.geojson",
    "defects_enu_geojson": "defects.enu.geojson",
}
_MAX_JSON_BYTES = 25 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 1024 * 1024
_RCEV_HEADER = struct.Struct("<4sIII6f6I")


def _read_json(path: Path, allowed_root: Path) -> Any:
    resolved = path.resolve()
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError as exc:
        raise ValueError("viewer JSON path escapes route result") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    size = resolved.stat().st_size
    if size > _MAX_JSON_BYTES:
        raise ValueError(f"viewer JSON exceeds {_MAX_JSON_BYTES} bytes")
    return json.loads(resolved.read_text(encoding="utf-8"))


def _route_root(workspace_root: Path, relative_path: str) -> tuple[Path, str]:
    root = resolve_relative_path(workspace_root, relative_path)
    if not root.is_dir():
        raise FileNotFoundError(root)
    normalized = str(root.relative_to(workspace_root.expanduser().resolve()))
    return root, normalized


def read_route_manifest(workspace_root: Path, relative_path: str) -> dict[str, Any]:
    root, normalized = _route_root(workspace_root, relative_path)
    manifest = _read_json(root / "route_manifest.json", root)
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise ValueError("unsupported route manifest")
    evidence_tiles: dict[str, dict[str, Any]] = {}
    evidence_path = root / "evidence" / "manifest.json"
    if evidence_path.is_file():
        evidence_manifest = _read_json(evidence_path, root)
        if (
            isinstance(evidence_manifest, dict)
            and evidence_manifest.get("format_version") == 1
            and evidence_manifest.get("evidence_contract") == "road-condition-rcev-v1"
        ):
            evidence_tiles = {
                str(item.get("tile_id")): item
                for item in evidence_manifest.get("tiles", [])
                if isinstance(item, dict) and item.get("state") == "completed"
            }
    tiles = []
    for item in manifest.get("tiles", []):
        if not isinstance(item, dict):
            continue
        tile_id = str(item.get("tile_id", ""))
        if not _TILE_ID.fullmatch(tile_id):
            continue
        public_tile = {
                key: deepcopy(item.get(key))
                for key in (
                    "tile_id",
                    "state",
                    "core_start_m",
                    "core_end_m",
                    "halo_start_m",
                    "halo_end_m",
                    "is_last",
                    "input_point_count",
                    "owned_defect_count",
                    "core_segment_count",
                    "error",
                )
                if key in item
            }
        evidence = evidence_tiles.get(tile_id)
        if evidence is not None:
            public_tile["evidence"] = {
                key: deepcopy(evidence.get(key))
                for key in (
                    "state",
                    "point_count",
                    "byte_size",
                    "masked_point_count",
                    "bbox_min_enu_m",
                    "bbox_max_enu_m",
                )
            }
        tiles.append(public_tile)
    tiles.sort(key=lambda item: str(item["tile_id"]))
    return {
        "format_version": 1,
        "dataset_id": hashlib.sha256(normalized.encode()).hexdigest()[:16],
        "workspace_relative_path": normalized,
        "state": manifest.get("state"),
        "route_config": deepcopy(manifest.get("route_config", {})),
        "tile_count": len(tiles),
        "completed_tile_count": sum(item.get("state") == "completed" for item in tiles),
        "failed_tile_count": sum(item.get("state") == "failed" for item in tiles),
        "defect_count": manifest.get("defect_count"),
        "segment_count": manifest.get("segment_count"),
        "tiles": tiles,
        "viewer_contract": {
            "loading": "one_selected_tile_json_at_a_time",
            "full_point_cloud_served": False,
            "allowed_artifacts": sorted(_ARTIFACTS),
            "point_evidence": {
                "available": bool(evidence_tiles),
                "contract": "road-condition-rcev-v1" if evidence_tiles else None,
                "loading": "one_selected_quantized_tile_at_a_time",
            },
        },
    }


def read_route_evidence_manifest(
    workspace_root: Path,
    relative_path: str,
) -> dict[str, Any]:
    root, normalized = _route_root(workspace_root, relative_path)
    payload = _read_json(root / "evidence" / "manifest.json", root)
    if (
        not isinstance(payload, dict)
        or payload.get("format_version") != 1
        or payload.get("evidence_contract") != "road-condition-rcev-v1"
    ):
        raise ValueError("unsupported route evidence manifest")
    output = deepcopy(payload)
    output["workspace_relative_path"] = normalized
    output.pop("source", None)
    return output


def resolve_route_evidence_tile(
    workspace_root: Path,
    relative_path: str,
    tile_id: str,
) -> tuple[Path, dict[str, Any]]:
    if not _TILE_ID.fullmatch(tile_id):
        raise ValueError("invalid route tile ID")
    root, _normalized = _route_root(workspace_root, relative_path)
    manifest = read_route_evidence_manifest(workspace_root, relative_path)
    tile = next(
        (
            item
            for item in manifest.get("tiles", [])
            if isinstance(item, dict) and item.get("tile_id") == tile_id
        ),
        None,
    )
    if tile is None or tile.get("state") != "completed":
        raise FileNotFoundError(tile_id)
    expected_artifact = f"tiles/{tile_id}.rcev"
    if tile.get("artifact") != expected_artifact:
        raise ValueError("route evidence artifact does not match tile ID")
    evidence_root = (root / "evidence").resolve()
    resolved = (evidence_root / expected_artifact).resolve()
    try:
        resolved.relative_to(evidence_root)
    except ValueError as exc:
        raise ValueError("route evidence path escapes route result") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    size = resolved.stat().st_size
    if size > _MAX_EVIDENCE_BYTES:
        raise ValueError("route evidence tile exceeds 1 MiB")
    with resolved.open("rb") as stream:
        header = stream.read(_RCEV_HEADER.size)
    if len(header) != _RCEV_HEADER.size:
        raise ValueError("route evidence header is incomplete")
    magic, version, count, stride = _RCEV_HEADER.unpack(header)[:4]
    if magic != b"RCEV" or version != 1 or stride != 12:
        raise ValueError("unsupported route evidence tile")
    if _RCEV_HEADER.size + int(count) * int(stride) != size:
        raise ValueError("route evidence payload size mismatch")
    return resolved, tile


def read_route_tile_artifact(
    workspace_root: Path,
    relative_path: str,
    tile_id: str,
    artifact: str,
) -> Any:
    if not _TILE_ID.fullmatch(tile_id):
        raise ValueError("invalid route tile ID")
    if artifact not in _ARTIFACTS:
        raise ValueError("unsupported route tile artifact")
    root, _normalized = _route_root(workspace_root, relative_path)
    manifest = read_route_manifest(workspace_root, relative_path)
    tile = next(
        (item for item in manifest["tiles"] if item["tile_id"] == tile_id),
        None,
    )
    if tile is None:
        raise FileNotFoundError(tile_id)
    if tile.get("state") != "completed":
        raise RuntimeError("route tile is not completed")
    result_root = (root / "tiles" / tile_id / "result").resolve()
    try:
        result_root.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("route tile path escapes route result") from exc
    return _read_json(result_root / _ARTIFACTS[artifact], root)
