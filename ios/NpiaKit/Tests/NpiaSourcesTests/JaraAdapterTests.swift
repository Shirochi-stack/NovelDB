import XCTest
import NpiaCore
@testable import NpiaSources

final class JaraAdapterTests: XCTestCase {
    let host = JaraAdapter.host

    func testMatches() {
        XCTAssertTrue(JaraAdapter.matches(URL(string: "https://www.\(host)/book/1234567")!))
        XCTAssertTrue(JaraAdapter.matches(URL(string: "https://m.\(host)/viewer?bookCode=1234567&cid=9")!))
        XCTAssertFalse(JaraAdapter.matches(URL(string: "https://www.\(host)/viewer?bookCode=0123")!))
        XCTAssertFalse(JaraAdapter.matches(URL(string: "https://example.com/book/1234567")!))
        XCTAssertEqual(JaraAdapter.bookCode(URL(string: "https://\(host)/book/77/")!), "77")
    }

    func testChapterNames() {
        XCTAssertEqual(JaraAdapter.chapterName(sortno: 3, title: ""), "3화")
        XCTAssertEqual(JaraAdapter.chapterName(sortno: 3, title: "만남"), "3화 - 만남")
        XCTAssertEqual(JaraAdapter.chapterName(sortno: 3, title: "이야기 183화"), "이야기 183화")
        XCTAssertEqual(JaraAdapter.chapterName(sortno: 0, title: ""), "Chapter")
    }

    func testSignedInfoToken() {
        let future = Date().timeIntervalSince1970 * 1000 + 60_000
        XCTAssertEqual(JaraAdapter.token(fromSignedInfo: "{\"data\":\"{\\\"token\\\":\\\"abc\\\"}\",\"expire\":\(Int(future))}"), "abc")
        XCTAssertEqual(JaraAdapter.token(fromSignedInfo: "{\"token\":\"xyz\"}"), "xyz")
        XCTAssertNil(JaraAdapter.token(fromSignedInfo: "{\"data\":{\"token\":\"old\"},\"expire\":1000}"))
        XCTAssertNil(JaraAdapter.token(fromSignedInfo: "not json"))
    }

    func testBundleParams() {
        let bundle = "x={baseURL:\"https://api.\(host)\",apiKey:\"mw_new\",version:\"3.3.0\",device:\"mw\",devicetoken:\"mw\"}"
        XCTAssertEqual(JaraAdapter.params(fromBundle: bundle), ["api_key": "mw_new", "ver": "3.3.0", "device": "mw", "devicetoken": "mw"])
    }

    func testDecryptMatchesDesktopCipher() throws {
        // Produced with the desktop's `cryptography` AES-CBC/PKCS7 path.
        let text = try JaraCrypto.decrypt("jmo66ZhGK4FFGOsdRmM40gS8PHtVrBAkKrtB4lYKLAk=", key: "0123456789abcdef", iv: "fedcba9876543210")
        XCTAssertEqual(text, "첫 문단\n둘째 문단")
        XCTAssertThrowsError(try JaraCrypto.decrypt("jmo66ZhGK4FFGOsdRmM40gS8PHtVrBAkKrtB4lYKLAk=", key: "0123456789abcdeX", iv: "fedcba9876543210"))
    }

    func bookPayload() -> [String: Any] {
        [
            "status": 1,
            "book": [
                "book_code": "77", "subject": "제목 &amp; 부제", "writer_name": "작가", "intro": "소개\r\n둘째",
                "book_img": "https://cdn.example.com/77.jpg", "keyword": ["#판타지", "회귀"], "category_ko_name": "판타지",
                "is_premium": "TRUE", "chk_finish": "N",
                "chapter": [
                    ["cid": "902", "sortno": 2, "sub_subject": "만남", "is_free": "N", "is_buy": "N"],
                    ["cid": "901", "sortno": 1, "sub_subject": "", "is_free": "Y"],
                ],
            ],
        ]
    }

