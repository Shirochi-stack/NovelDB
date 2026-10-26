import Foundation

/// Status, headers, and body of one finished request.
struct HTTPReply: Sendable {
    var status: Int
    var headers: [String: String]
    var body: Data

    init(status: Int, headers: [String: String], body: Data) {
        self.status = status
        self.headers = headers
        self.body = body
    }

    init(_ data: Data, _ response: URLResponse) {
        let http = response as? HTTPURLResponse
        var headers: [String: String] = [:]
        for (key, value) in http?.allHeaderFields ?? [:] {
            if let key = key as? String { headers[key.lowercased()] = "\(value)" }
        }
        self.init(status: http?.statusCode ?? 0, headers: headers, body: data)
    }

    func header(_ name: String) -> String? { headers[name.lowercased()] }

    var text: String { String(decoding: body, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) }

    var isSuccess: Bool { (200..<300).contains(status) }

    func json() -> Any? { try? JSONSerialization.jsonObject(with: body) }

    /// Short body excerpt for error messages.
    var snippet: String { String(text.prefix(200)) }

    func requireSuccess(_ host: String) throws {
        guard isSuccess else { throw UploadError.badResponse(host: host, status: status, body: snippet) }
    }
}

/// Forwards `didSendBodyData` to an `UploadProgress` callback. `base` offsets chunked uploads.
final class ProgressRelay: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let base: Int64
    let total: Int64
    let progress: (@Sendable (UploadProgress) -> Void)?

    init(base: Int64 = 0, total: Int64, progress: (@Sendable (UploadProgress) -> Void)?) {
        self.base = base
        self.total = total
        self.progress = progress
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        progress?(UploadProgress(sentBytes: min(base + totalBytesSent, total), totalBytes: total))
    }
}

extension UploadContext {
    func send(_ request: URLRequest) async throws -> HTTPReply {
        let (data, response) = try await session.data(for: request)
        return HTTPReply(data, response)
    }

    /// Streams the request body from `file` on disk.
    func send(_ request: URLRequest, fromFile file: URL, total: Int64, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> HTTPReply {
        let relay = ProgressRelay(total: total, progress: progress)
        let (data, response) = try await session.upload(for: request, fromFile: file, delegate: relay)
        progress?(UploadProgress(sentBytes: total, totalBytes: total))
        return HTTPReply(data, response)
    }

    func send(_ request: URLRequest, body: Data, base: Int64, total: Int64, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> HTTPReply {
        let relay = ProgressRelay(base: base, total: total, progress: progress)
        let (data, response) = try await session.upload(for: request, from: body, delegate: relay)
        return HTTPReply(data, response)
    }

    /// Builds the multipart body as a temp file (copied in 1 MiB chunks, never loaded whole)
    /// and streams it to `url`.
    func postMultipart(
        _ url: URL,
        form: MultipartForm,
        headers: [String: String] = [:],
        progress: (@Sendable (UploadProgress) -> Void)?
    ) async throws -> HTTPReply {
        let body = temporaryDirectory.appendingPathComponent("npia-upload-\(UUID().uuidString).multipart")
        defer { try? FileManager.default.removeItem(at: body) }
        let size = try form.write(to: body)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 300
        request.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        request.setValue(String(size), forHTTPHeaderField: "Content-Length")
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        return try await send(request, fromFile: body, total: size, progress: progress)
    }
}

/// The file's name in NFC. Apple file systems hand back decomposed Hangul and accents,
/// which hosts would otherwise show as broken names.
func uploadName(_ file: URL) -> String {
    file.lastPathComponent.precomposedStringWithCanonicalMapping
}

func fileSize(_ file: URL) throws -> Int64 {
    let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
    return (attributes[.size] as? NSNumber)?.int64Value ?? 0
}

/// `multipart/form-data` with text fields followed by one file part.
struct MultipartForm: Sendable {
    var boundary = "NpiaBoundary-\(UUID().uuidString)"
    var fields: [(name: String, value: String)] = []
    var fileField: String
    var file: URL
    var fileName: String
    var mimeType = "application/octet-stream"

    init(fileField: String, file: URL, fileName: String? = nil, fields: [(name: String, value: String)] = []) {
        self.fileField = fileField
        self.file = file
        self.fileName = fileName ?? uploadName(file)
        self.fields = fields
    }

    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    static func quoted(_ value: String) -> String {
        value.replacingOccurrences(of: "\r", with: "")
            .replacingOccurrences(of: "\n", with: "")
            .replacingOccurrences(of: "\"", with: "%22")
    }

    var head: Data {
        var text = ""
        for field in fields {
            text += "--\(boundary)\r\n"
            text += "Content-Disposition: form-data; name=\"\(Self.quoted(field.name))\"\r\n\r\n"
            text += "\(field.value)\r\n"
        }
        text += "--\(boundary)\r\n"
        text += "Content-Disposition: form-data; name=\"\(Self.quoted(fileField))\"; filename=\"\(Self.quoted(fileName))\"\r\n"
        text += "Content-Type: \(mimeType)\r\n\r\n"
        return Data(text.utf8)
    }

    var tail: Data { Data("\r\n--\(boundary)--\r\n".utf8) }

    /// Writes the full body to `url` and returns its size.
    @discardableResult
    func write(to url: URL, chunkSize: Int = 1 << 20) throws -> Int64 {
        guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
            throw CocoaError(.fileWriteUnknown, userInfo: [NSFilePathErrorKey: url.path])
        }
        let writer = try FileHandle(forWritingTo: url)
        defer { try? writer.close() }
        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }

        let prefix = head
        let suffix = tail
        try writer.write(contentsOf: prefix)
        var written = Int64(prefix.count)
        while true {
            let copied: Int = try autoreleasepool {
                guard let chunk = try reader.read(upToCount: chunkSize), !chunk.isEmpty else { return 0 }
                try writer.write(contentsOf: chunk)
                return chunk.count
            }
            if copied == 0 { break }
            written += Int64(copied)
        }
        try writer.write(contentsOf: suffix)
        return written + Int64(suffix.count)
    }
}

/// Percent-encodes `application/x-www-form-urlencoded` bodies.
func formEncoded(_ pairs: [(String, String)]) -> Data {
    let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    let text = pairs.map { key, value in
        let k = key.addingPercentEncoding(withAllowedCharacters: allowed) ?? key
        let v = value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
        return "\(k)=\(v)"
    }.joined(separator: "&")
    return Data(text.utf8)
}
