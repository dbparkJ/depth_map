#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from road_condition_core.report_v2 import generate_report_bundle, render_report_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate the Korean internal report v2 evidence package from result JSON."
    )
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--pdf-executable", default="chromium")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output = args.output or args.result_dir / "report"
    manifest = generate_report_bundle(args.result_dir, output)
    if args.pdf:
        render_report_pdf(output, args.pdf_executable)
        manifest = json.loads(
            (output / "report_manifest.json").read_text(encoding="utf-8")
        )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
