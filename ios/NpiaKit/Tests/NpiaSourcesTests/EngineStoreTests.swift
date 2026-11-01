import XCTest
import NpiaCore
@testable import NpiaSources

final class EngineStoreTests: XCTestCase {
    // MARK: - Shipped bundles

    func testShippedBundlesAreUsedUntilUpdatesArrive() throws {
        let fixture = try EngineFixture()
        let rules = try XCTUnwrap(fixture.store.current(.novelDownloader))
        let parsers = try XCTUnwrap(fixture.store.current(.webtoepub))
        XCTAssertEqual(rules.folder, fixture.shippedRules)
        XCTAssertFalse(rules.updated)
        XCTAssertEqual(rules.manifest.version, "5.2.1285", "read from the userscript header")
        XCTAssertEqual(rules.label, "5.2.1285, built in")
        XCTAssertEqual(parsers.folder, fixture.shippedParsers)
        XCTAssertEqual(parsers.label, "2026-10-27, aaaaaaa, built in")
        XCTAssertEqual(fixture.store.candidates(for: .webtoepub).count, 1)
    }

    func testAShippedCopyWithTheWrongAPIOrNoFilesIsMissing() throws {
        let fixture = try EngineFixture()
        try EngineFixture.parsersManifest.replacingOccurrences(of: "\"api\": 1", with: "\"api\": 2")
            .write(to: fixture.shippedParsers.appendingPathComponent("manifest.json"), atomically: true, encoding: .utf8)
        XCTAssertNil(fixture.store.shipped(.webtoepub))
        let empty = EngineStore(shipped: { _ in nil }, updatesRoot: fixture.root.appendingPathComponent("none"))
        XCTAssertNil(empty.current(.novelDownloader))
        XCTAssertEqual(empty.candidates(for: .novelDownloader).count, 0)
    }

    func testBundlesShippedWithTheAppAreValid() throws {
        let store = EngineStore.shared
        let parsers = try XCTUnwrap(store.shipped(.webtoepub), "the WebToEpub bundle is part of the Reader resources")
        XCTAssertEqual(parsers.manifest.api, 1)
        let bytes = try Data(contentsOf: parsers.path)
        XCTAssertEqual(EngineStore.sha256Hex(bytes), parsers.manifest.sha256, "the bundle is byte for byte what the manifest describes")
        XCTAssertEqual(bytes.count, parsers.manifest.size)
        XCTAssertFalse(bytes.contains(13), "line endings stay LF")
        XCTAssertTrue(FileManager.default.fileExists(atPath: parsers.folder.appendingPathComponent("LICENSE.md").path))

        let rules = try XCTUnwrap(store.shipped(.novelDownloader))
        XCTAssertFalse(EngineManifest.numbers(in: rules.manifest.version).isEmpty, "rules-lib.js has an @version line")
    }

    // MARK: - Site lists

    func testSitesEachEngineLists() throws {
        let fixture = try EngineFixture()
        let rules = try XCTUnwrap(fixture.store.current(.novelDownloader))
        let parsers = try XCTUnwrap(fixture.store.current(.webtoepub))

        func lists(_ bundle: EngineBundle, _ text: String) -> Bool {
            EngineSites.lists(bundle, URL(string: text)!)
        }
        XCTAssertTrue(lists(rules, "https://www.site.test/book/1"))
        XCTAssertTrue(lists(rules, "https://WWW.Site.test/book/1"), "case does not matter")
        XCTAssertTrue(lists(rules, "https://api.example.org/x"))
        XCTAssertTrue(lists(rules, "https://esc.sample.test/x"), "escapes in the script's host names are decoded")
        XCTAssertTrue(lists(rules, "https://book42.com/novel/1"), "host patterns count")
        XCTAssertFalse(lists(rules, "https://site.test/book/1"), "novel-downloader matches location.host exactly")
        XCTAssertFalse(lists(rules, "https://www.site.test:8080/book/1"), "the port is part of the host")
        XCTAssertFalse(lists(rules, "https://other.test/"))

        XCTAssertTrue(lists(parsers, "https://novel.test/b"))
        XCTAssertTrue(lists(parsers, "https://www.novel.test/b"), "www. is ignored on both sides")
        XCTAssertTrue(lists(parsers, "https://other.test/b"), "registered hosts are compared in lower case")
        XCTAssertFalse(lists(parsers, "https://unknown.test/b"))
    }

