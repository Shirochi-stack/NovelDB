import Foundation
import XCTest
import NpiaCore
@testable import NpiaUpload

/// Answers every request from `StubURLProtocol.handler` and records what was sent. No test
/// touches a live host.
final class StubURLProtocol: URLProtocol {
    struct Reply {
        var status: Int = 200
        var headers: [String: String] = [:]
        var body: Data = Data()

        static func text(_ text: String, status: Int = 200) -> Reply {
            Reply(status: status, headers: ["Content-Type": "text/plain"], body: Data(text.utf8))
        }

        static func json(_ text: String, status: Int = 200, headers: [String: String] = [:]) -> Reply {
            Reply(status: status, headers: headers.merging(["Content-Type": "application/json"]) { a, _ in a }, body: Data(text.utf8))
        }
    }

    struct Seen {
        var request: URLRequest
        /// nil when Foundation does not expose an upload task's body to the protocol.
        var body: Data?
    }

    private static let lock = NSLock()
    private static var _handler: ((URLRequest) throws -> Reply)?
    private static var _seen: [Seen] = []

    static var handler: ((URLRequest) throws -> Reply)? {
        get { lock.lock(); defer { lock.unlock() }; return _handler }
        set { lock.lock(); _handler = newValue; _seen = []; lock.unlock() }
    }

    static var seen: [Seen] {
        lock.lock(); defer { lock.unlock() }
        return _seen
    }

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: config)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            body = Self.read(stream)
        }
        Self.lock.lock()
        Self._seen.append(Seen(request: request, body: body))
        let handler = Self._handler
        Self.lock.unlock()

        do {
            guard let handler else { throw URLError(.unsupportedURL) }
            let reply = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: reply.body)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    private static func read(_ stream: InputStream) -> Data {
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}

class UploadTestCase: XCTestCase {
    var directory: URL!

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("npia-upload-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        StubURLProtocol.handler = nil
    }

    override func tearDownWithError() throws {
        StubURLProtocol.handler = nil
        try? FileManager.default.removeItem(at: directory)
    }

    func makeFile(_ name: String = "book.epub", bytes: Int = 1_000) throws -> URL {
        let url = directory.appendingPathComponent(name)
        try Data((0..<bytes).map { UInt8($0 % 251) }).write(to: url)
        return url
    }

    let fixedNow = Date(timeIntervalSince1970: 1_800_000_000)

    func context(secrets: [SecretKey: String] = [:], settings: UploadSettings = UploadSettings(), google: GoogleOAuthConfig? = nil) -> UploadContext {
        let now = fixedNow
        return UploadContext(
            session: StubURLProtocol.session(),
            secrets: InMemorySecretStore(secrets),
            settings: settings,
            googleOAuth: google,
            temporaryDirectory: directory,
            now: { now }
        )
    }
}

extension URLRequest {
    var query: [String: String] {
        var values: [String: String] = [:]
        for item in URLComponents(url: url!, resolvingAgainstBaseURL: false)?.queryItems ?? [] {
            values[item.name] = item.value
        }
        return values
    }
}
