import XCTest
import NpiaCore
@testable import NpiaSources

final class XiyuwxAdapterTests: XCTestCase {
    // MARK: Fixtures

    private static let bookHTML = """
    <html><head><title>Test Book</title>
    <meta property="og:novel:category" content="Fantasy"></head><body>
    <div id="info"><h1>Test Book</h1><p class="author">\u{4f5c}\u{8005}\u{ff1a}Writer</p></div>
    <div id="fmimg"><img src="/cover.jpg"></div>
    <div id="intro"><a href="/tag/a/">Tag1</a>\u{3001}<a href="/tag/b/">Tag2</a>\u{ff1a}A short story.</div>
    <div id="list"><dl>
    <dt>Latest</dt>
    <a href="/book/123/3.html">\u{7b2c}3\u{7ae0} C</a>
    <dt>\u{6b63}\u{6587}</dt>
    <a href="/book/123/1.html">\u{7b2c}1\u{7ae0} A</a>
    <a class="vip" href="https://www.xiyuwx.com/book/123/2.html">\u{7b2c}2\u{7ae0} B</a>
    <a href="/book/123/">Back</a>
    <a href="/book/123/1.html">\u{7b2c}1\u{7ae0} A</a>
    </dl></div></body></html>
    """

    private static let plainChapter = """
    <html><body><div id="content"><p>\u{7b2c}12\u{7ae0} Title</p><div class="ad">Buy now</div>Line one<br>Line &amp; two<script>var x = 1;</script></div>
    <a href="/book/123/5_2.html">\u{4e0b}\u{4e00}\u{9875}</a><a href="/book/123/6.html">\u{4e0b}\u{4e00}\u{7ae0}</a></body></html>
    """

    private static let secondPage = """
    <html><body><div id="content">Line three</div></body></html>
    """

    private static let shuffledChapter = """
    <html><body><div id="content"><div id="chapter">[\u{4f5c}\u{8005}\u{7684}\u{63d0}\u{793a}\u{ff1a}hello]Second<br/><br/>Third<br/><br/>First</div></div>
    <script>var a = _ii_rr('MTAwLDEwMiwxMDAsMTAx');</script></body></html>
    """

    private static func context(http: any HTTPFetching, web: any WebEngineProviding, skipPaid: Bool = false) -> SourceContext {
        var settings = AppSettings()
        settings.shared.intervalMin = 0
        settings.shared.intervalMax = 0
        settings.shared.skipPaid = skipPaid
        return SourceContext(http: http, web: web, secrets: InMemorySecretStore(), settings: settings)
    }

    private static func ref(_ url: String, paid: Bool = false) -> ChapterRef {
        ChapterRef(id: url, index: 1, title: "Chapter", url: URL(string: url), isPaid: paid)
    }

    private static func book() -> BookInfo {
        BookInfo(source: .xiyuwx, id: "123", url: URL(string: "https://xiyuwx.com/book/123/")!, title: "Test Book")
    }

    // MARK: URL matching

    func testMatchesBothHostsAndBookPaths() {
        for text in [
            "https://xiyuwx.com/123/", "https://www.xiyuwx.com/book/45/", "https://yywenxuan.com/9",
            "https://www.yywenxuan.com/book/9/3.html", "https://xiyuwx.com/12/7_2.html",
        ] {
            XCTAssertTrue(XiyuwxAdapter.matches(URL(string: text)!), text)
        }
    }

    func testRejectsOtherHostsAndPaths() {
        for text in [
            "https://example.com/123/", "https://xiyuwx.com/", "https://xiyuwx.com/0/", "https://xiyuwx.com/book/abc/",
            "https://xiyuwx.com.evil.com/12/", "https://xiyuwx.com/author/12/",
        ] {
            XCTAssertFalse(XiyuwxAdapter.matches(URL(string: text)!), text)
        }
    }

