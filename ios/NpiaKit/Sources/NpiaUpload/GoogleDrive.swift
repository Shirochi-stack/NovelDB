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
        if (try file.resourceValues(forKeys: [.isDirectoryKey])).isDirectory == true {
            return try await uploadFolder(file, context: context, auth: auth, progress: progress)
        }
        let metadata = try await uploadFile(file, parent: context.settings.googleDriveFolderID, context: context, auth: auth, progress: progress)
        guard let fileID = metadata["id"] as? String else {
            throw UploadError.rejected(host: displayName, message: "no file id")
        }
        let shared = try await authorized(context: context, auth: auth) { Self.permissionRequest(fileID: fileID, token: $0) }
        try shared.requireSuccess(displayName)
        let link = (metadata["webViewLink"] as? String).flatMap(URL.init(string:))
            ?? URL(string: "https://drive.google.com/file/d/\(fileID)/view?usp=sharing")!
        return result(for: file, link: link, context: context)
    }

    private func authorized(context: UploadContext, auth: GoogleDriveAuth, request: (String) -> URLRequest) async throws -> HTTPReply {
        try Task.checkCancellation()
        let token = try await auth.accessToken()
        let reply = try await context.send(request(token))
        if reply.status == 401 {
            let refreshed = try await auth.accessToken(forceRefresh: true)
            return try await context.send(request(refreshed))
        }
        return reply
    }

    private func uploadFile(_ file: URL, parent: String?, context: UploadContext, auth: GoogleDriveAuth, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> [String: Any] {
        let size = try fileSize(file)
        try check(file)
        let started = try await authorized(context: context, auth: auth) { Self.startRequest(file: file, size: size, folderID: parent, token: $0) }
        try started.requireSuccess(displayName)
        guard let location = started.header("Location"), let sessionURL = URL(string: location) else {
            throw UploadError.rejected(host: displayName, message: "no upload session")
        }

        let metadata = try await sendChunks(file, size: size, to: sessionURL, context: context, auth: auth, progress: progress)
        guard metadata["id"] is String else {
            throw UploadError.rejected(host: displayName, message: "no file id")
        }
        return metadata
    }

    private func folderEntries(_ file: URL) throws -> [URL] {
        try Task.checkCancellation()
        let values = try file.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        if values.isSymbolicLink == true { throw UploadError.rejected(host: displayName, message: "folder contains a symbolic link") }
        var entries = [file]
        if values.isDirectory == true {
            for child in try FileManager.default.contentsOfDirectory(at: file, includingPropertiesForKeys: nil).sorted(by: { $0.path < $1.path }) {
                entries += try folderEntries(child)
            }
        } else {
            try check(file)
        }
        return entries
    }

    private func uploadFolder(_ file: URL, context: UploadContext, auth: GoogleDriveAuth, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        let entries = try folderEntries(file)
        let files = try entries.filter { try $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory != true }
        let total = try files.reduce(Int64(0)) { try $0 + fileSize($1) }
        var folders: [String: String] = [:]
        var sent: Int64 = 0
        do {
            for entry in entries {
                let parent = entry == file ? context.settings.googleDriveFolderID : folders[entry.deletingLastPathComponent().path]
                if try entry.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true {
                    var metadata: [String: Any] = ["name": uploadName(entry), "mimeType": "application/vnd.google-apps.folder"]
                    if let parent, !parent.isEmpty { metadata["parents"] = [parent] }
                    let reply = try await authorized(context: context, auth: auth) { Self.metadataRequest(metadata, token: $0) }
                    try reply.requireSuccess(displayName)
                    guard let id = (reply.json() as? [String: Any])?["id"] as? String else {
                        throw UploadError.rejected(host: displayName, message: "no folder id")
                    }
                    folders[entry.path] = id
                } else {
                    let base = sent
                    _ = try await uploadFile(entry, parent: parent, context: context, auth: auth) { value in
                        progress?(UploadProgress(sentBytes: base + value.sentBytes, totalBytes: total))
                    }
                    sent += try fileSize(entry)
                }
            }
            let reply = try await authorized(context: context, auth: auth) { Self.permissionRequest(fileID: folders[file.path]!, token: $0) }
            try reply.requireSuccess(displayName)
        } catch {
            if let id = folders[file.path] {
                await Task.detached {
                    _ = try? await authorized(context: context, auth: auth) { Self.metadataRequest(["trashed": true], fileID: id, token: $0) }
                }.value
            }
            throw error
        }
        progress?(UploadProgress(sentBytes: total, totalBytes: total))
        return result(for: file, link: URL(string: "https://drive.google.com/drive/folders/\(folders[file.path]!)?usp=sharing")!, context: context)
    }

    static func metadataRequest(_ metadata: [String: Any], fileID: String? = nil, token: String) -> URLRequest {
        let url = fileID.map { filesEndpoint.appendingPathComponent($0) } ?? filesEndpoint
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "supportsAllDrives", value: "true"), URLQueryItem(name: "fields", value: "id")]
        var request = URLRequest(url: components.url!)
        request.httpMethod = fileID == nil ? "POST" : "PATCH"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json; charset=UTF-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: metadata)
        return request
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
