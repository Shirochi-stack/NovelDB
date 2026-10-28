import Foundation

/// A newer build of NovelDB that a release carries.
public struct AppUpdate: Equatable, Sendable, Identifiable {
    public var tag: String
    public var label: String
    public var build: Int
    public var asset: AppUpdateAsset
    public var downloadURL: URL
    /// The release page ("Release notes").
    public var releaseURL: URL

    public var id: Int { build }

    public init(tag: String, label: String, build: Int, asset: AppUpdateAsset, downloadURL: URL, releaseURL: URL) {
        self.tag = tag
        self.label = label
        self.build = build
        self.asset = asset
        self.downloadURL = downloadURL
        self.releaseURL = releaseURL
    }

    /// `v98` or `v98 hotfix3`.
    public var versionName: String { AppBuildInfo.versionName(tag: tag, label: label) }

    /// "NovelDB v98 hotfix3 is available"
    public var title: String {
        versionName.isEmpty ? "A new version of NovelDB is available" : "NovelDB \(versionName) is available"
    }
}

/// What a check found.
public enum AppUpdateOutcome: Equatable, Sendable {
    /// This is a development build: updates are off and nothing was fetched.
    case development
    case upToDate
    case available(AppUpdate)
    /// The latest release has no usable manifest or no entry for this app (older releases have
    /// no manifest). Treated as "no update".
    case noInformation
    /// The check itself failed (offline, server error).
    case failed(String)

    /// The line Settings shows.
    public var message: String {
        switch self {
        case .development: return "Development build: app updates are off."
        case .upToDate: return "NovelDB is up to date."
        case .available(let update): return "\(update.title)."
        case .noInformation: return "No update information in the latest release."
        case .failed(let reason): return "Could not check for updates: \(reason)"
        }
    }
}

