import XCTest
@testable import NpiaUpdate

final class ManifestTests: XCTestCase {
    private let sha = String(repeating: "ab", count: 32)

    private func json(_ object: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: object)
    }

    private func entry(_ overrides: [String: Any] = [:]) -> [String: Any] {
        var value: [String: Any] = [
            "name": "NovelDB-unsigned.ipa", "variant": "universal", "arch": "universal", "kind": "ipa",
            "build": 1_793_193_554, "label": "hotfix3", "commit": "abc", "size": 12_974_254, "sha256": sha,
        ]
        for (key, item) in overrides { value[key] = item }
        return value
    }

    func testParsesAManifestAndIgnoresUnknownFields() throws {
        let data = json([
            "schema": 1, "platform": "ios", "tag": "v98", "release_url": "https://github.com/x/y/releases/tag/v98",
            "assets": [entry(["future": [1, 2], "another": "x"])], "new_top_level": ["a": 1],
        ])
        let manifest = try XCTUnwrap(AppUpdateManifest(data: data))
        XCTAssertEqual(manifest.platform, "ios")
        XCTAssertEqual(manifest.tag, "v98")
        XCTAssertEqual(manifest.releaseURL, "https://github.com/x/y/releases/tag/v98")
        let asset = try XCTUnwrap(manifest.assets.first)
        XCTAssertEqual(manifest.assets.count, 1)
        XCTAssertEqual(asset.name, "NovelDB-unsigned.ipa")
        XCTAssertEqual(asset.kind, "ipa")
        XCTAssertEqual(asset.variant, "universal")
        XCTAssertEqual(asset.arch, "universal")
        XCTAssertEqual(asset.build, 1_793_193_554)
        XCTAssertEqual(asset.label, "hotfix3")
        XCTAssertEqual(asset.commit, "abc")
        XCTAssertEqual(asset.size, 12_974_254)
        XCTAssertEqual(asset.sha256, sha)
    }

    func testAWrongSchemaIsNoUpdateInformation() {
        for schema in [0, 2, 99] {
            XCTAssertNil(AppUpdateManifest(data: json(["schema": schema, "platform": "ios", "tag": "v98", "assets": [entry()]])))
        }
        XCTAssertNil(AppUpdateManifest(data: json(["platform": "ios", "tag": "v98", "assets": [entry()]])), "no schema")
        XCTAssertNil(AppUpdateManifest(data: json(["schema": "1", "tag": "v98", "assets": [entry()]])), "schema as text")
        XCTAssertNil(AppUpdateManifest(data: json(["schema": true, "tag": "v98", "assets": [entry()]])), "schema as boolean")
        XCTAssertNil(AppUpdateManifest(data: json(["schema": 1.5, "tag": "v98", "assets": [entry()]])), "schema as fraction")
    }

    func testBrokenDocumentsAreNoUpdateInformation() {
        XCTAssertNil(AppUpdateManifest(data: Data()))
        XCTAssertNil(AppUpdateManifest(data: Data("<html>404</html>".utf8)))
        XCTAssertNil(AppUpdateManifest(data: Data("[1, 2, 3]".utf8)), "an array is not a manifest")
        XCTAssertNil(AppUpdateManifest(data: Data("{\"schema\": 1".utf8)), "cut off")
    }

    func testAManifestWithoutAssetsParsesWithNoEntries() throws {
        let manifest = try XCTUnwrap(AppUpdateManifest(data: json(["schema": 1, "platform": "ios", "tag": "v98"])))
        XCTAssertEqual(manifest.assets, [])
        XCTAssertNil(manifest.asset(kind: "ipa", variant: "universal", arch: "universal"))
    }

    func testEntriesMissingRequiredFieldsAreSkippedAndTheRestKept() throws {
        let bad: [[String: Any]] = [
            entry(["sha256": "abc"]),  // too short
            entry(["sha256": String(repeating: "g", count: 64)]),  // not hex
            entry(["size": 0]),
            entry(["size": "12"]),
            entry(["build": 0]),
            entry(["build": "1793193554"]),
            entry(["name": ""]),
            entry(["kind": 5]),
        ]
        let data = json(["schema": 1, "platform": "ios", "tag": "v98", "assets": bad + [entry(["name": "good.ipa"])]])
        let manifest = try XCTUnwrap(AppUpdateManifest(data: data))
        XCTAssertEqual(manifest.assets.map(\.name), ["good.ipa"])
    }

    func testAnUppercaseDigestIsAccepted() throws {
        let data = json(["schema": 1, "tag": "v98", "assets": [entry(["sha256": sha.uppercased()])]])
        XCTAssertEqual(try XCTUnwrap(AppUpdateManifest(data: data)).assets.first?.sha256, sha)
    }

    func testPicksTheEntryForThisAppAmongOtherFiles() throws {
        let other = entry(["name": "NovelDB.apk", "kind": "apk", "build": 9_000_000_000])
        let lite = entry(["name": "lite.ipa", "variant": "lite", "build": 9_000_000_001])
        let arm = entry(["name": "arm.ipa", "arch": "arm64", "build": 9_000_000_002])
        let mine = entry(["name": "mine.ipa", "build": 100])
        let data = json(["schema": 1, "platform": "ios", "tag": "v98", "assets": [other, lite, arm, mine]])
        let manifest = try XCTUnwrap(AppUpdateManifest(data: data))
        XCTAssertEqual(manifest.assets.count, 4)
        XCTAssertEqual(manifest.asset(kind: "ipa", variant: "universal", arch: "universal")?.name, "mine.ipa")
        XCTAssertEqual(manifest.asset(kind: "apk", variant: "universal", arch: "universal")?.name, "NovelDB.apk")
        XCTAssertNil(manifest.asset(kind: "dmg", variant: "universal", arch: "universal"))
    }

    func testTheNewestEntryWinsWhenAFileIsListedTwice() throws {
        let older = entry(["name": "old.ipa", "build": 100])
        let newer = entry(["name": "new.ipa", "build": 200])
        let data = json(["schema": 1, "platform": "ios", "tag": "v98", "assets": [newer, older]])
        let manifest = try XCTUnwrap(AppUpdateManifest(data: data))
        XCTAssertEqual(manifest.asset(kind: "ipa", variant: "universal", arch: "universal")?.name, "new.ipa")
    }

    // MARK: - Addresses

    func testReleaseAddressesFollowTheContract() {
        let endpoints = AppUpdateEndpoints()
        XCTAssertEqual(
            endpoints.manifestURL(platform: "ios").absoluteString,
            "https://github.com/Shirochi-stack/NovelDB/releases/latest/download/update-ios.json"
        )
        XCTAssertEqual(
            endpoints.assetURL(tag: "v98", name: "NovelDB-unsigned.ipa")?.absoluteString,
            "https://github.com/Shirochi-stack/NovelDB/releases/download/v98/NovelDB-unsigned.ipa"
        )
        XCTAssertEqual(
            endpoints.releasePageURL(tag: "v98")?.absoluteString,
            "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98"
        )
    }

    func testAnOverriddenBaseMovesEveryAddress() {
        let endpoints = AppUpdateEndpoints(base: URL(string: "http://127.0.0.1:8765/releases/")!)
        XCTAssertEqual(endpoints.manifestURL(platform: "ios").absoluteString, "http://127.0.0.1:8765/releases/latest/download/update-ios.json")
        XCTAssertEqual(endpoints.assetURL(tag: "v2", name: "a.ipa")?.absoluteString, "http://127.0.0.1:8765/releases/download/v2/a.ipa")
    }

    func testAssetAddressesNeverLeaveTheReleaseFolder() {
        let endpoints = AppUpdateEndpoints()
        for name in ["", "../x.ipa", "a/b.ipa", "a\\b.ipa", ".hidden", "x..ipa", "a?b.ipa", "a#b.ipa", "a%2fb.ipa", "a\nb.ipa"] {
            XCTAssertNil(endpoints.assetURL(tag: "v98", name: name), "name \(name.debugDescription)")
            XCTAssertNil(endpoints.assetURL(tag: name, name: "a.ipa"), "tag \(name.debugDescription)")
        }
        XCTAssertNotNil(endpoints.assetURL(tag: "v98", name: "NovelDB unsigned.ipa"), "spaces are encoded, not refused")
    }
}
