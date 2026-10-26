import XCTest
import NpiaCore
@testable import NpiaSources

private struct GlobalNpiaStubHTTP: HTTPFetching {
    /// First route whose needle is contained in the URL wins.
    var routes: [(needle: String, status: Int, body: String)]

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        let text = request.url.absoluteString
        for route in routes where text.contains(route.needle) {
            return HTTPResponse(url: request.url, status: route.status, headers: [:], body: Data(route.body.utf8))
        }
        return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data("{}".utf8))
    }
}

private struct GlobalNpiaStubWeb: WebEngineProviding {
    func newPage(profile: String) async throws -> any WebPageDriving { throw WebEngineError.unavailable }
    func presentLogin(url: URL, profile: String) async throws { throw WebEngineError.unavailable }
}

final class GlobalNpiaAdapterTests: XCTestCase {
    private let jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln"
    private var base: String { GlobalNpiaParsing.base }

    private func context(_ routes: [(needle: String, status: Int, body: String)]) -> SourceContext {
        SourceContext(
            http: GlobalNpiaStubHTTP(routes: routes), web: GlobalNpiaStubWeb(),
            secrets: InMemorySecretStore(), settings: AppSettings()
        )
    }

    private func ref(_ no: String = "501", index: Int = 1) -> ChapterRef {
        ChapterRef(id: no, index: index, title: "Episode \(index)", url: URL(string: "\(base)/viewer/\(no)"))
    }

    private func book() -> BookInfo {
        BookInfo(source: .globalNpia, id: "77", url: URL(string: "\(base)/novel/77")!, title: "T", language: "en")
    }

    // MARK: matching

    func testMatches() {
        XCTAssertTrue(GlobalNpiaAdapter.matches(URL(string: "\(base)/novel/123")!))
        XCTAssertTrue(GlobalNpiaAdapter.matches(URL(string: "\(base)/novel/123/?x=1")!))
        XCTAssertTrue(GlobalNpiaAdapter.matches(URL(string: "\(base)/novel/123?x=1")!))
        XCTAssertFalse(GlobalNpiaAdapter.matches(URL(string: "\(base)/novel/abc")!))
        XCTAssertFalse(GlobalNpiaAdapter.matches(URL(string: "\(base)/viewer/123")!))
        XCTAssertFalse(GlobalNpiaAdapter.matches(URL(string: "https://example.com/novel/123")!))
        XCTAssertFalse(GlobalNpiaAdapter.matches(URL(string: "https://api-global.example.com/novel/1")!))
        let main = "https://\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com/novel/123"
        XCTAssertFalse(GlobalNpiaAdapter.matches(URL(string: main)!))
    }

    func testIDExtraction() {
        XCTAssertEqual(GlobalNpiaParsing.novelID(from: URL(string: "\(base)/novel/42/x")!), "42")
        XCTAssertEqual(GlobalNpiaParsing.episodeID(from: URL(string: "\(base)/viewer/9001")!), "9001")
        XCTAssertEqual(GlobalNpiaParsing.episodeID(from: URL(string: "\(base)/novel/9")!), "")
    }

    // MARK: helpers

