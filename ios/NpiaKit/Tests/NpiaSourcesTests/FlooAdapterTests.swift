import XCTest
import NpiaCore
@testable import NpiaSources

final class FlooAdapterTests: XCTestCase {
    // Hosts come from the parser's escaped constants so no provider name is spelled out here.
    private static let bookHost = FlooParser.bookHost
    private static let domain = FlooParser.domain

    private static let bookHTML = """
    <html><body><div class="C-Fo-Zuo"><h1 id="novelName">Test Book</h1>
    <a class="rentouOne" href="/guru/1">Writer</a>
    <div class="T-L-T-C-Box1">A story.</div>
    <img class="imgcss" src="/c.jpg">
    <div class="T-R-T-B2-Box1"><a href="/t/1">Tag1</a><a href="/t/2">Tag2</a></div>
    <div class="DivTable"><table>
    <tr><td><a href="/724903_1.html">Chapter 1</a></td></tr>
    <tr class="vip"><td><a href="/vip/724903/2.html">Chapter 2</a></td></tr>
    <tr><td><a href="/724903.html">Work page</a></td></tr>
    <tr><td><a href="https://other.example.com/x.html">Other</a></td></tr>
    <tr><td><a href="/724903_1.html">Chapter 1</a></td></tr>
    <tr><td><a href="/724903_3.aspx">Chapter 3</a></td></tr>
    </table></div></div></body></html>
    """

    private static let freeChapter = """
    <html><body><div class="noveContent">Line one<br>Line &amp; two<p><a href="https://pay.\(domain)/x">recharge</a></p><script>var x = 1;</script></div></body></html>
    """

    private static let loginWallChapter = """
    <html><body><div class="noveContent">\u{60a8}\u{8fd8}\u{6ca1}\u{6709}\u{767b}\u{5f55}</div></body></html>
    """

    private static let purchaseWallChapter = """
    <html><body><div class="noveContent"><div class="c_c3">\u{8ba2}\u{9605}\u{672c}\u{7ae0}</div></div></body></html>
    """

    private static let imageChapterHTML = """
    <html><body><div class="noveContent"><script>image_do3('1','0','724903','2','en1','p1','key','u1','123','32','000000','1','0','1')</script><div class="con_img"></div></div></body></html>
    """

    private static let imageURL = "https://read.\(domain)/Page4VipImage.aspx?num=1&o=0&id=724903&n=2&ct=1&en=en1&t=p1&font_size=32&font_color=000000&FontFamilyType=0&backgroundtype=1&u=u1&time=123&k=key"

    private static let gifBytes = Data([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00])

    private static func context(http: any HTTPFetching, web: any WebEngineProviding, skipPaid: Bool = false) -> SourceContext {
        var settings = AppSettings()
        settings.shared.intervalMin = 0
        settings.shared.intervalMax = 0
        settings.shared.skipPaid = skipPaid
        return SourceContext(http: http, web: web, secrets: InMemorySecretStore(), settings: settings)
    }

    private static func ref(_ path: String, index: Int = 1, paid: Bool = false) -> ChapterRef {
        let text = "https://\(bookHost)\(path)"
        return ChapterRef(id: text, index: index, title: "Chapter", url: URL(string: text), isPaid: paid)
    }

    private static func book() -> BookInfo {
        BookInfo(source: .floo, id: "724903", url: URL(string: "https://\(bookHost)/724903.html")!, title: "Test Book")
    }

    // MARK: URL matching

    func testMatchesBookCatalogVipAndChapterLinks() {
        let cases: [(String, String)] = [
            ("https://\(Self.bookHost)/724903.html", "724903"),
            ("https://wap.\(Self.domain)/724903_1.html", "724903"),
            ("https://www.\(Self.domain)/vip/724903/1658.html", "724903"),
            ("https://\(Self.bookHost)/html_724_724903/", "724903"),
            ("https://\(Self.bookHost)/book/55", "55"),
            ("https://\(Self.bookHost)/catalog?id=123", "123"),
        ]
        for (text, expected) in cases {
            let url = URL(string: text)!
            XCTAssertTrue(FlooAdapter.matches(url), text)
            XCTAssertEqual(FlooParser.bookID(url), expected, text)
        }
    }

    func testRejectsOtherHostsAndPaths() {
        let rejected = [
            "https://read.\(Self.domain)/724903.html",
            "https://\(Self.domain)/724903.html",
            "https://example.com/724903.html",
            "https://\(Self.bookHost)/",
            "https://\(Self.bookHost)/about",
            "https://\(Self.bookHost)/x?id=0",
            "https://\(Self.bookHost).evil.com/724903.html",
        ]
        for text in rejected {
            XCTAssertFalse(FlooAdapter.matches(URL(string: text)!), text)
        }
    }

