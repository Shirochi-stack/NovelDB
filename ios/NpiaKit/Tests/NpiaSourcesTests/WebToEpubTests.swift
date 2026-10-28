import XCTest
import NpiaCore
@testable import NpiaSources

/// A web page that answers each script by its first characters instead of running it. The
/// bundles' own text is never searched: it mentions every entry point.
final class ScriptedPage: WebPageDriving, @unchecked Sendable {
    typealias Handler = @Sendable (String) throws -> String

    private let lock = NSLock()
    private let handler: Handler
    private var scripts: [String] = []
    private var loadedURLs: [URL] = []
    private var handlerNames: [String] = []
    private var closedCount = 0
    private var transport: (@Sendable (String) async -> String)?
    var location: URL?

    init(_ handler: @escaping Handler) {
        self.handler = handler
    }

    var bodies: [String] { locked { scripts } }
    var loaded: [URL] { locked { loadedURLs } }
    var messageHandlers: [String] { locked { handlerNames } }
    var closes: Int { locked { closedCount } }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock(); defer { lock.unlock() }
        return body()
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {
        locked { loadedURLs.append(url) }
        location = url
    }

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        locked { scripts.append(functionBody) }
        return Data(try handler(functionBody).utf8)
    }

    func addInitScript(_ source: String) async {}

    func addMessageHandler(_ name: String, handler: @escaping @Sendable (String) async -> String) async {
        locked {
            handlerNames.append(name)
            transport = handler
        }
    }

    /// What the page's `__W2E_transport` would send through the message handler.
    func sendTransport(_ json: String) async -> String? {
        guard let transport = locked({ transport }) else { return nil }
        return await transport(json)
    }

    func currentURL() async -> URL? { location }
    func html() async throws -> String { "" }

    func close() async {
        locked { closedCount += 1 }
    }
}

struct ScriptedWeb: WebEngineProviding {
    let page: ScriptedPage
    func newPage(profile: String) async throws -> any WebPageDriving { page }
    func presentLogin(url: URL, profile: String) async throws { throw WebEngineError.unavailable }
}

/// The replies of a WebToEpub bundle that loads and parses a two-chapter book.
enum ScriptedParsers {
    static let bookURL = "https://www.novel.test/b/1"

    static let book = """
    {"api":1,"engine":"webtoepub","parser":"TestParser","match":"site","ruleName":"WebToEpub TestParser","bookUrl":"https://www.novel.test/b/1",\
    "bookname":"A Book","author":"An Author","introduction":"The intro.","introductionHTML":"","coverUrl":"https://www.novel.test/c.jpg",\
    "tags":["tag one","tag two"],"language":"en","chapterCount":2,\
    "chapters":[{"url":"https://www.novel.test/b/1/1","name":"One","number":1,"sectionName":null,"isVIP":false,"isPaid":false},\
    {"url":"https://www.novel.test/b/1/2","name":"Two","number":2,"sectionName":"Arc","isVIP":false,"isPaid":true}],\
    "warnings":["note"],"pageUrl":"https://www.novel.test/b/1"}
    """

    static let chapter = """
    {"chapterName":"One","contentHtml":"<p>Hello</p><p><img src=\\"https://img.test/a.png\\"></p><p>Bye</p>","contentText":"Hello\\nBye","imageCount":0,"images":[]}
    """

    /// Answers the load script, `__W2E_parseBook` and `__W2E_parseChapter`.
    static func handler(ready: @escaping @Sendable (String) -> String = { _ in "{\"ready\":true,\"error\":null}" },
                        book: String = ScriptedParsers.book,
                        chapter: String = ScriptedParsers.chapter) -> ScriptedPage.Handler {
        { body in
            if body.hasPrefix("if (typeof window.__W2E_transport") { return ready(body) }
            if body.hasPrefix("const data = JSON.parse(await window.__W2E_parseBook()") { return book }
            if body.hasPrefix("return JSON.parse(await window.__W2E_parseChapter(") { return chapter }
            throw WebEngineError.scriptFailed("unexpected script: " + String(body.prefix(60)))
        }
    }
}

