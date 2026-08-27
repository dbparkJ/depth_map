from __future__ import annotations

import argparse
from dataclasses import dataclass

from .postprocess_config import POSTPROCESS_PRESET_NAMES, PostprocessConfig


@dataclass(frozen=True)
class PostprocessOutputOptions:
    keep_raw_cloud: bool
    save_removed_cloud: bool
    write_diagnostics: bool
    write_debug_stages: bool
    debug_stage_max_points: int
    auto_fallback: bool


def add_postprocess_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("point-cloud postprocessing")
    group.add_argument(
        "--postprocess-preset",
        choices=POSTPROCESS_PRESET_NAMES,
        default="road-map",
        help="Cleanup preset applied after dense cloud construction (default: road-map)",
    )
    group.add_argument(
        "--neighbor-backend",
        choices=("auto", "open3d", "scipy"),
        default="auto",
        help="Radius/statistical neighbor backend (default: auto)",
    )
    group.add_argument(
        "--ground-backend",
        choices=("auto", "local", "pdal", "off"),
        default="auto",
        help="Ground cleanup backend (default: auto)",
    )
    group.add_argument(
        "--keep-raw-cloud",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Preserve cloud_raw_enu.ply and its aligned metadata",
    )
    group.add_argument(
        "--save-removed-cloud",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write removed points and reason metadata",
    )
    group.add_argument(
        "--write-postprocess-diagnostics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write top/side/reason diagnostic images and tile summaries",
    )
    group.add_argument(
        "--write-debug-stages",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Write capped survivor/removal samples and fixed-view diagnostics for "
            "every 3-D cleanup stage (default: disabled)"
        ),
    )
    group.add_argument(
        "--debug-stage-max-points",
        type=int,
        default=500_000,
        help="Maximum survivor and newly-removed sample points per debug stage",
    )
    group.add_argument(
        "--auto-postprocess-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Try at most one conservative/aggressive postprocess-only fallback",
    )

    group.add_argument("--depth-edge-radius-px", type=int, default=None)
    group.add_argument("--depth-edge-abs-m", type=float, default=None)
    group.add_argument("--depth-edge-rel-ratio", type=float, default=None)
    group.add_argument("--depth-edge-min-valid-neighbors", type=int, default=None)
    group.add_argument("--min-distinct-frames", type=int, default=None)
    group.add_argument("--max-voxel-position-std-m", type=float, default=None)
    group.add_argument("--radius-outlier-radius-m", type=float, default=None)
    group.add_argument("--radius-outlier-min-neighbors", type=int, default=None)
    group.add_argument("--statistical-neighbors", type=int, default=None)
    group.add_argument("--statistical-std-ratio", type=float, default=None)
    group.add_argument("--postprocess-tile-size-m", type=float, default=None)
    group.add_argument("--postprocess-tile-overlap-m", type=float, default=None)
    group.add_argument("--road-corridor-half-width-m", type=float, default=None)
    group.add_argument("--ground-grid-size-m", type=float, default=None)
    group.add_argument("--below-ground-tolerance-m", type=float, default=None)
    group.add_argument(
        "--far-depth-policy",
        choices=("off", "fixed", "adaptive"),
        default=None,
    )
    group.add_argument("--far-depth-soft-start-m", type=float, default=None)
    group.add_argument("--far-depth-hard-m", type=float, default=None)
    group.add_argument("--depth-confidence-threshold", type=float, default=None)
    group.add_argument(
        "--depth-confidence-order",
        choices=("lower-is-better", "higher-is-better"),
        default=None,
    )
    group.add_argument(
        "--depth-edge-domain",
        choices=("depth", "inverse-depth"),
        default=None,
    )
    group.add_argument("--support-voxel-size-m", type=float, default=None)
    group.add_argument("--support-far-voxel-size-m", type=float, default=None)
    group.add_argument("--support-min-independent-frames", type=int, default=None)
    group.add_argument("--support-min-baseline-m", type=float, default=None)
    group.add_argument("--temporal-window-seconds", type=float, default=None)
    group.add_argument("--temporal-depth-abs-m", type=float, default=None)
    group.add_argument("--temporal-depth-rel-ratio", type=float, default=None)
    group.add_argument(
        "--temporal-max-free-space-contradictions", type=int, default=None
    )
    group.add_argument(
        "--pose-cloud-policy",
        choices=("keep", "skip", "interpolate"),
        default=None,
    )
    group.add_argument("--pose-cloud-max-edge-dt-s", type=float, default=None)
    group.add_argument("--pose-cloud-min-inliers", type=int, default=None)
    group.add_argument("--pose-cloud-min-inlier-ratio", type=float, default=None)
    group.add_argument(
        "--pose-cloud-max-reprojection-error-px", type=float, default=None
    )
    group.add_argument("--ground-seed-half-width-m", type=float, default=None)
    group.add_argument("--ground-apply-half-width-m", type=float, default=None)
    group.add_argument(
        "--map-envelope-mode",
        choices=("off", "soft", "road-only"),
        default=None,
    )
    group.add_argument("--map-corridor-core-half-width-m", type=float, default=None)
    group.add_argument("--map-corridor-soft-half-width-m", type=float, default=None)
    group.add_argument("--map-envelope-end-buffer-m", type=float, default=None)


def resolve_output_options(
    args: argparse.Namespace, config: PostprocessConfig
) -> PostprocessOutputOptions:
    def selected(name: str, default: bool) -> bool:
        value = getattr(args, name, None)
        return default if value is None else bool(value)

    # Raw preservation is the safe default for every preset. Removed products and
    # diagnostics default on whenever a postprocess stage is enabled; road-map is
    # therefore fully enabled without extra flags as required by the CLI contract.
    debug_stage_max_points = int(getattr(args, "debug_stage_max_points", 500_000))
    if debug_stage_max_points <= 0:
        raise ValueError("--debug-stage-max-points must be positive")

    return PostprocessOutputOptions(
        keep_raw_cloud=selected("keep_raw_cloud", True),
        save_removed_cloud=selected("save_removed_cloud", config.enabled),
        write_diagnostics=selected("write_postprocess_diagnostics", config.enabled),
        write_debug_stages=bool(getattr(args, "write_debug_stages", False)),
        debug_stage_max_points=debug_stage_max_points,
        auto_fallback=bool(getattr(args, "auto_postprocess_fallback", True)),
    )
