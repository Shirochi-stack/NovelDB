import Foundation
import NpiaCore

private let day: TimeInterval = 86_400

/// Returns the first line of a plain-text reply when it is an http(s) URL.
private func plainLink(_ reply: HTTPReply, host: String) throws -> URL {
    try reply.requireSuccess(host)
    let line = reply.text.components(separatedBy: .newlines).first ?? ""
    guard line.hasPrefix("http"), let url = URL(string: line) else {
        throw UploadError.rejected(host: host, message: reply.snippet)
    }
    return url
}

private func https(_ url: URL) -> URL {
    guard url.scheme == "http", var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
    components.scheme = "https"
    return components.url ?? url
}

// MARK: - temp.sh

/// `curl -F file=@x https://temp.sh/upload`; 4 GB, files expire after 3 days.
public struct TempShUploader: Uploader {
    public let id = "tempsh"
    public let displayName = "temp.sh"
    public let maxBytes: Int64? = 4_000_000_000
    public let retention: TimeInterval? = 3 * day
    public let requiresAccount = false
    static let endpoint = URL(string: "https://temp.sh/upload")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        let reply = try await context.postMultipart(Self.endpoint, form: MultipartForm(fileField: "file", file: file), progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        try plainLink(reply, host: "temp.sh")
    }
}

// MARK: - Gofile

/// Gofile's global upload endpoint (the `getServer` lookup bot.py used is retired). Guest
/// uploads need no token; inactive guest content is removed after about 10 days.
public struct GofileUploader: Uploader {
    public let id = "gofile"
    public let displayName = "Gofile"
    public let maxBytes: Int64? = nil
    public let retention: TimeInterval? = 10 * day
    public let requiresAccount = false
    static let endpoint = URL(string: "https://upload.gofile.io/uploadfile")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        var headers: [String: String] = [:]
        if let token = context.secrets.string(for: .gofileToken), !token.isEmpty {
            headers["Authorization"] = "Bearer \(token)"
        }
        let reply = try await context.postMultipart(Self.endpoint, form: MultipartForm(fileField: "file", file: file), headers: headers, progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        guard let json = reply.json() as? [String: Any] else {
            try reply.requireSuccess("Gofile")
            throw UploadError.rejected(host: "Gofile", message: reply.snippet)
        }
        guard json["status"] as? String == "ok",
              let data = json["data"] as? [String: Any],
              let page = data["downloadPage"] as? String, let url = URL(string: page) else {
            throw UploadError.rejected(host: "Gofile", message: (json["status"] as? String) ?? reply.snippet)
        }
        return url
    }
}

// MARK: - Catbox

/// catbox.moe user API; 200 MB per file, kept permanently.
public struct CatboxUploader: Uploader {
    public let id = "catbox"
    public let displayName = "Catbox"
    public let maxBytes: Int64? = 200_000_000
    public let retention: TimeInterval? = nil
    public let requiresAccount = false
    static let endpoint = URL(string: "https://catbox.moe/user/api.php")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        var fields = [(name: "reqtype", value: "fileupload")]
        if let hash = context.secrets.string(for: .catboxUserHash), !hash.isEmpty {
            fields.append((name: "userhash", value: hash))
        }
        let form = MultipartForm(fileField: "fileToUpload", file: file, fields: fields)
        let reply = try await context.postMultipart(Self.endpoint, form: form, progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        try plainLink(reply, host: "Catbox")
    }
}

// MARK: - Pixeldrain

/// Pixeldrain no longer accepts anonymous uploads: the API key (sent as the Basic-auth
/// password) lives in `SecretStore` under `.pixeldrainAPIKey`. The per-file limit depends on
/// the account plan; free files are removed 90 days after the last view.
public struct PixeldrainUploader: Uploader {
    public let id = "pixeldrain"
    public let displayName = "Pixeldrain"
    public let maxBytes: Int64? = nil
    public let retention: TimeInterval? = 90 * day
    public let requiresAccount = true
    static let base = URL(string: "https://pixeldrain.com/api/file/")!

    public init() {}

    public func isReady(_ context: UploadContext) -> Bool {
        !(context.secrets.string(for: .pixeldrainAPIKey) ?? "").isEmpty
    }

    static func request(for file: URL, apiKey: String, size: Int64) -> URLRequest {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/?#;:")
        let name = file.lastPathComponent.addingPercentEncoding(withAllowedCharacters: allowed) ?? "file"
        var request = URLRequest(url: URL(string: base.absoluteString + name) ?? base.appendingPathComponent("file"))
        request.httpMethod = "PUT"
        request.timeoutInterval = 300
        let credentials = Data(":\(apiKey)".utf8).base64EncodedString()
        request.setValue("Basic \(credentials)", forHTTPHeaderField: "Authorization")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(String(size), forHTTPHeaderField: "Content-Length")
        return request
    }

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        guard let key = context.secrets.string(for: .pixeldrainAPIKey), !key.isEmpty else {
            throw UploadError.notSignedIn(displayName)
        }
        let size = try fileSize(file)
        let reply = try await context.send(Self.request(for: file, apiKey: key, size: size), fromFile: file, total: size, progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        let json = reply.json() as? [String: Any]
        guard reply.isSuccess, let id = json?["id"] as? String, !id.isEmpty else {
            let message = (json?["message"] as? String) ?? (json?["value"] as? String) ?? reply.snippet
            if reply.status == 401 { throw UploadError.notSignedIn("Pixeldrain") }
            throw UploadError.rejected(host: "Pixeldrain", message: message)
        }
        return URL(string: "https://pixeldrain.com/u/\(id)")!
    }
}

// MARK: - tmpfiles