    func testBookIDAndSiteName() {
        XCTAssertEqual(XiyuwxParser.bookID(URL(string: "https://xiyuwx.com/book/123/4.html")!), "123")
        XCTAssertEqual(XiyuwxParser.siteName(URL(string: "https://www.yywenxuan.com/1/")!), "YY Wenxuan")
        XCTAssertEqual(XiyuwxParser.siteName(URL(string: "https://xiyuwx.com/1/")!), "Xiyuwx")
        XCTAssertEqual(
            XiyuwxParser.canonicalBookURL(URL(string: "https://xiyuwx.com/book/123/4.html?x=1")!, bookID: "123").absoluteString,
            "https://xiyuwx.com/book/123/")
    }

    // MARK: Decoder

    func testDecodePositions() {
        XCTAssertEqual(XiyuwxParser.decodePositions("MTAwLDEwMiwxMDAsMTAx"), [100, 102, 100, 101])
        XCTAssertEqual(XiyuwxParser.decodePositions("Nyw4LDc"), [7, 8, 7])
        XCTAssertNil(XiyuwxParser.decodePositions("!!!"))
        XCTAssertNil(XiyuwxParser.decodePositions("YWJj"))
    }

    func testUnshuffleRequiresPermutation() {
        XCTAssertEqual(XiyuwxParser.unshuffle(["b", "c", "a"], positions: [100, 102, 100, 101]) ?? [], ["a", "b", "c"])
        XCTAssertNil(XiyuwxParser.unshuffle(["b", "c", "a"], positions: [100, 100, 100, 101]))
        XCTAssertNil(XiyuwxParser.unshuffle(["b", "c"], positions: [100, 102, 100, 101]))
        XCTAssertEqual(XiyuwxParser.unshuffle([], positions: [5]) ?? ["x"], [])
    }

    func testSplitParagraphs() {
        XCTAssertEqual(XiyuwxParser.splitParagraphs("a<br><br>b<BR/> <br />c"), ["a", "b", "c"])
        XCTAssertEqual(XiyuwxParser.splitParagraphs("a<br>b"), ["a<br>b"])
    }

    func testDecodePageKeepsChineseInGB18030() {
        let text = "<html><head><meta charset=\"gbk\"></head><body>\u{4e2d}\u{6587}</body></html>"
        let data = text.data(using: WebHTML.gb18030)!
        XCTAssertTrue(XiyuwxParser.decodePage(data).contains("\u{4e2d}\u{6587}"))
        var broken = Data("<meta charset=\"utf-8\">".utf8)
        broken.append(contentsOf: [0xff, 0xe4, 0xb8, 0xad])
        XCTAssertTrue(XiyuwxParser.decodePage(broken).contains("\u{4e2d}"))
    }

    // MARK: Book

