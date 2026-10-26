#!/usr/bin/env python3
"""Verify existing release bytes before uploading only missing assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return 'sha256:' + digest.hexdigest()


def upload_missing_assets(repository: str, tag: str, paths: list[Path]) -> list[str]:
    if not paths or len({path.name for path in paths}) != len(paths):
        raise ValueError('Release asset paths must be non-empty with unique basenames.')
    for path in paths:
        if not path.is_file():
            raise ValueError(f'Missing local release asset: {path}')
    response = subprocess.run(
        ['gh', 'api', f'repos/{repository}/releases/tags/{quote(tag, safe="")}'],
        check=True, capture_output=True, text=True,
    )
    assets = json.loads(response.stdout)['assets']
    published = {asset['name']: asset for asset in assets}
    missing = []
    # Preflight the entire set before any upload, including assets outside Package.swift.
    for path in paths:
        asset = published.get(path.name)
        if asset is None:
            missing.append(path)
            continue
        expected = sha256(path)
        actual = asset.get('digest')
        if actual is None:
            asset_id = asset.get('id')
            if type(asset_id) is not int or asset_id <= 0:
                raise ValueError(f'Missing release asset digest and valid id: {path.name}')
            with tempfile.TemporaryDirectory(prefix='release-asset-') as directory:
                downloaded = Path(directory) / 'asset'
                with downloaded.open('wb') as output:
                    subprocess.run(
                        ['gh', 'api', f'repos/{repository}/releases/assets/{asset_id}',
                         '-H', 'Accept: application/octet-stream'],
                        check=True, stdout=output,
                    )
                actual = sha256(downloaded)
        if actual != expected:
            raise ValueError(f'Refusing to replace different published bytes: {tag}/{path.name}')
    if missing:
        subprocess.run(
            ['gh', 'release', 'upload', tag, '--repo', repository, *map(str, missing)],
            check=True,
        )
    return [path.name for path in missing]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('assets', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps({'uploaded': upload_missing_assets(args.repo, args.tag, args.assets)}))


if __name__ == '__main__':
    main()
