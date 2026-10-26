from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts.spm import archive_builder
from scripts.spm import packaging
from scripts.spm import tag_selection


REPO_ROOT = Path(__file__).resolve().parents[3]
SELECT_UPSTREAM_TAG_SCRIPT = REPO_ROOT / "scripts" / "spm" / "select_upstream_tag.py"


class LatestStableTagTests(unittest.TestCase):
    def test_selects_latest_numeric_date_tag(self) -> None:
        self.assertEqual(
            tag_selection.select_latest_stable_tag(
                [
                    "refs/tags/v14.0",
                    "refs/tags/20260708",
                    "refs/tags/20261008",
                    "refs/tags/20260902",
                ]
            ),
            "20261008",
        )

    def test_accepts_upstream_tag_ref_namespace(self) -> None:
        self.assertEqual(
            tag_selection.select_latest_stable_tag(
                [
                    "refs/upstream-tags/20260708",
                    "refs/upstream-tags/20261008",
                    "refs/upstream-tags/20260902",
                ]
            ),
            "20261008",
        )

    def test_rejects_when_no_stable_tag_is_available(self) -> None:
        with self.assertRaises(ValueError):
            tag_selection.select_latest_stable_tag(["refs/tags/v14.0", "refs/tags/latest"])


class ArchiveDiscoveryTests(unittest.TestCase):
    def test_prefers_xcarchive_product_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_root = Path(temporary_directory) / "OpenFOAM.xcarchive"
            archive_binary = archive_root / "Products" / "usr" / "local" / "lib" / "libOpenFOAM.dylib"
            archive_binary.parent.mkdir(parents=True)
            archive_binary.write_bytes(b"archive-product")

            fallback_binary = (
                Path(temporary_directory)
                / "DerivedData"
                / "ArchiveIntermediates"
                / "OpenFOAM"
                / "IntermediateBuildFilesPath"
                / "UninstalledProducts"
                / "macosx"
                / "libOpenFOAM.dylib"
            )
            fallback_binary.parent.mkdir(parents=True)
            fallback_binary.write_bytes(b"fallback-product")

            self.assertEqual(
                archive_builder.find_dynamic_library(
                    archive_root=archive_root,
                    derived_data_root=Path(temporary_directory) / "DerivedData",
                    library_basename="OpenFOAM",
                ),
                archive_binary,
            )

    def test_falls_back_to_uninstalled_products(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_root = Path(temporary_directory) / "OpenFOAM.xcarchive"
            archive_root.mkdir(parents=True)

            fallback_binary = (
                Path(temporary_directory)
                / "DerivedData"
                / "ArchiveIntermediates"
                / "OpenFOAM"
                / "IntermediateBuildFilesPath"
                / "UninstalledProducts"
                / "ios"
                / "libOpenFOAM.dylib"
            )
            fallback_binary.parent.mkdir(parents=True)
            fallback_binary.write_bytes(b"fallback-product")

            self.assertEqual(
                archive_builder.find_dynamic_library(
                    archive_root=archive_root,
                    derived_data_root=Path(temporary_directory) / "DerivedData",
                    library_basename="OpenFOAM",
                ),
                fallback_binary,
            )

    def test_errors_when_no_dynamic_library_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_root = Path(temporary_directory) / "OpenFOAM.xcarchive"
            archive_root.mkdir(parents=True)

            with self.assertRaises(FileNotFoundError):
                archive_builder.find_dynamic_library(
                    archive_root=archive_root,
                    derived_data_root=Path(temporary_directory) / "DerivedData",
                    library_basename="OpenFOAM",
                )


class SelectUpstreamTagScriptTests(unittest.TestCase):
    def _init_git_repo(self, repo_root: Path) -> None:
        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo_root, check=True, capture_output=True, text=True)

    def _run_script(self, repo_root: Path, *args: str) -> dict[str, object]:
        process = subprocess.run(
            [sys.executable, str(SELECT_UPSTREAM_TAG_SCRIPT), "--repo-root", str(repo_root), *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, msg=process.stderr)
        return json.loads(process.stdout)

    def test_sync_mode_uses_alpha_1_package_tag_when_no_alpha_release_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)

            payload = self._run_script(repo_root, "--explicit-tag", "20261008")

            self.assertEqual(
                payload,
                {
                    "upstream_tag": "20261008",
                    "build_tag": packaging.package_tag_for_upstream_tag("20261008"),
                    "package_tag": packaging.package_tag_for_upstream_tag("20261008"),
                    "latest_package_tag": "",
                    "next_package_tag": packaging.package_tag_for_upstream_tag("20261008"),
                    "remote_tag_exists": False,
                    "remote_tag_commit": "",
                },
            )

    def test_sync_mode_reuses_latest_alpha_tag_when_head_already_matches_latest_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            subprocess.run(["git", "tag", "14.0.20261008-alpha.1"], cwd=repo_root, check=True, capture_output=True, text=True)

            payload = self._run_script(repo_root, "--explicit-tag", "20261008")

            self.assertEqual(payload["build_tag"], "14.0.20261008-alpha.1")
            self.assertEqual(payload["latest_package_tag"], "14.0.20261008-alpha.1")
            self.assertEqual(payload["next_package_tag"], "14.0.20261008-alpha.2")
            self.assertTrue(payload["remote_tag_exists"])

    def test_backfill_mode_bumps_alpha_number_when_package_tag_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            subprocess.run(["git", "tag", "14.0.20261008-alpha.1"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "tag", "14.0.20261008-alpha.2"], cwd=repo_root, check=True, capture_output=True, text=True)

            payload = self._run_script(repo_root, "--explicit-tag", "20261008", "--release-channel", "backfill")

            self.assertEqual(payload["build_tag"], "14.0.20261008-alpha.3")
            self.assertEqual(payload["latest_package_tag"], "14.0.20261008-alpha.2")
            self.assertEqual(payload["next_package_tag"], "14.0.20261008-alpha.3")
            self.assertFalse(payload["remote_tag_exists"])

    def test_stable_mode_uses_stable_package_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            subprocess.run(["git", "tag", "14.0.20261008-alpha.1"], cwd=repo_root, check=True, capture_output=True, text=True)

            payload = self._run_script(repo_root, "--explicit-tag", "20261008", "--release-channel", "stable")

            self.assertEqual(payload["build_tag"], "14.0.20261008")
            self.assertEqual(payload["package_tag"], "14.0.20261008")
            self.assertFalse(payload["remote_tag_exists"])


if __name__ == "__main__":
    unittest.main()
