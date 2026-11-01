import CryptoKit
import Foundation
import NpiaCore

/// The two engines that run site rules inside a page: the novel-downloader rules (`rules-lib.js`)
/// and the WebToEpub parsers (`webtoepub-lib.js`). Both ship with the app; the user can opt in to
/// newer builds from the `engines` release (see `EngineUpdater`).
public enum RuleEngine: String, CaseIterable, Sendable, Hashable {
    case novelDownloader = "novel_downloader"
    case webtoepub

    public var siteEngine: SiteEngine {
        switch self {
        case .novelDownloader: return .novelDownloader
        case .webtoepub: return .webtoepub
        }
    }

    /// The name shown to the user.
    public var title: String { siteEngine.title }

    /// The `window.*` contract this app's bridge speaks. A build with another API is never used.
    public var api: Int { 1 }

    /// The script file, in the app and in the release.
    public var lib: String {
        switch self {
        case .novelDownloader: return "rules-lib.js"
        case .webtoepub: return "webtoepub-lib.js"
        }
    }

    /// The manifest asset in the release.
    public var releaseManifest: String {
        switch self {
        case .novelDownloader: return "novel-downloader-manifest.json"
        case .webtoepub: return "webtoepub-manifest.json"
        }
    }

    /// The shipped copy's manifest; nil when its version is read from the script's own header.
    var shippedManifest: String? {
        switch self {
        case .novelDownloader: return nil
        case .webtoepub: return EngineStore.manifestName
        }
    }

    /// Where the shipped copy sits inside the Reader resources.
    var resourceFolders: [String] {
        switch self {
        case .novelDownloader: return ["Resources", ""]
        case .webtoepub: return ["Resources/webtoepub", "webtoepub"]
        }
    }
}

public extension SiteEngine {
    /// The rule engine behind this choice; nil for the built-in scrapers.
    var ruleEngine: RuleEngine? {
        switch self {
        case .native: return nil
        case .novelDownloader: return .novelDownloader
        case .webtoepub: return .webtoepub
        }
    }
}

/// A bundle's `manifest.json`: `{engine, api, commit, date, version, shim, file, size, sha256, built}`
/// plus whatever else the build recorded.
public struct EngineManifest: @unchecked Sendable {
    public let raw: [String: Any]

    public init(raw: [String: Any]) {
        self.raw = raw
    }

    public init?(data: Data) {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        self.raw = object
    }

    public func string(_ key: String) -> String {
        switch raw[key] {
        case let value as String: return value
        case let value as NSNumber: return value.stringValue
        default: return ""
        }
    }

    public var engine: String? { raw["engine"] as? String }
    public var api: Int? { (raw["api"] as? NSNumber)?.intValue }
    public var size: Int? { (raw["size"] as? NSNumber)?.intValue }
    public var version: String { string("version") }
    public var date: String { string("date") }
    public var built: String { string("built") }
    public var commit: String { string("commit") }
    public var shim: String { string("shim") }
    public var file: String { string("file") }
    public var sha256: String { string("sha256").lowercased() }

    /// What names this build in the rejected list.
    public var releaseKey: String { "\(commit)-\(shim)" }

    /// Newer bundles sort later: upstream version, then commit date, then build time.
    public var key: EngineKey {
        EngineKey(version: EngineManifest.numbers(in: version), date: date, built: built)
    }

    /// The runs of digits in `text` as integers ("1.0.14.61" gives [1, 0, 14, 61]).
    static func numbers(in text: String) -> [Int] {
        var result: [Int] = []
        var run = ""
        for character in text {
            if character.isASCII, character.isNumber {
                run.append(character)
            } else if !run.isEmpty {
                result.append(Int(run) ?? Int.max)
                run = ""
            }
        }
        if !run.isEmpty { result.append(Int(run) ?? Int.max) }
        return result
    }
}

/// Orders bundles: `(version numbers, commit date, build time)`, compared in that order.
public struct EngineKey: Comparable, Equatable, Sendable {
    public var version: [Int]
    public var date: String
    public var built: String

    public init(version: [Int], date: String, built: String) {
        self.version = version
        self.date = date
        self.built = built
    }

    public static func < (lhs: EngineKey, rhs: EngineKey) -> Bool {
        if lhs.version != rhs.version { return lhs.version.lexicographicallyPrecedes(rhs.version) }
        if lhs.date != rhs.date { return lhs.date < rhs.date }
        return lhs.built < rhs.built
    }
}

/// One copy of an engine's script: the one shipped with the app, or a downloaded update.
public struct EngineBundle: Sendable {
    public let engine: RuleEngine
    public let folder: URL
    public let manifest: EngineManifest
    /// True for a download, false for the copy shipped with the app.
    public let updated: Bool

