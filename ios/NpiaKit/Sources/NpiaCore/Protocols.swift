import Foundation

// MARK: - HTTP

public struct HTTPRequest: Sendable {
    public var url: URL
    public var method: String
    public var headers: [String: String]
    public var body: Data?
    public var timeout: TimeInterval

    public init(url: URL, method: String = "GET", headers: [String: String] = [:], body: Data? = nil, timeout: TimeInterval = 30) {
        self.url = url
        self.method = method
        self.headers = headers
        self.body = body
        self.timeout = timeout
    }
}

public struct HTTPResponse: Sendable {
    public var url: URL
    public var status: Int
    public var headers: [String: String]
    public var body: Data

    public init(url: URL, status: Int, headers: [String: String], body: Data) {
        self.url = url
        self.status = status
        self.headers = headers
        self.body = body
    }

    public var text: String { String(decoding: body, as: UTF8.self) }

    public func json<T: Decodable>(_ type: T.Type) throws -> T {
        try JSONDecoder().decode(type, from: body)
    }
}

public enum HTTPError: Error, Equatable {
    case status(Int, URL)
    case invalidResponse(URL)
}

/// Implemented by `NpiaNet.URLSessionHTTPClient`; tests use stubs.
public protocol HTTPFetching: Sendable {
    func send(_ request: HTTPRequest) async throws -> HTTPResponse
}

public extension HTTPFetching {
    func get(_ url: URL, headers: [String: String] = [:]) async throws -> HTTPResponse {
        try await send(HTTPRequest(url: url, headers: headers))
    }

    func post(_ url: URL, body: Data, headers: [String: String] = [:]) async throws -> HTTPResponse {
        try await send(HTTPRequest(url: url, method: "POST", headers: headers, body: body))
    }
}

// MARK: - Web engine

public enum WebLoadState: Sendable {
    case domContentLoaded
    case load
}

/// One browser tab. Replaces a Playwright `Page` from the desktop scraper.
public protocol WebPageDriving: Sendable {
    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws
    /// Runs an async JavaScript function body and returns its result encoded as JSON.
    /// `return document.title` yields `"\"...\""`; decode with `JSONDecoder`.
    func evaluateJSON(_ functionBody: String) async throws -> Data
    /// Registers a script that runs at document start in every frame (fetch/XHR hooks).
    func addInitScript(_ source: String) async
    /// Lets page scripts call into Swift: `await window.webkit.messageHandlers[name].postMessage(text)`
    /// sends a string and resolves with the string `handler` returns. Add it before loading the
    /// page; the page's cookies are copied to `HTTPCookieStorage.shared` before each call, so the
    /// handler can make requests with them. Only the top frame is answered; frames inside the page
    /// are refused. Pages that cannot message ignore this.
    func addMessageHandler(_ name: String, handler: @escaping @Sendable (String) async -> String) async
    /// Like `addInitScript`, but only for the page's own document, not for frames inside it. Sites
    /// that fill a page with ads should not run a large script in every ad frame.
    func addMainFrameInitScript(_ source: String) async
    /// Presents the page as a desktop browser: `userAgent` (nil keeps the web view's own) and a layout
    /// viewport of `width` x `height` CSS pixels. Call it before `load`; it applies to every later load of
    /// this page. Sites that serve a phone layout (or no reader at all) to a phone need it. The default
    /// ignores the request.
    func emulateDesktop(userAgent: String?, width: Int, height: Int) async
    func currentURL() async -> URL?
    func html() async throws -> String
    func close() async
}

public extension WebPageDriving {
    func addMessageHandler(_ name: String, handler: @escaping @Sendable (String) async -> String) async {}

    func addMainFrameInitScript(_ source: String) async {
        await addInitScript(source)
    }

    func emulateDesktop(userAgent: String?, width: Int, height: Int) async {}
}

public protocol WebEngineProviding: Sendable {
    /// Pages from the same profile share cookies and storage with each other and with `HTTPFetching`.
    func newPage(profile: String) async throws -> any WebPageDriving
    /// Presents a visible web view so the user can sign in; returns when they finish.
    func presentLogin(url: URL, profile: String) async throws
    /// The cookies the engine's pages share with the HTTP client, for sign-in status and the saved copy
    /// of a login. The default reads `HTTPCookieStorage.shared`; engines whose web views keep cookies
    /// elsewhere answer with a store that reads those too.
    var sessionCookies: any SessionCookieAccess { get }
}

public extension WebEngineProviding {
    var sessionCookies: any SessionCookieAccess { HTTPCookieStorageAccess() }
}

public enum WebEngineError: Error, Equatable {
    case timeout(URL)
    case navigationFailed(String)
    case scriptFailed(String)
    case unavailable
}

// MARK: - Logging

public enum LogLevel: String, Codable, Sendable {
    case debug, info, warning, error
}

public protocol JobLogging: Sendable {
    func log(_ level: LogLevel, _ message: String)
}

public struct NullLogger: JobLogging {
    public init() {}
    public func log(_ level: LogLevel, _ message: String) {}
}
