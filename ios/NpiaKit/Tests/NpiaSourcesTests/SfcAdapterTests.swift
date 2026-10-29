import XCTest
import NpiaCore
@testable import NpiaSources

final class SfcAdapterTests: XCTestCase {
    let host = SfcAdapter.bookHost

    func testMatches() {
        XCTAssertTrue(SfcAdapter.matches(URL(string: "https://\(host)/Novel/12345/")!))
        XCTAssertTrue(SfcAdapter.matches(URL(string: "https://\(host)/Novel/12345/MainIndex/")!))
        XCTAssertFalse(SfcAdapter.matches(URL(string: "https://\(host)/List/")!))
        XCTAssertFalse(SfcAdapter.matches(URL(string: "https://example.com/Novel/12345/")!))
        XCTAssertEqual(SfcAdapter.chapterID(URL(string: "https://\(host)/Novel/12345/678/9012/")!), "9012")
    }

    func testSignatureMatchesDesktop() {
        // Reference values from the desktop `_sfc_sign`.
        XCTAssertEqual(SfcAppAPI.sign(nonce: "0F6B2C1E-3A4D-4E5F-8A9B-112233445566", timestamp: 1761456000000), "2D57B27662C6BA05B90E81A2AB992378")
        XCTAssertEqual(SfcAppAPI.sign(nonce: "ABCDEF01-2345-4678-9ABC-DEF012345678", timestamp: 1700000000123), "3BEFB11AE80A54E17E2B8E514CDE4606")
    }

    func testHeaders() {
        let nonce = "0F6B2C1E-3A4D-4E5F-8A9B-112233445566"
        let headers = SfcAppAPI.headers(nonce: nonce, timestamp: 1761456000000, method: "POST")
        XCTAssertEqual(headers["content-type"], "application/json; charset=UTF-8")
        XCTAssertEqual(headers["sfsecurity"], "nonce=\(nonce)&timestamp=1761456000000&devicetoken=\(SfcAppAPI.deviceToken)&sign=2D57B27662C6BA05B90E81A2AB992378")
        XCTAssertNil(SfcAppAPI.headers(nonce: nonce, timestamp: 5)["content-type"])
        XCTAssertEqual(SfcAppAPI.sign(nonce: "N", timestamp: 5).count, 32)
    }

    func testCharacterTableDecodes() {
        XCTAssertEqual(SfcCharTable.obfuscated.unicodeScalars.count, 3751)
        XCTAssertEqual(SfcCharTable.replacement.unicodeScalars.count, 3751)
        XCTAssertEqual(SfcAppAPI.decode("\u{5ba6}\u{7f3a}x"), "\u{554a}\u{963f}x")
    }

    func testContentWithImages() {
        let ref = ChapterRef(id: "1", index: 2, title: "t")
        let body = SfcAppAPI.content("a<b\n\n[img=1,2]https://p.example.com/x/pic.jpg[/img]tail", ref: ref)
        XCTAssertEqual(body.html, "<div><p>a&lt;b</p><p><br/></p><p><img src=\"images/0002_01.jpg\" alt=\"\" />tail</p></div>")
        XCTAssertEqual(body.images.map(\.url.absoluteString), ["https://p.example.com/x/pic.jpg"])
    }

    func testCookieHandling() {
        let secrets = InMemorySecretStore()
        XCTAssertFalse(SfcAdapter.saveAppCookie(".SFCommunity=a", secrets: secrets))
        XCTAssertFalse(SfcAdapter.hasAppSession(secrets))
        XCTAssertTrue(SfcAdapter.saveAppCookie(" .SFCommunity=a;session_APP=b ", secrets: secrets))
        XCTAssertEqual(SfcAppAPI.savedCookie(secrets), ".SFCommunity=a; session_APP=b")
        let header = ".SFCommunity=AAA; path=/; HttpOnly, session_APP=BBB; expires=Wed, 01 Jan 2031 00:00:00 GMT; path=/"
        XCTAssertEqual(SfcAppAPI.sessionCookie(fromSetCookie: header), ".SFCommunity=AAA; session_APP=BBB")
    }

