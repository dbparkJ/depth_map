import pytest

from rgbd_map.cli import (
    CLOUD_PRESETS,
    CloudBuildConfig,
    build_parser,
    resolve_cloud_build_config,
)


def parse_cloud_args(*options: str):
    return build_parser().parse_args(
        ["/tmp/synthetic_dataset", "--output", "/tmp/synthetic_output", *options]
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "preview",
            CloudBuildConfig(10, 10, 0.25, 1_000_000, 300_000, 5_000, 0.15, 0.90),
        ),
        (
            "balanced",
            CloudBuildConfig(2, 4, 0.10, 5_000_000, 700_000, 20_000, 0.10, 0.98),
        ),
        (
            "dense",
            CloudBuildConfig(1, 2, 0.05, 20_000_000, 800_000, 50_000, 0.05, 0.98),
        ),
    ],
)
def test_cloud_presets_resolve_to_documented_values(name, expected):
    assert CLOUD_PRESETS[name] == expected
    assert resolve_cloud_build_config(parse_cloud_args("--cloud-preset", name)) == expected


def test_balanced_is_default_and_preset_arguments_start_unresolved():
    args = parse_cloud_args()
    assert args.cloud_preset == "balanced"
    assert args.cloud_frame_stride is None
    assert args.pixel_stride is None
    assert args.voxel_size_m is None
    assert args.max_points is None
    assert args.browser_max_points is None
    assert args.per_frame_max_points is None
    assert args.roi_top_ratio is None
    assert args.roi_bottom_ratio is None
    assert resolve_cloud_build_config(args) == CLOUD_PRESETS["balanced"]


def test_individual_arguments_override_only_the_selected_preset_values():
    args = parse_cloud_args(
        "--cloud-preset",
        "balanced",
        "--voxel-size-m",
        "0.07",
        "--per-frame-max-points",
        "0",
    )
    assert resolve_cloud_build_config(args) == CloudBuildConfig(
        frame_stride=2,
        pixel_stride=4,
        voxel_size_m=0.07,
        max_points=5_000_000,
        browser_max_points=700_000,
        per_frame_max_points=0,
        roi_top_ratio=0.10,
        roi_bottom_ratio=0.98,
    )


def test_partial_keyframe_options_leave_unspecified_conditions_disabled():
    config = resolve_cloud_build_config(
        parse_cloud_args(
            "--cloud-keyframe-distance-m",
            "0.2",
            "--cloud-keyframe-max-dt-s",
            "0.5",
        )
    )
    assert config.frame_stride == 2
    assert config.keyframe_distance_m == 0.2
    assert config.keyframe_angle_deg is None
    assert config.keyframe_max_dt_s == 0.5


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (("--cloud-frame-stride", "0"), "cloud-frame-stride"),
        (("--pixel-stride", "0"), "pixel-stride"),
        (("--voxel-size-m", "0"), "voxel-size-m"),
        (("--max-points", "0"), "max-points"),
        (("--browser-max-points", "0"), "browser-max-points"),
        (("--per-frame-max-points", "-1"), "per-frame-max-points"),
        (("--roi-top-ratio", "0.99"), "cloud ROI"),
        (("--cloud-keyframe-angle-deg", "0"), "cloud-keyframe-angle-deg"),
        (("--cloud-keyframe-angle-deg", "181"), "must not exceed 180"),
        (("--min-depth-m", "10", "--max-depth-m", "5"), "depth range"),
    ],
)
def test_invalid_cloud_config_is_rejected(options, message):
    with pytest.raises(ValueError, match=message):
        resolve_cloud_build_config(parse_cloud_args(*options))