/// Looks at NovelDB's GitHub releases for a newer build of this app, and downloads it after the
/// user asks. iOS apps cannot install themselves, so the result is a verified IPA file for the
/// share sheet; this type never installs anything.
///
/// Rules (the same on desktop, Android and iOS):
/// - A development build (no build number) never checks.
/// - The manifest is `update-ios.json` of the latest published release; the app's entry is the
///   `ipa` file with the universal variant and architecture. An update exists when that entry's
///   build number is greater than this app's.
/// - No entry, a 404 or a manifest that does not parse is "no update information", not an error.
/// - Automatic checks run at most once per 24 hours. The time of the last check is kept in
///   `UserDefaults` (not in the settings) and is recorded whenever the server answered, whatever
///   it said; a check that failed to reach the server does not use up the day.
/// - A download goes to a temporary file, and only a file whose size and SHA-256 match the
///   manifest keeps its name; anything else is deleted.
public actor AppUpdateChecker {
    /// Seconds between automatic checks.
    public static let checkInterval: TimeInterval = 24 * 3600
    public static let lastCheckKey = "appUpdateLastCheck"

    public static let platform = "ios"
    public static let kind = "ipa"
    public static let variant = "universal"
    public static let arch = "universal"

    /// Where verified downloads are kept; emptied by every new download.
    public static var defaultDownloadDirectory: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("NovelDBUpdate", isDirectory: true)
    }

    private let build: AppBuildInfo
    private let endpoints: AppUpdateEndpoints
    private let configuration: URLSessionConfiguration
    private let defaults: UserDefaults
    private let downloadDirectory: URL
    private let now: @Sendable () -> Date

    public init(
        build: AppBuildInfo,
        endpoints: AppUpdateEndpoints = AppUpdateEndpoints(),
        configuration: URLSessionConfiguration = .ephemeral,
        defaults: UserDefaults = .standard,
        downloadDirectory: URL = AppUpdateChecker.defaultDownloadDirectory,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.build = build
        self.endpoints = endpoints
        self.configuration = configuration
        self.defaults = defaults
        self.downloadDirectory = downloadDirectory
        self.now = now
    }

    // MARK: - The 24-hour rule

    /// True when no check has been recorded yet, a day has passed since the last one, or the
    /// clock was set back past it.
    public static func isDue(last: Date?, now: Date, every interval: TimeInterval = checkInterval) -> Bool {
        guard let last else { return true }
        let elapsed = now.timeIntervalSince(last)
        return elapsed < 0 || elapsed >= interval
    }

    public func lastChecked() -> Date? {
        (defaults.object(forKey: Self.lastCheckKey) as? Double).map { Date(timeIntervalSince1970: $0) }
    }

    public func isDue() -> Bool {
        Self.isDue(last: lastChecked(), now: now())
    }

    /// The automatic check: nil when this is a development build or less than a day has passed.
    public func checkIfDue() async -> AppUpdateOutcome? {
        guard !build.isDevelopment, isDue() else { return nil }
        return await check()
    }

    // MARK: - Checking

    /// Fetches the manifest now, whatever the last check time says ("Check for app updates now").
    public func check() async -> AppUpdateOutcome {
        if build.isDevelopment { return .development }
        var request = URLRequest(
            url: endpoints.manifestURL(platform: Self.platform),
            cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30
        )
        request.setValue("NovelDB", forHTTPHeaderField: "User-Agent")
        let session = URLSession(configuration: configuration)
        defer { session.finishTasksAndInvalidate() }

        let outcome: AppUpdateOutcome
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 200
            switch status {
            case 200..<300: outcome = Self.evaluate(manifest: data, build: build, endpoints: endpoints)
            case 404: outcome = .noInformation
            default: return .failed("the server answered HTTP \(status).")
            }
        } catch {
            return .failed(error.localizedDescription)
        }
        defaults.set(now().timeIntervalSince1970, forKey: Self.lastCheckKey)
        return outcome
    }

    /// Decides what a manifest means for `build`, without touching the network.
    public static func evaluate(
        manifest data: Data, build: AppBuildInfo, endpoints: AppUpdateEndpoints = AppUpdateEndpoints()
    ) -> AppUpdateOutcome {
        if build.isDevelopment { return .development }
        guard let manifest = AppUpdateManifest(data: data),
              manifest.platform.isEmpty || manifest.platform == platform,
              let entry = manifest.asset(kind: kind, variant: variant, arch: arch)
        else { return .noInformation }
        guard entry.build > build.build else { return .upToDate }
        guard let downloadURL = endpoints.assetURL(tag: manifest.tag, name: entry.name) else { return .noInformation }
        // The release page: the manifest's own address when it is https, else the tag's page.
        let advertised = URL(string: manifest.releaseURL).flatMap { $0.scheme?.lowercased() == "https" ? $0 : nil }
        guard let releaseURL = advertised ?? endpoints.releasePageURL(tag: manifest.tag) else { return .noInformation }
        return .available(AppUpdate(
            tag: manifest.tag, label: entry.label, build: entry.build, asset: entry,
            downloadURL: downloadURL, releaseURL: releaseURL
        ))
    }

    // MARK: - Downloading

    /// Downloads the update into a temporary file, checks its size and SHA-256 against the
    /// manifest, and returns the verified file. On a mismatch the file is deleted and
    /// `AppUpdateError.checksumMismatch` is thrown; cancelling the calling task deletes the
    /// partial file and throws `CancellationError`. `progress` gets (bytes so far, expected bytes).
    public func download(
        _ update: AppUpdate, progress: @escaping @Sendable (Int64, Int64) -> Void = { _, _ in }
    ) async throws -> URL {
        discardDownloads()
        do {
            try FileManager.default.createDirectory(at: downloadDirectory, withIntermediateDirectories: true)
        } catch {
            throw AppUpdateError.storage(error.localizedDescription)
        }
        let partialURL = downloadDirectory.appendingPathComponent(update.asset.name + ".part")
        let finalURL = downloadDirectory.appendingPathComponent(update.asset.name)

        let transfer = UpdateTransfer(
            url: update.downloadURL, destination: partialURL, expectedSize: update.asset.size,
            configuration: configuration, progress: progress
        )
        let received: UpdateTransfer.Received
        do {
            received = try await transfer.run()
        } catch {
            try? FileManager.default.removeItem(at: partialURL)
            throw error
        }
        guard received.size == update.asset.size, received.sha256 == update.asset.sha256 else {
            try? FileManager.default.removeItem(at: partialURL)
            throw AppUpdateError.checksumMismatch
        }
        do {
            try FileManager.default.moveItem(at: partialURL, to: finalURL)
        } catch {
            try? FileManager.default.removeItem(at: partialURL)
            throw AppUpdateError.storage(error.localizedDescription)
        }
        return finalURL
    }

    /// Deletes every downloaded update.
    public func discardDownloads() {
        try? FileManager.default.removeItem(at: downloadDirectory)
    }
}
