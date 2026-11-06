import CryptoKit
import XCTest
@testable import NpiaSources

/// The page script and its resources are shared with the Android app and must stay the same bytes.
final class HardResourcesTests: XCTestCase {
    /// SHA-256 of each file as committed on the Android side (`android/sources/.../hard/`).
    static let androidHashes: [String: String] = [
        "hard-sources.js": "9c61ee6942c3328e84c38d8a5a642d2f5734dec49763fef836cfea829be908e5",
        "qdn/NotoSansCJKsc-Regular-hanzi.otf": "81805f27acfad19ca7509a17e0b8d2fdf0bb944558612de120047b194f195eaa",
        "qdn/OFL.txt": "acc077d31316a689a0b0512998c47331278e4ae61d134e1edeae5a7a95916961",
        "qdn/BROTLI-LICENSE.txt": "3d180008e36922a4e8daec11c34c7af264fed5962d07924aea928c38e8663c94",
        "qdn/brotli_dictionary.bin": "20e42eb1b511c21806d4d227d07e5dd06877d8ce7b3a817f378f313653f35c70",
        "qdn/qdn_outline_table.json": "120c6332630516c2916260c5014e963adf34ca0fd4170020b2a904e5ff069b2c",
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
