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
        .package(url: "https://github.com/scinfu/SwiftSoup.git", from: "2.7.0"),
        .package(url: "https://github.com/SDWebImage/libwebp-Xcode.git", exact: "1.5.0"),
    ],
    targets: [
        // Models, settings, secrets, and the protocols every other module implements.
        .target(name: "NpiaCore"),
        // URLSession client with retries, pacing, and the shared cookie jar.
        .target(name: "NpiaNet", dependencies: ["NpiaCore"]),
        // Offscreen WKWebView pages that replace the desktop Playwright/CDP driver.
        .target(name: "NpiaWebEngine", dependencies: ["NpiaCore"]),
        // Image recompression: ImageIO decode, JPEG/PNG/GIF encode, libwebp for WebP.
        .target(name: "NpiaImages", dependencies: ["NpiaCore", .product(name: "libwebp", package: "libwebp-Xcode")]),
        // EPUB, TXT, and PDF writers.
        .target(name: "NpiaOutput", dependencies: ["NpiaCore", "ZIPFoundation"]),
        // Share-link hosts (temp.sh, gofile, Google Drive, ...).
        .target(name: "NpiaUpload", dependencies: ["NpiaCore", "ZIPFoundation"]),
        // One adapter per source codename.
        .target(name: "NpiaSources", dependencies: ["NpiaCore", "SwiftSoup"], resources: [.copy("Reader/Resources")]),
        // Download queue that wires sources, images, writers, and uploads together.
        .target(
            name: "NpiaPipeline",
            dependencies: ["NpiaCore", "NpiaNet", "NpiaWebEngine", "NpiaImages", "NpiaOutput", "NpiaUpload", "NpiaSources"]
        ),
        .testTarget(name: "NpiaCoreTests", dependencies: ["NpiaCore"]),
        .testTarget(name: "NpiaSourcesTests", dependencies: ["NpiaSources", "NpiaCore", "NpiaWebEngine"]),
        .testTarget(name: "NpiaOutputTests", dependencies: ["NpiaOutput", "NpiaCore", "ZIPFoundation"]),
        .testTarget(name: "NpiaUploadTests", dependencies: ["NpiaUpload", "NpiaCore", "ZIPFoundation"]),
        .testTarget(name: "NpiaWebEngineTests", dependencies: ["NpiaWebEngine", "NpiaCore"]),
        .testTarget(name: "NpiaImagesTests", dependencies: ["NpiaImages", "NpiaCore"]),
        .testTarget(name: "NpiaPipelineTests", dependencies: ["NpiaPipeline", "NpiaImages", "NpiaCore"]),
    ]
)