    func testBookParsing() throws {
        let pageURL = URL(string: "https://yywenxuan.com/book/123/")!
        let book = try XCTUnwrap(XiyuwxParser.book(fromHTML: Self.bookHTML, pageURL: pageURL, bookURL: pageURL, bookID: "123"))
        XCTAssertEqual(book.title, "Test Book")
        XCTAssertEqual(book.author, "Writer")
        XCTAssertEqual(book.synopsis, "A short story.")
        XCTAssertEqual(book.tags, ["Tag1", "Tag2", "Fantasy"])
        XCTAssertEqual(book.coverURL?.absoluteString, "https://yywenxuan.com/cover.jpg")
        XCTAssertEqual(book.language, "zh")
        XCTAssertEqual(book.chapters.map(\.index), [1, 2])
        XCTAssertEqual(book.chapters.map(\.title), ["\u{7b2c}1\u{7ae0} A", "\u{7b2c}2\u{7ae0} B"])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true])
        // The mirror keeps chapters on the serving host.
        XCTAssertEqual(book.chapters[1].url?.absoluteString, "https://yywenxuan.com/book/123/2.html")
    }

    func testBookParsingRejectsVerificationAndEmptyCatalog() {
        let pageURL = URL(string: "https://xiyuwx.com/book/123/")!
        let challenge = "<html><head><title>Just a moment...</title></head><body><h1>Wait</h1></body></html>"
        XCTAssertNil(XiyuwxParser.book(fromHTML: challenge, pageURL: pageURL, bookURL: pageURL, bookID: "123"))
        XCTAssertTrue(XiyuwxParser.isVerificationPage(challenge))
        XCTAssertFalse(XiyuwxParser.isVerificationPage(Self.bookHTML))
        let noCatalog = "<html><body><h1>Title</h1></body></html>"
        XCTAssertNil(XiyuwxParser.book(fromHTML: noCatalog, pageURL: pageURL, bookURL: pageURL, bookID: "123"))
    }

    // MARK: Chapter pages

    func testPlainChapterCleanupAndNextPage() throws {
        let pageURL = URL(string: "https://xiyuwx.com/book/123/5.html")!
        let page = try XCTUnwrap(XiyuwxParser.chapterPage(fromHTML: Self.plainChapter, pageURL: pageURL))
        XCTAssertEqual(page.lines, ["Line one", "Line & two"])
        XCTAssertEqual(page.nextPage?.absoluteString, "https://xiyuwx.com/book/123/5_2.html")
        XCTAssertEqual(XiyuwxParser.chapterHTML(page.lines), "<p>Line one</p>\n<p>Line &amp; two</p>")
    }

    func testShuffledChapterIsReordered() throws {
        let pageURL = URL(string: "https://xiyuwx.com/book/123/8.html")!
        let page = try XCTUnwrap(XiyuwxParser.chapterPage(fromHTML: Self.shuffledChapter, pageURL: pageURL))
        XCTAssertEqual(page.lines, ["First", "Second", "Third"])
        XCTAssertNil(page.nextPage)
    }

    func testShuffledChapterWithoutMapIsUnusable() {
        let html = "<html><body><div id=\"content\"><div id=\"chapter\">a<br><br>b</div></div></body></html>"
        XCTAssertNil(XiyuwxParser.chapterPage(fromHTML: html, pageURL: nil))
    }

    func testRenderedCopyWinsOverShuffledSource() throws {
        let html = """
        <html><body><div id="content"><div id="chapter">x<br><br>y</div><div id="ad">Real one<br>Real two</div></div></body></html>
        """
        let page = try XCTUnwrap(XiyuwxParser.chapterPage(fromHTML: html, pageURL: nil))
        XCTAssertEqual(page.lines, ["Real one", "Real two"])
    }

    func testNoContentReturnsNil() {
        XCTAssertNil(XiyuwxParser.chapterPage(fromHTML: "<html><body><p>nothing</p></body></html>", pageURL: nil))
    }

    // MARK: Adapter flows

    func testParseBookOverHTTP() async throws {
        let http = XiyuwxStubHTTP(routes: ["https://xiyuwx.com/book/123/": (200, Data(Self.bookHTML.utf8))])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: XiyuwxFakePage(pages: [:])))
        let book = try await XiyuwxAdapter().parseBook(URL(string: "https://xiyuwx.com/book/123/")!, context: context)
        XCTAssertEqual(book.source, .xiyuwx)
        XCTAssertEqual(book.id, "123")
        XCTAssertEqual(book.chapters.count, 2)
    }

    func testParseBookFallsBackToBrowserOnVerification() async throws {
        let challenge = "<html><head><title>Just a moment...</title></head><body></body></html>"
        let url = "https://xiyuwx.com/book/123/"
        let http = XiyuwxStubHTTP(routes: [url: (200, Data(challenge.utf8))])
        let page = XiyuwxFakePage(pages: [url: Self.bookHTML])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: page))
        let book = try await XiyuwxAdapter().parseBook(URL(string: url)!, context: context)
        XCTAssertEqual(book.title, "Test Book")
        XCTAssertEqual(page.loadedURLs.first?.absoluteString, url)
    }

    func testParseBookThrowsWhenNothingWorks() async {
        let http = XiyuwxStubHTTP(routes: [:])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: XiyuwxFakePage(pages: [:])))
        do {
            _ = try await XiyuwxAdapter().parseBook(URL(string: "https://xiyuwx.com/book/123/")!, context: context)
            XCTFail("expected an error")
        } catch {
            XCTAssertNotNil(error as? SourceError)
        }
    }

    func testMultiPageChapterOverHTTP() async throws {
        let http = XiyuwxStubHTTP(routes: [
            "https://xiyuwx.com/book/123/5.html": (200, Data(Self.plainChapter.utf8)),
            "https://xiyuwx.com/book/123/5_2.html": (200, Data(Self.secondPage.utf8)),
        ])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: XiyuwxFakePage(pages: [:])))
        let result = try await XiyuwxAdapter().parseChapter(Self.ref("https://xiyuwx.com/book/123/5.html"), book: Self.book(), context: context)
        guard case .content(let content) = result else { return XCTFail("expected content") }
        XCTAssertEqual(content.html, "<p>Line one</p>\n<p>Line &amp; two</p>\n<p>Line three</p>")
    }

    func testChapterFallsBackToBrowserWhenHTTPFails() async throws {
        let url = "https://xiyuwx.com/book/123/8.html"
        let http = XiyuwxStubHTTP(routes: [url: (403, Data())])
        let page = XiyuwxFakePage(pages: [url: Self.shuffledChapter])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: page))
        let result = try await XiyuwxAdapter().parseChapter(Self.ref(url), book: Self.book(), context: context)
        guard case .content(let content) = result else { return XCTFail("expected content") }
        XCTAssertEqual(content.html, "<p>First</p>\n<p>Second</p>\n<p>Third</p>")
    }

    func testPaidChapterIsSkippedWhenRequested() async throws {
        let http = XiyuwxStubHTTP(routes: [:])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: XiyuwxFakePage(pages: [:])), skipPaid: true)
        let result = try await XiyuwxAdapter().parseChapter(Self.ref("https://xiyuwx.com/book/123/2.html", paid: true), book: Self.book(), context: context)
        guard case .refused = result else { return XCTFail("expected refused") }
        XCTAssertEqual(http.requestCount, 0)
    }

    func testUnreadablePaidChapterIsRefusedAndFreeOneFails() async throws {
        let http = XiyuwxStubHTTP(routes: [:])
        let context = Self.context(http: http, web: XiyuwxStubWeb(page: XiyuwxFakePage(pages: [:])))
        let paid = try await XiyuwxAdapter().parseChapter(Self.ref("https://xiyuwx.com/book/123/2.html", paid: true), book: Self.book(), context: context)
        guard case .refused = paid else { return XCTFail("expected refused") }
        let free = try await XiyuwxAdapter().parseChapter(Self.ref("https://xiyuwx.com/book/123/3.html"), book: Self.book(), context: context)
        guard case .failed = free else { return XCTFail("expected failed") }
    }
}

