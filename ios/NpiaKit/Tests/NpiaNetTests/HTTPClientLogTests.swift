import XCTest
import NpiaCore
@testable import NpiaNet

/// Collects what the client writes to the job log.
private final class LineBox: JobLogging, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [(LogLevel, String)] = []

    func log(_ level: LogLevel, _ message: String) {
        lock.lock(); values.append((level, message)); lock.unlock()
    }

    var all: [(level: LogLevel, message: String)] {
        lock.lock(); defer { lock.unlock() }
        return values.map { (level: $0.0, message: $0.1) }
    }
}

/// Answers from a list of replies, one per request; no test reaches the network.
private final class ScriptedProtocol: URLProtocol {
    enum Reply {
        case status(Int)
        case failure(URLError.Code)
    }

    private static let lock = NSLock()
    private static var replies: [Reply] = []

    static func script(_ values: [Reply]) {
        lock.lock(); replies = values; lock.unlock()
    }

    private static func next() -> Reply {
        lock.lock(); defer { lock.unlock() }
        return replies.isEmpty ? .status(200) : replies.removeFirst()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        switch Self.next() {
        case .status(let code):
            let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data("body".utf8))
            client?.urlProtocolDidFinishLoading(self)
        case .failure(let code):
            client?.urlProtocol(self, didFailWithError: URLError(code))
        }
    }

    override func stopLoading() {}
}

final class HTTPClientLogTests: XCTestCase {
    private func client(retries: Int, log: LineBox) -> URLSessionHTTPClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ScriptedProtocol.self]
        return URLSessionHTTPClient(session: URLSession(configuration: config), maxRetries: retries, logger: log)
    }

    func testARetryIsLoggedWithTheReasonAndTheAttempt() async throws {
        ScriptedProtocol.script([.status(503), .status(200)])
        let log = LineBox()
        let response = try await client(retries: 1, log: log).get(URL(string: "https://example.test/novel/1/list?key=secret")!)

        XCTAssertEqual(response.status, 200)
        let lines = log.all
        XCTAssertEqual(lines.count, 1)
        XCTAssertEqual(lines[0].level, .warning)
        XCTAssertTrue(lines[0].message.hasPrefix("HTTP 503 for GET example.test/novel/1/list; retry 1 of 1 in "), lines[0].message)
        XCTAssertFalse(lines[0].message.contains("secret"), "the query string is never logged")
    }

    func testAnErrorAnswerThatKeepsComingIsAWarning() async throws {
        ScriptedProtocol.script([.status(500), .status(500)])
        let log = LineBox()
        let response = try await client(retries: 1, log: log).get(URL(string: "https://example.test/a")!)

        XCTAssertEqual(response.status, 500)
        let lines = log.all
        XCTAssertEqual(lines.count, 2)
        XCTAssertEqual(lines[1].level, .warning)
        XCTAssertEqual(lines[1].message, "HTTP 500 for GET example.test/a after 1 retries")
    }

    func testAMissingPageIsADetailLine() async throws {
        ScriptedProtocol.script([.status(404)])
        let log = LineBox()
        let response = try await client(retries: 3, log: log).get(URL(string: "https://example.test/missing.jpg")!)

        XCTAssertEqual(response.status, 404)
        let lines = log.all
        XCTAssertEqual(lines.count, 1)
        XCTAssertEqual(lines[0].level, .debug, "an expected 404 stays out of the default log view")
        XCTAssertEqual(lines[0].message, "HTTP 404 for GET example.test/missing.jpg")
    }

    func testAnExpectedErrorStatusIsNotLogged() async throws {
        ScriptedProtocol.script([.status(417)])
        let log = LineBox()
        let response = try await client(retries: 0, log: log).send(HTTPRequest(url: URL(string: "https://example.test/sig")!, quietStatuses: [417]))
        XCTAssertEqual(response.status, 417)
        XCTAssertTrue(log.all.isEmpty)
    }

    func testARefusedPlainHttpAddressIsAskedForOverHttps() async throws {
        ScriptedProtocol.script([.failure(.appTransportSecurityRequiresSecureConnection), .status(200)])
        let log = LineBox()
        let response = try await client(retries: 3, log: log).get(URL(string: "http://rs.example.test/cover.jpg?x=1")!)
        XCTAssertEqual(response.status, 200)
        XCTAssertEqual(response.url.absoluteString, "https://rs.example.test/cover.jpg?x=1")
        XCTAssertTrue(log.all.isEmpty)
    }

    func testAFailedConnectionIsLoggedAndStillThrown() async throws {
        ScriptedProtocol.script([.failure(.cannotFindHost)])
        let log = LineBox()
        do {
            _ = try await client(retries: 3, log: log).get(URL(string: "https://example.test/x")!)
            XCTFail("expected an error")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .cannotFindHost)
        }
        let lines = log.all
        XCTAssertEqual(lines.count, 1)
        XCTAssertEqual(lines[0].level, .warning)
        XCTAssertTrue(lines[0].message.hasPrefix("GET example.test/x failed: "), lines[0].message)
    }

    func testASuccessfulRequestWritesNothing() async throws {
        ScriptedProtocol.script([.status(200)])
        let log = LineBox()
        _ = try await client(retries: 3, log: log).get(URL(string: "https://example.test/ok")!)
        XCTAssertEqual(log.all.count, 0)
    }

    func testTheClientWorksWithoutALogger() async throws {
        ScriptedProtocol.script([.status(200)])
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ScriptedProtocol.self]
        let response = try await URLSessionHTTPClient(session: URLSession(configuration: config), maxRetries: 0).get(URL(string: "https://example.test/ok")!)
        XCTAssertEqual(response.status, 200)
    }
}
