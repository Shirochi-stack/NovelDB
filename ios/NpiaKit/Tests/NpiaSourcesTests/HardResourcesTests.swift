import CryptoKit
import XCTest
@testable import NpiaSources

/// The page script and its resources are shared with the Android app and must stay the same bytes.
final class HardResourcesTests: XCTestCase {
    /// SHA-256 of each file as committed on the Android side (`android/sources/.../hard/`).
    static let androidHashes: [String: String] = [
        "hard-sources.js": "66be5b7bab7d99ffcd81944b893ea3fe33e1bfae90413e0198065f8fbbf31f9d",
        "qdn/NotoSansCJKsc-Regular-hanzi.otf": "81805f27acfad19ca7509a17e0b8d2fdf0bb944558612de120047b194f195eaa",
        "qdn/OFL.txt": "acc077d31316a689a0b0512998c47331278e4ae61d134e1edeae5a7a95916961",
        "qdn/qdn_verified_maps.json": "9e6ff0c465fa50b511eede08bf07272fa39d7a32d49ff836af3e315892b90793",
        "qdn/zh_char_frequency.txt": "555c0d7862cf307c7aeacf3091e081dc8e80ca6e60baf79b93d7ff26af1ff7e2",
    ]

    static func hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    func testTheSharedFilesAreAndroidsBytes() throws {
        for (name, hash) in Self.androidHashes {
            XCTAssertEqual(Self.hex(try HardScripts.resource(name)), hash, name)
        }
    }

    func testTheWebViewsRunTheSharedFileAsItIs() throws {
        let shared = String(decoding: try HardScripts.resource("hard-sources.js"), as: UTF8.self)
        XCTAssertEqual(HardScripts.source, shared)
        // The coverage probe WebKit needs is in the shared file itself.
        XCTAssertTrue(shared.contains("'sans-serif', 'serif', 'system-ui', 'monospace'"))
        XCTAssertFalse(shared.contains("covered = !sameInk("))
    }

    func testTheScriptIsLoadedAsText() {
        XCTAssertTrue(HardScripts.source.contains("window.__npiaHard") || HardScripts.source.contains("__npiaHard"))
        XCTAssertGreaterThan(HardScripts.source.utf8.count, 100_000)
    }

    func testAMissingResourceIsAnError() {
        XCTAssertThrowsError(try HardScripts.resource("qdn/nothing.bin"))
    }

    func testStringLiteralsSurviveTheScriptBoundary() {
        XCTAssertEqual(HardScripts.literal("a\"b\\c\nd"), "\"a\\\"b\\\\c\\nd\"")
        let separators = HardScripts.literal("x\u{2028}y\u{2029}z")
        XCTAssertFalse(separators.contains("\u{2028}"))
        XCTAssertFalse(separators.contains("\u{2029}"))
        XCTAssertEqual(separators, "\"x" + "\u{5c}" + "u2028y" + "\u{5c}" + "u2029z\"")
        XCTAssertEqual(HardScripts.literal("章节"), "\"章节\"")
    }
}