    // MARK: Helpers

    func testFixTextRepairsMisreadGBK() {
        XCTAssertEqual(FlooParser.fixText("\u{c4}\u{e3}\u{ba}\u{c3}"), "\u{4f60}\u{597d}")
        XCTAssertEqual(FlooParser.fixText("\u{4f60}\u{597d}"), "\u{4f60}\u{597d}")
        XCTAssertEqual(FlooParser.fixText("plain"), "plain")
    }

    func testDecodingsHandleGBKWithoutCharset() {
        let data = "<html><body><h1>\u{4e2d}\u{6587}</h1></body></html>".data(using: WebHTML.gb18030)!
        XCTAssertTrue(FlooParser.decodings(data).first?.contains("\u{4e2d}\u{6587}") == true)
        var bom = Data([0xEF, 0xBB, 0xBF])
        bom.append(Data("<p>hi</p>".utf8))
        XCTAssertEqual(FlooParser.decodings(bom).first, "<p>hi</p>")
    }

    func testChallengeToken() {
        let challenge = Data("<script>document.cookie=\"C3VK=ab12CD\";location.reload()</script>".utf8)
        XCTAssertEqual(FlooParser.challengeToken(in: challenge), "ab12CD")
        XCTAssertNil(FlooParser.challengeToken(in: Data("<html>ok</html>".utf8)))
    }

    func testImageURLArguments() {
        let args = "'1','0','724903','2','en1','p1','key','u1','123','32','000000','1','0','1'"
        XCTAssertEqual(FlooParser.imageURL(arguments: args)?.absoluteString, Self.imageURL)
        let older = FlooParser.imageURL(arguments: "1,0,9,2,en,p,k,u,5,24,ff0000,0")
        XCTAssertTrue(older?.absoluteString.hasPrefix("https://read6.\(Self.domain)/Page4VipImage.aspx?num=1") == true)
        XCTAssertTrue(older?.absoluteString.contains("&FontFamilyType=0&backgroundtype=1&") == true)
        XCTAssertNil(FlooParser.imageURL(arguments: "1,2,3"))
    }

    func testImageNamingAndMagic() {
        XCTAssertEqual(FlooParser.imageFilename(chapterIndex: 7, number: 3, ext: "gif"), "images/0007_03.gif")
        XCTAssertEqual(FlooParser.imageFilename(chapterIndex: 12345, number: 10, ext: "png"), "images/12345_10.png")
        XCTAssertEqual(FlooParser.imageExtension(Self.gifBytes), "gif")
        XCTAssertEqual(FlooParser.imageExtension(Data([0x89, 0x50, 0x4E, 0x47, 0x0D])), "png")
        XCTAssertEqual(FlooParser.imageExtension(Data([0xFF, 0xD8, 0xFF, 0xE0])), "jpg")
        XCTAssertNil(FlooParser.imageExtension(Data("<html>login</html>".utf8)))
        XCTAssertEqual(FlooParser.imageHTML(["images/0001_01.gif"]), "<p><img src=\"images/0001_01.gif\" alt=\"\"/></p>")
    }

    // MARK: Book

