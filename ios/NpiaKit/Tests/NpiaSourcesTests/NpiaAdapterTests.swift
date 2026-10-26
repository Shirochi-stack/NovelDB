import XCTest
import NpiaCore
@testable import NpiaSources

private let host = "\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
private let imageHost = "images." + host

/// Hand-made, trimmed versions of the npia pages the desktop scraper reads.
private enum Fixture {
    static let novelPage = """
    <html><head>
    <title>Site - Ignored - Fallback Title</title>
    <meta property="og:image" content="https://\(imageHost)/imagebox/cover/ab/cover_q_1.jpg">
    <meta name="description" content="short">
    </head><body>
    <script>var productName = 'The Tower &amp; Me';</script>
    <div class="epnew-cover-box"><a href="//\(imageHost)/imagebox/original/ab/real_ori.file">x</a></div>
    <img class="cover_img" src="//\(imageHost)/imagebox/cover/readycover.jpg">
    <div class="epnew-novel-info"><p class="in-badge"><span class="b_plus">PLUS</span><span class="b_comp s_inv">완결</span></p></div>
    <a class="writer-name" href="/user/1">  Writer Kim  </a>
    <span class="tag">#Fantasy</span><span class="tag" data-x="1">#fantasy</span><span class="tag">#Regression</span>
    <div class="synopsis">Line one<br/>Line &lt;two&gt;</div>
    <table class="notice_table">
      <tr><td onclick="location='/viewer/902'"><b>Second notice</b></td></tr>
      <tr><td onclick="location='/viewer/901'"><b>First <i>notice</i></b></td></tr>
    </table>
    </body></html>
    """

    static let episodePage0 = """
    <table>
    <tr class="ep_style5"><td><i id="bookmark_101"></i>Chapter &amp; One</b></td></tr>
    <tr class="ep_style5"><td><span class="b_plus">PLUS</span><i id="bookmark_102"></i><span>Chapter</span> Two</b></td></tr>
    </table>
    """

    static let episodePage1 = """
    <tr><td><i id="bookmark_103"></i>Chapter Three</b></td></tr>
    <tr><td><i id="bookmark_101"></i>Duplicate</b></td></tr>
    """

    static func viewer(_ segments: [String]) -> String {
        let items = segments.map { try! String(data: JSONSerialization.data(withJSONObject: ["text": $0]), encoding: .utf8)! }
        return "{\"s\":[\(items.joined(separator: ","))]}"
    }
}

/// Routes requests by path and records them.
private final class RoutingHTTP: HTTPFetching, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [HTTPRequest] = []
    private let route: @Sendable (HTTPRequest, Int) -> (Int, String)

    init(route: @escaping @Sendable (HTTPRequest, Int) -> (Int, String)) {
        self.route = route
    }

    var requests: [HTTPRequest] {
        lock.lock(); defer { lock.unlock() }
        return log
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        lock.lock()
        let count = log.filter { $0.url == request.url && $0.body == request.body }.count
        log.append(request)
        lock.unlock()
        let (status, body) = route(request, count)
        return HTTPResponse(url: request.url, status: status, headers: [:], body: Data(body.utf8))
    }
}

private final class RecordingJar: NpiaCookieSetting, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String] = []
    var keys: [String] {
        lock.lock(); defer { lock.unlock() }
        return values
    }
    func setLoginKey(_ key: String) {
        lock.lock(); defer { lock.unlock() }
        values.append(key)
    }
}

