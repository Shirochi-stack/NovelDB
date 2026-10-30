import XCTest
import NpiaCore
@testable import NpiaSources

final class FenrirAdapterTests: XCTestCase {
    // MARK: URL matching

    func testMatchesSeriesAndChapterURLs() {
        XCTAssertTrue(FenrirAdapter.matches(URL(string: "https://fenrirealm.com/series/my-book")!))
        XCTAssertTrue(FenrirAdapter.matches(URL(string: "https://www.fenrirealm.com/series/my-book/12")!))
    }

    func testRejectsOtherURLs() {
        XCTAssertFalse(FenrirAdapter.matches(URL(string: "https://fenrirealm.com/")!))
        XCTAssertFalse(FenrirAdapter.matches(URL(string: "https://fenrirealm.com/series")!))
        XCTAssertFalse(FenrirAdapter.matches(URL(string: "https://fenrirealm.com/user/my-book")!))
        XCTAssertFalse(FenrirAdapter.matches(URL(string: "https://example.com/series/my-book")!))
        XCTAssertFalse(FenrirAdapter.matches(URL(string: "https://notfenrirealm.com/series/my-book")!))
    }

    func testURLParts() {
        let parts = FenrirParsing.urlParts(URL(string: "https://fenrirealm.com/series/book-a/3.1")!)
        XCTAssertEqual(parts.series, "book-a")
        XCTAssertEqual(parts.chapter, "3.1")
        XCTAssertEqual(FenrirParsing.urlParts(URL(string: "https://fenrirealm.com/series/book-a")!).chapter, "")
    }

    // MARK: Paid detection

    func testPaidLocked() {
        XCTAssertTrue(FenrirParsing.paidLocked(["locked": ["price": 5]]))
        XCTAssertTrue(FenrirParsing.paidLocked(["locked": ["price": "5"]]))
        XCTAssertFalse(FenrirParsing.paidLocked(["locked": ["price": 0]]))
        XCTAssertFalse(FenrirParsing.paidLocked(["locked": ["price": 5, "unlocked_at": "2025-01-01"]]))
        XCTAssertFalse(FenrirParsing.paidLocked(["locked": ["price": 5], "bought": ["bought_at": "2025-01-01"]]))
        XCTAssertFalse(FenrirParsing.paidLocked(["locked": ["price": 5], "bypass": true]))
        XCTAssertFalse(FenrirParsing.paidLocked([:]))
    }

    // MARK: Retry delay

    func testRetryDelaySeconds() {
        XCTAssertEqual(FenrirParsing.retryDelay("12"), 12)
        XCTAssertEqual(FenrirParsing.retryDelay(" 0 "), 0)
    }

    func testRetryDelayDefaults() {
        XCTAssertEqual(FenrirParsing.retryDelay(nil), 30)
        XCTAssertEqual(FenrirParsing.retryDelay(""), 30)
        XCTAssertEqual(FenrirParsing.retryDelay("soon"), 30)
        XCTAssertEqual(FenrirParsing.retryDelay("-5"), 30)
    }

    func testRetryDelayHTTPDate() {
        let now = Date(timeIntervalSince1970: 1_445_412_400) // Wed, 21 Oct 2015 07:26:40 GMT
        XCTAssertEqual(FenrirParsing.retryDelay("Wed, 21 Oct 2015 07:28:00 GMT", now: now), 80, accuracy: 0.5)
        XCTAssertEqual(FenrirParsing.retryDelay("Wed, 21 Oct 2015 07:00:00 GMT", now: now), 0)
    }

    // MARK: Book

    private static let seriesJSON = """
    {"title":"Test Saga","status":"ongoing","cover":"/covers/a.jpg",
     "description":"<p>First line</p><p>Second line</p>",
     "user":{"name":"Writer One","username":"writer1"},
     "genres":[{"name":"Fantasy"}],"tags":[{"name":"Magic"},{"name":"Fantasy"}]}
    """

    private static let chaptersJSON = """
    {"data":[
      {"id":3,"uuid":"u3","number":3,"name":"Chapter 3","title":"Third","locked":{"price":4}},
      {"id":1,"uuid":"u1","number":1,"index":1,"name":"Chapter 1","title":"Start"},
      {"id":2,"uuid":"u2","number":2,"index":2,"name":"Chapter 2","title":"chapter 2","slug":"two"},
      {"id":9,"number":4,"part":1,"name":"","title":"Part"}
    ]}
    """

