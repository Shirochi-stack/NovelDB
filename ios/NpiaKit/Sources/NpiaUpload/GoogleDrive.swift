import Foundation
import NpiaCore

/// Uploads into the user's Drive with the `drive.file` scope (the app only sees files it
/// created), then shares the file as "anyone with the link".
public struct GoogleDriveUploader: Uploader {
    public let id = "gdrive"
    public let displayName = "Google Drive"
    /// Drive's per-file limit; the account quota usually binds first.
    public let maxBytes: Int64? = 5_000_000_000_000
    public let retention: TimeInterval? = nil
    public let requiresAccount = true
    /// 8 MiB; Drive wants chunks in multiples of 256 KiB.
    public var chunkSize = 32 * 256 * 1024
    var maxChunkRetries = 5

    static let uploadEndpoint = URL(string: "https://www.googleapis.com/upload/drive/v3/files")!
    static let filesEndpoint = URL(string: "https://www.googleapis.com/drive/v3/files")!

    public init() {}

    public func isReady(_ context: UploadContext) -> Bool {
        context.googleOAuth != nil && !(context.secrets.string(for: .googleDriveRefreshToken) ?? "").isEmpty
    }

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        guard let config = context.googleOAuth else { throw UploadError.notConfigured("Google Drive client ID") }
        let auth = GoogleDriveAuth(config: config, secrets: context.secrets, session: context.session)
        let size = try fileSize(file)

        let folderID = context.settings.googleDriveFolderID
        var token = try await auth.accessToken()
        var started = try await context.send(Self.startRequest(file: file, size: size, folderID: folderID, token: token))
        if started.status == 401 {
            token = try await auth.accessToken(forceRefresh: true)
            started = try await context.send(Self.startRequest(file: file, size: size, folderID: folderID, token: token))
        }
        try started.requireSuccess(displayName)
        guard let location = started.header("Location"), let sessionURL = URL(string: location) else {
            throw UploadError.rejected(host: displayName, message: "no upload session")
        }

        let metadata = try await sendChunks(file, size: size, to: sessionURL, context: context, auth: auth, progress: progress)
        guard let fileID = metadata["id"] as? String else {
            throw UploadError.rejected(host: displayName, message: "no file id")
        }

        token = try await auth.accessToken()
        let shared = try await context.send(Self.permissionRequest(fileID: fileID, token: token))
        try shared.requireSuccess(displayName)

