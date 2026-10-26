# OpenFOAM-14 SwiftPM Binary Package

This repository is an independent wrapper repo on the `main` branch for publishing Apple-platform SwiftPM binary releases of upstream [`OpenFOAM/OpenFOAM-14`](https://github.com/OpenFOAM/OpenFOAM-14).

## Architecture & C++ / SwiftPM Bridging Model

### 1. Case-Sensitive Source Acquisition vs. Case-Safe Distribution

Upstream `OpenFOAM-14` relies on a case-sensitive filesystem layout where template declarations (`Vector.H`, `Tensor.H`, `PointHit.H`, `LduMatrix.H`, `PrimitivePatch.H`) coexist alongside concrete typedef headers (`vector.H`, `tensor.H`, `pointHit.H`, `lduMatrix.H`, `primitivePatch.H`).

- **Build-time**: On macOS hosts where default APFS is case-insensitive, `scripts/spm/source_acquisition.py export-source` detects case-colliding paths in the upstream tag and automatically creates and mounts a `Case-sensitive APFS` sparse image at the export destination before checking out the upstream git worktree.
- **Consumer-time**: SwiftPM consumers on macOS unpack `.xcframework.zip` archives onto default case-insensitive APFS. Therefore, `OpenFOAM.framework/Headers/` exposes a case-collision-free, modular umbrella header surface (`OpenFOAM.h`, `foamVersion.h`, `foamPrimitives.h`, `foamBridge.h`) using framework-style `#include <OpenFOAM/...>` directives, validated by `scripts/spm/validate_mergeable_xcframework.py`.

### 2. `wmake` to CMake + Xcode Mergeable Library Pipeline

Upstream `OpenFOAM-14` uses custom `wmake` (`Make/files` and `Make/options`) rather than CMake. `scripts/spm/build_apple_xcframework.py` harvests the upstream source list directly from:

- `src/OSspecific/POSIX/Make/files`
- `src/Pstream/dummy/Make/files`
- `src/OpenFOAM/Make/files` (including generating `global/global.C` from `global/global.Cver`)

It applies idempotent Darwin/Apple SDK compatibility shims:

- Removes `src/OpenFOAM/db/IOstreams/gzstream/version` so it never shadows the C++ standard library `<version>` header on case-insensitive include paths.
- Injects `apple_openfoam_compat.h` (`__BYTE_ORDER`, `<sys/time.h>`, `<sys/stat.h>` `st_atim`/`st_mtim`/`st_ctim` aliases, and float/long-double Bessel function wrappers forwarding to Darwin's `double` Bessel functions).
- Provides Darwin `<sys/sysmacros.h>` and `<link.h>` (`dl_iterate_phdr` backed by `<mach-o/dyld.h>`).
- Disambiguates Darwin `unsigned long` (`size_t` / `uintptr_t`), `long`, and `uint16_t` (`mode_t`) stream/trait overloads in `uint.H` and `int.H`, and guards `system()` / `popen()` under `TARGET_OS_IPHONE`.

It then configures a unified `OpenFOAM` shared library via `cmake -G Xcode` with `toolchains/ios.toolchain.cmake` and archives each slice via `xcodebuild archive` with `MERGEABLE_LIBRARY=YES`.

## Release Model

- Scheduled and default manual runs publish prereleases using `X.Y.Z-alpha.N` tags (`14.0.<YYYYMMDD>-alpha.<N>`).
- Manual runs choose `alpha` or `stable` for an explicit upstream `YYYYMMDD` tag.
- Alpha runs do not update the default branch; they are tag-only releases whose tags point directly at immutable generated metadata commits (`Package.swift` + `scripts/spm/current_release.json`) and do not publish `release/<package_tag>` branches.
- Stable runs publish `14.0.<YYYYMMDD>` and only update the default branch when `publish_to_default_branch=true` is explicitly enabled.

## Repo-Local Contracts

- `scripts/spm/platforms.json`: single source of truth for Apple platform deployment targets.
- `scripts/spm/source_acquisition.json` and `scripts/spm/source_acquisition.py`: repo-local source acquisition contract (`refs/upstream-tags/*`).
- `scripts/spm/current_release.json`: machine-readable release metadata for the committed `Package.swift`.
- `scripts/spm/preflight_apple_platforms.py`: fail-fast Apple SDK preflight check.
- `scripts/spm/build_apple_xcframework.py`: CMake + Xcode mergeable `.xcframework` builder.
- `scripts/spm/validate_mergeable_xcframework.py`: slice, `MergeableMetadata`, `vtool`/`otool` platform identity, versioned macOS layout, and framework header verifier.
- `scripts/spm/validate_package_contract.py`: end-to-end rendered manifest and local SwiftPM consumer verifier (`swift build -c debug`, `swift build -c release`, and `xcodebuild ... MERGED_BINARY_TYPE=automatic build`).
- `scripts/spm/verify_sop_conformance.py`: repository governance and SOP conformance gate.

## Runtime contract record

- `OpenFOAM`:
  - model: `system-only`
  - supplier: `apple-sdk`
  - linked system libraries: `/usr/lib/libz.1.dylib`, `/usr/lib/libc++.1.dylib`
  - consumer requirement: Release app/executable builds consuming mergeable `OpenFOAM.framework` slices use `MERGED_BINARY_TYPE=automatic`.

## Compiler Cache (`ccache`) Policy

Both `_publish-upstream-release-core.yml` and `validate-apple-release-pipeline.yml` restore `.ccache` with `actions/cache/restore@v5`, verify that `CCACHE_DIR` contains non-config cache entries after `build_apple_xcframework.py` succeeds, and only run `actions/cache/save@v5` when `cache-hit != 'true'`.