private func form(_ request: HTTPRequest) -> [String: String] {
    let text = String(decoding: request.body ?? Data(), as: UTF8.self)
    var fields: [String: String] = [:]
    for pair in text.split(separator: "&") {
        let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
        let value = parts.count > 1 ? parts[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? parts[1] : ""
        fields[parts[0]] = value
    }
    return fields
}

final class NpiaAdapterTests: XCTestCase {
    private func context(_ http: any HTTPFetching, secrets: InMemorySecretStore = InMemorySecretStore(), configure: (inout AppSettings) -> Void = { _ in }) -> SourceContext {
        var settings = AppSettings()
        settings.shared.maxRetries = 3
        configure(&settings)
        return SourceContext(http: http, web: StubWeb(), secrets: secrets, settings: settings)
    }

    // MARK: - URLs

    func testMatchesOnlyMainSiteNovelPages() {
        XCTAssertTrue(NpiaAdapter.matches(URL(string: "https://\(host)/novel/346898")!))
        XCTAssertTrue(NpiaAdapter.matches(URL(string: "https://www.\(host)/novel/346898?x=1")!))
        XCTAssertFalse(NpiaAdapter.matches(URL(string: "https://global.\(host)/novel/346898")!))
        XCTAssertFalse(NpiaAdapter.matches(URL(string: "https://\(host)/search")!))
        XCTAssertFalse(NpiaAdapter.matches(URL(string: "https://example.com/novel/346898")!))
        XCTAssertEqual(SourceCatalog.adapterType(for: URL(string: "https://\(host)/novel/5")!)?.id, .npia)
    }

    // MARK: - Parsing

    func testMetadata() {
        let meta = NpiaParser.parseMetadata(Fixture.novelPage, novelID: "1")
        XCTAssertEqual(meta.title, "The Tower & Me")
        XCTAssertEqual(meta.author, "Writer Kim")
        XCTAssertEqual(meta.tags, ["Fantasy", "Regression"])
        XCTAssertEqual(meta.synopsis, "Line one\nLine <two>")
        XCTAssertEqual(meta.status, "Completed")
        XCTAssertEqual(meta.coverURL?.absoluteString, "https://\(imageHost)/imagebox/original/ab/real_ori.file")
    }

    func testMetadataFallbacks() {
        let page = """
        <meta property="og:title" content="Site - Tagline - Real Title">
        <meta name="description" content="short"><meta property="og:description" content="a longer description">
        <div class="epnew-novel-info"><p class="in-badge"><span class="b_plus">PLUS</span></p></div>
        """
        let meta = NpiaParser.parseMetadata(page, novelID: "9")
        XCTAssertEqual(meta.title, "Real Title")
        XCTAssertEqual(meta.author, "Unknown Author")
        XCTAssertEqual(meta.synopsis, "a longer description")
        XCTAssertEqual(meta.status, "Ongoing")
        XCTAssertNil(meta.coverURL)
        XCTAssertNil(NpiaParser.parseStatus("<p>login please</p>"))
        XCTAssertEqual(NpiaParser.parseMetadata("", novelID: "9").title, "Novel_9")
    }

    func testEpisodePageMarksPlusRows() {
        let rows = NpiaParser.parseEpisodePage(Fixture.episodePage0)
        XCTAssertEqual(rows.map(\.id), ["101", "102"])
        XCTAssertEqual(rows.map(\.title), ["Chapter & One", "Chapter Two"])
        XCTAssertEqual(rows.map(\.isPaid), [false, true])
    }

    func testNoticesAreOldestFirst() {
        let notices = NpiaParser.parseNotices(Fixture.novelPage)
        XCTAssertEqual(notices.map(\.id), ["901", "902"])
        XCTAssertEqual(notices.map(\.title), ["Notice: First notice", "Notice: Second notice"])
        XCTAssertTrue(NpiaParser.parseNotices("<p>none</p>").isEmpty)
    }

    func testRenderSegments() {
        let segments: [[String: Any]] = [
            ["text": "<div class=\"cover-wrapper\">skip</div>"],
            ["text": "&nbsp;<b>Bold</b> &lt;주인공&gt; <주인공> <span>plain</span><br>next"],
            ["text": "<p style='height: 0px;'>hidden</p>"],
            ["text": "<img src=\"//\(imageHost)/imagebox/a/b.png\"><img src=\"file:///C:/clip.png\">caption"],
            ["text": "<img src=\"/imagebox/c/d\">"],
            ["text": ""],
        ]
        var options = NpiaParser.RenderOptions()
        options.stripLeadingSpaces = true
        options.imagePrefix = "0007"
        let (html, images) = NpiaParser.renderSegments(segments, options: options)
        XCTAssertEqual(html, """
        <p><b>Bold</b> &lt;주인공&gt; &lt;주인공&gt; plain<br/>next</p>\
        <p><img alt="1" src="images/0007_01.png" width="100%"/>caption</p>\
        <p><img alt="2" src="images/0007_02.jpg" width="100%"/></p>
        """)
        XCTAssertEqual(images.map(\.filename), ["images/0007_01.png", "images/0007_02.jpg"])
        XCTAssertEqual(images.map(\.url.absoluteString), [
            "https://\(imageHost)/imagebox/a/b.png", "https://\(imageHost)/imagebox/c/d",
        ])

        options.saveImageURLsOnly = true
        let remote = NpiaParser.renderSegments([["text": "<img src=\"//\(imageHost)/imagebox/a/b.png\">"]], options: options)
        XCTAssertTrue(remote.images.isEmpty)
        XCTAssertTrue(remote.html.contains("src=\"https://\(imageHost)/imagebox/a/b.png\""))

        options.removeNewlines = true
        XCTAssertEqual(NpiaParser.renderSegments([["text": "a<br>b\nc"]], options: options).html, "<p>abc</p>")
    }

    func testValidatePayload() {
        if case .failure(.invalid) = NpiaParser.validatePayload("<!DOCTYPE html><html></html>") {} else { XCTFail("html page") }
        if case .failure(.invalid) = NpiaParser.validatePayload("not json") {} else { XCTFail("bad json") }
        if case .failure(.blocked) = NpiaParser.validatePayload(#"{"status":500,"errmsg":"잘못된 접근입니다"}"#) {} else { XCTFail("blocked 500") }
        if case .failure(.invalid) = NpiaParser.validatePayload(#"{"code":"500","message":"oops"}"#) {} else { XCTFail("plain 500") }
        if case .failure(.blocked) = NpiaParser.validatePayload(#"{"errmsg":"로그인 후 이용해주세요"}"#) {} else { XCTFail("login wall") }
        // Chapter text that mentions logging in is still content.
        if case .success(let segments) = NpiaParser.validatePayload(Fixture.viewer(["로그인했다"])) {
            XCTAssertEqual(segments.count, 1)
        } else {
            XCTFail("content")
        }
    }

    // MARK: - Network flow

    func testParseBookLogsInAndCollectsChapters() async throws {
        let http = RoutingHTTP { request, _ in
            switch request.url.path {
            case "/proc/login": return (200, "<script>alert('감사합니다')</script>")
            case "/novel/123": return (200, Fixture.novelPage)
            case "/proc/episode_list":
                switch form(request)["page"] {
                case "0": return (200, Fixture.episodePage0)
                case "1": return (200, Fixture.episodePage1)
                default: return (200, "<table></table>")
                }
            default: return (404, "")
            }
        }
        let secrets = InMemorySecretStore([.npiaEmail: "user@example.com", .npiaPassword: "pa ss&word"])
        let jar = RecordingJar()
        let ctx = context(http, secrets: secrets) { $0.npia.autoLogin = true }

        let book = try await NpiaAdapter(cookies: jar, retryDelay: { _ in 0 }).parseBook(URL(string: "https://\(host)/novel/123")!, context: ctx)

        let login = try XCTUnwrap(http.requests.first)
        XCTAssertEqual(login.url.path, "/proc/login")
        XCTAssertEqual(form(login), ["redirectrurl": "", "email": "user@example.com", "wd": "pa ss&word"])
        let key = try XCTUnwrap(secrets.string(for: .npiaLoginKey))
        XCTAssertNotNil(key.range(of: #"^[0-9a-f]{32}_[0-9a-f]{32}$"#, options: .regularExpression))
        XCTAssertEqual(jar.keys, [key])

        XCTAssertEqual(book.id, "123")
        XCTAssertEqual(book.title, "The Tower & Me")
        XCTAssertEqual(book.chapters.map(\.id), ["101", "102", "103"])
        XCTAssertEqual(book.chapters.map(\.index), [1, 2, 3])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true, false])
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "https://\(host)/viewer/101")
        XCTAssertEqual(book.notices.map(\.id), ["901", "902"])
        XCTAssertTrue(book.notices.allSatisfy(\.isNotice))
        let pages = http.requests.filter { $0.url.path == "/proc/episode_list" }.map { form($0)["page"] }
        XCTAssertEqual(pages, ["0", "1", "2"])
        XCTAssertEqual(form(http.requests.first { $0.url.path == "/proc/episode_list" }!)["sort"], "DOWN")
    }

    func testParseBookUsesSavedKeyWithoutAutoLoginAndSkipsNotices() async throws {
        let http = RoutingHTTP { request, _ in
            switch request.url.path {
            case "/novel/5": return (200, Fixture.novelPage)
            case "/proc/episode_list": return (200, form(request)["page"] == "0" ? Fixture.episodePage0 : "")
            default: return (500, "")
            }
        }
        let secrets = InMemorySecretStore([.npiaLoginKey: "saved_key", .npiaEmail: "a", .npiaPassword: "b"])
        let jar = RecordingJar()
        let ctx = context(http, secrets: secrets) { $0.npia.includeNotices = false }
        let book = try await NpiaAdapter(cookies: jar).parseBook(URL(string: "https://\(host)/novel/5")!, context: ctx)
        XCTAssertEqual(jar.keys, ["saved_key"])
        XCTAssertFalse(http.requests.contains { $0.url.path == "/proc/login" })
        XCTAssertTrue(book.notices.isEmpty)
        XCTAssertEqual(book.chapters.count, 2)
    }

    func testChapterListRequiringLoginThrows() async {
        let http = RoutingHTTP { request, _ in
            request.url.path == "/novel/5" ? (200, Fixture.novelPage) : (200, "Authentication required")
        }
        do {
            _ = try await NpiaAdapter(cookies: RecordingJar()).parseBook(URL(string: "https://\(host)/novel/5")!, context: context(http))
            XCTFail("expected loginRequired")
        } catch {
            XCTAssertEqual(error as? SourceError, .loginRequired(.npia))
        }
    }

    func testParseChapterResults() async throws {
        let http = RoutingHTTP { request, count in
            switch request.url.lastPathComponent {
            case "1": return (200, Fixture.viewer(["<b>Hello</b>", "<img src=\"//\(imageHost)/imagebox/x/y.gif\">"]))
            case "2": return (200, "  ")
            case "3": return (200, #"{"status":500,"errmsg":"잘못된 접근"}"#)
            case "4": return count == 0 ? (502, "") : (200, Fixture.viewer(["recovered"]))
            case "5": return (200, "<html><body>error</body></html>")
            default: return (404, "")
            }
        }
        let adapter = NpiaAdapter(cookies: RecordingJar(), retryDelay: { _ in 0 })
        let ctx = context(http)
        let book = BookInfo(source: .npia, id: "b", url: URL(string: "https://\(host)/novel/1")!, title: "B")
        func ref(_ id: Int, paid: Bool = false, notice: Bool = false) -> ChapterRef {
            ChapterRef(id: "\(id)", index: id, title: "C\(id)", isPaid: paid, isNotice: notice)
        }

        guard case .content(let content) = try await adapter.parseChapter(ref(1, notice: true), book: book, context: ctx) else {
            return XCTFail("chapter 1 should have content")
        }
        XCTAssertEqual(content.html, #"<p><b>Hello</b></p><p><img alt="1" src="images/n0001_01.gif" width="100%"/></p>"#)
        XCTAssertEqual(content.images.map(\.filename), ["images/n0001_01.gif"])

        if case .refused = try await adapter.parseChapter(ref(2, paid: true), book: book, context: ctx) {} else { XCTFail("unpaid PLUS chapter") }
        if case .failed = try await adapter.parseChapter(ref(2), book: book, context: ctx) {} else { XCTFail("empty free chapter") }
        if case .refused = try await adapter.parseChapter(ref(3), book: book, context: ctx) {} else { XCTFail("blocked chapter") }
        if case .content(let recovered) = try await adapter.parseChapter(ref(4), book: book, context: ctx) {
            XCTAssertEqual(recovered.html, "<p>recovered</p>")
        } else {
            XCTFail("retry should recover")
        }
        if case .failed(_, let message) = try await adapter.parseChapter(ref(5), book: book, context: ctx) {
            XCTAssertTrue(message.contains("3 attempts"), message)
        } else {
            XCTFail("html error page")
        }
        let viewerCalls = http.requests.filter { $0.url.lastPathComponent == "5" }
        XCTAssertEqual(viewerCalls.count, 3)
        XCTAssertEqual(viewerCalls.first?.method, "POST")
        XCTAssertEqual(viewerCalls.first?.url.absoluteString, "https://\(host)/proc/viewer_data/5")
    }
}
