from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts" / "spm"
TOOLCHAIN_FILE = REPO_ROOT / "toolchains" / "ios.toolchain.cmake"
CURRENT_RELEASE_METADATA_PATH = SCRIPTS_ROOT / "current_release.json"
PLATFORM_METADATA_PATH = SCRIPTS_ROOT / "platforms.json"
DEFAULT_OWNER = "SPMForge"
DEFAULT_REPO = "OpenFOAM-14"
DEFAULT_PACKAGE_NAME = "OpenFOAM-14"
MAJOR_MINOR_PREFIX = "14.0"
BUILD_ARTIFACT_METADATA_SCHEMA_VERSION = 1

_UPSTREAM_TAG_PATTERN = re.compile(r"^\d{8}$")
_PACKAGE_PLATFORM_DECLARATION_NAMES = (
    ("ios", "iOS"),
    ("macos", "macOS"),
    ("ios-maccatalyst", "macCatalyst"),
    ("tvos", "tvOS"),
    ("watchos", "watchOS"),
    ("xros", "visionOS"),
)


@dataclass(frozen=True)
class Platform:
    swiftpm_platform: str
    cmake_platform: str
    xcode_destination: str
    archs: tuple[str, ...]
    deployment_target: str


@dataclass(frozen=True)
class Variant:
    target_name: str
    product_name: str
    module_name: str
    asset_suffix: str
    platforms: tuple[Platform, ...]
    runtime_dependency_model: str = "system-only"
    runtime_dependency_supplier: str = "apple-sdk"
    runtime_dependencies: tuple[str, ...] = ("/usr/lib/libz.1.dylib", "/usr/lib/libc++.1.dylib")
    strong_runtime_dependencies: tuple[str, ...] = ("/usr/lib/libz.1.dylib", "/usr/lib/libc++.1.dylib")
    weak_runtime_dependencies: tuple[str, ...] = ()
    forbidden_runtime_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseAsset:
    variant: Variant
    upstream_tag: str
    package_tag: str
    checksum: str


@dataclass(frozen=True)
class BuildArtifactMetadata:
    schema_version: int
    release_asset: ReleaseAsset
    asset_name: str
    artifact_path: str
    platforms: tuple[str, ...]


def _load_platform_deployment_targets() -> dict[str, str]:
    payload = json.loads(PLATFORM_METADATA_PATH.read_text())
    package_platforms = payload.get("package_platforms")
    if not isinstance(package_platforms, dict):
        raise ValueError(f"invalid platform metadata in {PLATFORM_METADATA_PATH}")

    required_keys = {key for key, _ in _PACKAGE_PLATFORM_DECLARATION_NAMES}
    if set(package_platforms) != required_keys:
        raise ValueError(f"unexpected platform keys in {PLATFORM_METADATA_PATH}: {sorted(package_platforms)}")

    normalized: dict[str, str] = {}
    for key, value in package_platforms.items():
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid deployment target for {key} in {PLATFORM_METADATA_PATH}")
        normalized[key] = value
    return normalized


PACKAGE_PLATFORM_DEPLOYMENT_TARGETS = _load_platform_deployment_targets()