    func testMakeBookFromFixture() throws {
        let info = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(Self.seriesJSON.utf8)) as? [String: Any])
        let list = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(Self.chaptersJSON.utf8)) as? [String: Any])
        let rows = try XCTUnwrap(list["data"] as? [[String: Any]])
        let book = try XCTUnwrap(FenrirParsing.makeBook(info: info, rows: rows, slug: "test-saga"))

        XCTAssertEqual(book.source, .fenrir)
        XCTAssertEqual(book.title, "Test Saga")
        XCTAssertEqual(book.author, "Writer One")
        XCTAssertEqual(book.synopsis, "First line\nSecond line")
        XCTAssertEqual(book.coverURL?.absoluteString, "https://fenrirealm.com/covers/a.jpg")
        XCTAssertEqual(book.tags, ["Fantasy", "Magic"])
        XCTAssertEqual(book.language, "en")
        XCTAssertEqual(book.chapters.map { $0.index }, [1, 2, 3, 4])
        // Rows with an index come first, then by number.
        XCTAssertEqual(book.chapters.map { $0.title }, ["Chapter 1 - Start", "Chapter 2", "Chapter 3 - Third", "Part"])
        XCTAssertEqual(book.chapters.map { $0.isPaid }, [false, false, true, false])
        XCTAssertEqual(book.chapters[1].url?.absoluteString, "https://fenrirealm.com/series/test-saga/two")
        XCTAssertEqual(book.chapters[3].url?.absoluteString, "https://fenrirealm.com/series/test-saga/4.1")
        XCTAssertEqual(FenrirParsing.splitRefID(book.chapters[1].id).slug, "two")
        XCTAssertEqual(FenrirParsing.splitRefID(book.chapters[1].id).chapterID, "u2")
        XCTAssertEqual(FenrirParsing.splitRefID(book.chapters[3].id).chapterID, "9")
    }

    func testMakeBookWithoutChaptersIsNil() {
        XCTAssertNil(FenrirParsing.makeBook(info: ["title": "x"], rows: [["name": "no slug"]], slug: "x"))
    }

    func testParseBookOverStubHTTP() async throws {
        let http = FenrirStubHTTP(routes: [
            "/api/new/v2/series/test-saga/chapters": FenrirStubHTTP.chaptersJSONText,
            "/api/new/v2/series/test-saga": FenrirStubHTTP.seriesJSONText,
        ])
        let context = SourceContext(http: http, web: FenrirStubEngine(page: FenrirFakePage()), secrets: InMemorySecretStore(), settings: AppSettings())
        let book = try await FenrirAdapter().parseBook(URL(string: "https://fenrirealm.com/series/test-saga")!, context: context)
        XCTAssertEqual(book.chapters.count, 4)
        XCTAssertEqual(book.id, "test-saga")
    }

    // MARK: Chapter content

    func testCleanHTMLDropsHiddenBlocksAndInvisibleChars() {
        let source = """
        <style>.junk { display:none } .keep { color: red }</style>
        <p>Real\u{200B} text</p><p class="junk">poison</p><p aria-hidden="true">poison2</p>
        <p style="font-size:0">poison3</p><p class="keep">kept</p><p hidden>poison4</p>
        """
        let blocks = FenrirParsing.htmlBlocks(FenrirParsing.cleanHTML(source), base: "https://fenrirealm.com/")
        XCTAssertEqual(blocks, [.text("Real text"), .text("kept")])
    }

    func testHTMLBlocksKeepImagesAndLineBreaks() {
        let blocks = FenrirParsing.htmlBlocks("<div>a<br>b</div><img src=\"/x/p.png\"><p>c\nd</p>", base: "https://fenrirealm.com/")
        XCTAssertEqual(blocks, [.text("a"), .text("b"), .image("https://fenrirealm.com/x/p.png"), .text("c"), .text("d")])
    }

    func testBuildChapterFromDocument() throws {
        let ref = ChapterRef(id: "1|u1", index: 7, title: "Chapter 7", url: nil)
        let doc = """
        {"type":"doc","content":[
          {"type":"heading","content":[{"type":"text","text":"Chapter 7"}]},
          {"type":"paragraph","content":[{"type":"text","text":"Hello "},{"type":"text","text":"world"}]},
          {"type":"image","attrs":{"src":"/img/a.webp"}},
          {"type":"paragraph","content":[{"type":"text","text":"x"},{"type":"hardBreak"},{"type":"text","text":"y <z>"}]}
        ]}
        """
        let content = try XCTUnwrap(FenrirParsing.buildChapter(payload: ["content": doc], ref: ref))
        XCTAssertTrue(content.html.contains("<p>Hello world</p>"))
        XCTAssertFalse(content.html.contains(">Chapter 7<"), "title heading is dropped")
        XCTAssertTrue(content.html.contains("<p>y &lt;z&gt;</p>"))
        XCTAssertEqual(content.images.map { $0.filename }, ["images/0007_01.webp"])
        XCTAssertTrue(content.html.contains("src=\"images/0007_01.webp\""))
    }

    func testBuildChapterPlainTextAndEmpty() {
        let ref = ChapterRef(id: "1|u1", index: 1, title: "T")
        let content = FenrirParsing.buildChapter(payload: ["content": "one\r\n\r\ntwo", "content_format": "text"], ref: ref)
        XCTAssertEqual(content?.html, "<div class=\"fenrir-content\">\n<p>one</p>\n<p>two</p>\n</div>")
        XCTAssertNil(FenrirParsing.buildChapter(payload: ["content": "   ", "content_format": "text"], ref: ref))
        XCTAssertNil(FenrirParsing.buildChapter(payload: ["content": 5], ref: ref))
    }

    // MARK: Batch over a fake page

    private func makeContext(page: FenrirFakePage) -> SourceContext {
        var settings = AppSettings()
        settings.shared.intervalMin = 0
        settings.shared.intervalMax = 0
        return SourceContext(http: FenrirStubHTTP(routes: [:]), web: FenrirStubEngine(page: page), secrets: InMemorySecretStore(), settings: settings)
    }

    private func makeBook() -> BookInfo {
        let chapters = [
            ChapterRef(id: "one|u1", index: 1, title: "Chapter 1", url: URL(string: "https://fenrirealm.com/series/s/one")),
            ChapterRef(id: "two|u2", index: 2, title: "Chapter 2", url: URL(string: "https://fenrirealm.com/series/s/two"), isPaid: true),
            ChapterRef(id: "three|u3", index: 3, title: "Chapter 3", url: URL(string: "https://fenrirealm.com/series/s/three")),
        ]
        return BookInfo(source: .fenrir, id: "s", url: URL(string: "https://fenrirealm.com/series/s")!, title: "S", chapters: chapters)
    }

    func testBatchReadsOwnedAndRefusesPaidAndLocked() async throws {
        let replies = """
        [{"status":200,"payload":{"content_format":"text","content":"Hello there"}},
         {"status":200,"payload":{"content_format":"locked","content":""}}]
        """
        let page = FenrirFakePage(tokenActive: true, replies: replies)
        let book = makeBook()
        let results = try await FenrirAdapter().parseChapters(book.chapters, book: book, context: makeContext(page: page))
        XCTAssertEqual(results.count, 3)
        guard case .content(let first) = results[0] else { return XCTFail("chapter 1 should be content") }
        XCTAssertTrue(first.html.contains("<p>Hello there</p>"))
        guard case .refused(_, let paidReason) = results[1] else { return XCTFail("paid chapter should be refused") }
        XCTAssertFalse(paidReason.isEmpty)
        guard case .refused = results[2] else { return XCTFail("locked chapter should be refused") }
    }

    func testBatchRetriesRateLimit() async throws {
        let page = FenrirFakePage(tokenActive: true, repliesSequence: [
            "[{\"status\":429,\"retryAfter\":\"0\"}]",
            "[{\"status\":200,\"payload\":{\"content_format\":\"text\",\"content\":\"After retry\"}}]",
        ])
        let book = makeBook()
        let ref = book.chapters[0]
        let result = try await FenrirAdapter().parseChapter(ref, book: book, context: makeContext(page: page))
        guard case .content(let content) = result else { return XCTFail("expected content after retry") }
        XCTAssertTrue(content.html.contains("After retry"))
    }
}

