#!/usr/bin/env python3
"""Archive patched upstream source together with the exact bridge and build recipe."""
from __future__ import annotations

import argparse
from pathlib import Path
import tarfile


def source_filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts=Path(member.name).parts
    if '.git' in parts or '__pycache__' in parts or member.name.endswith('.pyc') or '.prepared-workspace-path' in parts:
        return None
    return member


def archive_source(source_root: Path, generated_root: Path, wrapper_root: Path, output: Path) -> None:
    for name in ['foam_spm_bridge.C','apple_openfoam_compat.h','CMakeLists.txt']:
        if not (generated_root/name).is_file():
            raise ValueError(f'Missing compiled source input: {generated_root/name}')
    if not (wrapper_root/'config/release-inputs.json').is_file():
        raise ValueError('Source archive requires its immutable release input record.')
    if output.resolve().is_relative_to(source_root.resolve()):
        raise ValueError('Source archive output must be outside the source snapshot.')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz',dereference=False) as archive:
        archive.add(source_root,arcname='.',filter=source_filter)
        archive.add(generated_root,arcname='.spmforge/generated',filter=source_filter)
        for name in ['scripts','config','toolchains','docs/how-to-build','.github','tests','README.md','COPYING','LICENSE']:
            path=wrapper_root/name
            if path.exists():
                archive.add(path,arcname='.spmforge/'+name,filter=source_filter)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--generated-root',type=Path,required=True)
    parser.add_argument('--wrapper-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    archive_source(args.source_root,args.generated_root,args.wrapper_root,args.output)


if __name__=='__main__':
    main()
