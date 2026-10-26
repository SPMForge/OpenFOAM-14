#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spm import packaging


SDK_SUPPORT = {
    "ios": {"sdk": "iphoneos", "download_platform": "iOS"},
    "ios-simulator": {"sdk": "iphonesimulator", "download_platform": "iOS"},
    "macos": {"sdk": "macosx", "download_platform": "macOS"},
    "ios-maccatalyst": {"sdk": "macosx", "download_platform": "macOS"},
    "tvos": {"sdk": "appletvos", "download_platform": "tvOS"},
    "tvos-simulator": {"sdk": "appletvsimulator", "download_platform": "tvOS"},
    "watchos": {"sdk": "watchos", "download_platform": "watchOS"},
    "watchos-simulator": {"sdk": "watchsimulator", "download_platform": "watchOS"},
    "xros": {"sdk": "xros", "download_platform": "visionOS"},
    "xros-simulator": {"sdk": "xrsimulator", "download_platform": "visionOS"},
}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-fast Apple platform preflight for the declared build matrix."
    )
    parser.add_argument("--variant", required=True, choices=sorted(packaging.VARIANTS_BY_TARGET))
    parser.add_argument(
        "--required-platform",
        action="append",
        dest="required_platforms",
        default=[],
        help="Platform identifier declared by the workflow matrix.",
    )
    parser.add_argument("--developer-dir", help="Optional Xcode developer dir override.")
    return parser.parse_args()


def _capture_output(command: list[str], env: dict[str, str] | None = None) -> str:
    try:
        process = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise RuntimeError(detail or f"command failed: {' '.join(command)}") from error
    return process.stdout.strip()


def _environment(arguments: argparse.Namespace) -> dict[str, str]:
    environment = dict(os.environ)
    if arguments.developer_dir:
        environment["DEVELOPER_DIR"] = arguments.developer_dir
    return environment


def _validate_required_platforms(
    variant: packaging.Variant,
    required_platforms: list[str],
) -> list[str]:
    expected_platforms = [platform.swiftpm_platform for platform in variant.platforms]
    normalized_required = required_platforms or expected_platforms
    if normalized_required != expected_platforms:
        raise ValueError(
            "workflow platform list drifted from the centralized variant contract: "
            f"expected {expected_platforms}, got {normalized_required}"
        )
    return normalized_required


def _preflight_sdk_support(
    required_platforms: list[str],
    environment: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    validations = []
    for platform_name in required_platforms:
        try:
            support = SDK_SUPPORT[platform_name]
        except KeyError as error:
            raise ValueError(f"unsupported Apple platform in preflight: {platform_name}") from error

        sdk_name = support["sdk"]
        try:
            sdk_path = _capture_output(["xcrun", "--sdk", sdk_name, "--show-sdk-path"], env=environment)
        except RuntimeError as error:
            download_platform = support["download_platform"]
            raise RuntimeError(
                f"required Apple platform support is missing for {platform_name}. "
                f"Install it with 'xcodebuild -downloadPlatform {download_platform}'. "
                f"Original error: {error}"
            ) from error

        validations.append(
            {
                "platform": platform_name,
                "sdk": sdk_name,
                "sdk_path": sdk_path,
            }
        )
    return validations


def main() -> int:
    arguments = _parse_arguments()
    try:
        environment = _environment(arguments)
        variant = packaging.variant_for_target_name(arguments.variant)
        required_platforms = _validate_required_platforms(variant, arguments.required_platforms)
        payload = {
            "variant": variant.target_name,
            "platforms": _preflight_sdk_support(required_platforms, environment),
            "xcode_version": _capture_output(["xcodebuild", "-version"], env=environment),
        }
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
