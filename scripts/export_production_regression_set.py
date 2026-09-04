#!/usr/bin/env python3
"""Export the 2026-08-18..31 production regression set without network access."""

from __future__ import annotations

import argparse
import subprocess

from ht_lead_radar.regression_export import (
    DEFAULT_FIXTURE_OUTPUT,
    RegressionExportError,
    build_regression_set,
    validate_regression_set,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir")
    parser.add_argument("--output-dir", default=str(DEFAULT_FIXTURE_OUTPUT))
    parser.add_argument("--sqlite")
    parser.add_argument("--generator-git-sha")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate_only:
            manifest = validate_regression_set(args.output_dir)
        else:
            if not args.reports_dir:
                parser.error("--reports-dir is required unless --validate-only is used")
            manifest = build_regression_set(
                args.reports_dir,
                args.output_dir,
                generator_git_sha=args.generator_git_sha or _git_sha(),
                sqlite_path=args.sqlite,
            )
    except (OSError, ValueError, RegressionExportError) as error:
        print(f"regression export failed: {error}")
        return 2
    print(
        f"regression set validated: {len(manifest['days'])} days, "
        f"sha256={manifest['overall_sha256']}"
    )
    return 0


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


if __name__ == "__main__":
    raise SystemExit(main())
