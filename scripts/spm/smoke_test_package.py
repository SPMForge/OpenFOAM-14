#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spm import packaging


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test a built OpenFOAM XCFramework with a temporary SwiftPM consumer.")
    parser.add_argument("--variant", required=True, choices=sorted(packaging.VARIANTS_BY_TARGET))
    parser.add_argument("--xcframework", required=True, type=Path)
    return parser.parse_args()


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _stage_binary_target(package_root: Path, variant: packaging.Variant, xcframework_path: Path) -> str:
    staged_relative_path = Path("Artifacts") / f"{variant.target_name}.xcframework"
    staged_path = package_root / staged_relative_path
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(xcframework_path.resolve(), staged_path, symlinks=True)
    return staged_relative_path.as_posix()


def _write_consumer_package(
    package_root: Path,
    variant: packaging.Variant,
    xcframework_path: Path,
) -> None:
    staged_xcframework_path = _stage_binary_target(package_root, variant, xcframework_path)

    package_swift = f"""// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "OpenFOAMSmoke",
    platforms: [
        .macOS("{packaging.PACKAGE_PLATFORM_DEPLOYMENT_TARGETS['macos']}"),
    ],
    products: [
        .executable(name: "Smoke", targets: ["Smoke"]),
    ],
    targets: [
        .binaryTarget(name: "{variant.target_name}", path: "{staged_xcframework_path}"),
        .executableTarget(
            name: "Smoke",
            dependencies: ["{variant.target_name}"],
            path: "Smoke",
            linkerSettings: [
                .unsafeFlags([
                    "-Xlinker", "-rpath", "-Xlinker", "@loader_path",
                    "-Xlinker", "-rpath", "-Xlinker", "@loader_path/PackageFrameworks",
                ]),
            ]
        ),
    ],
    cxxLanguageStandard: .cxx17
)
"""

    main_cpp = """#include <OpenFOAM/OpenFOAM.h>
#include <cmath>

int main()
{
    const FoamVector3D v = foam_vector_make(3.0, 4.0, 0.0);
    if (std::fabs(foam_vector_magnitude(v) - 5.0) > 1e-9)
    {
        return 1;
    }

    double viscosity = 0.0;
    if (!Foam::spm::lookupScalar("nu 0.015;\\n", "nu", viscosity))
    {
        return 2;
    }
    if (std::fabs(viscosity - 0.015) > 1e-9)
    {
        return 3;
    }

    return Foam::spm::version().empty() ? 4 : 0;
}
"""

    (package_root / "Smoke").mkdir(parents=True, exist_ok=True)
    (package_root / "Package.swift").write_text(package_swift)
    (package_root / "Smoke" / "main.cpp").write_text(main_cpp)


def main() -> int:
    arguments = _parse_arguments()
    variant = packaging.variant_for_target_name(arguments.variant)

    with tempfile.TemporaryDirectory(prefix=f"openfoam-smoke-{variant.target_name}-") as temporary_directory:
        package_root = Path(temporary_directory)
        _write_consumer_package(
            package_root,
            variant,
            arguments.xcframework,
        )
        _run(["swift", "build", "-c", "debug"], cwd=package_root)
        _run(["swift", "build", "-c", "release"], cwd=package_root)
        _run(
            [
                "xcodebuild",
                "-scheme",
                "OpenFOAMSmoke",
                "-configuration",
                "Release",
                "-destination",
                "generic/platform=macOS",
                "MERGED_BINARY_TYPE=automatic",
                "build",
            ],
            cwd=package_root,
        )
        _run(["swift", "run", "-c", "debug", "Smoke"], cwd=package_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
