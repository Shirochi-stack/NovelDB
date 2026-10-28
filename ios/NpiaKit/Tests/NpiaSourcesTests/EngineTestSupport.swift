import Foundation
import NpiaCore
@testable import NpiaSources

/// A fake app bundle and an empty Application Support folder for an `EngineStore`:
/// rules-lib.js 5.2.1285 on one side, WebToEpub of 2026-10-27 on the other.
final class EngineFixture {
    let root: URL
    let store: EngineStore
    let shippedRules: URL
    let shippedParsers: URL

    static let backslash = "\u{5c}"

    /// A novel-downloader header and the `getRule` switch: two hosts, one written with an escape,
    /// and one host pattern.
    static var rulesText: String {
        let escaped = "esc." + backslash + "u0073ample.test"
        let pattern = "^book" + backslash + "d+" + backslash + ".com$"
        return "// ==UserScript==\n// @version        5.2.1285\n// ==/UserScript==\n"
            + "switch(e){case\"www.site.test\":{break}case\"api.example.org\":{break}case\"" + escaped + "\":{break}"
            + "default:if(/" + pattern + "/.test(e)){break}throw new Error(\"Not Found Rule!\")}"
    }

    static let parsersText = "parserFactory.register(\"www.novel.test\", () => 0);\nparserFactory.register(\"Other.TEST\", () => 0);"

    static let parsersManifest = """
    {"api": 1, "commit": "\(String(repeating: "a", count: 40))", "shim": "s1", "date": "2026-10-27T04:19:22Z", "built": "2026-10-28T00:00:00Z", "version": "1.0.14.61", "libraries": ["x@1"]}
    """

    init() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("engine-fixture-" + UUID().uuidString)
        let rules = root.appendingPathComponent("bundle", isDirectory: true)
        let parsers = rules.appendingPathComponent("webtoepub", isDirectory: true)
        try FileManager.default.createDirectory(at: parsers, withIntermediateDirectories: true)
        try Self.rulesText.write(to: rules.appendingPathComponent("rules-lib.js"), atomically: true, encoding: .utf8)
        try Self.parsersText.write(to: parsers.appendingPathComponent("webtoepub-lib.js"), atomically: true, encoding: .utf8)
        try Self.parsersManifest.write(to: parsers.appendingPathComponent("manifest.json"), atomically: true, encoding: .utf8)
        self.root = root
        self.shippedRules = rules
        self.shippedParsers = parsers
        self.store = EngineStore(
            shipped: { engine in engine == .novelDownloader ? rules : parsers },
            updatesRoot: root.appendingPathComponent("home/engines", isDirectory: true)
        )
    }

    deinit {
        try? FileManager.default.removeItem(at: root)
    }

    func updates(_ engine: RuleEngine) -> URL {
        store.updatesDirectory(engine)
    }
}

/// A fake `engines` release: manifests and scripts by URL, a record of what was fetched.
final class FakeRelease: @unchecked Sendable {
    private let lock = NSLock()
    private var files: [String: Data] = [:]
    private var failure: Error?
    private var requested: [String] = []

    var calls: [String] {
        lock.lock(); defer { lock.unlock() }
        return requested
    }

    func serve(_ name: String, _ data: Data) {
        lock.lock(); defer { lock.unlock() }
        files[EngineUpdater.releaseURL + name] = data
    }

    /// Publishes `engine`'s build: its manifest (with `overrides`) and the script itself.
    /// Returns the manifest.
    @discardableResult
    func publish(_ engine: RuleEngine, body: Data, overrides: [String: Any] = [:]) -> [String: Any] {
        var manifest: [String: Any] = [
            "engine": engine.rawValue, "api": 1, "commit": String(repeating: "b", count: 40), "shim": "s2",
            "built": "2026-11-01T00:00:00Z", "file": engine.lib + "-new.js", "size": body.count,
            "sha256": EngineStore.sha256Hex(body),
        ]
        if engine == .novelDownloader {
            manifest["version"] = "5.2.1289"
            manifest["date"] = "2026-10-16T01:44:06Z"
        } else {
            manifest["version"] = "1.0.14.61"
            manifest["date"] = "2026-11-01T00:00:00Z"
        }
        for (key, value) in overrides { manifest[key] = value }
        serve(engine.releaseManifest, try! JSONSerialization.data(withJSONObject: manifest))
        if let name = manifest["file"] as? String { serve(name, body) }
        return manifest
    }

    func failEverything(with error: Error) {
        lock.lock(); defer { lock.unlock() }
        failure = error
    }

    func fetch(_ url: URL, _ timeout: TimeInterval) async throws -> Data {
        try reply(url)
    }

    private func reply(_ url: URL) throws -> Data {
        lock.lock(); defer { lock.unlock() }
        requested.append(url.lastPathComponent)
        if let failure { throw failure }
        guard let data = files[url.absoluteString] else { throw URLError(.fileDoesNotExist) }
        return data
    }

    func updater(_ store: EngineStore, now: @escaping @Sendable () -> Date = { Date() }) -> EngineUpdater {
        EngineUpdater(store: store, fetch: { [self] url, timeout in try await self.fetch(url, timeout) }, now: now)
    }
}

/// A clock the test moves by hand.
final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current: Date

    init(_ start: Date = Date(timeIntervalSince1970: 1_800_000_000)) {
        current = start
    }

    var now: Date {
        lock.lock(); defer { lock.unlock() }
        return current
    }

    func advance(_ seconds: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        current = current.addingTimeInterval(seconds)
    }
}
