#!/usr/bin/env python3
"""Generate a first-pass actionable import report for Ahmad's historical profile data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.profile_inventory import build_profile_inventory_payload  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Extra local root to scan. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Override the directory scan depth limit for this report.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Override the total file metadata scan limit for this report.",
    )
    parser.add_argument(
        "--sample-bytes",
        type=int,
        default=4096,
        help="Bytes to sample from text-like files when looking for institution or era signals.",
    )
    return parser


def build_report(args: argparse.Namespace) -> dict:
    return build_profile_inventory_payload(
        extra_roots=args.root or None,
        max_depth=args.max_depth,
        max_files=args.max_files,
        sample_bytes=args.sample_bytes,
    )


def main() -> None:
    args = build_parser().parse_args()
    payload = build_report(args)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