    func testAdAndAccessDetection() throws {
        let ad = try JSONSerialization.jsonObject(with: Data(#"{"code":"0010","result":{"name":"NOVEL_ERROR","message":"Basic advertisement required"}}"#.utf8))
        XCTAssertTrue(GlobalNpiaParsing.adRequired(status: 403, payload: ad))
        XCTAssertFalse(GlobalNpiaParsing.adRequired(status: 200, payload: ad))
        let denied = try JSONSerialization.jsonObject(with: Data(#"{"errmsg":"Please purchase this episode"}"#.utf8))
        XCTAssertTrue(GlobalNpiaParsing.accessDenied(status: 400, payload: denied))
        XCTAssertFalse(GlobalNpiaParsing.accessDenied(status: 500, payload: ["error": "boom"]))
    }

    func testSavedLoginCookies() {
        XCTAssertFalse(GlobalNpiaParsing.hasSavedLogin(cookies: ["USERKEY": "u"]))
        XCTAssertTrue(GlobalNpiaParsing.hasSavedLogin(cookies: ["USERKEY": "u", "TKEY": "t"]))
        XCTAssertTrue(GlobalNpiaParsing.hasSavedLogin(cookies: ["USERKEY": "u", "LOGINKEY": "k", "ISLOGIN": "1"]))
        XCTAssertFalse(GlobalNpiaParsing.hasSavedLogin(cookies: ["USERKEY": "u", "LOGINKEY": "k", "ISLOGIN": "0"]))
    }

    func testLoginToken() throws {
        let payload = try JSONSerialization.jsonObject(with: Data(#"{"statusCode":200,"result":{"LOGINAT":"a%2Bb"}}"#.utf8))
        XCTAssertEqual(GlobalNpiaParsing.loginToken(status: 200, payload: payload), "a+b")
        XCTAssertNil(GlobalNpiaParsing.loginToken(status: 401, payload: payload))
    }

    func testTicketTokenOrder() throws {
        XCTAssertTrue(GlobalNpiaParsing.looksLikeJWT(jwt))
        XCTAssertFalse(GlobalNpiaParsing.looksLikeJWT("abc"))
        let plain = try JSONSerialization.jsonObject(with: Data("{\"result\":{\"_t\":\"plain\",\"t\":\"\(jwt)\"}}".utf8))
        XCTAssertEqual(GlobalNpiaParsing.extractTicketToken(plain).token, jwt)
        let nested = try JSONSerialization.jsonObject(with: Data("{\"result\":{\"x\":{\"token\":\"\(jwt)\"}}}".utf8))
        XCTAssertEqual(GlobalNpiaParsing.extractTicketToken(nested).token, jwt)
        let direct = try JSONSerialization.jsonObject(with: Data("{\"result\":{\"url\":\"\(GlobalNpiaParsing.api)/v1/novel/episode/content?_t=\(jwt)\"}}".utf8))
        let found = GlobalNpiaParsing.extractTicketToken(direct)
        XCTAssertEqual(found.token, jwt)
        XCTAssertTrue(found.directURL.contains("/v1/novel/episode/content"))
        XCTAssertEqual(GlobalNpiaParsing.extractTicketToken(["result": [String: Any]()]).token, "")
    }

    func testRawContentOrdering() throws {
        let payload = try JSONSerialization.jsonObject(with: Data(#"{"result":{"data":{"epi_content2":"<p>C</p>","epi_content":"<p>A</p>","epi_content1":"<p>B</p>","other":"x"}}}"#.utf8))
        XCTAssertEqual(GlobalNpiaParsing.rawContent(payload), "<p>A</p><p>B</p><p>C</p>")
        let fallback = try JSONSerialization.jsonObject(with: Data(#"{"result":{"content":" <p>Z</p> "}}"#.utf8))
        XCTAssertEqual(GlobalNpiaParsing.rawContent(fallback), "<p>Z</p>")
    }

    func testSrcsetAndImageExtension() {
        XCTAssertEqual(GlobalNpiaParsing.bestSrcset("a.jpg 1x, b.jpg 2x"), "b.jpg")
        XCTAssertEqual(GlobalNpiaParsing.bestSrcset("a.jpg 320w, b.jpg 800w, c.jpg 480w"), "b.jpg")
        XCTAssertEqual(GlobalNpiaParsing.imageExtension(for: "https://x.test/p/a.PNG"), "png")
        XCTAssertEqual(GlobalNpiaParsing.imageExtension(for: "https://x.test/p/a"), "jpg")
        XCTAssertEqual(GlobalNpiaParsing.imageExtension(for: "data:image/webp;base64,AAAA"), "webp")
    }

    // MARK: book

    private let novelJSON = """
    {"statusCode":200,"result":{"novel":{"novel_name":" Test Novel ","novel_story":"Story","flag_complete":"1","novel_full_img":"//img.test/c.jpg","tag_list":[{"tag_name":"fantasy"}]},"info":{"epi_cnt":3},"writer_list":[{"writer_name":"Writer"}],"cate_list":[{"cate_name":"Action"}]}}
    """
    private let listJSON = """
    {"result":{"list":[{"episode_no":303,"epi_num":3,"epi_title":"Third"},{"episode_no":101,"epi_num":1,"epi_title":"First"},{"episode_no":202,"epi_num":2,"epi_title":" "},{"episode_no":101,"epi_num":1,"epi_title":"Dup"}]}}
    """

    func testMakeBookFromFixtures() throws {
        let novel = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(novelJSON.utf8)) as? [String: Any])
        let list = try XCTUnwrap((JSONSerialization.jsonObject(with: Data(listJSON.utf8)) as? [String: Any])?["result"] as? [String: Any])
        let book = try GlobalNpiaParsing.makeBook(novelID: "77", novelPayload: novel, episodeList: try XCTUnwrap(list["list"] as? [Any]))
        XCTAssertEqual(book.title, "Test Novel")
        XCTAssertEqual(book.author, "Writer")
        XCTAssertEqual(book.status, "Completed")
        XCTAssertEqual(book.language, "en")
        XCTAssertEqual(book.coverURL?.absoluteString, "https://img.test/c.jpg")
        XCTAssertEqual(book.tags, ["Action", "fantasy"])
        XCTAssertEqual(book.chapters.map(\.id), ["101", "202", "303"])
        XCTAssertEqual(book.chapters.map(\.index), [1, 2, 3])
        XCTAssertEqual(book.chapters[1].title, "Episode 2")
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "\(base)/viewer/101")
        XCTAssertTrue(book.chapters.allSatisfy { !$0.isPaid })
    }

    func testParseBookThroughStub() async throws {
        let ctx = context([
            (needle: "/v1/novel/episode/list", status: 200, body: listJSON),
            (needle: "/v1/novel", status: 200, body: novelJSON),
        ])
        let book = try await GlobalNpiaAdapter().parseBook(URL(string: "\(base)/novel/77")!, context: ctx)
        XCTAssertEqual(book.id, "77")
        XCTAssertEqual(book.chapters.count, 3)
    }

    func testParseBookMetadataFailureThrows() async {
        let ctx = context([(needle: "/v1/novel", status: 404, body: "{\"message\":\"nope\"}")])
        do {
            _ = try await GlobalNpiaAdapter().parseBook(URL(string: "\(base)/novel/77")!, context: ctx)
            XCTFail("expected a throw")
        } catch {
            XCTAssertTrue(error is SourceError)
        }
    }

    // MARK: chapter HTML

    func testProcessChapterHTML() throws {
        let raw = """
        <p>Hello<br>World&nbsp;!</p><script>evil()</script>
        <picture><source srcset="/a/small.jpg 100w, /a/big.webp 900w"><img src="/a/fallback.png"></picture>
        <img data-src="https://cdn.test/x/lazy.png#frag" src="data:image/gif;base64,R0lGOD">
        <div style="background:url('/bg/pic.jpg')">t</div><iframe src="x"></iframe>
        """
        let page = try XCTUnwrap(URL(string: "\(base)/viewer/501"))
        let processed = try XCTUnwrap(GlobalNpiaParsing.processChapterHTML(raw, chapterURL: page, chapterIndex: 7))
        XCTAssertFalse(processed.html.contains("script"))
        XCTAssertFalse(processed.html.contains("iframe"))
        XCTAssertFalse(processed.html.contains("<picture"))
        XCTAssertFalse(processed.html.contains("&nbsp;"))
        XCTAssertTrue(processed.html.contains("<br/>"))
        XCTAssertTrue(processed.html.hasPrefix("<div class=\"global-npia-content\">"))
        XCTAssertEqual(processed.assets.map(\.url), [
            "\(base)/a/big.webp", "https://cdn.test/x/lazy.png", "\(base)/bg/pic.jpg",
        ])
        XCTAssertEqual(processed.assets.map(\.filename), ["images/0007_01.webp", "images/0007_02.png", "images/0007_03.jpg"])
        XCTAssertTrue(processed.html.contains("src=\"images/0007_01.webp\""))
        XCTAssertTrue(processed.html.contains("images/0007_03.jpg"))
    }

    func testEmptyChapterHTMLIsNil() throws {
        let page = try XCTUnwrap(URL(string: "\(base)/viewer/1"))
        XCTAssertNil(try GlobalNpiaParsing.processChapterHTML("<script>x</script>", chapterURL: page, chapterIndex: 1))
    }

    // MARK: chapter fetch

    func testParseChapterReturnsContent() async throws {
        let ticket = "{\"result\":{\"_t\":\"\(jwt)\"}}"
        let content = "{\"result\":{\"data\":{\"epi_content\":\"<p>Hello</p>\"}}}"
        let ctx = context([
            (needle: "/v1/novel/episode/content", status: 200, body: content),
            (needle: "/v1/novel/episode", status: 200, body: ticket),
        ])
        let result = try await GlobalNpiaAdapter().parseChapter(ref(), book: book(), context: ctx)
        guard case .content(let chapter) = result else { return XCTFail("expected content, got \(result)") }
        XCTAssertTrue(chapter.html.contains("<p>Hello</p>"))
        XCTAssertTrue(chapter.images.isEmpty)
    }

    func testParseChapterAdGateIsRefused() async throws {
        let body = #"{"code":"0010","result":{"name":"NOVEL_ERROR","message":"basic advertisement"}}"#
        let ctx = context([(needle: "/v1/novel/episode", status: 403, body: body)])
        let result = try await GlobalNpiaAdapter().parseChapter(ref(), book: book(), context: ctx)
        guard case .refused(_, let reason) = result else { return XCTFail("expected refused") }
        XCTAssertEqual(reason, "needs an ad ticket; not supported yet")
    }

    func testParseChapterLoginRequiredIsRefused() async throws {
        let ctx = context([(needle: "/v1/novel/episode", status: 401, body: "{\"message\":\"Not logged in\"}")])
        let result = try await GlobalNpiaAdapter().parseChapter(ref(), book: book(), context: ctx)
        guard case .refused = result else { return XCTFail("expected refused, got \(result)") }
    }

    func testParseChapterServerErrorFails() async throws {
        let ctx = context([(needle: "/v1/novel/episode", status: 400, body: "{\"message\":\"weird\"}")])
        let result = try await GlobalNpiaAdapter().parseChapter(ref(), book: book(), context: ctx)
        guard case .failed = result else { return XCTFail("expected failed, got \(result)") }
    }
}