COMMON_PLATFORMS = (
    Platform(
        swiftpm_platform="ios",
        cmake_platform="OS64",
        xcode_destination="generic/platform=iOS",
        archs=("arm64",),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["ios"],
    ),
    Platform(
        swiftpm_platform="ios-simulator",
        cmake_platform="SIMULATORARM64",
        xcode_destination="generic/platform=iOS Simulator",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["ios"],
    ),
    Platform(
        swiftpm_platform="macos",
        cmake_platform="MAC",
        xcode_destination="generic/platform=macOS",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["macos"],
    ),
    Platform(
        swiftpm_platform="ios-maccatalyst",
        cmake_platform="MAC_CATALYST",
        xcode_destination="generic/platform=macOS,variant=Mac Catalyst",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["ios-maccatalyst"],
    ),
    Platform(
        swiftpm_platform="tvos",
        cmake_platform="TVOS",
        xcode_destination="generic/platform=tvOS",
        archs=("arm64",),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["tvos"],
    ),
    Platform(
        swiftpm_platform="tvos-simulator",
        cmake_platform="SIMULATORARM64_TVOS",
        xcode_destination="generic/platform=tvOS Simulator",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["tvos"],
    ),
    Platform(
        swiftpm_platform="watchos",
        cmake_platform="WATCHOS",
        xcode_destination="generic/platform=watchOS",
        archs=("arm64", "arm64_32"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["watchos"],
    ),
    Platform(
        swiftpm_platform="watchos-simulator",
        cmake_platform="SIMULATOR_WATCHOS",
        xcode_destination="generic/platform=watchOS Simulator",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["watchos"],
    ),
    Platform(
        swiftpm_platform="xros",
        cmake_platform="VISIONOS",
        xcode_destination="generic/platform=visionOS",
        archs=("arm64",),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["xros"],
    ),
    Platform(
        swiftpm_platform="xros-simulator",
        cmake_platform="SIMULATOR_VISIONOS",
        xcode_destination="generic/platform=visionOS Simulator",
        archs=("arm64", "x86_64"),
        deployment_target=PACKAGE_PLATFORM_DEPLOYMENT_TARGETS["xros"],
    ),
)

OPENFOAM_VARIANT = Variant(
    target_name="OpenFOAM",
    product_name="OpenFOAM",
    module_name="OpenFOAM",
    asset_suffix="",
    platforms=COMMON_PLATFORMS,
)

VARIANTS = (OPENFOAM_VARIANT,)
VARIANTS_BY_TARGET = {variant.target_name: variant for variant in VARIANTS}


def required_dependencies_for_variant(variant: Variant) -> list[str]:
    return list(variant.runtime_dependencies)


def required_strong_dependencies_for_variant(variant: Variant) -> list[str]:
    return list(variant.strong_runtime_dependencies)


def required_weak_dependencies_for_variant(variant: Variant) -> list[str]:
    return list(variant.weak_runtime_dependencies)


def forbidden_dependencies_for_variant(variant: Variant) -> list[str]:
    return list(variant.forbidden_runtime_dependencies)


def validate_upstream_tag(upstream_tag: str) -> str:
    if not _UPSTREAM_TAG_PATTERN.match(upstream_tag):
        raise ValueError(f"unsupported upstream tag '{upstream_tag}'; expected YYYYMMDD")
    return upstream_tag


def package_version_for_upstream_tag(upstream_tag: str) -> str:
    normalized = validate_upstream_tag(upstream_tag)
    return f"{MAJOR_MINOR_PREFIX}.{normalized}"


def stable_package_tag_for_upstream_tag(upstream_tag: str) -> str:
    return package_version_for_upstream_tag(upstream_tag)


def package_tag_for_upstream_tag(upstream_tag: str, alpha_number: int = 1) -> str:
    if alpha_number < 1:
        raise ValueError(f"alpha_number must be >= 1; got {alpha_number}")
    return f"{package_version_for_upstream_tag(upstream_tag)}-alpha.{alpha_number}"


def next_alpha_number_for_upstream_tag(upstream_tag: str, refs: list[str]) -> int:
    latest_alpha_tag = latest_alpha_package_tag_for_upstream_tag(upstream_tag, refs)
    if latest_alpha_tag is None:
        return 1
    return int(latest_alpha_tag.rsplit(".alpha.", 1)[-1].rsplit("-alpha.", 1)[-1]) + 1


def latest_alpha_package_tag_for_upstream_tag(upstream_tag: str, refs: list[str]) -> str | None:
    base_version = package_version_for_upstream_tag(upstream_tag)
    alpha_pattern = re.compile(rf"^(?:refs/tags/)?{re.escape(base_version)}-alpha\.(\d+)$")
    alpha_numbers = []

    for ref in refs:
        match = alpha_pattern.match(ref.strip())
        if match:
            alpha_numbers.append(int(match.group(1)))

    if not alpha_numbers:
        return None

    return package_tag_for_upstream_tag(upstream_tag, alpha_number=max(alpha_numbers))


def asset_name_for_variant(variant: Variant, upstream_tag: str) -> str:
    normalized = validate_upstream_tag(upstream_tag)
    return f"OpenFOAM-14-{normalized}-apple{variant.asset_suffix}.xcframework.zip"


def release_url(owner: str, repo: str, package_tag: str, variant: Variant, upstream_tag: str) -> str:
    asset_name = asset_name_for_variant(variant, upstream_tag)
    return f"https://github.com/{owner}/{repo}/releases/download/{package_tag}/{asset_name}"


def variant_for_target_name(target_name: str) -> Variant:
    try:
        return VARIANTS_BY_TARGET[target_name]
    except KeyError as error:
        raise ValueError(f"unknown variant target '{target_name}'") from error


def build_artifact_metadata_payload(
    release: ReleaseAsset,
    *,
    artifact_path: str,
) -> dict[str, object]:
    validate_upstream_tag(release.upstream_tag)
    if not release.package_tag:
        raise ValueError("package_tag must be non-empty")
    if not release.checksum:
        raise ValueError("checksum must be non-empty")
    if not artifact_path:
        raise ValueError("artifact_path must be non-empty")

    return {
        "schema_version": BUILD_ARTIFACT_METADATA_SCHEMA_VERSION,
        "target_name": release.variant.target_name,
        "product_name": release.variant.product_name,
        "module_name": release.variant.module_name,
        "upstream_tag": release.upstream_tag,
        "package_tag": release.package_tag,
        "asset_name": asset_name_for_variant(release.variant, release.upstream_tag),
        "artifact_path": artifact_path,
        "checksum": release.checksum,
        "platforms": [platform.swiftpm_platform for platform in release.variant.platforms],
        "runtime_dependency_model": release.variant.runtime_dependency_model,
        "runtime_dependency_supplier": release.variant.runtime_dependency_supplier,
        "runtime_dependencies": required_dependencies_for_variant(release.variant),
        "strong_runtime_dependencies": required_strong_dependencies_for_variant(release.variant),
        "weak_runtime_dependencies": required_weak_dependencies_for_variant(release.variant),
        "forbidden_runtime_dependencies": forbidden_dependencies_for_variant(release.variant),
    }


def load_build_artifact_metadata(path: Path) -> BuildArtifactMetadata:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"invalid release metadata payload in {path}")

    schema_version = payload.get("schema_version")
    if schema_version != BUILD_ARTIFACT_METADATA_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported release metadata schema_version in {path}: "
            f"expected {BUILD_ARTIFACT_METADATA_SCHEMA_VERSION}, got {schema_version}"
        )

    target_name = payload.get("target_name")
    if not isinstance(target_name, str) or not target_name:
        raise ValueError(f"release metadata in {path} is missing a valid target_name")
    variant = variant_for_target_name(target_name)

    product_name = payload.get("product_name")
    if product_name != variant.product_name:
        raise ValueError(
            f"release metadata product_name mismatch in {path}: "
            f"expected {variant.product_name}, got {product_name}"
        )

    module_name = payload.get("module_name")
    if module_name != variant.module_name:
        raise ValueError(
            f"release metadata module_name mismatch in {path}: "
            f"expected {variant.module_name}, got {module_name}"
        )

    upstream_tag = payload.get("upstream_tag")
    if not isinstance(upstream_tag, str) or not upstream_tag:
        raise ValueError(f"release metadata in {path} is missing a valid upstream_tag")
    validate_upstream_tag(upstream_tag)

    package_tag = payload.get("package_tag")
    if not isinstance(package_tag, str) or not package_tag:
        raise ValueError(f"release metadata in {path} is missing a valid package_tag")

    checksum = payload.get("checksum")
    if not isinstance(checksum, str) or not checksum:
        raise ValueError(f"release metadata in {path} is missing a valid checksum")

    expected_asset_name = asset_name_for_variant(variant, upstream_tag)
    asset_name = payload.get("asset_name")
    if asset_name != expected_asset_name:
        raise ValueError(
            f"release metadata asset_name mismatch in {path}: "
            f"expected {expected_asset_name}, got {asset_name}"
        )

    artifact_path = payload.get("artifact_path")
    if not isinstance(artifact_path, str) or not artifact_path:
        raise ValueError(f"release metadata in {path} is missing a valid artifact_path")

    platforms = payload.get("platforms")
    expected_platforms = [platform.swiftpm_platform for platform in variant.platforms]
    if platforms != expected_platforms:
        raise ValueError(
            f"release metadata platforms mismatch in {path}: "
            f"expected {expected_platforms}, got {platforms}"
        )

    runtime_dependency_model = payload.get("runtime_dependency_model")
    if runtime_dependency_model != variant.runtime_dependency_model:
        raise ValueError(
            f"release metadata runtime_dependency_model mismatch in {path}: "
            f"expected {variant.runtime_dependency_model}, got {runtime_dependency_model}"
        )

    runtime_dependency_supplier = payload.get("runtime_dependency_supplier")
    if runtime_dependency_supplier != variant.runtime_dependency_supplier:
        raise ValueError(
            f"release metadata runtime_dependency_supplier mismatch in {path}: "
            f"expected {variant.runtime_dependency_supplier}, got {runtime_dependency_supplier}"
        )

    expected_runtime_dependencies = required_dependencies_for_variant(variant)
    runtime_dependencies = payload.get("runtime_dependencies")
    if runtime_dependencies != expected_runtime_dependencies:
        raise ValueError(
            f"release metadata runtime_dependencies mismatch in {path}: "
            f"expected {expected_runtime_dependencies}, got {runtime_dependencies}"
        )

    expected_strong_runtime_dependencies = required_strong_dependencies_for_variant(variant)
    strong_runtime_dependencies = payload.get("strong_runtime_dependencies")
    if strong_runtime_dependencies != expected_strong_runtime_dependencies:
        raise ValueError(
            f"release metadata strong_runtime_dependencies mismatch in {path}: "
            f"expected {expected_strong_runtime_dependencies}, got {strong_runtime_dependencies}"
        )

    expected_weak_runtime_dependencies = required_weak_dependencies_for_variant(variant)
    weak_runtime_dependencies = payload.get("weak_runtime_dependencies")
    if weak_runtime_dependencies != expected_weak_runtime_dependencies:
        raise ValueError(
            f"release metadata weak_runtime_dependencies mismatch in {path}: "
            f"expected {expected_weak_runtime_dependencies}, got {weak_runtime_dependencies}"
        )

    expected_forbidden_runtime_dependencies = forbidden_dependencies_for_variant(variant)
    forbidden_runtime_dependencies = payload.get("forbidden_runtime_dependencies")
    if forbidden_runtime_dependencies != expected_forbidden_runtime_dependencies:
        raise ValueError(
            f"release metadata forbidden_runtime_dependencies mismatch in {path}: "
            f"expected {expected_forbidden_runtime_dependencies}, got {forbidden_runtime_dependencies}"
        )

    return BuildArtifactMetadata(
        schema_version=schema_version,
        release_asset=ReleaseAsset(
            variant=variant,
            upstream_tag=upstream_tag,
            package_tag=package_tag,
            checksum=checksum,
        ),
        asset_name=asset_name,
        artifact_path=artifact_path,
        platforms=tuple(expected_platforms),
    )