    public var path: URL { folder.appendingPathComponent(engine.lib) }
    public var key: EngineKey { manifest.key }

    /// "5.2.1285, 2026-10-16, 1a2b3c4, built in" (the version only for novel-downloader).
    public var label: String {
        var parts: [String] = []
        if !manifest.version.isEmpty, engine == .novelDownloader { parts.append(manifest.version) }
        if !manifest.date.isEmpty { parts.append(String(manifest.date.prefix(10))) }
        if !manifest.commit.isEmpty { parts.append(String(manifest.commit.prefix(7))) }
        parts.append(updated ? "downloaded update" : "built in")
        return parts.joined(separator: ", ")
    }

    public func read() throws -> String {
        try String(contentsOf: path, encoding: .utf8)
    }
}

/// Where the engine bundles live and which of them is in use.
///
/// The shipped copies sit in the app's resources. Downloads go to Application Support under
/// `engines/<engine>/` with a `manifest.json` and a `state.json` (last check, rejected builds).
/// A download is used only when its API matches, its checksum matches its manifest and it is
/// newer than the shipped copy. One that then fails to load is set aside with `reject`.
public final class EngineStore: @unchecked Sendable {
    public static let manifestName = "manifest.json"
    static let stateName = "state.json"

    /// The store the app uses; tests replace it.
    public static var shared = EngineStore(
        shipped: EngineStore.bundledFolder,
        updatesRoot: EngineStore.defaultUpdatesRoot()
    )

    public typealias ShippedFolders = @Sendable (RuleEngine) -> URL?

    private let shippedFolder: ShippedFolders
    private let updatesRoot: URL
    private let lock = NSLock()
    private var changes = 0
    private var verified: [String: Bool] = [:]

    public init(shipped: @escaping ShippedFolders, updatesRoot: URL) {
        self.shippedFolder = shipped
        self.updatesRoot = updatesRoot
    }

    /// Counts installs and rejections, so caches built from a bundle know when to start over.
    public var generation: Int {
        lock.lock(); defer { lock.unlock() }
        return changes
    }

    // MARK: - Locations

