#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spm import packaging
from scripts.spm import validate_mergeable_xcframework


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ValidationReleaseInput:
    metadata_path: Path
    archive_path: Path
    build_metadata: packaging.BuildArtifactMetadata


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the generated Package.swift contract from freshly built release metadata."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--release-metadata",
        action="append",
        dest="release_metadata_paths",
        required=True,
        type=Path,
        help="Path to a variant release metadata json file. Pass once per artifact.",
    )
    parser.add_argument(
        "--release-archive",
        action="append",
        dest="release_archive_paths",
        required=True,
        type=Path,
        help="Path to a freshly built XCFramework archive zip. Pass once per artifact.",
    )
    parser.add_argument("--package-name", default=packaging.DEFAULT_PACKAGE_NAME)
    parser.add_argument("--owner", default=packaging.DEFAULT_OWNER)
    parser.add_argument("--repo", default=packaging.DEFAULT_REPO)
    parser.add_argument("--package-tag-override")
    return parser.parse_args(argv)


def _run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _capture_output(command: list[str], cwd: Path | None = None) -> str:
    process = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return process.stdout


def _stage_validation_root(package_root: Path) -> None:
    package_root.mkdir(parents=True, exist_ok=True)


def _describe_subprocess_error(error: subprocess.CalledProcessError) -> str:
    command = error.cmd if isinstance(error.cmd, list) else [str(error.cmd)]
    lines = [f"command failed with exit code {error.returncode}: {shlex.join(command)}"]
    stdout = getattr(error, "stdout", None)
    stderr = getattr(error, "stderr", None)
    if stdout:
        lines.append(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        lines.append(f"stderr:\n{stderr.rstrip()}")
    return "\n".join(lines)


def _compute_release_archive_checksum(archive_path: Path) -> str:
    digest = hashlib.sha256()
    with archive_path.open("rb") as archive_file:
        for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_release_inputs(
    release_metadata_paths: list[Path],
    release_archive_paths: list[Path],
) -> list[ValidationReleaseInput]:
    archive_paths_by_name: dict[str, Path] = {}
    for archive_path in release_archive_paths:
        resolved_archive_path = archive_path.resolve()
        if not resolved_archive_path.is_file():
            raise FileNotFoundError(f"missing release archive: {resolved_archive_path}")
        if resolved_archive_path.name in archive_paths_by_name:
            raise ValueError(f"duplicate release archive basename provided: {resolved_archive_path.name}")
        archive_paths_by_name[resolved_archive_path.name] = resolved_archive_path

    release_inputs: list[ValidationReleaseInput] = []
    target_names: set[str] = set()
    for metadata_path in release_metadata_paths:
        resolved_metadata_path = metadata_path.resolve()
        if not resolved_metadata_path.is_file():
            raise FileNotFoundError(f"missing release metadata: {resolved_metadata_path}")

        build_metadata = packaging.load_build_artifact_metadata(resolved_metadata_path)
        target_name = build_metadata.release_asset.variant.target_name
        if target_name in target_names:
            raise ValueError(f"duplicate release metadata target provided: {target_name}")
        target_names.add(target_name)

        expected_archive_name = packaging.asset_name_for_variant(
            build_metadata.release_asset.variant,
            build_metadata.release_asset.upstream_tag,
        )
        archive_path = archive_paths_by_name.pop(expected_archive_name, None)
        if archive_path is None:
            raise ValueError(
                f"missing release archive for {target_name}: expected {expected_archive_name}"
            )
        actual_checksum = _compute_release_archive_checksum(archive_path)
        expected_checksum = build_metadata.release_asset.checksum
        if actual_checksum != expected_checksum:
            raise ValueError(
                f"checksum mismatch for {target_name}: metadata declares {expected_checksum}, archive computes {actual_checksum}"
            )

        release_inputs.append(
            ValidationReleaseInput(
                metadata_path=resolved_metadata_path,
                archive_path=archive_path,
                build_metadata=build_metadata,
            )
        )

    if archive_paths_by_name:
        unexpected_archives = ", ".join(sorted(archive_paths_by_name))
        raise ValueError(f"unexpected release archives provided: {unexpected_archives}")

    variant_order = {variant.target_name: index for index, variant in enumerate(packaging.VARIANTS)}
    return sorted(release_inputs, key=lambda item: variant_order[item.build_metadata.release_asset.variant.target_name])


def _validate_release_archives(release_inputs: list[ValidationReleaseInput]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for release_input in release_inputs:
        variant = release_input.build_metadata.release_asset.variant
        result = validate_mergeable_xcframework.validate_xcframework(
            release_input.archive_path,
            [platform.swiftpm_platform for platform in variant.platforms],
            require_dependencies=packaging.required_dependencies_for_variant(variant),
            require_strong_dependencies=packaging.required_strong_dependencies_for_variant(variant),
            require_weak_dependencies=packaging.required_weak_dependencies_for_variant(variant),
            forbid_dependencies=packaging.forbidden_dependencies_for_variant(variant),
        )
        if result["issues"]:
            raise ValueError(
                f"xcframework validation failed for {variant.target_name}: "
                + "; ".join(str(issue) for issue in result["issues"])
            )
        results.append(result)
    return results


def _render_release_metadata(
    package_root: Path,
    release_inputs: list[ValidationReleaseInput],
    package_name: str,
    owner: str,
    repo: str,
    package_tag_override: str | None,
) -> Path:
    current_release_json = package_root / "scripts" / "spm" / "current_release.json"
    command = [
        sys.executable,
        str(packaging.SCRIPTS_ROOT / "render_package.py"),
        "--output",
        str(package_root / "Package.swift"),
        "--package-name",
        package_name,
        "--owner",
        owner,
        "--repo",
        repo,
        "--current-release-json",
        str(current_release_json),
    ]
    if package_tag_override:
        command.extend(["--package-tag-override", package_tag_override])
    for release_input in release_inputs:
        command.extend(["--release-metadata", str(release_input.metadata_path)])
    _run(command, cwd=REPO_ROOT)
    return current_release_json


def _write_local_package_manifest(package_root: Path, package_name: str, release_inputs: list[ValidationReleaseInput]) -> None:
    package_root.mkdir(parents=True, exist_ok=True)
    package_contents = packaging.render_local_package_swift(
        package_name=package_name,
        releases=[release_input.build_metadata.release_asset for release_input in release_inputs],
    )
    (package_root / "Package.swift").write_text(package_contents)


def _extract_archive(zip_path: Path, destination_dir: Path) -> None:
    ditto_path = shutil.which("ditto")
    if ditto_path and Path(ditto_path).exists():
        subprocess.run([ditto_path, "-x", "-k", str(zip_path), str(destination_dir)], check=True)
        return
    unzip_path = shutil.which("unzip")
    if unzip_path and Path(unzip_path).exists():
        subprocess.run([unzip_path, "-q", str(zip_path), "-d", str(destination_dir)], check=True)
        return
    raise FileNotFoundError("no supported archive extractor found; install ditto or unzip")


def _stage_local_release_archives(package_root: Path, release_inputs: list[ValidationReleaseInput]) -> Path:
    artifacts_root = package_root / "Artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)

    for release_input in release_inputs:
        variant = release_input.build_metadata.release_asset.variant
        expected_root_name = f"{variant.target_name}.xcframework"
        destination_path = artifacts_root / expected_root_name
        if destination_path.exists():
            raise ValueError(f"refusing to overwrite staged archive at {destination_path}")

        with tempfile.TemporaryDirectory(prefix=f"{variant.target_name}-archive-") as extract_directory:
            extract_root = Path(extract_directory)
            _extract_archive(release_input.archive_path, extract_root)
            xcframework_roots = sorted(
                child
                for child in extract_root.iterdir()
                if child.is_dir() and child.name.endswith(".xcframework")
            )
            if len(xcframework_roots) != 1:
                raise ValueError(
                    f"{release_input.archive_path} must contain exactly one root .xcframework; "
                    f"found {len(xcframework_roots)}"
                )
            extracted_xcframework = xcframework_roots[0]
            if extracted_xcframework.name != expected_root_name:
                raise ValueError(
                    f"{release_input.archive_path} extracted unexpected root {extracted_xcframework.name}; "
                    f"expected {expected_root_name}"
                )
            shutil.move(str(extracted_xcframework), destination_path)

    return artifacts_root


def _validate_manifest(package_root: Path) -> None:
    _capture_output(["swift", "package", "dump-package"], cwd=package_root)


def _consumer_package_name(target_name: str) -> str:
    suffix = "".join(component.capitalize() for component in target_name.split("_"))
    return f"PackageContractSmoke{suffix}"


def _write_consumer_package(
    consumer_root: Path,
    local_package_root: Path,
    package_name: str,
    release_input: ValidationReleaseInput,
) -> str:
    consumer_root.mkdir(parents=True, exist_ok=True)
    (consumer_root / "Smoke").mkdir(parents=True, exist_ok=True)

    consumer_package_name = _consumer_package_name(release_input.build_metadata.release_asset.variant.target_name)
    local_package_relative_path = os.path.relpath(local_package_root, consumer_root).replace(os.sep, "/")
    package_swift = f"""// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "{consumer_package_name}",
    platforms: [
        .macOS("{packaging.PACKAGE_PLATFORM_DEPLOYMENT_TARGETS['macos']}"),
    ],
    products: [
        .executable(name: "Smoke", targets: ["Smoke"]),
    ],
    dependencies: [
        .package(name: "{package_name}", path: "{local_package_relative_path}"),
    ],
    targets: [
        .executableTarget(
            name: "Smoke",
            dependencies: [
                .product(name: "{release_input.build_metadata.release_asset.variant.product_name}", package: "{package_name}"),
            ],
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

int main()
{
    double viscosity = 0.0;
    if (!Foam::spm::lookupScalar("nu 0.015;\\n", "nu", viscosity))
    {
        return 1;
    }
    return Foam::spm::version().empty() ? 2 : 0;
}
"""

    (consumer_root / "Package.swift").write_text(package_swift)
    (consumer_root / "Smoke" / "main.cpp").write_text(main_cpp)
    return consumer_package_name


def _validate_local_package_consumers(
    local_package_root: Path,
    package_name: str,
    release_inputs: list[ValidationReleaseInput],
) -> None:
    for release_input in release_inputs:
        with tempfile.TemporaryDirectory(
            prefix=f"openfoam-package-contract-smoke-{release_input.build_metadata.release_asset.variant.target_name}-"
        ) as temporary_directory:
            consumer_root = Path(temporary_directory)
            consumer_package_name = _write_consumer_package(
                consumer_root=consumer_root,
                local_package_root=local_package_root,
                package_name=package_name,
                release_input=release_input,
            )
            _run(["swift", "build", "-c", "debug"], cwd=consumer_root)
            _run(["swift", "build", "-c", "release"], cwd=consumer_root)
            _run(
                [
                    "xcodebuild",
                    "-scheme",
                    consumer_package_name,
                    "-configuration",
                    "Release",
                    "-destination",
                    "generic/platform=macOS",
                    "MERGED_BINARY_TYPE=automatic",
                    "build",
                ],
                cwd=consumer_root,
            )


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    repo_root = arguments.repo_root.resolve()

    try:
        if not repo_root.exists():
            raise FileNotFoundError(f"repo root does not exist: {repo_root}")

        release_inputs = _load_release_inputs(
            release_metadata_paths=arguments.release_metadata_paths,
            release_archive_paths=arguments.release_archive_paths,
        )
        archive_validation_results = _validate_release_archives(release_inputs)
        with tempfile.TemporaryDirectory(prefix="openfoam-package-contract-") as temporary_directory:
            validation_root = Path(temporary_directory)

            rendered_package_root = validation_root / "rendered-package"
            _stage_validation_root(rendered_package_root)
            current_release_json = _render_release_metadata(
                package_root=rendered_package_root,
                release_inputs=release_inputs,
                package_name=arguments.package_name,
                owner=arguments.owner,
                repo=arguments.repo,
                package_tag_override=arguments.package_tag_override,
            )
            _validate_manifest(rendered_package_root)

            local_package_root = validation_root / "local-package"
            _write_local_package_manifest(local_package_root, arguments.package_name, release_inputs)
            artifacts_root = _stage_local_release_archives(local_package_root, release_inputs)
            _validate_local_package_consumers(local_package_root, arguments.package_name, release_inputs)

            print(
                json.dumps(
                    {
                        "archive_validation_count": len(archive_validation_results),
                        "consumer_validation_count": len(release_inputs),
                        "current_release_json": str(current_release_json),
                        "release_count": len(release_inputs),
                        "rendered_package_root": str(rendered_package_root),
                        "staged_artifacts_root": str(artifacts_root),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    except (FileNotFoundError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(_describe_subprocess_error(error), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
