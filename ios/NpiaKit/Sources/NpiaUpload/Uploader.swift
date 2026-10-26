import Foundation
import NpiaCore

public struct UploadResult: Codable, Sendable, Hashable {
    public var hostID: String
    public var fileName: String
    public var link: URL
    /// Page or token that deletes the upload, when the host returns one.
    public var deleteLink: URL?
    public var expiresAt: Date?
    public var uploadedAt: Date

    public init(hostID: String, fileName: String, link: URL, deleteLink: URL? = nil, expiresAt: Date? = nil, uploadedAt: Date = Date()) {
        self.hostID = hostID
        self.fileName = fileName
        self.link = link
        self.deleteLink = deleteLink
        self.expiresAt = expiresAt
        self.uploadedAt = uploadedAt
    }
}

public struct UploadProgress: Sendable {
    public var sentBytes: Int64
    public var totalBytes: Int64
}

public enum UploadError: Error, Equatable, LocalizedError {
    case notImplemented(String)
    case tooLarge(host: String, limitBytes: Int64)
    case notSignedIn(String)
    case rejected(host: String, message: String)

    public var errorDescription: String? {
        switch self {
        case .notImplemented(let host): return "\(host) upload is not available in this build yet."
        case .tooLarge(let host, let limit): return "\(host) accepts files up to \(ByteCountFormatter.string(fromByteCount: limit, countStyle: .file))."
        case .notSignedIn(let host): return "Sign in to \(host) first."
        case .rejected(let host, let message): return "\(host) rejected the upload: \(message)"
        }
    }
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
    func upload(_ file: URL, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult
}

public extension Uploader {
    func check(_ file: URL) throws {
        guard let maxBytes else { return }
        let size = Int64((try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        if size > maxBytes { throw UploadError.tooLarge(host: displayName, limitBytes: maxBytes) }
    }
}

/// Placeholder until the upload workstream ports the host.
public struct PendingUploader: Uploader {
    public let id: String
    public let displayName: String
    public let maxBytes: Int64?
    public let retention: TimeInterval?
    public let requiresAccount: Bool

    public func upload(_ file: URL, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        throw UploadError.notImplemented(displayName)
    }
}

public enum UploaderCatalog {
    // TODO(upload workstream): replace each placeholder with a real uploader and confirm the
    // current limits; bot.py's gofile code uses a retired server-lookup endpoint.
    public static let all: [any Uploader] = [
        PendingUploader(id: "tempsh", displayName: "temp.sh", maxBytes: 4_000_000_000, retention: 3 * 86_400, requiresAccount: false),
        PendingUploader(id: "gofile", displayName: "Gofile", maxBytes: nil, retention: nil, requiresAccount: false),
        PendingUploader(id: "catbox", displayName: "Catbox", maxBytes: 200_000_000, retention: nil, requiresAccount: false),
        PendingUploader(id: "pixeldrain", displayName: "Pixeldrain", maxBytes: nil, retention: nil, requiresAccount: false),
        PendingUploader(id: "tmpfiles", displayName: "tmpfiles", maxBytes: 100_000_000, retention: 3_600, requiresAccount: false),
        PendingUploader(id: "krakenfiles", displayName: "KrakenFiles", maxBytes: nil, retention: nil, requiresAccount: false),
        PendingUploader(id: "sendcm", displayName: "Send.cm", maxBytes: nil, retention: nil, requiresAccount: false),
        PendingUploader(id: "gdrive", displayName: "Google Drive", maxBytes: nil, retention: nil, requiresAccount: true),
    ]

    public static func uploader(id: String) -> (any Uploader)? {
        all.first { $0.id == id }
    }

    /// Tries each configured host in order and returns the first success.
    public static func upload(_ file: URL, settings: UploadSettings, progress: (@Sendable (UploadProgress) -> Void)? = nil) async throws -> UploadResult {
        var lastError: Error = UploadError.notImplemented("upload")
        for id in settings.hosts {
            guard let uploader = uploader(id: id) else { continue }
            do {
                try uploader.check(file)
                return try await uploader.upload(file, progress: progress)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                lastError = error
            }
        }
        throw lastError
    }
}