final class WebToEpubTests: XCTestCase {
    private func context(_ page: ScriptedPage, settings: AppSettings = AppSettings(), logger: CollectingLogger = CollectingLogger()) -> SourceContext {
        SourceContext(http: RoutedHTTP(), web: ScriptedWeb(page: page), secrets: InMemorySecretStore(), settings: settings, logger: logger)
    }

    /// Only the WebToEpub parsers are on.
    private var parsersOnly: AppSettings {
        var settings = AppSettings()
        settings.engines = EngineSettings(order: [.webtoepub, .native, .novelDownloader], disabled: [.native, .novelDownloader])
        return settings
    }

    // MARK: - The engine end to end

    func testBookAndChaptersThroughWebToEpub() async throws {
        let page = ScriptedPage(ScriptedParsers.handler())
        let logger = CollectingLogger()
        let ctx = context(page, settings: parsersOnly, logger: logger)
        let url = URL(string: ScriptedParsers.bookURL)!
        let adapter = ReaderAdapter()
        let book = try await adapter.parseBook(url, context: ctx)

        XCTAssertEqual(book.source, .reader)
        XCTAssertEqual(book.title, "A Book")
        XCTAssertEqual(book.author, "An Author")
        XCTAssertEqual(book.synopsis, "The intro.")
        XCTAssertEqual(book.tags, ["tag one", "tag two"])
        XCTAssertEqual(book.language, "en")
        XCTAssertEqual(book.coverURL?.absoluteString, "https://www.novel.test/c.jpg")
        XCTAssertEqual(book.url.absoluteString, ScriptedParsers.bookURL)
        XCTAssertEqual(book.chapters.map(\.title), ["One", "Two"])
        XCTAssertEqual(book.chapters.map(\.index), [1, 2])
        XCTAssertEqual(book.chapters[1].url?.absoluteString, "https://www.novel.test/b/1/2")
        XCTAssertEqual(page.loaded, [url], "the book page loaded once")
        XCTAssertEqual(page.messageHandlers, ["npiaTransport"], "the page can reach the app's network")
        XCTAssertTrue(logger.lines.contains { $0.contains("TestParser") }, logger.lines.joined(separator: "\n"))

        guard case .content(let first) = try await adapter.parseChapter(book.chapters[0], book: book, context: ctx) else {
            return XCTFail("chapter 1 should parse")
        }
        // Images stay as absolute URLs for the app's image downloader.
        XCTAssertEqual(first.html, """
        <div class="reader-content">
        <p>Hello</p>
        <p class="img"><img src="images/0001_01.png" alt=""/></p>
        <p>Bye</p>
        </div>
        """)
        XCTAssertEqual(first.images, [ImageRef(url: URL(string: "https://img.test/a.png")!, filename: "images/0001_01.png")])
        let request = try XCTUnwrap(page.bodies.last)
        XCTAssertTrue(request.hasPrefix("return JSON.parse(await window.__W2E_parseChapter("), request)
        XCTAssertTrue(request.contains("https://www.novel.test/b/1/1"), "the chapter address is sent as the bridge listed it")

        let results = try await adapter.parseChapters(Array(book.chapters), book: book, context: ctx)
        XCTAssertEqual(results.count, 2)
        XCTAssertEqual(page.closes, 0, "the page stays open while the book is in use")
    }

