import Foundation

/// Persisted list of finished uploads, newest first, for the Uploads screen. Expired entries
/// stay until `pruneExpired` so the UI can show them greyed out.
public final class UploadHistoryStore: @unchecked Sendable {
    public static let didChange = Notification.Name("NpiaUploadHistoryDidChange")

    /// `Application Support/upload-history.json`.
    public static let shared: UploadHistoryStore = {
        let base = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true))
            ?? FileManager.default.temporaryDirectory
        return UploadHistoryStore(fileURL: base.appendingPathComponent("upload-history.json"))
    }()

    public let fileURL: URL
    public let limit: Int
    private let lock = NSLock()
    private var entries: [UploadResult]

    public init(fileURL: URL, limit: Int = 500) {
        self.fileURL = fileURL
        self.limit = limit
        self.entries = (try? Data(contentsOf: fileURL)).flatMap { try? Self.decoder.decode([UploadResult].self, from: $0) } ?? []
    }

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }()

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    public var all: [UploadResult] {
        lock.lock(); defer { lock.unlock() }
        return entries
    }

    /// Entries whose links should still work.
    public func active(at date: Date = Date()) -> [UploadResult] {
        all.filter { !$0.isExpired(at: date) }
    }

    public func add(_ result: UploadResult) {
        mutate { entries in
            entries.removeAll { $0.link == result.link }
            entries.insert(result, at: 0)
            if entries.count > limit { entries.removeLast(entries.count - limit) }
        }
    }

    public func remove(_ result: UploadResult) {
        mutate { entries in entries.removeAll { $0.link == result.link } }
    }

    public func pruneExpired(at date: Date = Date()) {
        mutate { entries in entries.removeAll { $0.isExpired(at: date) } }
    }

    public func clear() {
        mutate { entries in entries.removeAll() }
    }

    private func mutate(_ change: (inout [UploadResult]) -> Void) {
        lock.lock()
        change(&entries)
        do {
            try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Self.encoder.encode(entries).write(to: fileURL, options: .atomic)
        } catch {
            // History is a convenience; a failed write must not fail the upload.
        }
        lock.unlock()
        NotificationCenter.default.post(name: Self.didChange, object: self)
    }
}