        let link = (metadata["webViewLink"] as? String).flatMap(URL.init(string:))
            ?? URL(string: "https://drive.google.com/file/d/\(fileID)/view?usp=sharing")!
        return result(for: file, link: link, context: context)
    }

    static func startRequest(file: URL, size: Int64, folderID: String?, token: String) -> URLRequest {
        var components = URLComponents(url: uploadEndpoint, resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "uploadType", value: "resumable"),
            URLQueryItem(name: "supportsAllDrives", value: "true"),
            URLQueryItem(name: "fields", value: "id,name,webViewLink"),
        ]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json; charset=UTF-8", forHTTPHeaderField: "Content-Type")
        request.setValue(mimeType(for: file), forHTTPHeaderField: "X-Upload-Content-Type")
        request.setValue(String(size), forHTTPHeaderField: "X-Upload-Content-Length")
        var metadata: [String: Any] = ["name": uploadName(file)]
        if let folderID = folderID?.trimmingCharacters(in: .whitespaces), !folderID.isEmpty {
            metadata["parents"] = [folderID]
        }
        request.httpBody = try? JSONSerialization.data(withJSONObject: metadata)
        return request
    }

    static func chunkRequest(session: URL, offset: Int64, length: Int, total: Int64, token: String) -> URLRequest {
        var request = URLRequest(url: session)
        request.httpMethod = "PUT"
        request.timeoutInterval = 300
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue(String(length), forHTTPHeaderField: "Content-Length")
        if total > 0 {
            request.setValue("bytes \(offset)-\(offset + Int64(length) - 1)/\(total)", forHTTPHeaderField: "Content-Range")
        }
        return request
    }

    /// Asks how much of an interrupted session Drive already has.
    static func statusRequest(session: URL, total: Int64, token: String) -> URLRequest {
        var request = URLRequest(url: session)
        request.httpMethod = "PUT"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("0", forHTTPHeaderField: "Content-Length")
        request.setValue("bytes */\(total)", forHTTPHeaderField: "Content-Range")
        return request
    }

    static func permissionRequest(fileID: String, token: String) -> URLRequest {
        var components = URLComponents(url: filesEndpoint.appendingPathComponent(fileID).appendingPathComponent("permissions"), resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "supportsAllDrives", value: "true")]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json; charset=UTF-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(#"{"role":"reader","type":"anyone"}"#.utf8)
        return request
    }

    /// Next byte Drive expects after a 308 reply (`Range: bytes=0-N`), or 0 when it has none.
    static func nextOffset(from reply: HTTPReply) -> Int64 {
        guard let range = reply.header("Range"), let last = range.split(separator: "-").last, let end = Int64(last) else { return 0 }
        return end + 1
    }

    static func mimeType(for file: URL) -> String {
        switch file.pathExtension.lowercased() {
        case "epub": return "application/epub+zip"
        case "pdf": return "application/pdf"
        case "txt": return "text/plain"
        case "html", "htm": return "text/html"
        case "zip": return "application/zip"
        default: return "application/octet-stream"
        }
    }

    /// Sends the file in `chunkSize` pieces, holding one chunk in memory at a time. Network
    /// errors and 5xx replies resume from the offset Drive reports.
    func sendChunks(
        _ file: URL,
        size: Int64,
        to sessionURL: URL,
        context: UploadContext,
        auth: GoogleDriveAuth,
        progress: (@Sendable (UploadProgress) -> Void)?
    ) async throws -> [String: Any] {
        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }
        var offset: Int64 = 0
        var failures = 0

        while true {
            try Task.checkCancellation()
            try reader.seek(toOffset: UInt64(offset))
            let chunk = try reader.read(upToCount: chunkSize) ?? Data()
            var reply: HTTPReply?
            do {
                let token = try await auth.accessToken()
                let request = Self.chunkRequest(session: sessionURL, offset: offset, length: chunk.count, total: size, token: token)
                reply = try await context.send(request, body: chunk, base: offset, total: size, progress: progress)
            } catch let error as URLError where error.code != .cancelled {
                reply = nil
            }

            if let reply {
                switch reply.status {
                case 200, 201:
                    progress?(UploadProgress(sentBytes: size, totalBytes: size))
                    return (reply.json() as? [String: Any]) ?? [:]
                case 308:
                    offset = Self.nextOffset(from: reply)
                    failures = 0
                    continue
                case 401:
                    _ = try await auth.accessToken(forceRefresh: true)
                case 404, 410:
                    throw UploadError.rejected(host: displayName, message: "upload session expired")
                case 500...599:
                    break
                default:
                    throw UploadError.badResponse(host: displayName, status: reply.status, body: reply.snippet)
                }
            }

            failures += 1
            if failures > maxChunkRetries {
                throw UploadError.rejected(host: displayName, message: "too many interrupted chunks")
            }
            try await Task.sleep(nanoseconds: UInt64(min(30, 1 << failures)) * 200_000_000)
            let token = try await auth.accessToken()
            let status = try await context.send(Self.statusRequest(session: sessionURL, total: size, token: token))
            switch status.status {
            case 200, 201:
                return (status.json() as? [String: Any]) ?? [:]
            case 308:
                offset = Self.nextOffset(from: status)
            case 404, 410:
                throw UploadError.rejected(host: displayName, message: "upload session expired")
            default:
                continue
            }
        }
    }
}
