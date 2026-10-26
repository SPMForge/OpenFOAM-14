#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.spm import packaging


def required_release_asset_names(upstream_tag: str) -> tuple[str, ...]:
    return tuple(packaging.asset_name_for_variant(variant, upstream_tag) for variant in packaging.VARIANTS)


def _load_release_view(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("release view payload must be a JSON object")
    return payload


def _git_show(repo_root: Path, ref_name: str, file_path: str) -> str | None:
    process = subprocess.run(
        ["git", "show", f"{ref_name}:{file_path}"],
        check=False,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        return None
    return process.stdout


def _ref_exists(repo_root: Path, ref_name: str) -> bool:
    process = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref_name],
        check=False,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return process.returncode == 0


def _rev_parse(repo_root: Path, ref_name: str) -> str:
    process = subprocess.run(
        ["git", "rev-parse", f"{ref_name}^{{commit}}"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return process.stdout.strip()


def inspect_release_state(
    *,
    package_tag: str,
    upstream_tag: str,
    release_channel: str,
    tag_exists: bool,
    release_view: dict[str, object] | None,
    latest_release_tag: str | None,
) -> dict[str, object]:
    if release_channel not in {"sync", "alpha", "stable"}:
        raise ValueError(f"unsupported release channel: {release_channel}")

    expected_prerelease = release_channel != "stable"
    required_assets = required_release_asset_names(upstream_tag)
    published_assets: tuple[str, ...] = ()
    release_exists = release_view is not None
    release_is_prerelease = False
    release_is_latest = latest_release_tag == package_tag if latest_release_tag else False

    if release_view is not None:
        raw_prerelease = release_view.get("isPrerelease")
        if not isinstance(raw_prerelease, bool):
            raise ValueError("release view payload missing boolean isPrerelease")
        release_is_prerelease = raw_prerelease

        raw_assets = release_view.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("release view payload missing assets list")
        asset_names: list[str] = []
        for asset in raw_assets:
            if not isinstance(asset, dict):
                raise ValueError("release asset payload must be an object")
            name = asset.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("release asset payload missing non-empty name")
            asset_names.append(name)
        published_assets = tuple(sorted(asset_names))

    missing_assets = tuple(asset for asset in required_assets if asset not in published_assets)
    metadata_needs_repair = False
    if release_exists:
        if expected_prerelease:
            metadata_needs_repair = (not release_is_prerelease) or release_is_latest
        else:
            metadata_needs_repair = release_is_prerelease

    if not tag_exists:
        mode = "create"
    elif (not release_exists) or missing_assets or metadata_needs_repair:
        mode = "repair"
    else:
        mode = "skip"

    return {
        "package_tag": package_tag,
        "upstream_tag": upstream_tag,
        "release_channel": release_channel,
        "tag_exists": tag_exists,
        "release_exists": release_exists,
        "required_assets": list(required_assets),
        "published_assets": list(published_assets),
        "missing_assets": list(missing_assets),
        "expected_prerelease": expected_prerelease,
        "release_is_prerelease": release_is_prerelease,
        "release_is_latest": release_is_latest,
        "metadata_needs_repair": metadata_needs_repair,
        "mode": mode,
    }


def select_publication_tag(
    *,
    repo_root: Path,
    release_channel: str,
    build_tag: str,
    latest_package_tag: str | None,
    next_package_tag: str | None,
    rendered_package_swift: str,
    rendered_current_release_json: str | None = None,
) -> dict[str, object]:
    if release_channel not in {"sync", "alpha", "stable"}:
        raise ValueError(f"unsupported release channel: {release_channel}")

    final_package_tag = build_tag
    latest_package_tag = latest_package_tag or None
    next_package_tag = next_package_tag or None
    contract_matches = True

    def _latest_package_matches(tag: str) -> bool:
        latest_package_swift = _git_show(repo_root, f"refs/tags/{tag}", "Package.swift")
        if latest_package_swift is None:
            return False
        if latest_package_swift != rendered_package_swift:
            return False
        if rendered_current_release_json is None:
            return True
        latest_current_release_json = _git_show(
            repo_root,
            f"refs/tags/{tag}",
            "scripts/spm/current_release.json",
        )
        return latest_current_release_json == rendered_current_release_json

    if release_channel in {"sync", "alpha"}:
        if latest_package_tag is not None:
            contract_matches = _latest_package_matches(latest_package_tag)
            if contract_matches:
                final_package_tag = latest_package_tag
            elif next_package_tag is not None:
                final_package_tag = next_package_tag
            elif build_tag == latest_package_tag and release_channel == "alpha":
                raise ValueError("next_package_tag is required when the latest alpha Package.swift does not match.")
    elif release_channel == "stable":
        pass

    tag_ref = f"refs/tags/{final_package_tag}"
    remote_tag_exists = _ref_exists(repo_root, tag_ref)
    remote_tag_commit = _rev_parse(repo_root, tag_ref) if remote_tag_exists else ""

    return {
        "final_package_tag": final_package_tag,
        "package_contract_matches": contract_matches,
        "remote_tag_exists": remote_tag_exists,
        "remote_tag_commit": remote_tag_commit,
    }


def _write_github_output(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"mode={payload['mode']}\n")
        handle.write(f"release_exists={str(payload['release_exists']).lower()}\n")
        handle.write(f"metadata_needs_repair={str(payload['metadata_needs_repair']).lower()}\n")
        handle.write(f"missing_assets={','.join(payload['missing_assets'])}\n")


def _write_publication_github_output(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"final_package_tag={payload['final_package_tag']}\n")
        handle.write(f"package_contract_matches={str(payload['package_contract_matches']).lower()}\n")
        handle.write(f"remote_tag_exists={str(payload['remote_tag_exists']).lower()}\n")
        handle.write(f"remote_tag_commit={payload['remote_tag_commit']}\n")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect GitHub release completeness and metadata drift.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect-release", help="Inspect whether a package tag should be created, repaired, or skipped.")
    inspect_parser.add_argument("--package-tag", required=True)
    inspect_parser.add_argument("--upstream-tag", required=True)
    inspect_parser.add_argument("--release-channel", required=True)
    inspect_parser.add_argument("--tag-exists", action="store_true")
    inspect_parser.add_argument("--release-view-json", type=Path)
    inspect_parser.add_argument("--latest-release-tag")
    inspect_parser.add_argument("--github-output", type=Path)

    publication_parser = subparsers.add_parser(
        "select-publication-tag",
        help="Resolve whether an alpha publish should reuse the latest tagged manifest or advance to the next alpha tag.",
    )
    publication_parser.add_argument("--repo-root", type=Path, default=packaging.REPO_ROOT)
    publication_parser.add_argument("--release-channel", required=True)
    publication_parser.add_argument("--build-tag", required=True)
    publication_parser.add_argument("--latest-package-tag")
    publication_parser.add_argument("--next-package-tag")
    publication_parser.add_argument("--rendered-package-swift", required=True, type=Path)
    publication_parser.add_argument("--rendered-current-release-json", type=Path)
    publication_parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()

    try:
        if arguments.command == "inspect-release":
            payload = inspect_release_state(
                package_tag=arguments.package_tag,
                upstream_tag=arguments.upstream_tag,
                release_channel=arguments.release_channel,
                tag_exists=arguments.tag_exists,
                release_view=_load_release_view(arguments.release_view_json),
                latest_release_tag=arguments.latest_release_tag,
            )
        elif arguments.command == "select-publication-tag":
            payload = select_publication_tag(
                repo_root=arguments.repo_root,
                release_channel=arguments.release_channel,
                build_tag=arguments.build_tag,
                latest_package_tag=arguments.latest_package_tag,
                next_package_tag=arguments.next_package_tag,
                rendered_package_swift=arguments.rendered_package_swift.read_text(encoding="utf-8"),
                rendered_current_release_json=(
                    arguments.rendered_current_release_json.read_text(encoding="utf-8")
                    if arguments.rendered_current_release_json is not None
                    else None
                ),
            )
        else:
            raise ValueError(f"unsupported command: {arguments.command}")
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    if arguments.github_output is not None:
        if arguments.command == "inspect-release":
            _write_github_output(arguments.github_output, payload)
        else:
            _write_publication_github_output(arguments.github_output, payload)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