    func testParseBook() async throws {
        let http = RoutedHTTP()
        http.on("/v1/book/detail.joa", json: bookPayload())
        let book = try await JaraAdapter().parseBook(URL(string: "https://www.\(host)/book/77")!, context: makeContext(http))
        XCTAssertEqual(book.title, "제목 & 부제")
        XCTAssertEqual(book.tags, ["판타지", "회귀"])
        XCTAssertEqual(book.synopsis, "소개\n둘째")
        XCTAssertEqual(book.status, "연재")
        XCTAssertEqual(book.chapters.map(\.title), ["1화", "2화 - 만남"])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true])
        XCTAssertEqual(book.chapters.map(\.id), ["901", "902"])
        let request = try XCTUnwrap(http.requested("detail.joa").first)
        XCTAssertEqual(HTTPSourceSupport.query(request.url, "api_key"), "mw_8ba234e7801ba288554ca07ae44c7")
        XCTAssertEqual(HTTPSourceSupport.query(request.url, "book_code"), "77")
        XCTAssertNil(HTTPSourceSupport.query(request.url, "token"))
    }

    func testParseBookUsesSavedTokenAndRequiresLogin() async throws {
        let http = RoutedHTTP()
        http.on("/v1/book/detail.joa", json: ["status": 0, "error_code": 9200, "message": "로그인이 필요합니다"])
        let secrets = InMemorySecretStore([JaraAdapter.tokenKey: "tok123"])
        do {
            _ = try await JaraAdapter().parseBook(URL(string: "https://www.\(host)/book/77")!, context: makeContext(http, secrets: secrets))
            XCTFail("expected login error")
        } catch let error as SourceError {
            XCTAssertEqual(error, .loginRequired(.jara))
        }
        let calls = http.requested("detail.joa")
        XCTAssertEqual(calls.count, 2)
        XCTAssertEqual(HTTPSourceSupport.query(calls[0].url, "token"), "tok123")
        XCTAssertNil(HTTPSourceSupport.query(calls[1].url, "token"))
    }

    func testParseChapterDecryptsAndAppendsAuthorNote() async throws {
        let key = "0123456789abcdef", iv = "fedcba9876543210"
        let http = RoutedHTTP()
        http.on("/v1/book/chapter_valid.joa", json: ["status": 1, "data": [key, iv]])
        http.on("/v1/book/chapter.joa", json: [
            "status": 1,
            "chapter": ["content": try JaraCrypto.encrypt("2화 - 만남\n본문 첫 줄\n\n본문 둘째 줄", key: key, iv: iv), "episode": "작가의 말"],
        ])
        let ref = ChapterRef(id: "902", index: 2, title: "2화 - 만남", url: URL(string: "https://www.\(host)/viewer?cid=902&bookCode=77&sortno=2"))
        let book = BookInfo(source: .jara, id: "77", url: URL(string: "https://www.\(host)/book/77")!, title: "t")
        let body = try XCTUnwrap(content(try await JaraAdapter().parseChapter(ref, book: book, context: makeContext(http))))
        XCTAssertEqual(body.html, """
        <div class="jara-content">
        <p>본문 첫 줄</p>
        <p>본문 둘째 줄</p>
        <p>* * *</p>
        <p>작가의 말</p>
        </div>
        """)
        XCTAssertEqual(HTTPSourceSupport.query(try XCTUnwrap(http.requested("chapter.joa?").first).url, "cid"), "902")
    }

    func testParseChapterLoginErrorIsRefused() async throws {
        let http = RoutedHTTP()
        http.on("/v1/book/chapter_valid.joa", json: ["status": 1, "data": ["0123456789abcdef", "fedcba9876543210"]])
        http.on("/v1/book/chapter.joa", json: ["status": 0, "error_code": 9200, "message": "로그인"])
        let ref = ChapterRef(id: "902", index: 2, title: "2화", isPaid: true)
        let book = BookInfo(source: .jara, id: "77", url: URL(string: "https://www.\(host)/book/77")!, title: "t")
        XCTAssertTrue(isRefused(try await JaraAdapter().parseChapter(ref, book: book, context: makeContext(http))))
    }

    func testPlainTextHTMLChapter() throws {
        let ref = ChapterRef(id: "1", index: 1, title: "x")
        let body = try XCTUnwrap(content(try JaraAdapter.chapter(text: "<p>하나</p><p>둘<br>셋</p>", note: "", ref: ref)))
        XCTAssertTrue(body.html.contains("<p>하나</p>\n<p>둘</p>\n<p>셋</p>"))
    }
}
