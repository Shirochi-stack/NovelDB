import Foundation
import NpiaCore
@testable import NpiaSources

/// A web page that answers calls to `hard-sources.js` entry points with canned JSON. `respond` gets the
/// entry point and its arguments, for example `qdn.pageState()` or `rbooks.discover({...})`, and returns the
/// JSON text (nil means JSON null). A `tasks.start` call runs `respond("<path>(<args>)")` and the matching
/// `tasks.poll` hands the answer back.
final class ScriptedPage: WebPageDriving, @unchecked Sendable {
    let name: String
    let respond: @Sendable (ScriptedPage, String) -> String?

    private let lock = NSLock()
    private var loadedURLs: [URL] = []
    private var calledEntryPoints: [String] = []
    private var scripts: [String] = []
    private var mainFrameScripts: [String] = []
    private var finished: [String: String?] = [:]
    private var ticket = 0
    private var shownURL: URL?
    private var wasClosed = false

    var userAgent: String?
    var viewport: (Int, Int)?
    /// URLs (as text) that fail to load, mapped to what to throw.
    var loadFailures: [String: Error] = [:]
    /// What the page shows after loading a URL; the default is the URL itself.
    var redirect: @Sendable (URL) -> URL = { $0 }
    var titleText = ""

    init(name: String = "page", respond: @escaping @Sendable (ScriptedPage, String) -> String?) {
        self.name = name
        self.respond = respond
    }

    var loads: [URL] { lock.lock(); defer { lock.unlock() }; return loadedURLs }
    var calls: [String] { lock.lock(); defer { lock.unlock() }; return calledEntryPoints }
    var initScripts: [String] { lock.lock(); defer { lock.unlock() }; return scripts }
    var documentStartScripts: [String] { lock.lock(); defer { lock.unlock() }; return mainFrameScripts }
    var closed: Bool { lock.lock(); defer { lock.unlock() }; return wasClosed }
    var url: URL? {
        get { lock.lock(); defer { lock.unlock() }; return shownURL }
        set { lock.lock(); defer { lock.unlock() }; shownURL = newValue }
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {
        lock.lock()
        loadedURLs.append(url)
        shownURL = redirect(url)
        let failure = loadFailures[url.absoluteString]
        lock.unlock()
        if let failure { throw failure }
    }

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        Data(answer(functionBody).utf8)
    }

    private func answer(_ body: String) -> String {
        if body.contains("window.__npiaHard && window.__npiaHard.version") { return "true" }
        if body.hasPrefix("(0, eval)(") { return "true" }
        if body.contains("document.title") { return HardScripts.literal(titleText) }
        if let tasks = Self.match("window\\.__npiaHard\\.tasks\\.(start|poll)\\((.*)\\);\\s*$", in: body) {
            let kind = tasks[0]
            let argument = tasks[1]
            if kind == "start" {
                // `"path", [args]` -> `path(args)`
                let parts = Self.match("^\"([^\"]+)\", (.*)$", in: argument) ?? ["", ""]
                var args = parts[1]
                if args.hasPrefix("[") { args.removeFirst() }
                if args.hasSuffix("]") { args.removeLast() }
                let call = "\(parts[0])(\(args))"
                lock.lock()
                calledEntryPoints.append(call)
                ticket += 1
                let id = "t\(ticket)"
                lock.unlock()
                let reply = respond(self, call)
                lock.lock()
                finished[id] = .some(reply)
                lock.unlock()
                return "{\"ticket\":\"\(id)\"}"
            }
            let id = argument.trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            lock.lock()
            let stored = finished.removeValue(forKey: id)
            lock.unlock()
            let json = (stored ?? nil) ?? "null"
            return "{\"state\":\"done\",\"json\":\(HardScripts.literal(json))}"
        }
        guard let call = Self.match("window\\.__npiaHard\\.(.+?);\\s*$", in: body)?.first else { return "null" }
        lock.lock()
        calledEntryPoints.append(call)
        lock.unlock()
        return respond(self, call) ?? "null"
    }