/// tmpfiles.org API; 100 MB, `expire` in seconds up to 48 hours (default is one hour).
public struct TmpfilesUploader: Uploader {
    public let id = "tmpfiles"
    public let displayName = "tmpfiles"
    public let maxBytes: Int64? = 100_000_000
    public let retention: TimeInterval? = 2 * day
    public let requiresAccount = false
    static let endpoint = URL(string: "https://tmpfiles.org/api/v1/upload")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        let form = MultipartForm(fileField: "file", file: file, fields: [(name: "expire", value: String(Int(2 * day)))])
        let reply = try await context.postMultipart(Self.endpoint, form: form, progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        try reply.requireSuccess("tmpfiles")
        let json = reply.json() as? [String: Any]
        if let data = json?["data"] as? [String: Any], let link = data["url"] as? String, let url = URL(string: link) {
            return https(url)
        }
        return https(try plainLink(reply, host: "tmpfiles"))
    }
}

// MARK: - KrakenFiles

/// KrakenFiles anonymous uploads: ask for a server, then post `files[]` to its gallery
/// uploader. 1 GB per file; files with no downloads for 30 days are removed.
public struct KrakenFilesUploader: Uploader {
    public let id = "krakenfiles"
    public let displayName = "KrakenFiles"
    public let maxBytes: Int64? = 1_000_000_000
    public let retention: TimeInterval? = 30 * day
    public let requiresAccount = false
    static let serverLookup = URL(string: "https://krakenfiles.com/api/server/available")!
    static let site = URL(string: "https://krakenfiles.com")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        var lookup = URLRequest(url: Self.serverLookup)
        lookup.setValue("application/json", forHTTPHeaderField: "Accept")
        let endpoint = try Self.uploadEndpoint(from: try await context.send(lookup))
        let reply = try await context.postMultipart(endpoint, form: MultipartForm(fileField: "files[]", file: file), progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    /// `{"data": {"url": "https://sN.krakenfiles.com/_uploader/upload/..."}}` → `https://sN.krakenfiles.com/_uploader/gallery/upload`.
    static func uploadEndpoint(from reply: HTTPReply) throws -> URL {
        try reply.requireSuccess("KrakenFiles")
        guard let json = reply.json() as? [String: Any],
              let data = json["data"] as? [String: Any],
              let server = (data["url"] as? String).flatMap(URL.init(string:)),
              let scheme = server.scheme, let host = server.host else {
            throw UploadError.rejected(host: "KrakenFiles", message: "no upload server: \(reply.snippet)")
        }
        return URL(string: "\(scheme)://\(host)/_uploader/gallery/upload")!
    }

    static func parse(_ reply: HTTPReply) throws -> URL {
        try reply.requireSuccess("KrakenFiles")
        let json = reply.json() as? [String: Any]
        guard let entry = (json?["files"] as? [[String: Any]])?.first else {
            throw UploadError.rejected(host: "KrakenFiles", message: reply.snippet)
        }
        if let error = entry["error"] as? String, !error.isEmpty {
            throw UploadError.rejected(host: "KrakenFiles", message: error)
        }
        guard let path = entry["url"] as? String, let url = URL(string: path, relativeTo: site)?.absoluteURL else {
            throw UploadError.rejected(host: "KrakenFiles", message: reply.snippet)
        }
        return url
    }
}

// MARK: - Send.now (formerly send.cm)

/// send.cm became send.now, an XFileSharing site: the home page names the upload server,
/// which takes `file_0` anonymously. 100 GB per file, kept 15 days after the last download.
public struct SendNowUploader: Uploader {
    public let id = "sendnow"
    public let displayName = "Send.now"
    public let maxBytes: Int64? = 100_000_000_000
    public let retention: TimeInterval? = 15 * day
    public let requiresAccount = false
    static let home = URL(string: "https://send.now/")!

    public init() {}

    public func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
        var page = URLRequest(url: Self.home)
        page.setValue("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", forHTTPHeaderField: "User-Agent")
        let endpoint = try Self.uploadEndpoint(fromHomePage: try await context.send(page))
        let reply = try await context.postMultipart(endpoint, form: MultipartForm(fileField: "file_0", file: file), progress: progress)
        return result(for: file, link: try Self.parse(reply), context: context)
    }

    static func uploadEndpoint(fromHomePage reply: HTTPReply) throws -> URL {
        try reply.requireSuccess("Send.now")
        let html = String(decoding: reply.body, as: UTF8.self)
        let patterns = [
            #"https://([A-Za-z0-9-]+)\.send\.now/cgi-bin/upload\.cgi"#,
            #"https://([A-Za-z0-9]{4,})\.send\.now"#,
        ]
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: html, range: NSRange(html.startIndex..., in: html)),
                  let range = Range(match.range(at: 1), in: html) else { continue }
            let server = String(html[range])
            return URL(string: "https://\(server).send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon")!
        }
        throw UploadError.rejected(host: "Send.now", message: "upload server not found on the home page")
    }

    /// `[{"file_code": "abc123", "file_status": "OK"}]`
    static func parse(_ reply: HTTPReply) throws -> URL {
        try reply.requireSuccess("Send.now")
        guard let entry = (reply.json() as? [[String: Any]])?.first else {
            throw UploadError.rejected(host: "Send.now", message: reply.snippet)
        }
        guard let code = entry["file_code"] as? String, !code.isEmpty, code != "undef",
              (entry["file_status"] as? String ?? "OK").uppercased() == "OK" else {
            throw UploadError.rejected(host: "Send.now", message: (entry["file_status"] as? String) ?? reply.snippet)
        }
        return URL(string: "https://send.now/\(code)")!
    }
}