    func testNoParserForTheSiteIsANoMatch() async throws {
        let page = ScriptedPage(ScriptedParsers.handler(book: #"{"error":"WebToEpub has no parser for this site","noParser":true}"#))
        let ctx = context(page, settings: parsersOnly)
        do {
            _ = try await ReaderAdapter().parseBook(URL(string: "https://no-parser-\(UUID().uuidString.prefix(6)).test/b")!, context: ctx)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("WebToEpub has no parser for this site"))
        }
        XCTAssertEqual(page.closes, 1, "a page no engine kept is closed")
    }

    func testAParserFailureKeepsItsMessage() async throws {
        let page = ScriptedPage(ScriptedParsers.handler(book: #"{"error":"Could not find the chapter list","stack":"x"}"#))
        let ctx = context(page, settings: parsersOnly)
        do {
            _ = try await ReaderAdapter().parseBook(URL(string: "https://www.novel.test/fail-\(UUID().uuidString.prefix(6))")!, context: ctx)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("WebToEpub: Could not find the chapter list"))
        }
    }

    func testAScriptErrorWhileReadingTheBookReadsAsAWebToEpubFailure() async throws {
        let page = ScriptedPage { body in
            if body.hasPrefix("const data = JSON.parse(await window.__W2E_parseBook()") {
                throw WebEngineError.scriptFailed("TypeError: undefined is not a function")
            }
            return try ScriptedParsers.handler()(body)
        }
        let ctx = context(page, settings: parsersOnly)
        do {
            _ = try await ReaderAdapter().parseBook(URL(string: "https://www.novel.test/b/7")!, context: ctx)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("WebToEpub: TypeError: undefined is not a function"))
        }
    }

    func testABookWithoutChaptersIsAnError() async throws {
        let json = #"{"api":1,"bookname":"X","chapters":[],"pageUrl":"https://www.novel.test/b/2"}"#
        let page = ScriptedPage(ScriptedParsers.handler(book: json))
        let ctx = context(page, settings: parsersOnly)
        do {
            _ = try await ReaderAdapter().parseBook(URL(string: "https://www.novel.test/b/2")!, context: ctx)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("WebToEpub found no chapters on https://www.novel.test/b/2."))
        }
        XCTAssertEqual(page.closes, 1)
    }

