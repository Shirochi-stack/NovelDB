import Foundation

public enum EngineUpdateStatus: Sendable, Equatable {
    case updated, current, failed
}

public struct EngineUpdateResult: Sendable {
    public var engine: RuleEngine
    public var status: EngineUpdateStatus
    public var message: String
    /// The bundle in use after the check.
    public var bundle: EngineBundle?
}

struct EngineUpdateError: Error, LocalizedError {
    var message: String
    var errorDescription: String? { message }
}

/// Opt-in updates of the rule engines from the `engines` release of this repository, which a
/// daily job rebuilds from the official sources after checking that they load.
///
/// A build is taken only when its manifest is for this engine, its API matches, it was not set
/// aside before, it is not a rebuild of the bundle in use (same commit and shim), it sorts after
/// the bundle in use, its file name is a plain name, and the
/// download matches the manifest's size and SHA-256.
public actor EngineUpdater {
    public static let releaseURL = "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/"
    /// Seconds between automatic checks.
    public static let checkInterval: TimeInterval = 24 * 3600

    /// Downloads a URL's bytes; throws for network errors and non-2xx replies.
    public typealias Fetch = @Sendable (URL, TimeInterval) async throws -> Data

    public static let shared = EngineUpdater(store: EngineStore.shared)

    private let store: EngineStore
    private let releaseBase: String
    private let fetch: Fetch
    private let now: @Sendable () -> Date

    public init(
        store: EngineStore,
        releaseBase: String = EngineUpdater.releaseURL,
        fetch: @escaping Fetch = EngineUpdater.download,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.store = store
        self.releaseBase = releaseBase
        self.fetch = fetch
        self.now = now
    }

    public static func download(_ url: URL, _ timeout: TimeInterval) async throws -> Data {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.setValue("NovelDB", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw EngineUpdateError(message: "HTTP \(http.statusCode)")
        }
        return data
    }

    /// Downloads the release's build of `engine` when it is newer than the one in use.
    public func check(_ engine: RuleEngine, timeout: TimeInterval = 60) async -> EngineUpdateResult {
        let title = engine.title
        store.markChecked(engine, at: now())

        func result(_ status: EngineUpdateStatus, _ message: String, _ bundle: EngineBundle? = nil) -> EngineUpdateResult {
            EngineUpdateResult(engine: engine, status: status, message: message, bundle: bundle)
        }

        guard let manifestURL = URL(string: releaseBase + engine.releaseManifest) else {
            return result(.failed, "Could not check for \(title) updates: the release address is invalid.")
        }
        let remote: EngineManifest
        do {
            let data = try await fetch(manifestURL, timeout)
            guard let parsed = EngineManifest(data: data) else {
                throw EngineUpdateError(message: "the manifest is not an object")
            }
            remote = parsed
        } catch {
            return result(.failed, "Could not check for \(title) updates: \(Self.describe(error))")
        }

        let inUse = store.current(engine)
        if let name = remote.engine, name != engine.rawValue {
            return result(.failed, "The \(title) update manifest is for another engine.")
        }
        if remote.api != engine.api {
            return result(.current, "The newest \(title) need a newer NovelDB.", inUse)
        }
        if store.isRejected(engine, releaseKey: remote.releaseKey) {
            return result(.current, "The newest \(title) failed to load before; keeping these.", inUse)
        }
        // The same upstream commit and build patch: a rebuild of what is in use.
        if let inUse, !inUse.manifest.commit.isEmpty, remote.releaseKey == inUse.manifest.releaseKey {
            return result(.current, "The \(title) are up to date (\(inUse.label)).", inUse)
        }
        if let inUse, remote.key <= inUse.key {
            return result(.current, "The \(title) are up to date (\(inUse.label)).", inUse)
        }
        let name = remote.file
        guard Self.isPlainFileName(name),
              let fileName = name.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let fileURL = URL(string: releaseBase + fileName)
        else {
            return result(.failed, "The \(title) update manifest names no valid file.")
        }
        let data: Data
        do {
            data = try await fetch(fileURL, max(timeout, 60))
        } catch {
            return result(.failed, "Could not download the \(title) update: \(Self.describe(error))")
        }
        guard EngineStore.sha256Hex(data) == remote.sha256, data.count == remote.size else {
            return result(.failed, "The downloaded \(title) update did not match its checksum.")
        }
        do {
            let bundle = try store.install(engine, data: data, manifest: remote)
            return result(.updated, "Updated the \(title) to \(bundle.label).", bundle)
        } catch {
            return result(.failed, "Could not store the \(title) update: \(Self.describe(error))")
        }
    }

    /// The automatic check: at most once per `every` seconds. Nil when it is not due yet.
    public func checkIfDue(_ engine: RuleEngine, every: TimeInterval = EngineUpdater.checkInterval) async -> EngineUpdateResult? {
        if let last = store.lastChecked(engine) {
            let elapsed = now().timeIntervalSince(last)
            if elapsed >= 0, elapsed < every { return nil }
        }
        return await check(engine)
    }

    /// A release file name must be a plain name inside the release.
    static func isPlainFileName(_ name: String) -> Bool {
        !name.isEmpty && !name.contains("/") && !name.contains("\\") && !name.contains("..")
    }

    private static func describe(_ error: Error) -> String {
        if let known = error as? LocalizedError, let text = known.errorDescription { return text }
        return error.localizedDescription
    }
}
