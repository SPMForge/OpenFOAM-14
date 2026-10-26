from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from scripts.spm import release_state


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_STATE_SCRIPT = REPO_ROOT / "scripts" / "spm" / "release_state.py"


class ReleaseStateTests(unittest.TestCase):
    def _init_git_repo(self, repo_root: Path) -> None:
        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo_root, check=True, capture_output=True, text=True)

    def _commit_package(self, repo_root: Path, package_contents: str, message: str) -> str:
        package_path = repo_root / "Package.swift"
        package_path.write_text(package_contents)
        subprocess.run(["git", "add", "Package.swift"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _commit_package_metadata(
        self,
        repo_root: Path,
        package_contents: str,
        current_release_contents: str,
        message: str,
    ) -> str:
        package_path = repo_root / "Package.swift"
        metadata_path = repo_root / "config" / "current_release.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        package_path.write_text(package_contents)
        metadata_path.write_text(current_release_contents)
        subprocess.run(
            ["git", "add", "Package.swift", "config/current_release.json"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "commit", "-m", message], cwd=repo_root, check=True, capture_output=True, text=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_required_release_asset_names_cover_all_variants(self) -> None:
        self.assertEqual(
            release_state.required_release_asset_names("20261008"),
            (
                "OpenFOAM-14-20261008-apple.xcframework.zip",
                "OpenFOAM-14-20261008-source.tar.gz",
            ),
        )

    def test_complete_alpha_release_skips(self) -> None:
        payload = release_state.inspect_release_state(
            package_tag="14.0.20261008-alpha.1",
            upstream_tag="20261008",
            release_channel="sync",
            tag_exists=True,
            release_view={
                "isPrerelease": True,
                "assets": [
                    {"name": "OpenFOAM-14-20261008-apple.xcframework.zip"},
                    {"name": "OpenFOAM-14-20261008-source.tar.gz"},
                ],
            },
            latest_release_tag=None,
        )

        self.assertEqual(payload["mode"], "skip")
        self.assertFalse(payload["metadata_needs_repair"])
        self.assertEqual(payload["missing_assets"], [])

    def test_release_repairs_when_source_tarball_missing(self) -> None:
        payload = release_state.inspect_release_state(
            package_tag="14.0.20261008-alpha.1",
            upstream_tag="20261008",
            release_channel="sync",
            tag_exists=True,
            release_view={
                "isPrerelease": True,
                "assets": [
                    {"name": "OpenFOAM-14-20261008-apple.xcframework.zip"},
                ],
            },
            latest_release_tag=None,
        )

        self.assertEqual(payload["mode"], "repair")
        self.assertEqual(payload["missing_assets"], ["OpenFOAM-14-20261008-source.tar.gz"])

    def test_alpha_release_repairs_metadata_when_latest_or_not_prerelease(self) -> None:
        payload = release_state.inspect_release_state(
            package_tag="14.0.20261008-alpha.1",
            upstream_tag="20261008",
            release_channel="alpha",
            tag_exists=True,
            release_view={
                "isPrerelease": False,
                "assets": [
                    {"name": "OpenFOAM-14-20261008-apple.xcframework.zip"},
                ],
            },
            latest_release_tag="14.0.20261008-alpha.1",
        )

        self.assertEqual(payload["mode"], "repair")
        self.assertTrue(payload["metadata_needs_repair"])

    def test_existing_tag_without_release_repairs(self) -> None:
        payload = release_state.inspect_release_state(
            package_tag="14.0.20261008",
            upstream_tag="20261008",
            release_channel="stable",
            tag_exists=True,
            release_view=None,
            latest_release_tag=None,
        )

        self.assertEqual(payload["mode"], "repair")
        self.assertFalse(payload["release_exists"])

    def test_missing_tag_creates_release(self) -> None:
        payload = release_state.inspect_release_state(
            package_tag="14.0.20261008-alpha.2",
            upstream_tag="20261008",
            release_channel="alpha",
            tag_exists=False,
            release_view=None,
            latest_release_tag=None,
        )

        self.assertEqual(payload["mode"], "create")

    def test_select_publication_tag_reuses_latest_alpha_when_rendered_package_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            commit_sha = self._commit_package(repo_root, "// generated manifest\n", "release alpha.1")
            subprocess.run(
                ["git", "tag", "14.0.20261008-alpha.1", commit_sha],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = release_state.select_publication_tag(
                repo_root=repo_root,
                release_channel="sync",
                build_tag="14.0.20261008-alpha.1",
                latest_package_tag="14.0.20261008-alpha.1",
                next_package_tag="14.0.20261008-alpha.2",
                rendered_package_swift="// generated manifest\n",
            )

            self.assertEqual(payload["final_package_tag"], "14.0.20261008-alpha.1")
            self.assertTrue(payload["package_contract_matches"])
            self.assertTrue(payload["remote_tag_exists"])
            self.assertEqual(payload["remote_tag_commit"], commit_sha)

    def test_select_publication_tag_advances_when_current_release_metadata_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            self._commit_package_metadata(
                repo_root,
                "// generated manifest\n",
                '{"runtime_dependency_model":"old"}\n',
                "release alpha.1",
            )
            subprocess.run(["git", "tag", "14.0.20261008-alpha.1"], cwd=repo_root, check=True, capture_output=True, text=True)

            payload = release_state.select_publication_tag(
                repo_root=repo_root,
                release_channel="alpha",
                build_tag="14.0.20261008-alpha.1",
                latest_package_tag="14.0.20261008-alpha.1",
                next_package_tag="14.0.20261008-alpha.2",
                rendered_package_swift="// generated manifest\n",
                rendered_current_release_json='{"runtime_dependency_model":"new"}\n',
            )

            self.assertEqual(payload["final_package_tag"], "14.0.20261008-alpha.2")
            self.assertFalse(payload["remote_tag_exists"])
            self.assertEqual(payload["remote_tag_commit"], "")

    def test_publication_script_advances_alpha_when_checksum_or_url_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            self._init_git_repo(repo_root)
            tagged_manifest = (
                '// swift-tools-version: 6.0\n'
                'import PackageDescription\n'
                'let package = Package(name: "OpenFOAM-14", targets: [\n'
                '    .binaryTarget(name: "OpenFOAM", url: "https://github.com/SPMForge/OpenFOAM-14/releases/download/14.0.20261008-alpha.1/OpenFOAM-14-apple.xcframework.zip", checksum: "1111111111111111111111111111111111111111111111111111111111111111")\n'
                '])\n'
            )
            commit_sha = self._commit_package(repo_root, tagged_manifest, "release alpha.1")
            subprocess.run(
                ["git", "tag", "14.0.20261008-alpha.1", commit_sha],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            rendered_manifest = (
                '// swift-tools-version: 6.0\n'
                'import PackageDescription\n'
                'let package = Package(name: "OpenFOAM-14", targets: [\n'
                '    .binaryTarget(name: "OpenFOAM", url: "https://github.com/SPMForge/OpenFOAM-14/releases/download/14.0.20261008-alpha.2/OpenFOAM-14-apple.xcframework.zip", checksum: "2222222222222222222222222222222222222222222222222222222222222222")\n'
                '])\n'
            )
            rendered_path = repo_root / "rendered-Package.swift"
            rendered_path.write_text(rendered_manifest)

            output_path = repo_root / "github-output.txt"
            process = subprocess.run(
                [
                    sys.executable,
                    str(RELEASE_STATE_SCRIPT),
                    "select-publication-tag",
                    "--repo-root",
                    str(repo_root),
                    "--release-channel",
                    "alpha",
                    "--build-tag",
                    "14.0.20261008-alpha.1",
                    "--latest-package-tag",
                    "14.0.20261008-alpha.1",
                    "--next-package-tag",
                    "14.0.20261008-alpha.2",
                    "--rendered-package-swift",
                    str(rendered_path),
                    "--github-output",
                    str(output_path),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )

            payload = json.loads(process.stdout)
            self.assertEqual(payload["final_package_tag"], "14.0.20261008-alpha.2")
            self.assertFalse(payload["package_contract_matches"])


if __name__ == "__main__":
    unittest.main()