    func testBookParsing() throws {
        let pageURL = URL(string: "https://\(Self.bookHost)/724903.html")!
        let book = try XCTUnwrap(FlooParser.book(fromHTML: [Self.bookHTML], pageURL: pageURL, bookID: "724903", canonical: pageURL))
        XCTAssertEqual(book.source, .floo)
        XCTAssertEqual(book.title, "Test Book")
        XCTAssertEqual(book.author, "Writer")
        XCTAssertEqual(book.synopsis, "A story.")
        XCTAssertEqual(book.tags, ["Tag1", "Tag2"])
        XCTAssertEqual(book.coverURL?.absoluteString, "https://\(Self.bookHost)/c.jpg")
        XCTAssertEqual(book.chapters.map(\.index), [1, 2, 3])
        XCTAssertEqual(book.chapters.map(\.title), ["Chapter 1", "Chapter 2", "Chapter 3"])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true, false])
        XCTAssertEqual(book.chapters[1].url?.absoluteString, "https://\(Self.bookHost)/vip/724903/2.html")
    }

    func testBookParsingNeedsTitleAndCatalog() {
        let pageURL = URL(string: "https://\(Self.bookHost)/724903.html")!
        let empty = "<html><body><h1 id=\"novelName\">T</h1></body></html>"
        XCTAssertNil(FlooParser.book(fromHTML: [empty], pageURL: pageURL, bookID: "724903", canonical: pageURL))
    }

    // MARK: Chapter pages

    func testFreeChapterText() {
        let outcome = FlooParser.chapter(fromHTML: [Self.freeChapter], pageURL: nil)
        XCTAssertEqual(outcome, .text(["Line one", "Line & two"]))
    }

    func testLoginAndPurchaseWalls() {
        XCTAssertEqual(FlooParser.chapter(fromHTML: [Self.loginWallChapter], pageURL: nil), .locked("login"))
        XCTAssertEqual(FlooParser.chapter(fromHTML: [Self.purchaseWallChapter], pageURL: nil), .locked("purchase"))
        let loginLink = "<html><body><div class=\"noveContent\"><a href=\"/regist/login.aspx\">x</a></div></body></html>"
        XCTAssertEqual(FlooParser.chapter(fromHTML: [loginLink], pageURL: nil), .locked("login"))
        let noContainer = "<html><body><p>\u{8bf7}\u{767b}\u{5f55}\u{540e}\u{67e5}\u{770b}</p></body></html>"
        XCTAssertEqual(FlooParser.chapter(fromHTML: [noContainer], pageURL: nil), .locked("login"))
        XCTAssertEqual(FlooParser.chapter(fromHTML: ["<html><body><p>hello</p></body></html>"], pageURL: nil), .none)
    }

    func testVipImagesFromScriptCalls() throws {
        let outcome = FlooParser.chapter(fromHTML: [Self.imageChapterHTML], pageURL: nil)
        XCTAssertEqual(outcome, .images([URL(string: Self.imageURL)!]))
    }

    func testVipImagesFromRenderedStyle() {
        let html = "<html><body><div class=\"noveContent\"><div class=\"con_img\"><div style=\"background:url('/Page4VipImage.aspx?num=1')\"></div></div></div></body></html>"
        let pageURL = URL(string: "https://\(Self.bookHost)/724903_2.html")!
        XCTAssertEqual(FlooParser.chapter(fromHTML: [html], pageURL: pageURL),
                       .images([URL(string: "https://\(Self.bookHost)/Page4VipImage.aspx?num=1")!]))
    }

    func testEmptySlotWithoutImagesIsPurchaseWall() {
        let html = "<html><body><div class=\"noveContent\"><div class=\"con_img\"></div></div></body></html>"
        XCTAssertEqual(FlooParser.chapter(fromHTML: [html], pageURL: nil), .locked("purchase"))
    }

    // MARK: Adapter flows

    func testParseBookOverHTTP() async throws {
        let canonical = "https://\(Self.bookHost)/724903.html"
        let http = FlooStubHTTP(routes: [canonical: (200, Data(Self.bookHTML.utf8))])
        let context = Self.context(http: http, web: FlooStubWeb(page: FlooFakePage(pages: [:])))
        let book = try await FlooAdapter().parseBook(URL(string: "https://wap.\(Self.domain)/724903_1.html")!, context: context)
        XCTAssertEqual(book.id, "724903")
        XCTAssertEqual(book.url.absoluteString, canonical)
        XCTAssertEqual(book.chapters.count, 3)
    }

    func testParseBookFallsBackToBrowser() async throws {
        let canonical = "https://\(Self.bookHost)/724903.html"
        let http = FlooStubHTTP(routes: [canonical: (200, Data("<html></html>".utf8))])
        let page = FlooFakePage(pages: [canonical: Self.bookHTML])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let book = try await FlooAdapter().parseBook(URL(string: canonical)!, context: context)
        XCTAssertEqual(book.title, "Test Book")
    }

    func testParseBookRejectsForeignURL() async {
        let context = Self.context(http: FlooStubHTTP(routes: [:]), web: FlooStubWeb(page: FlooFakePage(pages: [:])))
        do {
            _ = try await FlooAdapter().parseBook(URL(string: "https://example.com/1.html")!, context: context)
            XCTFail("expected an error")
        } catch {
            XCTAssertNotNil(error as? SourceError)
        }
    }

    func testFreeChapterOverHTTP() async throws {
        let path = "/724903_1.html"
        let http = FlooStubHTTP(routes: ["https://\(Self.bookHost)\(path)": (200, Data(Self.freeChapter.utf8))])
        let context = Self.context(http: http, web: FlooStubWeb(page: FlooFakePage(pages: [:])))
        let result = try await FlooAdapter().parseChapter(Self.ref(path), book: Self.book(), context: context)
        guard case .content(let content) = result else { return XCTFail("expected content") }
        XCTAssertEqual(content.html, "<p>Line one</p>\n<p>Line &amp; two</p>")
        XCTAssertTrue(content.images.isEmpty)
    }

    func testLockedFreeChapterIsRefusedWhenBrowserAlsoLocked() async throws {
        let path = "/724903_4.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [url: (200, Data(Self.loginWallChapter.utf8))])
        let page = FlooFakePage(pages: [url: Self.loginWallChapter])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path), book: Self.book(), context: context)
        guard case .refused(_, let reason) = result else { return XCTFail("expected refused") }
        XCTAssertTrue(reason.contains("Sign in"))
    }

    func testLockedOverHTTPButReadableInSignedInBrowser() async throws {
        let path = "/724903_5.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [url: (200, Data(Self.loginWallChapter.utf8))])
        let page = FlooFakePage(pages: [url: Self.freeChapter])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path), book: Self.book(), context: context)
        guard case .content(let content) = result else { return XCTFail("expected content") }
        XCTAssertTrue(content.html.contains("Line one"))
    }

    func testPaidChapterSkippedWhenRequested() async throws {
        let http = FlooStubHTTP(routes: [:])
        let page = FlooFakePage(pages: [:])
        let context = Self.context(http: http, web: FlooStubWeb(page: page), skipPaid: true)
        let result = try await FlooAdapter().parseChapter(Self.ref("/vip/724903/2.html", paid: true), book: Self.book(), context: context)
        guard case .refused = result else { return XCTFail("expected refused") }
        XCTAssertEqual(http.requestCount, 0)
        XCTAssertTrue(page.loadedURLs.isEmpty)
    }

    func testPaidChapterWithoutPurchaseIsRefused() async throws {
        let path = "/vip/724903/2.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [:])
        let page = FlooFakePage(pages: [url: Self.purchaseWallChapter])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path, paid: true), book: Self.book(), context: context)
        guard case .refused = result else { return XCTFail("expected refused") }
        // Paid chapters are read in the web profile only, never as a guest over HTTP.
        XCTAssertEqual(http.requestCount, 0)
    }

    func testBoughtVipChapterReturnsImages() async throws {
        let path = "/vip/724903/2.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [Self.imageURL: (200, Self.gifBytes)])
        let page = FlooFakePage(pages: [url: Self.imageChapterHTML])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path, index: 2, paid: true), book: Self.book(), context: context)
        guard case .content(let content) = result else { return XCTFail("expected content") }
        XCTAssertEqual(content.images.count, 1)
        XCTAssertEqual(content.images[0].filename, "images/0002_01.gif")
        XCTAssertEqual(content.images[0].data, Self.gifBytes)
        XCTAssertEqual(content.images[0].url.absoluteString, Self.imageURL)
        XCTAssertEqual(content.html, "<p><img src=\"images/0002_01.gif\" alt=\"\"/></p>")
    }

    func testVipImageRequestThatReturnsAPageIsRefused() async throws {
        let path = "/vip/724903/2.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [Self.imageURL: (200, Data("<html>please log in</html>".utf8))])
        let page = FlooFakePage(pages: [url: Self.imageChapterHTML])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path, paid: true), book: Self.book(), context: context)
        guard case .refused = result else { return XCTFail("expected refused") }
    }

    func testVipImageForbiddenIsRefused() async throws {
        let path = "/vip/724903/2.html"
        let url = "https://\(Self.bookHost)\(path)"
        let http = FlooStubHTTP(routes: [Self.imageURL: (403, Data())])
        let page = FlooFakePage(pages: [url: Self.imageChapterHTML])
        let context = Self.context(http: http, web: FlooStubWeb(page: page))
        let result = try await FlooAdapter().parseChapter(Self.ref(path, paid: true), book: Self.book(), context: context)
        guard case .refused = result else { return XCTFail("expected refused") }
    }
}

// MARK: - Stubs

private final class FlooRequestCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func increment() { lock.lock(); count += 1; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}

private struct FlooStubHTTP: HTTPFetching {
    var routes: [String: (Int, Data)]
    private let counter = FlooRequestCounter()

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

private final class FlooFakePage: WebPageDriving, @unchecked Sendable {
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

    /// `waitFor` conditions are immediately truthy so tests never wait for a selector.
    func evaluateJSON(_ functionBody: String) async throws -> Data { Data("true".utf8) }
    func addInitScript(_ source: String) async {}
    func currentURL() async -> URL? { snapshot().0 }
    func html() async throws -> String { snapshot().1 }
    func close() async {}
}

private struct FlooStubWeb: WebEngineProviding {
    var page: FlooFakePage
    func newPage(profile: String) async throws -> any WebPageDriving { page }
    func presentLogin(url: URL, profile: String, userAgentSuffix: String?) async throws {}
}
