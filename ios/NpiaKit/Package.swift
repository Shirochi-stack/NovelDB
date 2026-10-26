// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "NpiaKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "NpiaCore", targets: ["NpiaCore"]),
        .library(name: "NpiaNet", targets: ["NpiaNet"]),
        .library(name: "NpiaWebEngine", targets: ["NpiaWebEngine"]),
        .library(name: "NpiaImages", targets: ["NpiaImages"]),
        .library(name: "NpiaOutput", targets: ["NpiaOutput"]),
        .library(name: "NpiaUpload", targets: ["NpiaUpload"]),
        .library(name: "NpiaSources", targets: ["NpiaSources"]),
        .library(name: "NpiaPipeline", targets: ["NpiaPipeline"]),
    ],
    dependencies: [
        .package(url: "https://github.com/weichsel/ZIPFoundation.git", exact: "0.9.19"),
    ],
    targets: [
        // Models, settings, secrets, and the protocols every other module implements.
        .target(name: "NpiaCore"),
        // URLSession client with retries, pacing, and the shared cookie jar.
        .target(name: "NpiaNet", dependencies: ["NpiaCore"]),
        // Offscreen WKWebView pages that replace the desktop Playwright/CDP driver.
        .target(name: "NpiaWebEngine", dependencies: ["NpiaCore"]),
        // Image recompression (ImageIO; libwebp is added by the images workstream).
        .target(name: "NpiaImages", dependencies: ["NpiaCore"]),
        // EPUB, TXT, and PDF writers.
        .target(name: "NpiaOutput", dependencies: ["NpiaCore", "ZIPFoundation"]),
        // Share-link hosts (temp.sh, gofile, Google Drive, ...).
        .target(name: "NpiaUpload", dependencies: ["NpiaCore", "ZIPFoundation"]),
        // One adapter per source codename.
        .target(name: "NpiaSources", dependencies: ["NpiaCore"]),
        // Download queue that wires sources, images, writers, and uploads together.
        .target(
            name: "NpiaPipeline",
            dependencies: ["NpiaCore", "NpiaNet", "NpiaWebEngine", "NpiaImages", "NpiaOutput", "NpiaUpload", "NpiaSources"]
        ),
        .testTarget(name: "NpiaCoreTests", dependencies: ["NpiaCore"]),
        .testTarget(name: "NpiaSourcesTests", dependencies: ["NpiaSources", "NpiaCore"]),
        .testTarget(name: "NpiaOutputTests", dependencies: ["NpiaOutput", "NpiaCore", "ZIPFoundation"]),
        .testTarget(name: "NpiaUploadTests", dependencies: ["NpiaUpload", "NpiaCore", "ZIPFoundation"]),
    ]
)
