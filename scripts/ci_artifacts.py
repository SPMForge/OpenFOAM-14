#!/usr/bin/env python3
"""Transfer immutable build evidence and report Actions stage outcomes."""
import argparse
import hashlib
import importlib
import json
import os
import re
from urllib.parse import quote
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def record(root):
    files = {str(path.relative_to(root)): digest(path) for path in sorted(root.rglob('*'))
             if path.is_file() and path.name != 'ci-artifacts.json'
             and (path.parent == root or path.relative_to(root).parts[0] in ('repo', 'release-assets'))}
    if not any(name.endswith('.xcframework.zip') for name in files):
        raise ValueError('No final XCFramework archive to record')
    toolchain = subprocess.check_output(['xcodebuild', '-version'], text=True).strip()
    payload = {'schema_version': 1, 'wrapper_commit': os.environ.get('GITHUB_SHA'),
               'upstream_commit': os.environ.get('SPMFORGE_EXPECTED_UPSTREAM_COMMIT'),
               'toolchain': toolchain, 'files': files}
    (root / 'ci-artifacts.json').write_text(json.dumps(payload, indent=2) + '\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write('toolchain=' + toolchain.replace('\n', ' / ') + '\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('## Built artifact identity\n\n' + toolchain + '\n\n| File | SHA-256 |\n|---|---|\n')
            for name, checksum in files.items():
                stream.write(f'| {name} | `{checksum}` |\n')



def consumers(root, product):
    inventories = list(root.rglob('ci-artifacts.json'))
    if not inventories:
        raise ValueError('Missing build inventory')
    for inventory in inventories:
        payload = json.loads(inventory.read_text())
        if not payload.get('upstream_commit'):
            raise ValueError('Build inventory is missing immutable source identity')
        for relative, expected in payload['files'].items():
            path = inventory.parent / relative
            if not path.resolve().is_relative_to(inventory.parent.resolve()) or digest(path) != expected:
                raise ValueError(f'Artifact transfer digest mismatch: {relative}')
    archives = list(root.rglob('*.xcframework.zip'))
    with tempfile.TemporaryDirectory(prefix='spmforge-consumers-') as temporary:
        extracted = Path(temporary)
        for archive in archives:
            subprocess.run(['ditto', '-x', '-k', str(archive.resolve()), str(extracted)], check=True)
        frameworks = {path.stem: path for path in extracted.glob('*.xcframework')}
        if not frameworks:
            raise ValueError('No XCFramework at archive root')
        print('::group::Debug and Release consumer acceptance', flush=True)
        if product == 'MoltenVK':
            artifacts = ROOT / 'Artifacts'
            artifacts.mkdir(exist_ok=True)
            import shutil
            shutil.copytree(frameworks['MoltenVK'], artifacts / 'MoltenVK.xcframework', symlinks=True, dirs_exist_ok=True)
            subprocess.run(['bash', str(ROOT / 'tests/verify_swift_package_consumer.sh')],
                           env={**os.environ, 'MVK_PACKAGE_ROOT': str(ROOT)}, check=True)
        elif product == 'libwebp':
            sys.path.insert(0, str(ROOT / 'scripts'))
            module = importlib.import_module('spm_release')
            module.verify_consumer_fixture(frameworks, extracted / 'consumers')
        else:
            subprocess.run([sys.executable, str(ROOT / 'scripts/spm/smoke_test_package.py'),
                            '--variant', product, '--xcframework', str(frameworks[product])], check=True)
        print('::endgroup::', flush=True)


def summary():
    needs = json.loads(os.environ['CI_NEEDS'])
    lines = ['## Pipeline result', '', '| Stage | Result |', '|---|---|']
    for name, job in needs.items():
        lines.append(f"| {name} | {job['result']} |")
    lines += ['', '### Resolved inputs and publication decision', '']
    for name in ('resolve', 'build', 'publish', 'release'):
        for key, value in needs.get(name, {}).get('outputs', {}).items():
            lines.append(f'- **{key}**: `{str(value).replace(chr(10), " ")}`')
    repo = os.environ['GITHUB_REPOSITORY']
    for name in ('publish', 'release'):
        tag = needs.get(name, {}).get('outputs', {}).get('package_tag')
        if tag:
            lines.append(f'[Release {tag}](https://github.com/{repo}/releases/tag/{quote(tag, safe="")})')
    lines += ['', '### Checked-in package entry', '', '| Target | Availability |', '|---|---|']
    manifest = (ROOT / 'Package.swift').read_text()
    for target, url, checksum in re.findall(r'\.binaryTarget\(\s*name:\s*"([^"]+)"\s*,\s*url:\s*"([^"]+)"\s*,\s*checksum:\s*"([^"]+)"', manifest):
        parts = url.split('/releases/download/', 1)
        state = 'UNVERIFIED'
        if checksum == '0' * 64:
            state = 'UNAVAILABLE: placeholder checksum'
        elif len(parts) == 2:
            tag, asset = parts[1].split('/', 1)
            response = subprocess.run(['gh', 'api', f'repos/{repo}/releases/tags/{quote(tag, safe="")}'], capture_output=True, text=True)
            if response.returncode == 0:
                release = json.loads(response.stdout)
                match = next((a for a in release['assets'] if a['name'] == asset), None)
                state = 'AVAILABLE: asset digest matches' if match and not release['draft'] and match.get('digest') == 'sha256:' + checksum else 'UNAVAILABLE OR UNVERIFIED: asset or digest mismatch'
            elif '(HTTP 404)' in response.stderr:
                state = 'UNAVAILABLE: release missing'
        lines.append(f'| {target} | {state} |')
    lines += ['', f'[Run and artifacts](https://github.com/{repo}/actions/runs/{os.environ["GITHUB_RUN_ID"]})',
              '', 'Use an accepted package version tag. Alpha publication does not promote the default-branch manifest.',
              'Build inventories record archive SHA-256 values, immutable upstream commit and Xcode version.',
              'A skipped consumer job is not consumer acceptance; reuse refers to a previously published identity.']
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write('\n'.join(lines) + '\n')
    if any(job['result'] in ('failure', 'cancelled') for job in needs.values()):
        raise SystemExit('Pipeline failed or was cancelled; see the stage results above.')
    for name in ('verify', 'resolve'):
        if needs.get(name, {}).get('result') != 'success':
            raise SystemExit(f'Required stage {name} did not succeed')
    outputs = needs.get('resolve', {}).get('outputs', {})
    mode = outputs.get('publication_mode', outputs.get('release_mode'))
    if mode not in ('skip', 'metadata'):
        for name in ('build', 'consumers', 'package_contract', 'publish', 'release'):
            if name in needs and needs[name]['result'] != 'success':
                raise SystemExit(f'Required stage {name} did not succeed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['record', 'consumers', 'summary'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--product')
    args = parser.parse_args()
    if args.command == 'summary':
        summary()
    elif args.command == 'record':
        record(args.root.resolve())
    else:
        consumers(args.root.resolve(), args.product)


if __name__ == '__main__':
    main()