def release_asset_from_current_release_record(record: dict[str, object]) -> ReleaseAsset:
    if not isinstance(record, dict):
        raise ValueError("current release record must be an object")

    target_name = record.get("target_name")
    if not isinstance(target_name, str) or not target_name:
        raise ValueError("current release record is missing a valid target_name")
    variant = variant_for_target_name(target_name)

    product_name = record.get("product_name")
    if product_name is not None and product_name != variant.product_name:
        raise ValueError(
            f"current release product_name mismatch for {target_name}: "
            f"expected {variant.product_name}, got {product_name}"
        )

    module_name = record.get("module_name")
    if module_name is not None and module_name != variant.module_name:
        raise ValueError(
            f"current release module_name mismatch for {target_name}: "
            f"expected {variant.module_name}, got {module_name}"
        )

    upstream_tag = record.get("upstream_tag")
    if not isinstance(upstream_tag, str) or not upstream_tag:
        raise ValueError(f"current release record for {target_name} is missing a valid upstream_tag")
    validate_upstream_tag(upstream_tag)

    package_tag = record.get("package_tag")
    if not isinstance(package_tag, str) or not package_tag:
        raise ValueError(f"current release record for {target_name} is missing a valid package_tag")

    checksum = record.get("checksum")
    if not isinstance(checksum, str) or not checksum:
        raise ValueError(f"current release record for {target_name} is missing a valid checksum")

    return ReleaseAsset(
        variant=variant,
        upstream_tag=upstream_tag,
        package_tag=package_tag,
        checksum=checksum,
    )