    func testSiteListsOfTheShippedBundles() throws {
        let store = EngineStore.shared
        func rules(_ text: String) -> Bool { EngineSites.lists(.novelDownloader, URL(string: text)!, store: store) }
        func parsers(_ text: String) -> Bool { EngineSites.lists(.webtoepub, URL(string: text)!, store: store) }
        XCTAssertTrue(rules("https://ncode.syosetu.com/n1234ab/"))
        XCTAssertTrue(rules("https://www.sudugu.com/1001/"))
        XCTAssertTrue(rules("https://booktoki123.com/novel/1"))
        XCTAssertFalse(rules("https://example.com/book/1"))
        XCTAssertTrue(parsers("https://www.fictionpress.com/s/1"))
        XCTAssertTrue(parsers("https://fictionpress.com/s/1"))
        XCTAssertFalse(parsers("https://unknown.example/book"))

        let index = try XCTUnwrap(store.shipped(.webtoepub)).path
        let hosts = EngineSites.parseWebToEpub(try Data(contentsOf: index)).hosts
        XCTAssertGreaterThan(hosts.count, 500)
        XCTAssertTrue(hosts.allSatisfy { !$0.hasPrefix("www.") })
    }

    func testParsingTolerantOfOddScripts() {
        XCTAssertTrue(EngineSites.parseWebToEpub(Data()).hosts.isEmpty)
        XCTAssertTrue(EngineSites.parseNovelDownloader(Data("no rules here".utf8)).hosts.isEmpty)
        let noSwitch = EngineSites.parseNovelDownloader(Data("throw new Error(\"Not Found Rule!\")".utf8))
        XCTAssertTrue(noSwitch.hosts.isEmpty)
        XCTAssertEqual(EngineSites.unescape("a.b"), "a.b")
        XCTAssertEqual(EngineSites.unescape(EngineFixture.backslash + "u0041"), "A")
    }

    // MARK: - Ordering

    func testKeysSortByVersionThenDateThenBuildTime() {
        func key(_ version: String, _ date: String = "", _ built: String = "") -> EngineKey {
            EngineManifest(raw: ["version": version, "date": date, "built": built]).key
        }
        XCTAssertLessThan(key("5.2.1285"), key("5.2.1289"))
        XCTAssertLessThan(key("1.0.14.61"), key("1.0.14.62"))
        XCTAssertLessThan(key("1.9"), key("1.10"), "versions compare as numbers")
        XCTAssertLessThan(key("1.0"), key("1.0.0"), "a longer version is newer when the start is the same")
        XCTAssertLessThan(key("1.0", "2026-10-27"), key("1.0", "2026-11-01"))
        XCTAssertLessThan(key("1.0", "2026-10-27", "a"), key("1.0", "2026-10-27", "b"))
        XCTAssertLessThan(key("5.2.1289", "2026-10-16"), key("5.2.1290", "2026-01-01"), "the version decides, not the date")
        XCTAssertEqual(key("1.0.14.61"), key("1.0.14.61"))
        XCTAssertEqual(EngineManifest.numbers(in: "v1.0.14-61rc2"), [1, 0, 14, 61, 2])
    }

