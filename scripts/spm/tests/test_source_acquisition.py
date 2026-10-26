from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.spm import source_acquisition


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ACQUISITION_SCRIPT = REPO_ROOT / "scripts" / "spm" / "source_acquisition.py"
SOURCE_ACQUISITION_CONTRACT = REPO_ROOT / "scripts" / "spm" / "source_acquisition.json"


class SourceAcquisitionContractTests(unittest.TestCase):
    def test_contract_file_exists(self) -> None:
        self.assertTrue(SOURCE_ACQUISITION_CONTRACT.exists())

    def test_fetch_tags_requires_explicit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            process = subprocess.run(
                [
                    sys.executable,
                    str(SOURCE_ACQUISITION_SCRIPT),
                    "fetch-tags",
                    "--repo-root",
                    str(repo_root),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("source acquisition contract", process.stderr)

    def test_help_mentions_export_source_command(self) -> None:
        process = subprocess.run(
            [sys.executable, str(SOURCE_ACQUISITION_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, msg=process.stderr)
        self.assertIn("export-source", process.stdout)

    def test_detects_case_colliding_paths_in_git_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory)
            subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True, text=True)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo_root,
                input="// header\n",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            tree_input = f"100644 blob {blob}\tVector.H\n100644 blob {blob}\tvector.H\n"
            tree = subprocess.run(
                ["git", "mktree"],
                cwd=repo_root,
                input=tree_input,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            commit = subprocess.run(
                ["git", "commit-tree", tree, "-m", "case collision"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "update-ref", "refs/upstream-tags/20261008", commit], cwd=repo_root, check=True)
            self.assertTrue(source_acquisition._ref_has_case_collisions(repo_root, "refs/upstream-tags/20261008"))

    def test_export_source_handles_case_colliding_paths_into_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_root = Path(temporary_directory) / "repo"
            repo_root.mkdir()
            subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True, capture_output=True, text=True)
            blob_upper = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo_root,
                input="// UPPER\n",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            blob_lower = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo_root,
                input="// lower\n",
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            tree_input = f"100644 blob {blob_upper}\tVector.H\n100644 blob {blob_lower}\tvector.H\n"
            tree = subprocess.run(
                ["git", "mktree"],
                cwd=repo_root,
                input=tree_input,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            commit = subprocess.run(
                ["git", "commit-tree", tree, "-m", "case collision"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "update-ref", "refs/upstream-tags/20261008", commit], cwd=repo_root, check=True)

            destination = Path(temporary_directory) / "exported-src"
            sparse_image = destination.parent / f"{destination.name}.sparseimage"
            try:
                source_acquisition._export_source(repo_root, SOURCE_ACQUISITION_CONTRACT, "20261008", destination)
                self.assertEqual((destination / "Vector.H").read_text(), "// UPPER\n")
                self.assertEqual((destination / "vector.H").read_text(), "// lower\n")
            finally:
                source_acquisition._detach_sparse_image_if_mounted(destination, sparse_image, repo_root)


if __name__ == "__main__":
    unittest.main()
