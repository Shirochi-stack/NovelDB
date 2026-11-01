import Foundation
import NpiaCore

/// One file packed in an uploaded ZIP.
public struct UploadedEntry: Codable, Sendable, Hashable {
    /// The entry's name in the ZIP: the original file name.
    public var fileName: String
    /// The book's title, when it was known.
    public var title: String?

    public init(fileName: String, title: String? = nil) {
        self.fileName = fileName
        self.title = title
    }

    /// The title, else the file name without its extension.
    public var displayTitle: String {
        if let title, !title.isEmpty { return title }
        return (fileName as NSString).deletingPathExtension
    }

    public var isEPUB: Bool { (fileName as NSString).pathExtension.lowercased() == "epub" }
}

public struct UploadResult: Codable, Sendable, Hashable {
    public var hostID: String
    public var fileName: String
    public var link: URL
    /// Page or token that deletes the upload, when the host returns one.
    public var deleteLink: URL?
    public var expiresAt: Date?
    public var uploadedAt: Date
    /// What a ZIP holds, one entry per packed file; nil for a file uploaded as it is (and for
    /// history saved before ZIP bundles existed).
    public var contents: [UploadedEntry]?

    public init(hostID: String, fileName: String, link: URL, deleteLink: URL? = nil, expiresAt: Date? = nil, uploadedAt: Date = Date(), contents: [UploadedEntry]? = nil) {
        self.hostID = hostID
        self.fileName = fileName
        self.link = link
        self.deleteLink = deleteLink
        self.expiresAt = expiresAt
        self.uploadedAt = uploadedAt
        self.contents = contents
    }

    /// "NovelDB_3_EPUBs.zip (3 books)" for a ZIP, the file name for a file.
    public var summary: String {
        guard let contents, contents.count > 1 else { return fileName }
        let noun = contents.allSatisfy { $0.isEPUB } ? "books" : "files"
        return "\(fileName) (\(contents.count) \(noun))"
    }

    /// The titles inside a ZIP, in the order they were packed; empty for a file.
    public var titlesInside: [String] {
        (contents ?? []).map { $0.displayTitle }
    }

    /// One line for Copy and Share: what it is, and where.
    public var linkLine: String {
        "\(summary): \(link.absoluteString)"
    }

    public func isExpired(at date: Date = Date()) -> Bool {
        guard let expiresAt else { return false }
        return expiresAt <= date
    }
}

public struct UploadProgress: Sendable, Equatable {
    public var sentBytes: Int64
    public var totalBytes: Int64

    public init(sentBytes: Int64, totalBytes: Int64) {
        self.sentBytes = sentBytes
        self.totalBytes = totalBytes
    }

    public var fraction: Double {
        totalBytes > 0 ? min(1, Double(sentBytes) / Double(totalBytes)) : 0
    }
}

/// What `UploaderCatalog.upload` reports while it works through the configured hosts, so the
/// UI can say which host is being tried and how many bytes it has taken.
public enum UploadEvent: Sendable, Equatable {
    /// A host is about to receive the file; `position` counts from 1 among the hosts that can be tried.
    case trying(host: String, position: Int, of: Int)
    /// Bytes the host being tried has received.
    case progress(host: String, UploadProgress)
    /// The host did not take the file; the next one is tried, or the upload fails.
    case failed(host: String, message: String)
}

/// What `UploaderCatalog.uploadBatch` reports while it packs and uploads several files.
public enum UploadBatchEvent: Sendable, Equatable {
    /// A ZIP is being written: `zip` counts from 1 among the ZIPs of this batch.
    case preparing(zip: Int, of: Int, name: String)
    /// Upload `index` of `of` (a ZIP or a lone file) starts.
    case starting(index: Int, of: Int, name: String)
    /// The hosts' own progress for the upload that is running.
    case host(UploadEvent)
    case uploaded(UploadResult)
    case failed(name: String, message: String)
}

public struct UploadBatchFailure: Sendable, Equatable {
    public var name: String
    public var message: String

    public init(name: String, message: String) {
        self.name = name
        self.message = message
    }
}

