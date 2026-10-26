import Foundation
import NpiaCore
@testable import NpiaSources

/// Canned HTTP replies keyed by a URL substring; the longest matching key wins.
final class RoutedHTTP: HTTPFetching, @unchecked Sendable {
    struct Reply {
        var status = 200
        var body: Data
        var headers: [String: String] = [:]
        /// Final URL after redirects; defaults to the request URL.
        var finalURL: URL?
    }

    private var routes: [String: Reply] = [:]
    private(set) var requests: [HTTPRequest] = []
    private let lock = NSLock()

    init(_ routes: [String: Reply] = [:]) {
        self.routes = routes
    }

    func on(_ fragment: String, _ text: String, status: Int = 200, headers: [String: String] = [:], finalURL: URL? = nil) {
        lock.lock(); defer { lock.unlock() }
        routes[fragment] = Reply(status: status, body: Data(text.utf8), headers: headers, finalURL: finalURL)
    }

    func on(_ fragment: String, json: Any) {
        let data = try! JSONSerialization.data(withJSONObject: json)
        lock.lock(); defer { lock.unlock() }
        routes[fragment] = Reply(body: data)
    }

    func on(_ fragment: String, data: Data) {
        lock.lock(); defer { lock.unlock() }
        routes[fragment] = Reply(body: data)
    }

    func requested(_ fragment: String) -> [HTTPRequest] {
        lock.lock(); defer { lock.unlock() }
        return requests.filter { $0.url.absoluteString.contains(fragment) }
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        respond(request)
    }

    private func respond(_ request: HTTPRequest) -> HTTPResponse {
        lock.lock(); defer { lock.unlock() }
        requests.append(request)
        let key = routes.keys.filter { request.url.absoluteString.contains($0) }.max { $0.count < $1.count }
        let reply = key.flatMap { routes[$0] }
        guard let reply else {
            return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
        }
        return HTTPResponse(url: reply.finalURL ?? request.url, status: reply.status, headers: reply.headers, body: reply.body)
    }
}

final class CollectingLogger: JobLogging, @unchecked Sendable {
    private(set) var lines: [String] = []
    private let lock = NSLock()
    func log(_ level: LogLevel, _ message: String) {
        lock.lock(); lines.append(message); lock.unlock()
    }
}

func makeContext(_ http: RoutedHTTP, secrets: InMemorySecretStore = InMemorySecretStore(), settings: AppSettings = AppSettings()) -> SourceContext {
    SourceContext(http: http, web: StubWeb(), secrets: secrets, settings: settings)
}

func content(_ result: ChapterResult, file: StaticString = #filePath, line: UInt = #line) -> ChapterContent? {
    if case .content(let value) = result { return value }
    return nil
}

func isRefused(_ result: ChapterResult) -> Bool {
    if case .refused = result { return true }
    return false
}
