import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.spm import build_apple_xcframework
from scripts.spm import packaging
from scripts.spm import preflight_apple_platforms
from scripts.spm import render_package
from scripts.spm import smoke_test_package
from scripts.spm import verify_sop_conformance


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_SWIFT_PATH = REPO_ROOT / "Package.swift"
CURRENT_RELEASE_METADATA_PATH = REPO_ROOT / "scripts" / "spm" / "current_release.json"
SMOKE_TEST_PATH = REPO_ROOT / "scripts" / "spm" / "smoke_test_package.py"


class PackageVersionTests(unittest.TestCase):
    def test_maps_upstream_tag_to_alpha_package_tag(self) -> None:
        self.assertEqual(packaging.package_tag_for_upstream_tag("20261008"), "14.0.20261008-alpha.1")

    def test_maps_upstream_tag_to_stable_package_tag(self) -> None:
        self.assertEqual(packaging.stable_package_tag_for_upstream_tag("20261008"), "14.0.20261008")

    def test_maps_upstream_tag_to_package_version(self) -> None:
        self.assertEqual(packaging.package_version_for_upstream_tag("20261008"), "14.0.20261008")

    def test_rejects_invalid_upstream_tag(self) -> None:
        with self.assertRaises(ValueError):
            packaging.package_tag_for_upstream_tag("v20261008")

    def test_finds_next_alpha_number_for_existing_package_refs(self) -> None:
        self.assertEqual(
            packaging.next_alpha_number_for_upstream_tag(
                "20261008",
                [
                    "refs/tags/14.0.20261008-alpha.1",
                    "refs/tags/14.0.20261008-alpha.2",
                    "refs/tags/14.0.20260708-alpha.4",
                ],
            ),
            3,
        )

    def test_finds_latest_alpha_package_tag_for_existing_package_refs(self) -> None:
        self.assertEqual(
            packaging.latest_alpha_package_tag_for_upstream_tag(
                "20261008",
                [
                    "refs/tags/14.0.20261008-alpha.1",
                    "refs/tags/14.0.20261008-alpha.3",
                    "refs/tags/14.0.20261008-alpha.2",
                ],
            ),
            "14.0.20261008-alpha.3",
        )


class AssetNamingTests(unittest.TestCase):
    def test_openfoam_asset_name(self) -> None:
        self.assertEqual(
            packaging.asset_name_for_variant(packaging.OPENFOAM_VARIANT, "20261008"),
            "OpenFOAM-14-20261008-apple.xcframework.zip",
        )


class PlatformMatrixTests(unittest.TestCase):
    def test_openfoam_variant_covers_all_expected_apple_slices(self) -> None:
        self.assertEqual(
            [platform.swiftpm_platform for platform in packaging.OPENFOAM_VARIANT.platforms],
            [
                "ios",
                "ios-simulator",
                "macos",
                "ios-maccatalyst",
                "tvos",
                "tvos-simulator",
                "watchos",
                "watchos-simulator",
                "xros",
                "xros-simulator",
            ],
        )

    def test_all_variant_cmake_platforms_are_supported_by_ios_toolchain(self) -> None:
        toolchain_text = packaging.TOOLCHAIN_FILE.read_text(encoding="utf-8")
        for platform in packaging.OPENFOAM_VARIANT.platforms:
            self.assertIn(
                f'"{platform.cmake_platform}"',
                toolchain_text,
                f"cmake_platform {platform.cmake_platform} for {platform.swiftpm_platform} is not supported in ios.toolchain.cmake",
            )


