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
            url: "https://github.com/SPMForge/OpenFOAM-14/releases/download/14.0.20261008-alpha.1/OpenFOAM-14-20261008-apple.xcframework.zip",
            checksum: "0000000000000000000000000000000000000000000000000000000000000000"
        )
    ]
)