    func testAChapterErrorFromTheParsersFailsThatChapter() async throws {
        let page = ScriptedPage(ScriptedParsers.handler(chapter: #"{"error":"Could not find the chapter text"}"#))
        let ctx = context(page, settings: parsersOnly)
        let url = URL(string: "https://www.novel.test/b/3")!
        let book = try await ReaderAdapter().parseBook(url, context: ctx)
        let results = try await ReaderAdapter().parseChapters([book.chapters[0]], book: book, context: ctx)
        guard case .failed(_, let message) = results[0] else { return XCTFail("\(results)") }
        XCTAssertTrue(message.contains("Could not find the chapter text"), message)
    }

    func testChapterTextWithoutHTMLFallsBackToLines() async throws {
        let plain = #"{"chapterName":"One","contentHtml":"","contentText":"First line\nSecond line","imageCount":0,"images":[]}"#
        let page = ScriptedPage(ScriptedParsers.handler(chapter: plain))
        let ctx = context(page, settings: parsersOnly)
        let book = try await ReaderAdapter().parseBook(URL(string: "https://www.novel.test/b/4")!, context: ctx)
        guard case .content(let content) = try await ReaderAdapter().parseChapter(book.chapters[0], book: book, context: ctx) else {
            return XCTFail("chapter should parse")
        }
        XCTAssertTrue(content.html.contains("<p>First line</p>\n<p>Second line</p>"), content.html)
    }

    func testTheBatchEntryPointAnswersOneJSONStringPerChapter() async throws {
        let one = ScriptedParsers.chapter
        let failure = #"{"error":"The chapter page was empty"}"#
        let encoded = try JSONSerialization.data(withJSONObject: [one, failure], options: [.withoutEscapingSlashes])
        let reply = String(decoding: encoded, as: UTF8.self)
        let page = ScriptedPage { body in
            if body.hasPrefix("return JSON.parse(await window.__W2E_parseChapterBatch(") { return reply }
            return try ScriptedParsers.handler()(body)
        }
        let ctx = context(page, settings: parsersOnly)
        let (_, session) = try await WebToEpubSession.openBook(
            URL(string: "https://www.novel.test/b/5")!, context: ctx, page: EnginePage(url: URL(string: "https://www.novel.test/b/5")!, context: ctx)
        )
        let infos = [
            ReaderBookSession.ChapterInfo(url: "https://www.novel.test/b/1/1", name: "One", isVIP: false, isPaid: false),
            ReaderBookSession.ChapterInfo(url: "https://www.novel.test/b/1/2", name: "Two", isVIP: false, isPaid: false),
            ReaderBookSession.ChapterInfo(url: "https://www.novel.test/b/1/3", name: "Three", isVIP: false, isPaid: false),
        ]
        let results = try await session.chapters(infos)
        XCTAssertEqual(results.count, 3)
        if case .success(let blocks) = results[0] {
            XCTAssertEqual(blocks.first, .text("Hello"))
        } else {
            XCTFail("chapter 1 should parse")
        }
        if case .failure(let error) = results[1] {
            XCTAssertEqual(error as? SourceError, .parse("WebToEpub: The chapter page was empty"))
        } else {
            XCTFail("chapter 2 should fail")
        }
        if case .success = results[2] { XCTFail("chapter 3 had no answer") }
    }

    // MARK: - Engine order and the shared page

    func testBothJavaScriptEnginesShareOnePageLoad() async throws {
        // Novel-downloader's rules find nothing on this page, so WebToEpub takes over in the same page.
        let url = URL(string: "https://www.novel.test/shared-\(UUID().uuidString.prefix(6))")!
        let page = ScriptedPage { body in
            if body.hasPrefix("return !!window.__ND_BRIDGE_READY") { return "false" }
            if body.hasPrefix("(0, eval)(") { return "true" }
            if body.hasPrefix("try {\n  (0, eval)(") { return "{\"ready\":true,\"error\":null}" }
            if body.hasPrefix("const data = JSON.parse(await window.__ND_parseBook()") {
                return #"{"bookname":"Nothing","chapters":[],"pageUrl":"\#(url.absoluteString)"}"#
            }
            return try ScriptedParsers.handler()(body)
        }
        let logger = CollectingLogger()
        let ctx = context(page, logger: logger)
        let book = try await ReaderAdapter().parseBook(url, context: ctx)
        XCTAssertEqual(book.title, "A Book")
        XCTAssertEqual(page.loaded.count, 1, "one page load served both engines")
        XCTAssertTrue(page.bodies.contains { $0.hasPrefix("const data = JSON.parse(await window.__ND_parseBook()") })
        XCTAssertTrue(page.bodies.contains { $0.hasPrefix("const data = JSON.parse(await window.__W2E_parseBook()") })
        XCTAssertEqual(page.closes, 0)
        XCTAssertTrue(logger.lines.contains { $0.contains("trying WebToEpub parsers next") }, logger.lines.joined(separator: "\n"))
    }

    func testTheOrderDecidesWhichEngineRunsFirst() async throws {
        // WebToEpub first: novel-downloader is never asked.
        var settings = AppSettings()
        settings.engines = EngineSettings(order: [.webtoepub, .novelDownloader, .native])
        let page = ScriptedPage(ScriptedParsers.handler())
        let ctx = context(page, settings: settings)
        let url = URL(string: "https://www.novel.test/order-\(UUID().uuidString.prefix(6))")!
        let opened = try await SiteEngineRouter.open(url, context: ctx)
        XCTAssertEqual(opened.engine, .webtoepub)
        XCTAssertTrue(opened.adapter is ReaderAdapter)
        XCTAssertFalse(page.bodies.contains { $0.contains("__ND_") && !$0.contains("__W2E_") })
    }

    func testABookTheRulesCanReadNeverOpensAWebView() async throws {
        let page = ScriptedPage { _ in throw WebEngineError.scriptFailed("no page expected") }
        let ctx = SourceContext(http: FixtureHTTP(Fixtures.sudugu), web: ScriptedWeb(page: page), secrets: InMemorySecretStore(), settings: AppSettings())
        let opened = try await SiteEngineRouter.open(URL(string: "https://www.sudugu.com/1001/")!, context: ctx)
        XCTAssertEqual(opened.engine, .novelDownloader)
        XCTAssertEqual(opened.book.chapters.count, 3)
        XCTAssertEqual(opened.book.title, "测试之书")
        XCTAssertEqual(page.loaded, [])
        XCTAssertEqual(page.closes, 0)
    }

    func testEveryEngineFailingGivesTheFirstRealError() async throws {
        let page = ScriptedPage { _ in throw WebEngineError.scriptFailed("page script failed") }
        let ctx = SourceContext(http: FixtureHTTP([:]), web: ScriptedWeb(page: page), secrets: InMemorySecretStore(), settings: AppSettings())
        do {
            _ = try await SiteEngineRouter.open(URL(string: "https://www.sudugu.com/404/")!, context: ctx)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("HTTP 404 for https://www.sudugu.com/404/"))
        }
        XCTAssertEqual(page.closes, 1, "the page the engines shared is closed at the end")
    }

    // MARK: - Updates that do not load

    func testAParsersUpdateThatFailsToLoadIsSetAsideForTheShippedCopy() async throws {
        let fixture = try EngineFixture()
        let data = Data("/* update */".utf8)
        let manifest = EngineManifest(raw: [
            "engine": "webtoepub", "api": 1, "commit": "bad1", "shim": "s", "version": "9.9", "date": "2027-01-01T00:00:00Z",
            "built": "2027-01-01T00:00:00Z", "file": "webtoepub-lib.js", "size": data.count, "sha256": EngineStore.sha256Hex(data),
        ])
        _ = try fixture.store.install(.webtoepub, data: data, manifest: manifest)
        XCTAssertTrue(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)

        let loads = CallCounter()
        let page = ScriptedPage(ScriptedParsers.handler(ready: { body in
            // The first load script carries the update; the second the shipped copy.
            loads.bump() == 1 ? "{\"ready\":false,\"error\":\"SyntaxError: nope\"}" : "{\"ready\":true,\"error\":null}"
        }))
        let logger = CollectingLogger()
        let bundle = try await WebToEpubSession.inject(into: page, logger: logger, store: fixture.store)
        XCTAssertFalse(bundle.updated, "the shipped copy loaded")
        XCTAssertEqual(loads.value, 2)
        XCTAssertFalse(try XCTUnwrap(fixture.store.current(.webtoepub)).updated)
        XCTAssertEqual(fixture.store.rejectedKeys(.webtoepub), ["bad1-s"])
        XCTAssertTrue(logger.lines.contains { $0.contains("using the built-in copy") }, logger.lines.joined(separator: "\n"))
        XCTAssertTrue(page.bodies[0].contains("/* update */"))
        XCTAssertTrue(page.bodies[1].contains("parserFactory.register"))
    }

    func testAShippedCopyThatFailsToLoadIsAnErrorAndStaysPut() async throws {
        let fixture = try EngineFixture()
        let page = ScriptedPage(ScriptedParsers.handler(ready: { _ in "{\"ready\":false,\"error\":\"boom\"}" }))
        do {
            _ = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: fixture.store)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("The WebToEpub parsers failed to load: boom"))
        }
        XCTAssertTrue(fixture.store.rejectedKeys(.webtoepub).isEmpty)
        XCTAssertNotNil(fixture.store.current(.webtoepub))
    }

    func testAScriptErrorWhileLoadingCountsAsAFailureToLoad() async throws {
        let fixture = try EngineFixture()
        let page = ScriptedPage { _ in throw WebEngineError.scriptFailed("SyntaxError: Unexpected token") }
        do {
            _ = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: fixture.store)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("The WebToEpub parsers failed to load: SyntaxError: Unexpected token"))
        }
    }

    // MARK: - The network transport

    func testTheTransportSendsTheRequestThroughTheAppsHTTPClient() async throws {
        let http = RoutedHTTP()
        http.on("api.novel.test/chapter", "chapter body", headers: ["content-type": "text/plain; charset=utf-8", "set-cookie": "a=b"])
        let request = #"{"url":"https://api.novel.test/chapter?id=7","method":"post","headers":{"Content-Type":"application/json","X-Number":5},"data":"{\"q\":1}","responseType":"arraybuffer","timeout":15000}"#
        let reply = await EngineTransport.handle(request, http: http)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(reply.utf8)) as? [String: Any])
        XCTAssertEqual(object["status"] as? Int, 200)
        XCTAssertFalse((object["statusText"] as? String ?? "").isEmpty)
        XCTAssertEqual(object["finalUrl"] as? String, "https://api.novel.test/chapter?id=7")
        XCTAssertEqual(String(decoding: Data(base64Encoded: try XCTUnwrap(object["responseBase64"] as? String)) ?? Data(), as: UTF8.self), "chapter body")
        XCTAssertEqual(object["responseHeaders"] as? String, "content-type: text/plain; charset=utf-8\r\nset-cookie: a=b")

        let sent = try XCTUnwrap(http.requested("api.novel.test").first)
        XCTAssertEqual(sent.method, "POST")
        XCTAssertEqual(sent.headers["Content-Type"], "application/json")
        XCTAssertEqual(sent.headers["X-Number"], "5")
        XCTAssertEqual(sent.body, Data(#"{"q":1}"#.utf8))
        XCTAssertEqual(sent.timeout, 15)
    }

    func testTheTransportSendsBinaryBodiesAndReportsStatusesAndErrors() async throws {
        let http = RoutedHTTP()
        http.on("down.test", "gone", status: 503)
        let bytes = Data([0, 1, 2, 255]).base64EncodedString()
        let post = await EngineTransport.handle(#"{"url":"https://down.test/x","method":"PUT","dataBase64":"\#(bytes)"}"#, http: http)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(post.utf8)) as? [String: Any])
        XCTAssertEqual(object["status"] as? Int, 503)
        XCTAssertEqual(http.requested("down.test").first?.body, Data([0, 1, 2, 255]))
        XCTAssertEqual(http.requested("down.test").first?.timeout, 30)

        for bad in ["not json", #"{"url":"ftp://x.test/"}"#, #"{"method":"GET"}"#, #"{"url":"file:///etc/passwd"}"#] {
            let answer = await EngineTransport.handle(bad, http: http)
            XCTAssertEqual(answer, #"{"error":"invalid request"}"#, bad)
        }
        struct Offline: HTTPFetching {
            func send(_ request: HTTPRequest) async throws -> HTTPResponse { throw URLError(.notConnectedToInternet) }
        }
        let failed = await EngineTransport.handle(#"{"url":"https://x.test/"}"#, http: Offline())
        XCTAssertTrue(failed.hasPrefix(#"{"error":""#), failed)
    }

    func testThePageReachesTheTransportThroughItsMessageHandler() async throws {
        let http = RoutedHTTP()
        http.on("chapters.test/1", "<p>one</p>")
        let page = ScriptedPage(ScriptedParsers.handler())
        let ctx = SourceContext(http: http, web: ScriptedWeb(page: page), secrets: InMemorySecretStore(), settings: parsersOnly)
        _ = try await ReaderAdapter().parseBook(URL(string: "https://www.novel.test/b/6")!, context: ctx)
        let answer = await page.sendTransport(#"{"url":"https://chapters.test/1"}"#)
        let reply = try XCTUnwrap(answer)
        XCTAssertTrue(reply.contains("\"status\":200"), reply)
        // The page-side glue is part of the load script.
        let load = try XCTUnwrap(page.bodies.first)
        XCTAssertTrue(load.contains("window.webkit.messageHandlers.npiaTransport.postMessage"), String(load.prefix(300)))
    }
}

final class CallCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var value: Int {
        lock.lock(); defer { lock.unlock() }
        return count
    }

    @discardableResult
    func bump() -> Int {
        lock.lock(); defer { lock.unlock() }
        count += 1
        return count
    }
}