class PackageRenderingTests(unittest.TestCase):
    def test_renders_binary_target_for_openfoam_product(self) -> None:
        package_contents = packaging.render_package_swift(
            package_name="OpenFOAM-14",
            owner="SPMForge",
            repo="OpenFOAM-14",
            releases=[
                packaging.ReleaseAsset(
                    variant=packaging.OPENFOAM_VARIANT,
                    upstream_tag="20261008",
                    package_tag="14.0.20261008-alpha.1",
                    checksum="openfoam-checksum",
                ),
            ],
        )

        self.assertIn('library(name: "OpenFOAM", targets: ["OpenFOAM"])', package_contents)
        self.assertIn(
            '.binaryTarget(\n'
            '            name: "OpenFOAM",\n'
            '            url: "https://github.com/SPMForge/OpenFOAM-14/releases/download/14.0.20261008-alpha.1/OpenFOAM-14-20261008-apple.xcframework.zip",\n'
            '            checksum: "openfoam-checksum"\n'
            "        )",
            package_contents,
        )
        self.assertNotIn("current_release.json", package_contents)
        self.assertNotIn("Foundation", package_contents)

    def test_combined_metadata_preserves_requested_package_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "current_release.json"
            render_package._write_combined_metadata(
                output_path=output_path,
                package_name="custom-openfoam",
                owner="SPMForge",
                repo="OpenFOAM-14",
                releases=[
                    packaging.ReleaseAsset(
                        variant=packaging.OPENFOAM_VARIANT,
                        upstream_tag="20261008",
                        package_tag="14.0.20261008-alpha.1",
                        checksum="openfoam-checksum",
                    )
                ],
            )

            payload = json.loads(output_path.read_text())
            self.assertEqual(payload["package_name"], "custom-openfoam")

    def test_renders_local_binary_target_for_package_contract_validation(self) -> None:
        package_contents = packaging.render_local_package_swift(
            package_name="OpenFOAM-14",
            releases=[
                packaging.ReleaseAsset(
                    variant=packaging.OPENFOAM_VARIANT,
                    upstream_tag="20261008",
                    package_tag="14.0.20261008-alpha.1",
                    checksum="openfoam-checksum",
                ),
            ],
        )

        self.assertIn(
            '.binaryTarget(\n'
            '            name: "OpenFOAM",\n'
            '            path: "Artifacts/OpenFOAM.xcframework"\n'
            "        )",
            package_contents,
        )

    def test_build_artifact_metadata_payload_uses_schema_and_canonical_variant_fields(self) -> None:
        release = packaging.ReleaseAsset(
            variant=packaging.OPENFOAM_VARIANT,
            upstream_tag="20261008",
            package_tag="14.0.20261008-alpha.1",
            checksum="openfoam-checksum",
        )

        payload = packaging.build_artifact_metadata_payload(
            release,
            artifact_path="/tmp/OpenFOAM-14-20261008-apple.xcframework.zip",
        )

        self.assertEqual(payload["schema_version"], packaging.BUILD_ARTIFACT_METADATA_SCHEMA_VERSION)
        self.assertEqual(payload["target_name"], "OpenFOAM")
        self.assertEqual(payload["product_name"], "OpenFOAM")
        self.assertEqual(payload["module_name"], "OpenFOAM")
        self.assertEqual(payload["asset_name"], "OpenFOAM-14-20261008-apple.xcframework.zip")
        self.assertEqual(payload["runtime_dependency_model"], "system-only")
        self.assertEqual(payload["runtime_dependency_supplier"], "apple-sdk")


class RootManifestTests(unittest.TestCase):
    def test_root_manifest_matches_current_release_metadata(self) -> None:
        payload = json.loads(CURRENT_RELEASE_METADATA_PATH.read_text())
        releases = [
            packaging.release_asset_from_current_release_record(release)
            for release in payload["releases"]
        ]

        expected_manifest = packaging.render_package_swift(
            package_name=payload["package_name"],
            owner=payload["owner"],
            repo=payload["repo"],
            releases=releases,
        )

        self.assertEqual(PACKAGE_SWIFT_PATH.read_text(), expected_manifest)

    def test_root_manifest_uses_expected_package_platform_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            package_root = Path(temporary_directory)
            shutil.copy2(PACKAGE_SWIFT_PATH, package_root / "Package.swift")

            process = subprocess.run(
                ["swift", "package", "dump-package"],
                cwd=package_root,
                capture_output=True,
                text=True,
            )

            self.assertEqual(process.returncode, 0, msg=process.stderr)
            payload = json.loads(process.stdout)
            platforms = {item["platformName"]: item["version"] for item in payload["platforms"]}
            self.assertEqual(
                platforms,
                {
                    "ios": "15.0",
                    "macos": "12.0",
                    "maccatalyst": "15.0",
                    "tvos": "15.0",
                    "watchos": "9.0",
                    "visionos": "1.0",
                },
            )