/// The links a batch produced, and the uploads that did not work. One ZIP failing does not
/// stop the others.
public struct UploadBatchOutcome: Sendable, Equatable {
    public var uploads: [UploadResult]
    public var failures: [UploadBatchFailure]

    public init(uploads: [UploadResult] = [], failures: [UploadBatchFailure] = []) {
        self.uploads = uploads
        self.failures = failures
    }
}

public enum UploadError: Error, Equatable, LocalizedError {
    case notImplemented(String)
    case tooLarge(host: String, limitBytes: Int64)
    case notSignedIn(String)
    case notConfigured(String)
    case rejected(host: String, message: String)
    case badResponse(host: String, status: Int, body: String)

    public var errorDescription: String? {
        switch self {
        case .notImplemented(let host): return "\(host) upload is not available in this build yet."
        case .tooLarge(let host, let limit): return "\(host) accepts files up to \(ByteCountFormatter.string(fromByteCount: limit, countStyle: .file))."
        case .notSignedIn(let host): return "Sign in to \(host) first."
        case .notConfigured(let what): return "\(what) is not configured in this build."
        case .rejected(let host, let message): return "\(host) rejected the upload: \(message)"
        case .badResponse(let host, let status, let body): return "\(host) returned HTTP \(status): \(body)"
        }
    }
}

/// Credentials for account-based hosts, kept in `SecretStore` next to the core keys.
public extension SecretKey {
    /// Pixeldrain API key (pixeldrain.com/user/api_keys). Anonymous uploads were removed.
    static let pixeldrainAPIKey = SecretKey(rawValue: "upload.pixeldrain.key")
    /// Optional Gofile account token; guest uploads work without it.
    static let gofileToken = SecretKey(rawValue: "upload.gofile.token")
    /// Optional Catbox user hash so uploads show up in the account.
    static let catboxUserHash = SecretKey(rawValue: "upload.catbox.userhash")
}

/// Everything an uploader needs besides the file. Tests swap the session for a stub `URLProtocol`.
public struct UploadContext: Sendable {
    public var session: URLSession
    public var secrets: any SecretStore
    public var settings: UploadSettings
    /// nil until the Google OAuth client ID is filled in (see `GoogleOAuthConfig`).
    public var googleOAuth: GoogleOAuthConfig?
    /// Where multipart bodies and folder zips are staged.
    public var temporaryDirectory: URL
    /// The book id files are named with when their title is not plain ASCII ("<id>.epub").
    public var nameID: String?
    public var now: @Sendable () -> Date

    public init(
        session: URLSession = UploadContext.defaultSession,
        secrets: any SecretStore = KeychainSecretStore(),
        settings: UploadSettings = UploadSettings(),
        googleOAuth: GoogleOAuthConfig? = GoogleOAuthConfig.fromBundle(),
        temporaryDirectory: URL = FileManager.default.temporaryDirectory,
        nameID: String? = nil,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.session = session
        self.secrets = secrets
        self.settings = settings
        self.googleOAuth = googleOAuth
        self.temporaryDirectory = temporaryDirectory
        self.nameID = nameID
        self.now = now
    }

    /// Long resource timeout so multi-GB uploads on slow links are not cut off.
    public static let defaultSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 24 * 3_600
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()
}

/// One share-link host. Ports of the `_upload_to_*` helpers in the desktop `bot.py`, plus
/// account-based hosts such as Google Drive.
public protocol Uploader: Sendable {
    /// Stable id stored in `UploadSettings.hosts` (e.g. `tempsh`, `gofile`, `gdrive`).
    var id: String { get }
    var displayName: String { get }
    /// nil when the host has no published limit.
    var maxBytes: Int64? { get }
    /// How long anonymous uploads stay up; nil for permanent or account storage.
    var retention: TimeInterval? { get }
    var requiresAccount: Bool { get }
    /// False when an account host has no credentials yet, so the UI can show "Sign in".
    func isReady(_ context: UploadContext) -> Bool
    func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult
}

