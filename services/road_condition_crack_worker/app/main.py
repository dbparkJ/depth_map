from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .bev import extract_crack_candidates
from .contracts import canonical_sha256, verify_model_bundle
from .metrics import evaluate_holdout


def _protocol_path() -> Path:
    configured = os.environ.get("ROAD_CONDITION_CRACK_PROTOCOL")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "config" / "holdout_protocol_v1.json"


def _protocol() -> dict[str, Any]:
    payload = json.loads(_protocol_path().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("holdout protocol must be a JSON object")
    return payload


def _capabilities() -> dict[str, Any]:
    protocol = _protocol()
    return {
        "service": "road-condition-crack-worker",
        "contract_version": 1,
        "ready_for_neural_inference": False,
        "readiness_reason": "approved model manifest and runtime adapter are not configured",
        "geometry_api_contains_pytorch": False,
        "implemented": [
            "model_manifest_validation",
            "weights_sha256_verification",
            "holdout_metric_evaluation",
            "depth_road_pose_masked_bev_contract",
            "bev_probability_postprocessing",
            "immutable_prediction_review_history",
        ],
        "not_implemented": [
            "neural_segmentation_adapter",
            "trained_weights",
            "validated_crack_type_classifier",
            "production_gpu_runtime",
        ],
        "holdout_protocol": protocol["protocol_id"],
        "holdout_protocol_sha256": canonical_sha256(protocol),
        "automatic_approval_enabled": False,
    }


def _samples_from_npz(path: Path) -> list[dict[str, Any]]:
    with np.load(path, allow_pickle=False) as payload:
        truth = np.asarray(payload["truth_mask"], dtype=bool)
        probability = np.asarray(payload["probability"], dtype=np.float64)
        if truth.shape != probability.shape or truth.ndim != 3:
            raise ValueError("truth_mask and probability must have shape [sample, height, width]")
        count = truth.shape[0]
        route_length = np.asarray(payload["route_length_m"], dtype=float)
        wet = np.asarray(payload["wet"], dtype=bool)
        shadow = np.asarray(payload["shadow"], dtype=bool)
        if any(len(value) != count for value in (route_length, wet, shadow)):
            raise ValueError("sample metadata lengths do not match masks")
        samples: list[dict[str, Any]] = []
        for index in range(count):
            sample: dict[str, Any] = {
                "truth_mask": truth[index],
                "probability": probability[index],
                "route_length_m": float(route_length[index]),
                "conditions": {
                    "wet": bool(wet[index]),
                    "shadow": bool(shadow[index]),
                },
            }
            if "truth_length_m" in payload and "predicted_length_m" in payload:
                sample["matched_lengths_m"] = [
                    [
                        float(payload["truth_length_m"][index]),
                        float(payload["predicted_length_m"][index]),
                    ]
                ]
            samples.append(sample)
        return samples


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed RGB crack worker contract")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")

    verify = subparsers.add_parser("verify-model")
    verify.add_argument("--manifest", required=True)
    verify.add_argument(
        "--workspace-root",
        default=os.environ.get("ROAD_CONDITION_WORKSPACE_ROOT", "/workspace"),
    )
    verify.add_argument("--allow-unapproved", action="store_true")

    evaluate = subparsers.add_parser("evaluate-npz")
    evaluate.add_argument("input", type=Path)

    postprocess = subparsers.add_parser("postprocess-npz")
    postprocess.add_argument("input", type=Path)
    postprocess.add_argument("--manifest", required=True)
    postprocess.add_argument(
        "--workspace-root",
        default=os.environ.get("ROAD_CONDITION_WORKSPACE_ROOT", "/workspace"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "capabilities":
            result = _capabilities()
        elif args.command == "verify-model":
            protocol = _protocol()
            result = verify_model_bundle(
                args.workspace_root,
                args.manifest,
                require_approved=not args.allow_unapproved,
                expected_holdout_protocol_sha256=canonical_sha256(protocol),
            )
            result.pop("manifest")
        elif args.command == "evaluate-npz":
            protocol = _protocol()
            result = evaluate_holdout(
                _samples_from_npz(args.input),
                probability_threshold=protocol["metric_parameters"][
                    "probability_threshold"
                ],
                instance_iou_threshold=protocol["metric_parameters"][
                    "instance_iou_threshold"
                ],
            )
            result["protocol_id"] = protocol["protocol_id"]
            result["automatic_approval"] = "disabled"
        else:
            protocol = _protocol()
            verified = verify_model_bundle(
                args.workspace_root,
                args.manifest,
                require_approved=True,
                expected_holdout_protocol_sha256=canonical_sha256(protocol),
            )
            with np.load(args.input, allow_pickle=False) as payload:
                result = {
                    "defects": extract_crack_candidates(
                        payload["probability"],
                        s_min_m=float(payload["s_min_m"]),
                        t_min_m=float(payload["t_min_m"]),
                        grid_size_m=float(payload["grid_size_m"]),
                        model=verified["manifest"]["model"],
                    ),
                    "model_manifest_sha256": verified["manifest_sha256"],
                    "input_contract": "approved BEV probability; neural inference external",
                }
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "rejected", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
