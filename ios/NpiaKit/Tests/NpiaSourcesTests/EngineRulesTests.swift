import XCTest
import NpiaCore
@testable import NpiaSources

/// The novel-downloader rules from a downloaded update: used by the script runtime and the web
/// view alike, and set aside when they do not initialise.
final class EngineRulesTests: XCTestCase {
    private var original: EngineStore!

    override func setUp() {
        super.setUp()
        original = EngineStore.shared
    }

    override func tearDown() {
        EngineStore.shared = original
        ReaderRules.shared.reset()
        super.tearDown()
    }

    /// A store whose shipped rules are the app's own, with nothing downloaded.
    private func realRulesStore(_ fixture: EngineFixture) -> EngineStore {
        let real = original!
        return EngineStore(shipped: { engine in real.shipped(engine)?.folder }, updatesRoot: fixture.root.appendingPathComponent("home/engines"))
    }

    /// Downloads `script` as a rules-lib.js newer than any shipped one.
    @discardableResult
    private func installUpdate(_ store: EngineStore, script: String, commit: String) throws -> EngineBundle {
        let data = Data(script.utf8)
        let manifest = EngineManifest(raw: [
            "engine": "novel_downloader", "api": 1, "commit": commit, "shim": "s", "version": "99.0.0",
            "date": "2030-01-01T00:00:00Z", "built": "2030-01-01T00:00:00Z", "file": "rules-lib.js",
            "size": data.count, "sha256": EngineStore.sha256Hex(data),
        ])
        let bundle = try store.install(.novelDownloader, data: data, manifest: manifest)
        XCTAssertTrue(try XCTUnwrap(store.current(.novelDownloader)).updated)
        return bundle
    }

    private func storeWithUpdate(_ fixture: EngineFixture, script: String, commit: String) throws -> EngineStore {
        let store = realRulesStore(fixture)
        try installUpdate(store, script: script, commit: commit)
        return store
    }

    func testADownloadedRulesUpdateIsUsedByTheScriptRuntime() throws {
        let fixture = try EngineFixture()
        // Stands in for rules-lib.js: ready, and marked so the test can tell it from the shipped copy.
        EngineStore.shared = try storeWithUpdate(fixture, script: "window.__ND_READY = true; window.__UPDATE_MARK = 'downloaded';", commit: "good1")
        let runtime = try ReaderRuntime(http: FixtureHTTP([:]))
        XCTAssertEqual(runtime.evaluate("window.__UPDATE_MARK"), "downloaded")
        XCTAssertTrue(try XCTUnwrap(EngineStore.shared.current(.novelDownloader)).updated)
        XCTAssertTrue(EngineStore.shared.rejectedKeys(.novelDownloader).isEmpty)
    }

    func testARulesUpdateThatNeverGetsReadyIsSetAside() throws {
        for (name, script) in [
            ("silent", "window.__SILENT = true;"),
            ("throwing", "throw new Error('broken update');"),
            ("syntax error", "this is not javascript {"),
        ] {
            let fixture = try EngineFixture()
            let tag = "bad-" + String(name.prefix(3))
            let store = try storeWithUpdate(fixture, script: script, commit: tag)
            EngineStore.shared = store
            let logger = CollectingLogger()
            let runtime = try ReaderRuntime(http: FixtureHTTP([:]), logger: logger)
            // The copy shipped with the app took over in a fresh context.
            XCTAssertEqual(runtime.evaluate("window.__ND_READY === true && window.__ND_BRIDGE_READY === true"), "true", name)
            XCTAssertEqual(runtime.evaluate("typeof window.__SILENT"), "undefined", name)
            XCTAssertFalse(try XCTUnwrap(store.current(.novelDownloader)).updated, name)
            XCTAssertEqual(store.rejectedKeys(.novelDownloader), [tag + "-s"], name)
            XCTAssertTrue(logger.lines.contains { $0.contains("using the built-in copy") }, logger.lines.joined(separator: "\n"))
        }
    }

    func testTheShippedRulesNeverGetRejected() throws {
        // A runtime that cannot load the shipped copy fails; it does not blame the copy.
        let fixture = try EngineFixture()
        EngineStore.shared = EngineStore(shipped: { _ in fixture.shippedRules }, updatesRoot: fixture.root.appendingPathComponent("home/engines"))
        XCTAssertThrowsError(try ReaderRuntime(http: FixtureHTTP([:])))
        XCTAssertTrue(EngineStore.shared.rejectedKeys(.novelDownloader).isEmpty)
        XCTAssertNotNil(EngineStore.shared.current(.novelDownloader))
    }

    func testTheRuleLookupFollowsANewBundle() throws {
        let fixture = try EngineFixture()
        let store = realRulesStore(fixture)
        EngineStore.shared = store
        let rules = ReaderRules.shared
        rules.reset()
        let url = URL(string: "https://www.sudugu.com/1001/")!
        XCTAssertTrue(rules.supports(url))
        // An update that only knows how to be ready has no rule for any site; the lookup rebuilds from it.
        try installUpdate(store, script: "window.__ND_READY = true;", commit: "empty")
        XCTAssertFalse(rules.supports(url))
        // Setting it aside brings the shipped rules back.
        store.reject(try XCTUnwrap(store.current(.novelDownloader)), reason: "test")
        XCTAssertTrue(rules.supports(url))
    }

    func testAWebViewRulesUpdateThatDoesNotInitialiseIsSetAside() async throws {
        let fixture = try EngineFixture()
        let store = try storeWithUpdate(fixture, script: "window.__SILENT = true;", commit: "web1")
        let loads = CallCounter()
        let page = ScriptedPage { body in
            if body.hasPrefix("return !!window.__ND_BRIDGE_READY") { return "false" }
            if body.hasPrefix("(0, eval)(") { return "true" }
            if body.hasPrefix("try {\n  (0, eval)(") {
                return loads.bump() == 1 ? "{\"ready\":false,\"error\":null}" : "{\"ready\":true,\"error\":null}"
            }
            throw WebEngineError.scriptFailed("unexpected script")
        }
        let logger = CollectingLogger()
        try await ReaderWebFallback.inject(into: page, logger: logger, store: store)
        XCTAssertEqual(loads.value, 2)
        let rulesScripts = page.bodies.filter { $0.hasPrefix("try {\n  (0, eval)(") }
        XCTAssertTrue(rulesScripts[0].contains("window.__SILENT"), "the update was tried first")
        XCTAssertFalse(rulesScripts[1].contains("window.__SILENT"), "then the shipped copy")
        XCTAssertFalse(try XCTUnwrap(store.current(.novelDownloader)).updated)
        XCTAssertEqual(store.rejectedKeys(.novelDownloader), ["web1-s"])
        XCTAssertTrue(logger.lines.contains { $0.contains("using the built-in copy") }, logger.lines.joined(separator: "\n"))
    }

    func testAWebViewWithNoWorkingRulesIsAnError() async throws {
        let fixture = try EngineFixture()
        let store = EngineStore(shipped: { _ in fixture.shippedRules }, updatesRoot: fixture.root.appendingPathComponent("home/engines"))
        let page = ScriptedPage { body in
            if body.hasPrefix("return !!window.__ND_BRIDGE_READY") { return "false" }
            if body.hasPrefix("(0, eval)(") { return "true" }
            return "{\"ready\":false,\"error\":\"stuck\"}"
        }
        do {
            try await ReaderWebFallback.inject(into: page, logger: NullLogger(), store: store)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("The reader rules failed to load: stuck"))
        }
        XCTAssertTrue(store.rejectedKeys(.novelDownloader).isEmpty)
    }
}