    func testLoginRetriesRejectedNonceAndStoresCookie() async throws {
        let http = SequenceHTTP(replies: [
            (["status": ["httpCode": 417]], [:]),
            (["status": ["httpCode": 200]], ["set-cookie": ".SFCommunity=C1; path=/, session_APP=S1; path=/"]),
        ])
        let secrets = InMemorySecretStore()
        let ok = try await SfcAdapter.login(username: "user", password: "pw", http: http, secrets: secrets)
        XCTAssertTrue(ok)
        XCTAssertEqual(secrets.string(for: SfcAppAPI.cookieKey), ".SFCommunity=C1; session_APP=S1")
        XCTAssertEqual(http.requests.count, 2)
        XCTAssertEqual(http.requests[0].method, "POST")
        XCTAssertNotEqual(http.requests[0].headers["sfsecurity"], http.requests[1].headers["sfsecurity"])
        let body = try JSONSerialization.jsonObject(with: http.requests[1].body!) as? [String: String]
        XCTAssertEqual(body?["username"], "user")
    }

    let index = """
    <html><body><h1 class="story-title">测试小说</h1>
    <div class="story-catalog"><div class="catalog-title"><h3>【测试小说】第一卷</h3></div>
    <div class="catalog-list"><ul>
    <li><a href="/Novel/12345/600/7001/" title="第一章 开端">第一章 开端</a></li>
    <li><a href="/Novel/12345/600/7002/" title="第二章 付费"><span class="icn_vip"></span>第二章 付费</a></li>
    </ul></div></div></body></html>
    """
    let info = """
    <html><body><span class="author-name">作者甲</span>
    <div id="hasTicket"><div class="pic"><img src="//img.example.com/cover/12345.jpg"></div></div>
    <p class="introduce">简介第一行<br>简介第二行</p>
    <ul class="tag-list"><li class="tag"><a href="#"><span class="icn">x</span>恋爱(12)</a></li><li class="tag"><a href="#">校园</a></li></ul>
    </body></html>
    """