public extension Uploader {
    func isReady(_ context: UploadContext) -> Bool { true }

    func check(_ file: URL) throws {
        guard let maxBytes else { return }
        let size = Int64((try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        if size > maxBytes { throw UploadError.tooLarge(host: displayName, limitBytes: maxBytes) }
    }

    func upload(_ file: URL, progress: (@Sendable (UploadProgress) -> Void)? = nil) async throws -> UploadResult {
        try await upload(file, context: UploadContext(), progress: progress)
    }

    func result(for file: URL, link: URL, deleteLink: URL? = nil, context: UploadContext) -> UploadResult {
        let now = context.now()
        return UploadResult(
            hostID: id,
            fileName: uploadName(file),
            link: link,
            deleteLink: deleteLink,
            expiresAt: retention.map { now.addingTimeInterval($0) },
            uploadedAt: now
        )
    }
}

public enum UploaderCatalog {
    // Limits checked against each host's docs in October 2026. 0x0.st, file.io and
    // transfer.sh from bot.py are gone or closed to anonymous uploads and are not ported.
    public static let all: [any Uploader] = [
        TempShUploader(),
        GofileUploader(),
        CatboxUploader(),
        PixeldrainUploader(),
        TmpfilesUploader(),
        KrakenFilesUploader(),
        SendNowUploader(),
        GoogleDriveUploader(),
    ]

    /// Ids that were renamed; old settings keep working.
    static let aliases = ["sendcm": "sendnow", "google_drive": "gdrive"]

    public static func uploader(id: String) -> (any Uploader)? {
        let id = aliases[id] ?? id
        return all.first { $0.id == id }
    }

    /// Tries each configured host in order and returns the first success. Folders (separate
    /// TXT output) are zipped first. Successful uploads are added to `history`.
    ///
    /// `nameID` is the stand-in name (like `npia_419368`) for a file whose title keeps fewer than
    /// three ASCII letters (digits do not count); the result keeps the original name. `contents` is what a
    /// ZIP holds, stored with the result. `events` reports the host being tried, its byte
    /// progress, and each failure that makes the next host run.
    public static func upload(
        _ file: URL,
        settings: UploadSettings,
        context: UploadContext? = nil,
        history: UploadHistoryStore? = .shared,
        nameID: String? = nil,
        contents: [UploadedEntry]? = nil,
        events: (@Sendable (UploadEvent) -> Void)? = nil,
        progress: (@Sendable (UploadProgress) -> Void)? = nil
    ) async throws -> UploadResult {
        var context = context ?? UploadContext(settings: settings)
        context.settings = settings
        if let nameID { context.nameID = nameID }

        var payload = file
        var staged: URL?
        if (try? file.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
            payload = try FolderArchiver.zip(file, in: context.temporaryDirectory)
            staged = payload.deletingLastPathComponent()
        }
        defer { if let staged { try? FileManager.default.removeItem(at: staged) } }

        var lastError: Error = UploadError.notConfigured("No upload host")
        let candidates = settings.hosts.compactMap { uploader(id: $0) }
        var position = 0
        for uploader in candidates {
            position += 1
            let host = uploader.displayName
            guard uploader.isReady(context) else {
                lastError = UploadError.notSignedIn(host)
                events?(.failed(host: host, message: lastError.localizedDescription))
                continue
            }
            events?(.trying(host: host, position: position, of: candidates.count))
            let relay: (@Sendable (UploadProgress) -> Void)?
            if progress == nil && events == nil {
                relay = nil
            } else {
                relay = { value in
                    progress?(value)
                    events?(.progress(host: host, value))
                }
            }
            do {
                try uploader.check(payload)
                var result = try await uploader.upload(payload, context: context, progress: relay)
                result.contents = contents
                history?.add(result)
                return result
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as URLError where error.code == .cancelled {
                throw CancellationError()
            } catch {
                lastError = error
                events?(.failed(host: host, message: error.localizedDescription))
            }
        }
        throw lastError
    }

    /// The per-file maximum of the first host that can take an upload now, when it has one.
    /// Bundles are sized to it, so a ZIP is not refused by the host it is aimed at.
    public static func hostMaxBytes(settings: UploadSettings, context: UploadContext) -> Int64? {
        for id in settings.hosts {
            guard let host = Self.uploader(id: id), host.isReady(context) else { continue }
            return host.maxBytes
        }
        return nil
    }

    /// What `uploadBatch` would upload for `items` with these settings, without writing anything.
    public static func planBatch(_ items: [UploadItem], settings: UploadSettings, context: UploadContext? = nil) -> [UploadBundle] {
        planBatch(items, settings: settings, context: context, limitBytes: nil)
    }

    /// `limitBytes` replaces the size limit worked out from the setting and the host (tests use
    /// small ones).
    static func planBatch(_ items: [UploadItem], settings: UploadSettings, context: UploadContext?, limitBytes: Int64?) -> [UploadBundle] {
        let limit: Int64
        if let limitBytes {
            limit = limitBytes
        } else {
            let context = context ?? UploadContext(settings: settings)
            limit = UploadBundler.limitBytes(zipMaxMB: settings.zipMaxMB, hostMaxBytes: hostMaxBytes(settings: settings, context: context))
        }
        return UploadBundler.plan(items, zipBundle: settings.zipBundle, limit: limit)
    }

    /// Uploads several files. With `UploadSettings.zipBundle` on and two or more files they are
    /// packed into ZIPs no bigger than the "Largest ZIP" setting or the host's own maximum
    /// (`UploadBundler`); otherwise each file goes up alone. ZIPs are built in the temporary
    /// folder one at a time and deleted after their upload, whether it worked or not. The
    /// result has one link per ZIP or lone file. An upload that fails is listed in `failures`
    /// and the rest go on; cancelling throws `CancellationError`.
    public static func uploadBatch(
        _ items: [UploadItem],
        settings: UploadSettings,
        context: UploadContext? = nil,
        history: UploadHistoryStore? = .shared,
        events: (@Sendable (UploadBatchEvent) -> Void)? = nil
    ) async throws -> UploadBatchOutcome {
        try await uploadBatch(items, settings: settings, context: context, history: history, limitBytes: nil, events: events)
    }

    static func uploadBatch(
        _ items: [UploadItem],
        settings: UploadSettings,
        context: UploadContext?,
        history: UploadHistoryStore?,
        limitBytes: Int64?,
        events: (@Sendable (UploadBatchEvent) -> Void)?
    ) async throws -> UploadBatchOutcome {
        var context = context ?? UploadContext(settings: settings)
        context.settings = settings
        let bundles = planBatch(items, settings: settings, context: context, limitBytes: limitBytes)
        let zipCount = bundles.filter { $0.isZip }.count
        var zipNumber = 0
        var outcome = UploadBatchOutcome()
        for (offset, bundle) in bundles.enumerated() {
            try Task.checkCancellation()
            let name = bundle.displayName
            events?(.starting(index: offset + 1, of: bundles.count, name: name))
            var staged: URL?
            defer { if let staged { try? FileManager.default.removeItem(at: staged) } }
            do {
                var payload = bundle.items[0].file
                var contents: [UploadedEntry]?
                if bundle.isZip {
                    zipNumber += 1
                    events?(.preparing(zip: zipNumber, of: zipCount, name: name))
                    payload = try UploadBundler.writeZip(bundle, in: context.temporaryDirectory)
                    staged = payload.deletingLastPathComponent()
                    contents = bundle.items.map { UploadedEntry(fileName: $0.fileName, title: $0.title) }
                }
                let result = try await upload(
                    payload, settings: settings, context: context, history: history,
                    nameID: bundle.isZip ? nil : bundle.items[0].nameID, contents: contents,
                    events: { event in events?(.host(event)) }
                )
                outcome.uploads.append(result)
                events?(.uploaded(result))
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                outcome.failures.append(UploadBatchFailure(name: name, message: error.localizedDescription))
                events?(.failed(name: name, message: error.localizedDescription))
            }
        }
        return outcome
    }
}
