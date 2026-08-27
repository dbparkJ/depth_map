from __future__ import annotations

import hashlib
import json
import re
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
    tiles = []
    for item in manifest.get("tiles", []):
        if not isinstance(item, dict):
            continue
        tile_id = str(item.get("tile_id", ""))
        if not _TILE_ID.fullmatch(tile_id):
            continue
        tiles.append(
            {
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
        )
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
        },
    }


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