    func testManifestFieldsAreReadLeniently() {
        let manifest = EngineManifest(data: Data(#"{"api": 1, "size": 12, "commit": "abc", "shim": "s", "sha256": "ABCD", "extra": [1]}"#.utf8))
        XCTAssertEqual(manifest?.api, 1)
        XCTAssertEqual(manifest?.size, 12)
        XCTAssertEqual(manifest?.sha256, "abcd")
        XCTAssertEqual(manifest?.releaseKey, "abc-s")
        XCTAssertNil(manifest?.engine)
        XCTAssertEqual(manifest?.file, "")
        XCTAssertNil(EngineManifest(data: Data("[1]".utf8)))
        XCTAssertNil(EngineManifest(data: Data("nope".utf8)))
    }

    // MARK: - Downloads

    private func install(_ fixture: EngineFixture, _ engine: RuleEngine, body: String, version: String = "9.9.9", commit: String = "c1") throws -> EngineBundle {
        let data = Data(body.utf8)
        let manifest = EngineManifest(raw: [
            "engine": engine.rawValue, "api": 1, "commit": commit, "shim": "s9", "version": version,
            "date": "2026-12-01T00:00:00Z", "built": "2026-12-02T00:00:00Z", "file": "something-else.js",
            "size": data.count, "sha256": EngineStore.sha256Hex(data),
        ])
        return try fixture.store.install(engine, data: data, manifest: manifest)
    }

    func testANewerDownloadIsUsedAndAnOlderOneIsNot() throws {
        let fixture = try EngineFixture()
        let bundle = try install(fixture, .webtoepub, body: "/* newer */")
        XCTAssertTrue(bundle.updated)
        XCTAssertEqual(bundle.manifest.file, "webtoepub-lib.js", "the file is renamed to the engine's own name")
        let current = try XCTUnwrap(fixture.store.current(.webtoepub))
        XCTAssertTrue(current.updated)
        XCTAssertEqual(try current.read(), "/* newer */")
        XCTAssertEqual(fixture.store.candidates(for: .webtoepub).map(\.updated), [true, false], "the shipped copy is the fallback")

        let old = try install(fixture, .novelDownloader, body: "/* older */", version: "5.2.1200")
        XCTAssertTrue(old.updated)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated, "an older download is ignored")
    }

    func testAModifiedDownloadIsIgnored() throws {
        let fixture = try EngineFixture()
        _ = try install(fixture, .novelDownloader, body: "/* good */")
        XCTAssertTrue(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated)
        try "/* changed on disk */".write(to: fixture.updates(.novelDownloader).appendingPathComponent("rules-lib.js"), atomically: true, encoding: .utf8)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.novelDownloader)).updated)
    }

    func testADownloadWithAnotherAPIIsIgnored() throws {
        let fixture = try EngineFixture()
        _ = try install(fixture, .webtoepub, body: "/* x */")
        let manifestURL = fixture.updates(.webtoepub).appendingPathComponent("manifest.json")
        var manifest = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL)) as? [String: Any])
        manifest["api"] = 2
        try JSONSerialization.data(withJSONObject: manifest).write(to: manifestURL)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)
    }

    func testRejectingADownloadSetsItAsideForGood() throws {
        let fixture = try EngineFixture()
        let bundle = try install(fixture, .webtoepub, body: "/* broken */", commit: "badc0de")
        let generation = fixture.store.generation
        fixture.store.reject(bundle, reason: "SyntaxError")
        XCTAssertGreaterThan(fixture.store.generation, generation)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)
        XCTAssertEqual(fixture.store.rejectedKeys(.webtoepub), ["badc0de-s9"])
        XCTAssertTrue(fixture.store.isRejected(.webtoepub, releaseKey: "badc0de-s9"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.updates(.webtoepub).appendingPathComponent("webtoepub-lib.js").path))
        // Rejecting the shipped copy does nothing.
        let shipped = try XCTUnwrap(fixture.store.shipped(.webtoepub))
        fixture.store.reject(shipped)
        XCTAssertEqual(fixture.store.rejectedKeys(.webtoepub), ["badc0de-s9"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: shipped.path.path))
    }

    func testStateRemembersTheLastCheck() throws {
        let fixture = try EngineFixture()
        XCTAssertNil(fixture.store.lastChecked(.webtoepub))
        let when = Date(timeIntervalSince1970: 1_800_000_000)
        fixture.store.markChecked(.webtoepub, at: when)
        XCTAssertEqual(fixture.store.lastChecked(.webtoepub), when)
        XCTAssertNil(fixture.store.lastChecked(.novelDownloader))
        // The check time and the rejected list share one file without erasing each other.
        let bundle = try install(fixture, .webtoepub, body: "/* x */")
        fixture.store.reject(bundle)
        XCTAssertEqual(fixture.store.lastChecked(.webtoepub), when)
        XCTAssertEqual(fixture.store.rejectedKeys(.webtoepub).count, 1)
    }
}
