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

- `config/source_acquisition.json`
- `scripts/spm/source_acquisition.py`

The helper fetches upstream tags from `https://github.com/OpenFOAM/OpenFOAM-14.git` into `refs/upstream-tags/*` and exports the selected tag into a temporary worktree for building.

## Release Workflows

- `.github/workflows/validate-apple-release-pipeline.yml`
- `.github/workflows/publish-latest-upstream-alpha.yml`
- `.github/workflows/publish-upstream-release-manually.yml`
- `.github/workflows/_publish-upstream-release-core.yml`

See [docs/how-to-build/swiftpm-binary-package.md](docs/how-to-build/swiftpm-binary-package.md) for the full operator contract.

## License

This repository and its distributed binary frameworks are licensed under the **GNU General Public License v3.0** (see [COPYING](COPYING) or [LICENSE](LICENSE)), identical to and in compliance with upstream [`OpenFOAM/OpenFOAM-14`](https://github.com/OpenFOAM/OpenFOAM-14).

All SPM wrapper code, Darwin compatibility patches, and the C/C++ bridge surface (`foam_spm_bridge.C`) are distributed under the terms of GNU GPLv3 as derivative/associated works.

> [!WARNING]
> ### Important Legal Notice: Apple App Store & GPLv3 Distribution
> OpenFOAM is licensed under the **GNU General Public License v3.0 (GPLv3)**.
> - **Copyleft Requirement**: Any application or library that links against `OpenFOAM.framework` must make its complete corresponding source code available under GPLv3.
> - **App Store DRM Restriction Conflict**: The terms of GNU GPLv3 (Section 10) forbid imposing further restrictions on the exercise of granted rights. The digital rights management (DRM) and distribution terms enforced by the Apple App Store conflict with GPLv3.
> - **Usage Recommendation**: We strongly advise developers **NOT** to distribute closed-source applications linking OpenFOAM via the public Apple App Store. This package is ideal for academic research, internal computational pipelines, self-hosted distribution, Ad-Hoc / Developer ID signed macOS software, or open-source GPLv3-compliant iOS apps.

### Corresponding Source Distribution
In accordance with GPLv3 Section 6, each automated release on this repository publishes the exact corresponding source tarball (`OpenFOAM-14-<tag>-source.tar.gz`) containing all Darwin patches and bridge translation units alongside the precompiled binary `.xcframework.zip` on [GitHub Releases](https://github.com/SPMForge/OpenFOAM-14/releases).

## Release Input Identity

New releases record immutable upstream and wrapper input identity in
`config/release-inputs.json`. Scheduled and manual alpha use the same reuse/evaluate
policy and repository publication lock. See the
[repeat-publication SOP](docs/how-to-build/swiftpm-binary-package.md#immutable-source-and-repeat-publication-sop)
for source pinning, legacy tags, strict repair checks, and verification boundaries.

## CI stages and package consumption

Actions uses the same stage vocabulary across the wrapper repositories:
`Verify → Resolve → Build (product) → Validate Consumers → Publish → Summary`.
Validation runs omit publication. CPU/Vulkan products are separate branches;
platform slices remain grouped within each build job. Consumer jobs verify archive
transfer digests and test the final extracted XCFrameworks in Debug and Release.
A failed consumer blocks publication. Completed matching releases use the `Reuse`
path; metadata-only drift uses `Repair Metadata` without rebuilding.

Consume an accepted **package version tag**, rather than the default branch.
Alpha releases do not promote default-branch package metadata; a successful CI run
alone does not establish that the default-branch binary URLs are downloadable.
The Summary job records stage results and links to the run artifacts. Each build
artifact inventory records upstream commit, toolchain and archive SHA-256 values.

### Canonical release policy

This repository's scripts and tests own its executable package contract. The shared
policy references are [input identity and Alpha name allocation](https://github.com/RbBtSn0w/apple-skills/blob/main/app-develop/skills/apple-spm-binary-distribution/references/release-input-identity.md),
[publication and repair decisions](https://github.com/RbBtSn0w/apple-skills/blob/main/app-develop/skills/apple-spm-binary-distribution/references/release-repair-and-metadata-normalization.md),
and the [Actions stage contract](https://github.com/RbBtSn0w/apple-skills/blob/main/app-develop/skills/apple-spm-binary-distribution/references/ci-topologies.md).
Use accepted version tags; Alpha publication does not promote the default-branch manifest.
