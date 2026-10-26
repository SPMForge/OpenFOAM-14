# OpenFOAM-14 Apple Swift Package

This repository is an independent Swift Package Manager wrapper repository for upstream [`OpenFOAM/OpenFOAM-14`](https://github.com/OpenFOAM/OpenFOAM-14).

It does not carry the upstream C++ source tree on the default branch. Instead, the repository fetches upstream release tags on demand, exports the selected tag into an ephemeral build worktree, builds Apple-platform mergeable XCFramework artifacts (`libOpenFOAM` + `libOSspecific` + dummy `libPstream` unified into `OpenFOAM.framework`), and publishes the resulting binary package releases here.

## Products

- `OpenFOAM`: core OpenFOAM-14 runtime (`src/OpenFOAM`, `src/OSspecific/POSIX`, `src/Pstream/dummy`, and the case-safe C/C++ SwiftPM bridge surface `<OpenFOAM/OpenFOAM.h>`)

## Supported Apple Platforms

- `iOS` (`arm64`) and `iOS Simulator` (`arm64`, `x86_64`), minimum deployment target `15.0`
- `macOS` (`arm64`, `x86_64`), minimum deployment target `12.0`
- `Mac Catalyst` (`arm64`, `x86_64`), minimum deployment target `15.0`
- `tvOS` (`arm64`) and `tvOS Simulator` (`arm64`, `x86_64`), minimum deployment target `15.0`
- `watchOS` (`arm64`, `arm64_32`) and `watchOS Simulator` (`arm64`, `x86_64`), minimum deployment target `9.0`
- `visionOS` (`arm64`) and `visionOS Simulator` (`arm64`, `x86_64`), minimum deployment target `1.0`

## Consumer Usage

### Alpha prerelease

Alpha package tags point directly at immutable generated metadata commits and attach their binary archives to matching GitHub Pre-releases:

```swift
dependencies: [
    .package(url: "https://github.com/SPMForge/OpenFOAM-14.git", exact: "14.0.20261008-alpha.1")
]
```

### Stable release

Stable promotions may update the default branch when `publish_to_default_branch=true` is explicitly selected, and publish `14.0.<YYYYMMDD>` tags:

```swift
dependencies: [
    .package(url: "https://github.com/SPMForge/OpenFOAM-14.git", exact: "14.0.20261008")
]
```

Then depend on the product you need:

```swift
targets: [
    .target(
        name: "YourTarget",
        dependencies: [
            .product(name: "OpenFOAM", package: "OpenFOAM-14")
        ]
    )
]
```

### C++ & Swift Interop Header Surface

Upstream `OpenFOAM-14` contains 23 path pairs and over 40 header basenames that differ only by letter case (`Vector.H` vs `vector.H`, `Tensor.H` vs `tensor.H`, `PointHit.H` vs `pointHit.H`, `LduMatrix.H` vs `lduMatrix.H`), which collide when extracted onto macOS's default case-insensitive APFS filesystem.

To guarantee lossless extraction on default macOS filesystems and full Clang module compatibility:

1. **Build-time source export**: `scripts/spm/source_acquisition.py export-source` automatically mounts a **Case-Sensitive APFS** sparse volume on macOS when the host filesystem is case-insensitive, compiling all 524 upstream C++ translation units (`src/OSspecific/POSIX`, `src/Pstream/dummy`, `src/OpenFOAM`) into `OpenFOAM.framework/OpenFOAM`.
2. **Consumer-time framework headers**: `OpenFOAM.framework/Headers/` exposes a case-safe, modular C/C++ umbrella surface (`<OpenFOAM/OpenFOAM.h>`, `<OpenFOAM/foamVersion.h>`, `<OpenFOAM/foamPrimitives.h>`, `<OpenFOAM/foamBridge.h>`) backed by the compiled `Foam::*` symbols in `OpenFOAM.framework`.

```cpp
#include <OpenFOAM/OpenFOAM.h>

double nu = 0.0;
bool ok = Foam::spm::lookupScalar("nu 0.015;\n", "nu", nu);
```

### Mergeable binary consumer requirement

The packaged `OpenFOAM.framework` slices are built with `MERGEABLE_LIBRARY=YES`. App or executable targets that consume this package in Release builds should set `MERGED_BINARY_TYPE=automatic` (or `manual`) so Xcode merges or embeds the mergeable framework slice into the final product bundle.

## Upstream Acquisition Contract

Upstream source acquisition is owned by repo-local files:

- `scripts/spm/source_acquisition.json`
- `scripts/spm/source_acquisition.py`

The helper fetches upstream tags from `https://github.com/OpenFOAM/OpenFOAM-14.git` into `refs/upstream-tags/*` and exports the selected tag into a temporary worktree for building.

## Release Workflows

- `.github/workflows/validate-apple-release-pipeline.yml`
- `.github/workflows/publish-latest-upstream-alpha.yml`
- `.github/workflows/publish-upstream-release-manually.yml`
- `.github/workflows/_publish-upstream-release-core.yml`

See [docs/how-to-build/swiftpm-binary-package.md](docs/how-to-build/swiftpm-binary-package.md) for the full operator contract.
