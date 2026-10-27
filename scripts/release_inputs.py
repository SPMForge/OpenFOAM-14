#!/usr/bin/env python3
"""Record immutable source identity and wrapper inputs for release reuse."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

RECORD = 'config/release-inputs.json'
GENERATED_FILES = {
    'Package.swift', RECORD, 'config/current_release.json', 'config/PackageVersion.txt',
    'config/UpstreamSourceRef.txt', 'config/checksums.json',
    'scripts/spm/current_release.json', 'SwiftPackage/PackageVersion.txt',
    'SwiftPackage/UpstreamSourceRef.txt',
}
GENERATED_DIRECTORIES = ('config/checksums/', 'SwiftPackage/checksums/', 'Artifacts/')


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def resolve_source(root: Path, tag: str, expected: str | None = None) -> str:
    commit = git(root, 'rev-parse', '--verify', f'refs/upstream-tags/{tag}^{{commit}}')
    expected = expected or os.environ.get('SPMFORGE_EXPECTED_UPSTREAM_COMMIT')
    if expected and commit != expected:
        raise ValueError(f'Upstream tag moved after resolution: {tag}; expected {expected}, got {commit}')
    return commit


def wrapper_digest(root: Path) -> str:
    output = subprocess.run(['git', 'ls-files', '--stage', '-z'], cwd=root, check=True, capture_output=True).stdout
    digest = hashlib.sha256()
    for entry in sorted(output.split(b'\0')):
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        relative = os.fsdecode(raw_path)
        if relative in GENERATED_FILES or relative.startswith(GENERATED_DIRECTORIES):
            continue
        mode, _, stage = metadata.split()
        if stage != b'0':
            raise ValueError('Cannot capture release inputs with unresolved merge conflicts.')
        path = root / relative
        if mode == b'160000':
            payload = metadata.split()[1]
        elif path.is_symlink():
            payload = os.fsencode(os.readlink(path))
        else:
            payload = path.read_bytes()
        digest.update(mode + b'\0' + raw_path + b'\0' + hashlib.sha256(payload).digest())
    return digest.hexdigest()


def upstream_repository(root: Path) -> str:
    for relative in ('config/source_acquisition.json', 'config/source-acquisition.json'):
        path = root / relative
        if path.is_file():
            return json.loads(path.read_text())['upstream_repository_url']
    return (root / 'config/UpstreamRepository.txt').read_text().strip()


def capture(root: Path, tag: str, commit: str | None = None) -> dict:
    commit = commit or resolve_source(root, tag)
    if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit) is None:
        raise ValueError('Upstream commit must be a full Git object id.')
    record = {'schema_version': 1, 'upstream_repository': upstream_repository(root),
              'upstream_tag': tag, 'upstream_commit': commit, 'wrapper_inputs_sha256': wrapper_digest(root)}
    destination = root / RECORD
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record


def tag_input_state(root: Path, tag: str) -> str:
    revision = subprocess.run(['git', 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}'],
                              cwd=root, capture_output=True, text=True)
    if revision.returncode:
        return 'tag_absent'
    process = subprocess.run(['git', 'show', f'{revision.stdout.strip()}:{RECORD}'],
                             cwd=root, capture_output=True, text=True)
    if process.returncode:
        return 'identity_missing'
    try:
        recorded = json.loads(process.stdout)
        current = json.loads((root / RECORD).read_text())
        required = {'schema_version', 'upstream_repository', 'upstream_tag',
                    'upstream_commit', 'wrapper_inputs_sha256'}
        for value in (recorded, current):
            if not isinstance(value, dict) or not required <= value.keys():
                return 'identity_invalid'
            if value['schema_version'] != 1 or not all(isinstance(value[key], str) and value[key] for key in required - {'schema_version'}):
                return 'identity_invalid'
            if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value['upstream_commit']) is None or re.fullmatch(r'[0-9a-f]{64}', value['wrapper_inputs_sha256']) is None:
                return 'identity_invalid'
    except (OSError, ValueError, TypeError):
        return 'identity_invalid'
    return 'identity_matches' if recorded == current else 'identity_differs'


def tag_inputs_match(root: Path, tag: str) -> bool | None:
    if not (root / RECORD).is_file():
        return None  # Legacy local tooling; CI captures inputs before planning.
    return tag_input_state(root, tag) == 'identity_matches'


def github_json(endpoint: str) -> dict | None:
    result = subprocess.run(['gh', 'api', endpoint], capture_output=True, text=True)
    if result.returncode:
        if '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(f'Unable to establish GitHub release state: {endpoint}')
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError(f'Expected GitHub JSON object: {endpoint}')
    return payload


def read_release_state(repository: str, tag: str) -> tuple[dict | None, str | None]:
    release = github_json(f'repos/{repository}/releases/tags/{quote(tag, safe="")}')
    latest = github_json(f'repos/{repository}/releases/latest')
    view = None if release is None else {'isPrerelease': release['prerelease'], 'isDraft': release.get('draft', False), 'assets': release['assets']}
    return view, None if latest is None else latest['tag_name']


def increment_alpha_tag(tag: str) -> str:
    match = re.fullmatch(r'([0-9]+\.[0-9]+\.[0-9]+-alpha\.)([0-9]+)', tag)
    if match is None:
        raise ValueError(f'Invalid Alpha tag: {tag!r}')
    return match[1] + str(int(match[2]) + 1)


def alpha_name_occupied(root: Path, repository: str, tag: str) -> bool:
    increment_alpha_tag(tag)  # Validate before using the name in refs or API paths.
    if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is None:
        raise ValueError('Expected owner/repository')
    reference = f'refs/tags/{tag}'
    local = subprocess.run(['git', 'show-ref', '--verify', '--quiet', reference], cwd=root, capture_output=True)
    if local.returncode not in (0, 1):
        raise RuntimeError('Unable to establish local tag state')
    remote = subprocess.run(['git', 'ls-remote', '--exit-code', '--tags', '--refs', 'origin', reference],
                            cwd=root, capture_output=True, text=True)
    if remote.returncode not in (0, 2):
        raise RuntimeError('Unable to establish remote tag state')
    release = github_json(f'repos/{repository}/releases/tags/{quote(tag, safe="")}')
    if release is not None:
        if (type(release.get('id')) is not int or release['id'] <= 0
                or release.get('tag_name') != tag or type(release.get('draft')) is not bool
                or not isinstance(release.get('assets'), list)):
            raise ValueError('Incomplete or inconsistent GitHub Release response')
    return local.returncode == 0 or remote.returncode == 0 or release is not None


def find_available_alpha(root: Path, repository: str, candidate: str, increment,
                         *, max_candidates: int = 1000) -> dict:
    if not 1 <= max_candidates <= 1000:
        raise ValueError('Candidate limit must be between 1 and 1000')
    occupied = []
    for _ in range(max_candidates):
        if not alpha_name_occupied(root, repository, candidate):
            return {'tag': candidate, 'occupied_tags': occupied,
                    'reason': f'Exact Git and Release checks permit {candidate}; skipped {len(occupied)} occupied names'}
        occupied.append(candidate)
        next_candidate = increment(candidate)
        if next_candidate == candidate:
            raise ValueError('Alpha increment did not advance')
        candidate = next_candidate
    raise RuntimeError('Alpha candidate limit reached; no public name allocated')


def assert_alpha_available(root: Path, repository: str, tag: str) -> None:
    if alpha_name_occupied(root, repository, tag):
        raise RuntimeError(f'Alpha name became occupied after Resolve: {tag}; rerun resolution without changing public state')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['capture', 'verify-source', 'guard', 'allocate-alpha', 'assert-alpha-available'])
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--upstream-tag')
    parser.add_argument('--upstream-commit')
    parser.add_argument('--tag')
    parser.add_argument('--release-channel', default='alpha')
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--repository')
    parser.add_argument('--output-name', default='next_package_tag')
    args = parser.parse_args()
    outputs = {}
    if args.command in ('allocate-alpha', 'assert-alpha-available'):
        if not args.repository or not args.tag:
            parser.error('Alpha name checks require --repository and --tag')
        if args.command == 'assert-alpha-available':
            assert_alpha_available(args.repo_root, args.repository, args.tag)
            return
        result = find_available_alpha(args.repo_root, args.repository, args.tag, increment_alpha_tag)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.output_name):
            parser.error('Invalid output name')
        outputs = {args.output_name: result['tag'], 'allocation_reason': result['reason'],
                   'occupied_alpha_tags': json.dumps(result['occupied_tags'])}
    elif args.command == 'capture':
        if not args.upstream_tag:
            parser.error('--upstream-tag is required')
        record = capture(args.repo_root, args.upstream_tag, args.upstream_commit)
        outputs = {'upstream_commit': record['upstream_commit'], 'wrapper_inputs_sha256': record['wrapper_inputs_sha256']}
    elif args.command == 'verify-source':
        if not args.upstream_tag:
            parser.error('--upstream-tag is required')
        print(resolve_source(args.repo_root, args.upstream_tag, args.upstream_commit))
        return
    else:
        if not args.tag or not (args.repo_root / RECORD).is_file():
            parser.error('guard requires --tag and a captured input record')
        exists = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'refs/tags/{args.tag}'], cwd=args.repo_root, capture_output=True).returncode == 0
        if exists and not tag_inputs_match(args.repo_root, args.tag):
            if args.release_channel == 'stable':
                raise ValueError('Existing stable tag has different or unrecorded release inputs; refusing reuse.')
            outputs = {'mode': 'evaluate', 'publication_mode': 'evaluate'}
    if args.github_output:
        with args.github_output.open('a') as output:
            for key, value in outputs.items():
                output.write(f'{key}={value}\n')
    print(json.dumps(outputs, sort_keys=True))


if __name__ == '__main__':
    main()
