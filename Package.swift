// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "OpenFOAM-14",
    platforms: [
        .iOS("15.0"),
        .macOS("12.0"),
        .macCatalyst("15.0"),
        .tvOS("15.0"),
        .watchOS("9.0"),
        .visionOS("1.0")
    ],
    products: [
        .library(name: "OpenFOAM", targets: ["OpenFOAM"])
    ],
    targets: [
        .binaryTarget(
            name: "OpenFOAM",
            url: "https://github.com/SPMForge/OpenFOAM-14/releases/download/14.0.20261008-alpha.2/OpenFOAM-14-20261008-apple.xcframework.zip",
            checksum: "3ce4cc862703b8f69924915103b4f6bf0f551eb3037a180095ce320e280eaeb1"
        )
    ]
)
