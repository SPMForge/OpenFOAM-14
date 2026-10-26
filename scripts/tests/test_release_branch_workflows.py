from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
PUBLISH_CORE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "_publish-upstream-release-core.yml"
SYNC_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish-latest-upstream-alpha.yml"
BACKFILL_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish-upstream-release-manually.yml"
VALIDATE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "validate-apple-release-pipeline.yml"
PACKAGE_DOC_PATH = REPO_ROOT / "docs" / "how-to-build" / "swiftpm-binary-package.md"
README_PATH = REPO_ROOT / "README.md"


class ReleaseBranchWorkflowTests(unittest.TestCase):
    def _job_section(self, workflow: str, job_name: str, next_job_name: str | None = None) -> str:
        start_marker = f"  {job_name}:\n"
        start_index = workflow.index(start_marker)
        if next_job_name is None:
            return workflow[start_index:]
        end_marker = f"  {next_job_name}:\n"
        end_index = workflow.index(end_marker, start_index + len(start_marker))
        return workflow[start_index:end_index]

    def test_repo_keeps_only_packaging_specific_workflows(self) -> None:
        workflow_names = sorted(path.name for path in WORKFLOWS_DIR.glob("*.yml"))
        self.assertEqual(
            workflow_names,
            [
                "_publish-upstream-release-core.yml",
                "publish-latest-upstream-alpha.yml",
                "publish-upstream-release-manually.yml",
                "validate-apple-release-pipeline.yml",
            ],
        )

    def test_sync_workflow_delegates_to_shared_publish_core(self) -> None:
        workflow = SYNC_WORKFLOW_PATH.read_text()
        self.assertIn("name: publish-latest-upstream-alpha", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("uses: ./.github/workflows/_publish-upstream-release-core.yml", workflow)
        self.assertIn("release_channel: sync", workflow)
        self.assertIn("publish_to_default_branch: false", workflow)

    def test_backfill_workflow_delegates_to_shared_publish_core(self) -> None:
        workflow = BACKFILL_WORKFLOW_PATH.read_text()
        self.assertIn("name: publish-upstream-release-manually", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("push:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("uses: ./.github/workflows/_publish-upstream-release-core.yml", workflow)
        self.assertIn('upstream_tag: ${{ inputs.upstream_tag }}', workflow)
        self.assertIn('release_channel: ${{ inputs.release_channel }}', workflow)
        self.assertIn(
            "publish_to_default_branch: ${{ inputs.release_channel == 'stable' && inputs.publish_to_default_branch }}",
            workflow,
        )

    def test_publish_core_workflow_owns_release_steps(self) -> None:
        workflow = PUBLISH_CORE_WORKFLOW_PATH.read_text()
        self.assertIn("workflow_call:", workflow)
        self.assertIn("publish_to_default_branch is only supported for stable releases.", workflow)
        self.assertIn("scripts/spm/source_acquisition.py fetch-tags", workflow)
        self.assertIn("scripts/spm/release_state.py", workflow)
        self.assertIn("inspect-release", workflow)
        self.assertIn("select-publication-tag", workflow)
        self.assertIn("scripts/spm/source_acquisition.py export-source", workflow)
        self.assertIn("--output Package.swift", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("gh api --method PATCH", workflow)
        self.assertIn("git switch --detach", workflow)
        self.assertIn('git tag "${{ steps.publication_plan.outputs.final_package_tag }}" "${commit_sha}"', workflow)
        self.assertIn('git push origin "refs/tags/${{ steps.publication_plan.outputs.final_package_tag }}"', workflow)
        self.assertIn("release_args+=(--prerelease --latest=false)", workflow)
        self.assertIn("-F make_latest=false", workflow)

    def test_validate_workflow_runs_on_push_and_pull_request(self) -> None:
        workflow = VALIDATE_WORKFLOW_PATH.read_text()
        build_job = self._job_section(workflow, "build", "package_contract")
        self.assertIn("name: validate-apple-release-pipeline", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("- main", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("uses: actions/cache/restore@v5", build_job)
        self.assertIn("uses: actions/cache/save@v5", build_job)
        self.assertIn("compiler cache remained empty after build-xcframework", build_job)

    def test_readme_and_docs_describe_wrapper_repo_and_runtime_contract(self) -> None:
        readme = README_PATH.read_text()
        document = PACKAGE_DOC_PATH.read_text()
        self.assertIn("wrapper repository", readme)
        self.assertIn("OpenFOAM/OpenFOAM-14", readme)
        self.assertIn("Alpha package tags point directly at immutable generated metadata commits", readme)
        self.assertIn("Stable promotions may update the default branch", readme)
        self.assertIn("Runtime contract record", document)
        self.assertIn("model: `system-only`", document)


if __name__ == "__main__":
    unittest.main()