    public static func defaultUpdatesRoot() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("engines", isDirectory: true)
    }

    /// The folders of the scripts shipped in the NpiaSources resources.
    public static let bundledFolder: ShippedFolders = { engine in
        for folder in engine.resourceFolders {
            let base = (engine.lib as NSString).deletingPathExtension
            if let url = Bundle.module.url(forResource: base, withExtension: "js", subdirectory: folder.isEmpty ? nil : folder) {
                return url.deletingLastPathComponent()
            }
        }
        return nil
    }

    public func updatesDirectory(_ engine: RuleEngine) -> URL {
        updatesRoot.appendingPathComponent(engine.rawValue, isDirectory: true)
    }

    // MARK: - Bundles

    /// The copy shipped with the app.
    public func shipped(_ engine: RuleEngine) -> EngineBundle? {
        guard let folder = shippedFolder(engine) else { return nil }
        guard FileManager.default.fileExists(atPath: folder.appendingPathComponent(engine.lib).path) else { return nil }
        let manifest: EngineManifest
        if let name = engine.shippedManifest {
            guard let loaded = Self.readManifest(folder.appendingPathComponent(name)), loaded.api == engine.api else { return nil }
            manifest = loaded
        } else {
            manifest = EngineManifest(raw: ["api": engine.api, "version": Self.headerVersion(folder.appendingPathComponent(engine.lib))])
        }
        return EngineBundle(engine: engine, folder: folder, manifest: manifest, updated: false)
    }

    /// A valid download: right API, a checksum that matches its manifest.
    public func downloaded(_ engine: RuleEngine) -> EngineBundle? {
        let folder = updatesDirectory(engine)
        let manifestURL = folder.appendingPathComponent(Self.manifestName)
        let scriptURL = folder.appendingPathComponent(engine.lib)
        guard let manifest = Self.readManifest(manifestURL), manifest.api == engine.api,
              FileManager.default.fileExists(atPath: scriptURL.path) else { return nil }
        guard checksumMatches(scriptURL, manifestURL, expected: manifest.sha256) else { return nil }
        return EngineBundle(engine: engine, folder: folder, manifest: manifest, updated: true)
    }

    /// The bundle to run: a valid download when it is newer than the shipped copy.
    public func current(_ engine: RuleEngine) -> EngineBundle? {
        let base = shipped(engine)
        guard let update = downloaded(engine) else { return base }
        if let base, update.key <= base.key { return base }
        return update
    }

    /// The bundles to try in order: `current`, then the shipped copy when `current` is a download
    /// (a download that fails to load is set aside and the shipped copy takes over).
    public func candidates(for engine: RuleEngine) -> [EngineBundle] {
        guard let first = current(engine) else { return [] }
        if first.updated, let base = shipped(engine) { return [first, base] }
        return [first]
    }

    /// Hashing a 1.5 MB script on every lookup would add up, so a verdict is kept until the files change.
    private func checksumMatches(_ script: URL, _ manifest: URL, expected: String) -> Bool {
        let key = [script, manifest].map { url -> String in
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
            let stamp = values?.contentModificationDate?.timeIntervalSince1970 ?? 0
            return "\(url.path)|\(values?.fileSize ?? -1)|\(stamp)"
        }.joined(separator: "|")
        lock.lock()
        if let known = verified[key] {
            lock.unlock()
            return known
        }
        lock.unlock()
        let matches: Bool
        if let data = try? Data(contentsOf: script) {
            matches = Self.sha256Hex(data) == expected
        } else {
            matches = false
        }
        lock.lock()
        verified[key] = matches
        lock.unlock()
        return matches
    }

    static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func readManifest(_ url: URL) -> EngineManifest? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return EngineManifest(data: data)
    }

    /// `@version` from the first 4 KB of a userscript header, or "".
    static func headerVersion(_ url: URL) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        let head = (try? handle.read(upToCount: 4096)) ?? Data()
        let text = String(decoding: head, as: UTF8.self)
        guard let regex = try? NSRegularExpression(pattern: #"@version\s+(\S+)"#),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              let range = Range(match.range(at: 1), in: text) else { return "" }
        return String(text[range])
    }

    // MARK: - State

    struct State: Codable {
        var checked: Double?
        var rejected: [String]?
        var rejectedReason: String?

        enum CodingKeys: String, CodingKey {
            case checked, rejected
            case rejectedReason = "rejected_reason"
        }
    }

    func state(_ engine: RuleEngine) -> State {
        let url = updatesDirectory(engine).appendingPathComponent(Self.stateName)
        guard let data = try? Data(contentsOf: url), let state = try? JSONDecoder().decode(State.self, from: data) else {
            return State()
        }
        return state
    }

    /// Lock held.
    private func save(_ state: State, _ engine: RuleEngine) {
        let folder = updatesDirectory(engine)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(state) {
            try? data.write(to: folder.appendingPathComponent(Self.stateName), options: .atomic)
        }
    }

    public func lastChecked(_ engine: RuleEngine) -> Date? {
        lock.lock(); defer { lock.unlock() }
        return state(engine).checked.map { Date(timeIntervalSince1970: $0) }
    }

    func markChecked(_ engine: RuleEngine, at date: Date) {
        lock.lock(); defer { lock.unlock() }
        var current = state(engine)
        current.checked = date.timeIntervalSince1970
        save(current, engine)
    }

    /// True when a build with this release key failed to load before.
    public func isRejected(_ engine: RuleEngine, releaseKey: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return (state(engine).rejected ?? []).contains(releaseKey)
    }

    public func rejectedKeys(_ engine: RuleEngine) -> [String] {
        lock.lock(); defer { lock.unlock() }
        return state(engine).rejected ?? []
    }

    /// Sets a downloaded bundle aside because it failed to load: it is not used or fetched again.
    /// The shipped copy is never touched.
    public func reject(_ bundle: EngineBundle, reason: String = "") {
        guard bundle.updated else { return }
        lock.lock(); defer { lock.unlock() }
        var current = state(bundle.engine)
        current.rejected = Array(Set(current.rejected ?? []).union([bundle.manifest.releaseKey])).sorted()
        current.rejectedReason = String(reason.prefix(300))
        save(current, bundle.engine)
        for name in [bundle.engine.lib, Self.manifestName] {
            try? FileManager.default.removeItem(at: bundle.folder.appendingPathComponent(name))
        }
        changes += 1
    }

    /// Stores a downloaded script and its manifest (the file name becomes the engine's own).
    func install(_ engine: RuleEngine, data: Data, manifest remote: EngineManifest) throws -> EngineBundle {
        lock.lock(); defer { lock.unlock() }
        let folder = updatesDirectory(engine)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try data.write(to: folder.appendingPathComponent(engine.lib), options: .atomic)
        var raw = remote.raw
        raw["file"] = engine.lib
        let manifestData = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys])
        try manifestData.write(to: folder.appendingPathComponent(Self.manifestName), options: .atomic)
        changes += 1
        return EngineBundle(engine: engine, folder: folder, manifest: EngineManifest(raw: raw), updated: true)
    }
}