    private static func match(_ pattern: String, in text: String) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]),
              let found = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (1..<found.numberOfRanges).map { index in
            Range(found.range(at: index), in: text).map { String(text[$0]) } ?? ""
        }
    }

    func addInitScript(_ source: String) async {
        lock.lock(); scripts.append(source); lock.unlock()
    }

    func addMainFrameInitScript(_ source: String) async {
        lock.lock(); mainFrameScripts.append(source); lock.unlock()
    }

    func emulateDesktop(userAgent: String?, width: Int, height: Int) async {
        lock.lock()
        self.userAgent = userAgent
        viewport = (width, height)
        lock.unlock()
    }

    func currentURL() async -> URL? { url }
    func html() async throws -> String { "" }

    func close() async {
        lock.lock(); wasClosed = true; lock.unlock()
    }
}

/// Hands out pages from `make`, remembering them so tests can look at every page a job opened.
final class ScriptedWeb: WebEngineProviding, @unchecked Sendable {
    private let make: @Sendable (Int) -> ScriptedPage
    private let lock = NSLock()
    private var opened: [ScriptedPage] = []
    private var loginCount = 0
    private var loginURLs: [URL] = []
    var loginFailure: Error?

    init(_ make: @escaping @Sendable (Int) -> ScriptedPage) {
        self.make = make
    }

    var pages: [ScriptedPage] { lock.lock(); defer { lock.unlock() }; return opened }
    var logins: Int { lock.lock(); defer { lock.unlock() }; return loginCount }
    var loginPages: [URL] { lock.lock(); defer { lock.unlock() }; return loginURLs }

    func newPage(profile: String) async throws -> any WebPageDriving {
        lock.lock()
        let page = make(opened.count)
        opened.append(page)
        lock.unlock()
        return page
    }

    func presentLogin(url: URL, profile: String) async throws {
        lock.lock(); loginCount += 1; loginURLs.append(url); lock.unlock()
        if let loginFailure { throw loginFailure }
    }
}

/// A cookie store in memory: the same cookies for every URL.
final class FakeCookies: SessionCookieAccess, @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [String: String]
    private var removedNames: [String] = []

    init(_ initial: [String: String] = [:]) {
        stored = initial
    }

    var values: [String: String] { lock.lock(); defer { lock.unlock() }; return stored }
    var removed: [String] { lock.lock(); defer { lock.unlock() }; return removedNames }

    func cookies(for url: URL) async -> [String: String] { values }

    func setCookie(name: String, value: String, url: URL, domain: String) async {
        lock.lock(); stored[name] = value; lock.unlock()
    }

    func removeCookie(name: String, url: URL) async {
        lock.lock(); stored[name] = nil; removedNames.append(name); lock.unlock()
    }

    func put(_ name: String, _ value: String) {
        lock.lock(); stored[name] = value; lock.unlock()
    }

    func clear() {
        lock.lock(); stored = [:]; lock.unlock()
    }
}

/// Records requests (image downloads); never reached for page work.
final class RecordingHTTP: HTTPFetching, @unchecked Sendable {
    private let reply: @Sendable (HTTPRequest) -> HTTPResponse
    private let lock = NSLock()
    private var seen: [HTTPRequest] = []

    init(_ reply: @escaping @Sendable (HTTPRequest) -> HTTPResponse = { HTTPResponse(url: $0.url, status: 404, headers: [:], body: Data()) }) {
        self.reply = reply
    }

    var requests: [HTTPRequest] { lock.lock(); defer { lock.unlock() }; return seen }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        lock.lock(); seen.append(request); lock.unlock()
        return reply(request)
    }
}

/// A job context whose chapter pause is zero for `source`, so tests do not wait between chapters.
func hardContext(
    _ web: ScriptedWeb, secrets: InMemorySecretStore = InMemorySecretStore(), settings: AppSettings = AppSettings(),
    http: any HTTPFetching = RecordingHTTP(), logger: CollectingLogger = CollectingLogger(),
    onChapter: @escaping @Sendable (ChapterResult) -> Void = { _ in }
) -> SourceContext {
    var effective = settings
    effective.shared.intervalMin = 0
    effective.shared.intervalMax = 0
    return SourceContext(http: http, web: web, secrets: secrets, settings: effective, logger: logger, onChapter: onChapter)
}

func hardURL(_ text: String) -> URL {
    URL(string: text)!
}

/// Collects values from concurrent tasks.
final class Collected<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [T] = []

    func add(_ item: T) {
        lock.lock(); items.append(item); lock.unlock()
    }

    var all: [T] { lock.lock(); defer { lock.unlock() }; return items }
}