def _render_platform_lines() -> str:
    return ",\n".join(
        f'        .{swiftpm_name}("{PACKAGE_PLATFORM_DEPLOYMENT_TARGETS[platform_key]}")'
        for platform_key, swiftpm_name in _PACKAGE_PLATFORM_DECLARATION_NAMES
    )


def _render_product_targets(variant: Variant) -> str:
    return f'["{variant.target_name}"]'


def _render_package_manifest(
    package_name: str,
    releases: list[ReleaseAsset],
    binary_target_lines: list[str],
) -> str:
    if not releases:
        raise ValueError("at least one release asset is required")

    product_lines = [
        f'        .library(name: "{release.variant.product_name}", targets: {_render_product_targets(release.variant)})'
        for release in releases
    ]
    platform_lines = _render_platform_lines()
    products_block = ",\n".join(product_lines)
    targets_block = ",\n".join(binary_target_lines)

    return f"""// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "{package_name}",
    platforms: [
{platform_lines}
    ],
    products: [
{products_block}
    ],
    targets: [
{targets_block}
    ]
)
"""


def render_package_swift(
    package_name: str,
    owner: str,
    repo: str,
    releases: list[ReleaseAsset],
) -> str:
    target_lines = []
    for release in releases:
        url = release_url(owner, repo, release.package_tag, release.variant, release.upstream_tag)
        target_lines.append(
            f"""        .binaryTarget(
            name: "{release.variant.target_name}",
            url: "{url}",
            checksum: "{release.checksum}"
        )"""
        )

    return _render_package_manifest(
        package_name=package_name,
        releases=releases,
        binary_target_lines=target_lines,
    )


def render_local_package_swift(
    package_name: str,
    releases: list[ReleaseAsset],
) -> str:
    target_lines = [
        f"""        .binaryTarget(
            name: "{release.variant.target_name}",
            path: "Artifacts/{release.variant.target_name}.xcframework"
        )"""
        for release in releases
    ]
    return _render_package_manifest(
        package_name=package_name,
        releases=releases,
        binary_target_lines=target_lines,
    )