class OpenFOAMBuildSynthesisTests(unittest.TestCase):
    def test_parse_wmake_files_expands_variables_maps_cver_and_skips_sunos_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            make_dir = base_dir / "Make"
            make_dir.mkdir()
            files_path = make_dir / "files"
            files_path.write_text(
                """
global/global.Cver
signals = signals
$(signals)/sigFpe.C
ifeq "$(WM_ARCH)" "SunOS64"
dummyPrintStack.C
else
printStack.C
endif
ifneq "$(WM_PRECISION_OPTION)" "SP"
doubleScalar.C
endif
LIB = $(FOAM_LIBBIN)/libOSspecific
"""
            )
            parsed = build_apple_xcframework._parse_wmake_files(files_path, base_dir)
            self.assertEqual(
                parsed,
                [
                    base_dir / "global/global.C",
                    base_dir / "signals/sigFpe.C",
                    base_dir / "printStack.C",
                    base_dir / "doubleScalar.C",
                ],
            )

    def test_apply_darwin_source_patches_embeds_config_dict_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            debug_dir = source_root / "src" / "OpenFOAM" / "global" / "debug"
            posix_dir = source_root / "src" / "OSspecific" / "POSIX"
            pstream_dir = source_root / "src" / "Pstream" / "dummy"
            etc_dir = source_root / "etc"
            debug_dir.mkdir(parents=True)
            posix_dir.mkdir(parents=True)
            pstream_dir.mkdir(parents=True)
            etc_dir.mkdir(parents=True)

            (etc_dir / "configDict").write_text("OptimisationSwitches { fileModificationSkew 10; }\n")
            (debug_dir / "debug.C").write_text(
                '            fileNameList configDictFiles = findEtcFiles("configDict", true);\n'
                "            configDictPtr_ = new dictionary();\n"
            )

            build_apple_xcframework._apply_darwin_source_patches(source_root, "20261008")
            patched = (debug_dir / "debug.C").read_text()
            self.assertIn("APPLE_SPM_EMBEDDED_CONFIGDICT", patched)
            self.assertIn('findEtcFiles("configDict", false)', patched)
            self.assertIn("fileModificationSkew 10;", patched)

    def test_synthesizes_case_safe_framework_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cmake_dir = Path(temporary_directory) / "cmake_src"
            cmake_dir.mkdir()
            build_apple_xcframework._write_openfoam_apple_compat_and_bridge(cmake_dir)

            headers_root = cmake_dir / "include" / "OpenFOAM"
            header_names = sorted(p.name for p in headers_root.glob("*.h"))
            self.assertEqual(
                header_names,
                ["OpenFOAM.h", "foamBridge.h", "foamPrimitives.h", "foamVersion.h"],
            )
            self.assertEqual(len(header_names), len({name.lower() for name in header_names}))
            umbrella = (headers_root / "OpenFOAM.h").read_text()
            self.assertIn("#include <OpenFOAM/foamVersion.h>", umbrella)
            self.assertIn("#include <OpenFOAM/foamPrimitives.h>", umbrella)
            self.assertIn("#include <OpenFOAM/foamBridge.h>", umbrella)


class SmokeTestScriptTests(unittest.TestCase):
    def test_release_validation_uses_merged_binary_type_automatic(self) -> None:
        script = SMOKE_TEST_PATH.read_text()

        self.assertIn("MERGED_BINARY_TYPE=automatic", script)
        self.assertIn("xcodebuild", script)
        self.assertIn("OpenFOAMSmoke", script)

    def test_smoke_test_stages_xcframework_under_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            package_root = temporary_root / "consumer"
            xcframework_path = temporary_root / "fixtures" / "OpenFOAM.xcframework"
            xcframework_path.mkdir(parents=True)
            (xcframework_path / "Info.plist").write_text("fixture")

            smoke_test_package._write_consumer_package(
                package_root,
                packaging.OPENFOAM_VARIANT,
                xcframework_path,
            )

            manifest = (package_root / "Package.swift").read_text()
            main_cpp = (package_root / "Smoke" / "main.cpp").read_text()
            self.assertIn('.binaryTarget(name: "OpenFOAM", path: "Artifacts/OpenFOAM.xcframework")', manifest)
            self.assertNotIn(str(xcframework_path), manifest)
            self.assertTrue((package_root / "Artifacts" / "OpenFOAM.xcframework" / "Info.plist").exists())
            self.assertIn("#include <OpenFOAM/OpenFOAM.h>", main_cpp)


class SOPConformanceTests(unittest.TestCase):
    def test_repo_passes_sop_conformance(self) -> None:
        self.assertEqual(verify_sop_conformance.main(REPO_ROOT), 0)

    def test_stage_framework_bundle_embeds_license(self) -> None:
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary_directory:
            temp_root = Path(temporary_directory)
            binary_src = temp_root / "bin"
            binary_src.write_bytes(b"binary")
            headers_src = temp_root / "headers"
            headers_src.mkdir()
            (headers_src / "OpenFOAM.h").write_text("// test")
            output_dir = temp_root / "out"

            macos_platform = next(p for p in packaging.OPENFOAM_VARIANT.platforms if p.swiftpm_platform == "macos")
            ios_platform = next(p for p in packaging.OPENFOAM_VARIANT.platforms if p.swiftpm_platform == "ios")

            with mock.patch("scripts.spm.build_apple_xcframework._run"):
                macos_fw = build_apple_xcframework._stage_framework_bundle(
                    binary_src, headers_src, output_dir, "OpenFOAM", "OpenFOAM", macos_platform, {}
                )
                self.assertTrue((macos_fw / "Versions" / "A" / "Resources" / "LICENSE").is_file())
                self.assertTrue((macos_fw / "Resources" / "LICENSE").is_file())

                ios_fw = build_apple_xcframework._stage_framework_bundle(
                    binary_src, headers_src, output_dir / "ios", "OpenFOAM", "OpenFOAM", ios_platform, {}
                )
                self.assertTrue((ios_fw / "LICENSE").is_file())


class PreflightValidationTests(unittest.TestCase):
    def test_rejects_platform_list_drift(self) -> None:
        with self.assertRaises(ValueError):
            preflight_apple_platforms._validate_required_platforms(
                packaging.OPENFOAM_VARIANT,
                ["macos"],
            )


if __name__ == "__main__":
    unittest.main()