    func testParseBook() async throws {
        let http = RoutedHTTP()
        http.on("/Novel/12345/MainIndex/", index)
        http.on("/Novel/12345/", info)
        let book = try await SfcAdapter().parseBook(URL(string: "https://\(host)/Novel/12345/")!, context: makeContext(http))
        XCTAssertEqual(book.title, "测试小说")
        XCTAssertEqual(book.author, "作者甲")
        XCTAssertEqual(book.synopsis, "简介第一行\n简介第二行")
        XCTAssertEqual(book.tags, ["恋爱", "校园"])
        XCTAssertEqual(book.coverURL?.absoluteString, "https://img.example.com/cover/12345.jpg")
        XCTAssertEqual(book.chapters.map(\.title), ["第一章 开端", "第二章 付费"])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true])
        XCTAssertEqual(book.chapters.map(\.id), ["7001", "7002"])
    }

    func book() -> BookInfo {
        BookInfo(source: .sfc, id: "12345", url: URL(string: "https://\(host)/Novel/12345/")!, title: "测试小说")
    }

    func testFreeChapterFromWebPage() async throws {
        let http = RoutedHTTP()
        http.on("/Novel/12345/600/7001/", "<h1 class=\"article-title\">第一章 开端</h1><div class=\"article-content\"><p>正文</p><p><img src=\"/pic/1.png\"></p></div>")
        let ref = ChapterRef(id: "7001", index: 1, title: "第一章", url: URL(string: "https://\(host)/Novel/12345/600/7001/"))
        let awaited1 = try await SfcAdapter().parseChapter(ref, book: book(), context: makeContext(http))
        let body = try XCTUnwrap(content(awaited1))
        XCTAssertEqual(body.ref.title, "第一章 开端")
        XCTAssertEqual(body.images.map(\.url.absoluteString), ["https://\(host)/pic/1.png"])
        XCTAssertTrue(http.requested(SfcAppAPI.host).isEmpty)
    }

    func testVIPChapterWithoutSessionIsRefused() async throws {
        let http = RoutedHTTP()
        http.on("/Novel/12345/600/7002/", "<div class=\"article-content\"><p>预览</p></div><div class=\"pay-section\">购买</div>")
        let ref = ChapterRef(id: "7002", index: 2, title: "第二章", url: URL(string: "https://\(host)/Novel/12345/600/7002/"), isPaid: true)
        let awaited2 = try await SfcAdapter().parseChapter(ref, book: book(), context: makeContext(http))
        XCTAssertTrue(isRefused(awaited2))

        http.on("/Novel/12345/600/7002/", "<div class=\"user-bar\"><div class=\"top-link\"><span class=\"normal-link\">您好，SF游客</span></div></div><div class=\"article-content\"><p>预览</p></div>")
        let awaited3 = try await SfcAdapter().parseChapter(ref, book: book(), context: makeContext(http))
        XCTAssertTrue(isRefused(awaited3))
    }

    func testVIPChapterThroughAppAPI() async throws {
        let http = RoutedHTTP()
        http.on("/Chaps/7002", json: ["status": ["httpCode": 200], "data": ["title": "第二章 付费", "expand": ["content": "\u{5ba6}ab\ncd"]]])
        let secrets = InMemorySecretStore([SfcAppAPI.cookieKey: ".SFCommunity=a; session_APP=b"])
        let ref = ChapterRef(id: "7002", index: 2, title: "第二章", url: URL(string: "https://\(host)/Novel/12345/600/7002/"), isPaid: true)
        let awaited4 = try await SfcAdapter().parseChapter(ref, book: book(), context: makeContext(http, secrets: secrets))
        let body = try XCTUnwrap(content(awaited4))
        XCTAssertEqual(body.html, "<div><p>\u{554a}ab</p><p>cd</p></div>")
        XCTAssertEqual(body.ref.title, "第二章 付费")
        XCTAssertEqual(http.requested("/Chaps/7002").first?.headers["Cookie"], ".SFCommunity=a; session_APP=b")

        http.on("/Chaps/7002", json: ["status": ["httpCode": 403]])
        http.on("/Novel/12345/600/7002/", "<div class=\"pay-section\">购买</div>")
        let awaited5 = try await SfcAdapter().parseChapter(ref, book: book(), context: makeContext(http, secrets: secrets))
        XCTAssertTrue(isRefused(awaited5))
    }

    func testWebsiteSignInTicketIsOfferedToTheAppAPI() async throws {
        let http = RoutedHTTP()
        http.on("/Chaps/7002", json: ["status": ["httpCode": 200], "data": ["title": "第二章", "content": "正文"]])
        let adapter = SfcAdapter(websiteTicket: { "ticket" })
        let ref = ChapterRef(id: "7002", index: 2, title: "第二章", url: URL(string: "https://\(host)/Novel/12345/600/7002/"), isPaid: true)
        let first = try await adapter.parseChapter(ref, book: book(), context: makeContext(http))
        XCTAssertNotNil(content(first))
        XCTAssertEqual(http.requested("/Chaps/7002").first?.headers["Cookie"], ".SFCommunity=ticket")
        // An app session wins over the website ticket.
        let secrets = InMemorySecretStore([SfcAppAPI.cookieKey: ".SFCommunity=a; session_APP=b"])
        let second = try await adapter.parseChapter(ref, book: book(), context: makeContext(http, secrets: secrets))
        XCTAssertNotNil(content(second))
        XCTAssertEqual(http.requested("/Chaps/7002").last?.headers["Cookie"], ".SFCommunity=a; session_APP=b")
    }

    func testATicketTheAppAPITurnsDownFallsBackToTheWebsite() async throws {
        let http = RoutedHTTP()
        http.on("/Chaps/7002", json: ["status": ["httpCode": 401]])
        http.on("/Chaps/7003", json: ["status": ["httpCode": 401]])
        http.on("/Novel/12345/600/7002/", "<div class=\"pay-section\">购买</div>")
        http.on("/Novel/12345/600/7003/", "<div class=\"article-content\"><p>免费</p></div>")
        let adapter = SfcAdapter(websiteTicket: { "ticket" })
        let lockedRef = ChapterRef(id: "7002", index: 2, title: "第二章", url: URL(string: "https://\(host)/Novel/12345/600/7002/"), isPaid: true)
        let locked = try await adapter.parseChapter(lockedRef, book: book(), context: makeContext(http))
        guard case .refused(_, let reason) = locked else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("not bought"))
        // The turned-down ticket is not offered again; the next chapter comes from the website.
        let freeRef = ChapterRef(id: "7003", index: 3, title: "第三章", url: URL(string: "https://\(host)/Novel/12345/600/7003/"))
        let free = try await adapter.parseChapter(freeRef, book: book(), context: makeContext(http))
        XCTAssertNotNil(content(free))
        XCTAssertTrue(http.requested("/Chaps/7003").isEmpty)
    }
}

/// Returns canned JSON replies in order.
final class SequenceHTTP: HTTPFetching, @unchecked Sendable {
    private var replies: [([String: Any], [String: String])]
    private(set) var requests: [HTTPRequest] = []
    private let lock = NSLock()

    init(replies: [([String: Any], [String: String])]) {
        self.replies = replies
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        try respond(request)
    }

    private func respond(_ request: HTTPRequest) throws -> HTTPResponse {
        lock.lock(); defer { lock.unlock() }
        requests.append(request)
        let (json, headers) = replies.isEmpty ? ([:], [:]) : replies.removeFirst()
        return HTTPResponse(url: request.url, status: 200, headers: headers, body: try JSONSerialization.data(withJSONObject: json))
    }
}