// MARK: - Stubs

private struct FenrirStubHTTP: HTTPFetching {
    var routes: [String: String]

    static let seriesJSONText = """
    {"title":"Test Saga","user":{"name":"Writer One"},"description":"<p>d</p>"}
    """
    static let chaptersJSONText = """
    [{"number":1,"index":1,"name":"Chapter 1"},{"number":2,"index":2,"name":"Chapter 2"},
     {"number":3,"index":3,"name":"Chapter 3","locked":{"price":2}},{"number":4,"index":4,"name":"Chapter 4"}]
    """

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        // Longest matching route wins so the chapter list beats the series route.
        let path = request.url.path
        let key = routes.keys.filter { path.hasSuffix($0) }.max { $0.count < $1.count }
        guard let key, let body = routes[key] else {
            return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
        }
        return HTTPResponse(url: request.url, status: 200, headers: [:], body: Data(body.utf8))
    }
}

private final class FenrirFakePage: WebPageDriving, @unchecked Sendable {
    private let lock = NSLock()
    private let tokenActive: Bool
    private var sequence: [String]

    init(tokenActive: Bool = true, replies: String = "[]") {
        self.tokenActive = tokenActive
        self.sequence = [replies]
    }

    init(tokenActive: Bool, repliesSequence: [String]) {
        self.tokenActive = tokenActive
        self.sequence = repliesSequence
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {}

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        if functionBody.contains("__fn") {
            lock.lock()
            defer { lock.unlock() }
            let next = sequence.count > 1 ? sequence.removeFirst() : (sequence.first ?? "[]")
            return Data(next.utf8)
        }
        if functionBody.contains("read-token") {
            return Data((tokenActive ? "true" : "false").utf8)
        }
        return Data("[]".utf8)
    }

    func addInitScript(_ source: String) async {}
    func currentURL() async -> URL? { nil }
    func html() async throws -> String { "" }
    func close() async {}
}

private struct FenrirStubEngine: WebEngineProviding {
    let page: FenrirFakePage
    func newPage(profile: String) async throws -> any WebPageDriving { page }
    func presentLogin(url: URL, profile: String, userAgentSuffix: String?) async throws {}
}