// MARK: - Stubs

private final class XiyuwxRequestCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func increment() { lock.lock(); count += 1; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}

private struct XiyuwxStubHTTP: HTTPFetching {
    var routes: [String: (Int, Data)]
    private let counter = XiyuwxRequestCounter()

    init(routes: [String: (Int, Data)]) {
        self.routes = routes
    }

    var requestCount: Int { counter.value }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        counter.increment()
        if let route = routes[request.url.absoluteString] {
            return HTTPResponse(url: request.url, status: route.0, headers: [:], body: route.1)
        }
        return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
    }
}

private final class XiyuwxFakePage: WebPageDriving, @unchecked Sendable {
    private let lock = NSLock()
    private let pages: [String: String]
    private var current: URL?
    private var loaded: [URL] = []

    init(pages: [String: String]) {
        self.pages = pages
    }

    var loadedURLs: [URL] {
        lock.lock(); defer { lock.unlock() }
        return loaded
    }

    private func remember(_ url: URL) {
        lock.lock(); defer { lock.unlock() }
        current = url
        loaded.append(url)
    }

    private func snapshot() -> (URL?, String) {
        lock.lock(); defer { lock.unlock() }
        return (current, current.flatMap { pages[$0.absoluteString] } ?? "<html></html>")
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {
        remember(url)
    }

    func evaluateJSON(_ functionBody: String) async throws -> Data { Data("null".utf8) }
    func addInitScript(_ source: String) async {}
    func currentURL() async -> URL? { snapshot().0 }
    func html() async throws -> String { snapshot().1 }
    func close() async {}
}

private struct XiyuwxStubWeb: WebEngineProviding {
    var page: XiyuwxFakePage
    func newPage(profile: String) async throws -> any WebPageDriving { page }
    func presentLogin(url: URL, profile: String, userAgentSuffix: String?) async throws {}
}
