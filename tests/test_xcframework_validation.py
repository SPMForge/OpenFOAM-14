from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts.spm import validate_mergeable_xcframework


class XCFrameworkValidationTests(unittest.TestCase):
    @staticmethod
    def _which_without_xcrun(command: str) -> str | None:
        if command == "xcrun":
            return None
        return f"/usr/bin/{command}"

    def _write_xcframework(
        self,
        root: Path,
        *,
        libraries: list[dict[str, object]],
    ) -> Path:
        xcframework_path = root / "OpenFOAM.xcframework"
        xcframework_path.mkdir(parents=True)
        info_path = xcframework_path / "Info.plist"
        info_path.write_bytes(
            plistlib.dumps(
                {
                    "AvailableLibraries": libraries,
                    "CFBundlePackageType": "XFWK",
                    "XCFrameworkFormatVersion": "1.0",
                }
            )
        )
        return xcframework_path

    def _write_framework_bundle(
        self,
        slice_root: Path,
        *,
        framework_name: str,
        versioned: bool,
    ) -> Path:
        framework_root = slice_root / f"{framework_name}.framework"
        if versioned:
            active_root = framework_root / "Versions" / "A"
            (framework_root / "Versions").mkdir(parents=True)
            active_root.mkdir(parents=True)
            binary_path = active_root / framework_name
            headers_path = active_root / "Headers"
            modules_path = active_root / "Modules"
            resources_path = active_root / "Resources"
            for path in (headers_path, modules_path, resources_path):
                path.mkdir(parents=True)
            binary_path.write_bytes(b"binary")
            (resources_path / "Info.plist").write_bytes(plistlib.dumps({"CFBundlePackageType": "FMWK"}))
            (modules_path / "module.modulemap").write_text(
                f"""framework module {framework_name} {{
    header "OpenFOAM.h"
    export *
}}
"""
            )
            (headers_path / "OpenFOAM.h").write_text("// header")
            (framework_root / "Versions" / "Current").symlink_to("A")
            (framework_root / framework_name).symlink_to(Path("Versions") / "Current" / framework_name)
            (framework_root / "Headers").symlink_to(Path("Versions") / "Current" / "Headers")
            (framework_root / "Modules").symlink_to(Path("Versions") / "Current" / "Modules")
            (framework_root / "Resources").symlink_to(Path("Versions") / "Current" / "Resources")
            return framework_root

        framework_root.mkdir(parents=True)
        (framework_root / framework_name).write_bytes(b"binary")
        (framework_root / "Headers").mkdir()
        (framework_root / "Modules").mkdir()
        (framework_root / "Resources").mkdir()
        (framework_root / "Resources" / "Info.plist").write_bytes(plistlib.dumps({"CFBundlePackageType": "FMWK"}))
        (framework_root / "Modules" / "module.modulemap").write_text(
            f"""framework module {framework_name} {{
    header "OpenFOAM.h"
    export *
}}
"""
        )
        (framework_root / "Headers" / "OpenFOAM.h").write_text("// header")
        return framework_root

    def _write_framework_header(self, framework_root: Path, header_name: str, contents: str) -> None:
        active_root = framework_root / "Versions" / "Current"
        headers_root = active_root / "Headers" if active_root.exists() else framework_root / "Headers"
        headers_root.mkdir(parents=True, exist_ok=True)
        (headers_root / header_name).write_text(contents)

    def _write_zip_from_directory(self, source_root: Path, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "w") as archive:
            for path in sorted(source_root.rglob("*")):
                archive.write(path, path.relative_to(source_root))

    def test_reports_missing_mergeable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            xcframework_path = self._write_xcframework(
                temporary_root,
                libraries=[
                    {
                        "LibraryIdentifier": "ios-arm64",
                        "BinaryPath": "libOpenFOAM.dylib",
                        "SupportedArchitectures": ["arm64"],
                        "SupportedPlatform": "ios",
                    }
                ],
            )
            slice_root = xcframework_path / "ios-arm64"
            slice_root.mkdir()
            (slice_root / "libOpenFOAM.dylib").write_bytes(b"binary")

            result = validate_mergeable_xcframework.validate_xcframework(xcframework_path, ["ios"])

            self.assertIn("ios: missing MergeableMetadata", result["issues"])

    def test_reports_non_versioned_macos_framework_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            xcframework_path = self._write_xcframework(
                temporary_root,
                libraries=[
                    {
                        "LibraryIdentifier": "macos-arm64_x86_64",
                        "LibraryPath": "OpenFOAM.framework",
                        "MergeableMetadata": True,
                        "SupportedArchitectures": ["arm64", "x86_64"],
                        "SupportedPlatform": "macos",
                    }
                ],
            )
            slice_root = xcframework_path / "macos-arm64_x86_64"
            slice_root.mkdir()
            self._write_framework_bundle(slice_root, framework_name="OpenFOAM", versioned=False)

            with mock.patch("scripts.spm.validate_mergeable_xcframework.shutil.which", return_value=None):
                result = validate_mergeable_xcframework.validate_xcframework(xcframework_path, ["macos"])

            self.assertIn("macos: framework bundle must use versioned macOS layout", result["issues"])

    def test_accepts_versioned_macos_framework_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            xcframework_path = self._write_xcframework(
                temporary_root,
                libraries=[
                    {
                        "LibraryIdentifier": "macos-arm64_x86_64",
                        "LibraryPath": "OpenFOAM.framework",
                        "MergeableMetadata": True,
                        "SupportedArchitectures": ["arm64", "x86_64"],
                        "SupportedPlatform": "macos",
                    }
                ],
            )
            slice_root = xcframework_path / "macos-arm64_x86_64"
            slice_root.mkdir()
            self._write_framework_bundle(slice_root, framework_name="OpenFOAM", versioned=True)

            with mock.patch("scripts.spm.validate_mergeable_xcframework.shutil.which", return_value=None):
                result = validate_mergeable_xcframework.validate_xcframework(xcframework_path, ["macos"])

            self.assertEqual(result["issues"], [])

    def test_reports_framework_header_with_quoted_same_framework_include(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            xcframework_path = self._write_xcframework(
                temporary_root,
                libraries=[
                    {
                        "LibraryIdentifier": "ios-arm64",
                        "LibraryPath": "OpenFOAM.framework",
                        "MergeableMetadata": True,
                        "SupportedArchitectures": ["arm64"],
                        "SupportedPlatform": "ios",
                    }
                ],
            )
            framework_root = self._write_framework_bundle(
                xcframework_path / "ios-arm64",
                framework_name="OpenFOAM",
                versioned=False,
            )
            self._write_framework_header(framework_root, "foamVersion.h", "// version header\n")
            self._write_framework_header(framework_root, "OpenFOAM.h", '#include "foamVersion.h"\n')

            with mock.patch("scripts.spm.validate_mergeable_xcframework.shutil.which", return_value=None):
                result = validate_mergeable_xcframework.validate_xcframework(xcframework_path, ["ios"])

            self.assertIn(
                'ios: framework header OpenFOAM.h uses quoted or relative include "foamVersion.h" for same-framework public header',
                result["issues"],
            )

    def test_accepts_zip_with_root_xcframework(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            payload_root = temporary_root / "payload"
            xcframework_path = self._write_xcframework(
                payload_root,
                libraries=[
                    {
                        "LibraryIdentifier": "ios-arm64",
                        "LibraryPath": "OpenFOAM.framework",
                        "MergeableMetadata": True,
                        "SupportedArchitectures": ["arm64"],
                        "SupportedPlatform": "ios",
                    }
                ],
            )
            self._write_framework_bundle(xcframework_path / "ios-arm64", framework_name="OpenFOAM", versioned=False)
            zip_path = temporary_root / "OpenFOAM.xcframework.zip"
            self._write_zip_from_directory(payload_root, zip_path)

            with mock.patch(
                "scripts.spm.validate_mergeable_xcframework.shutil.which",
                side_effect=self._which_without_xcrun,
            ):
                result = validate_mergeable_xcframework.validate_xcframework(zip_path, ["ios"])

            self.assertEqual(result["issues"], [])


if __name__ == "__main__":
    unittest.main()
