import XCTest
import NpiaCore
@testable import NpiaSources

final class NtkAdapterTests: XCTestCase {
    private func url(_ string: String) -> URL { URL(string: string)! }

    func testRecognisesNtkHostsAndPaths() {
        XCTAssertTrue(NtkAdapter.matches(url("https://ntk01.com/novel/12345")))
        XCTAssertTrue(NtkAdapter.matches(url("https://newtoki468.com/webtoon/999/1000")))
        XCTAssertTrue(NtkAdapter.matches(url("https://sbxh9.com/novel/35110")))
        XCTAssertTrue(NtkAdapter.matches(url("https://www.sbxh12.org/novel/1/2")))
        XCTAssertFalse(NtkAdapter.matches(url("https://example.com/novel/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk01.com/comic/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk.com/novel/1")))
        XCTAssertFalse(NtkAdapter.matches(url("https://ntk01.com/")))
    }

    func testRecognisesMirrorSourceEpisodeIds() {
        XCTAssertTrue(NtkAdapter.matches(url("https://sbxh9.com/webtoon/56566288/goodtoon-74517682585840")))
        XCTAssertTrue(NtkAdapter.matches(url("https://sbxh9.com/webtoon/56566288/kp-56566288-67553760")))
        XCTAssertTrue(NtkAdapter.matches(url("https://sbxh9.com/webtoon/56566288/tkor5-1182039/")))
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://sbxh9.com/webtoon/56566288/goodtoon-74517682585840")).absoluteString, "https://sbxh9.com/webtoon/56566288")
    }

    func testOwnsRotatingMirrorHosts() {
        XCTAssertTrue(NtkAdapter.ownsSite("ntk01.com"))
        XCTAssertTrue(NtkAdapter.ownsSite("newtoki468.com"))
        XCTAssertTrue(NtkAdapter.ownsSite("sbxh12.org"))
        XCTAssertTrue(NtkAdapter.ownsSite("www.newtoki1.com"))
        XCTAssertFalse(NtkAdapter.ownsSite("example.com"))
        XCTAssertFalse(NtkAdapter.ownsSite("ntk.com"))
    }

    func testChapterURLCanonicalisesToBookIndex() {
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://sbxh9.com/novel/35110/812345")).absoluteString, "https://sbxh9.com/novel/35110")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://newtoki468.com/webtoon/999/1000")).absoluteString, "https://newtoki468.com/webtoon/999")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://sbxh9.com/novel/35110")).absoluteString, "https://sbxh9.com/novel/35110")
        XCTAssertEqual(NtkAdapter.canonicalURL(url("https://example.com/novel/1")).absoluteString, "https://example.com/novel/1")
    }

    // MARK: - Webtoon pages

    private let webp = Data("RIFF\0\0\0\0WEBPVP8 ".utf8)

    private let pages = #"""
    {"ok":true,"chapterName":"One","contentText":"","contentHtml":"<div class=\"ntk-content\"><div class=\"ntk-webtoon-page\"><img src=\"images/0001_0001.webp\" alt=\"page 1\"/></div>\n<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0002.webp\" alt=\"page 2\"/></div></div>","images":[
      {"url":"https://dead.example/a.js","name":"images/0001_0001.webp","candidates":["https://dead.example/a.js","https://live.example/a.js"]},
      {"url":"https://dead.example/b.js","name":"images/0001_0002.webp","candidates":["https://dead.example/b.js","https://gone.example/b.js"]}
    ]}
    """#

    private func book() -> BookInfo {
        BookInfo(source: .ntk, id: "5", url: url("https://sbxh9.com/webtoon/5"), title: "T")
    }

    private func chapter() -> ChapterRef {
        ChapterRef(id: "kp-5-6", index: 1, title: "One", url: url("https://sbxh9.com/webtoon/5/kp-5-6"))
    }

    private func web(_ reply: String) -> HardScriptedWeb {
        HardScriptedWeb { index in
            HardScriptedPage(name: "page\(index)") { _, call in
                if call.hasPrefix("ntk.chapterState") { return "{\"ready\":true}" }
                if call.hasPrefix("ntk.webtoon") { return reply }
                return nil
            }
        }
    }

    private func content(_ result: ChapterResult, file: StaticString = #filePath, line: UInt = #line) -> ChapterContent? {
        if case .content(let content) = result { return content }
        XCTFail("expected content, got \(result)", file: file, line: line)
        return nil
    }

    func testAWebtoonPageComesFromTheFirstMirrorServingItAndDeadPagesAreLeftOut() async throws {
        let webp = self.webp
        let http = RecordingHTTP { request in
            let live = request.url.host == "live.example"
            return HTTPResponse(url: request.url, status: live ? 200 : 522, headers: [:], body: live ? webp : Data())
        }
        let scripted = web(pages)
        let result = try await NtkAdapter().parseChapter(chapter(), book: book(), context: hardContext(scripted, http: http))
        let found = try XCTUnwrap(content(result))
        XCTAssertEqual(found.images.map(\.filename), ["images/0001_0001.webp"])
        XCTAssertEqual(found.images[0].data, webp)
        XCTAssertEqual(found.images[0].url.absoluteString, "https://dead.example/a.js")
        XCTAssertTrue(found.html.contains("images/0001_0001.webp"))
        XCTAssertFalse(found.html.contains("images/0001_0002.webp"), "the page no mirror served is gone from the chapter")
        let asked = http.requests.map(\.url)
        XCTAssertEqual(Set(asked).count, asked.count, "one try per host: no address is asked for twice")
        let calls = scripted.pages.first?.calls ?? []
        XCTAssertTrue(calls.contains { $0.contains("\"index\":1") }, "the chapter number reaches the page")
    }

    func testAMirrorAnsweringWithAnErrorPageDoesNotCountAsServingThePage() async throws {
        let webp = self.webp
        let http = RecordingHTTP { request in
            HTTPResponse(url: request.url, status: 200, headers: [:], body: request.url.host == "live.example" ? webp : Data("<html>nope</html>".utf8))
        }
        let result = try await NtkAdapter().parseChapter(chapter(), book: book(), context: hardContext(web(pages), http: http))
        XCTAssertEqual(try XCTUnwrap(content(result)).images.map(\.data), [webp])
    }

    func testAChapterNoMirrorServesFails() async throws {
        let http = RecordingHTTP { HTTPResponse(url: $0.url, status: 523, headers: [:], body: Data()) }
        let result = try await NtkAdapter().parseChapter(chapter(), book: book(), context: hardContext(web(pages), http: http))
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure, got \(result)") }
        XCTAssertTrue(message.contains("no mirror"), message)
    }

    func testSavingOnlyImageURLsDownloadsNothing() async throws {
        let http = RecordingHTTP()
        var settings = AppSettings()
        settings.images.saveImageURLsOnly = true
        let result = try await NtkAdapter().parseChapter(chapter(), book: book(), context: hardContext(web(pages), settings: settings, http: http))
        XCTAssertEqual(try XCTUnwrap(content(result)).images.count, 2)
        XCTAssertTrue(http.requests.isEmpty)
    }

    func testMirrorsThatFailedGoLast() async {
        let mirrors = NtkMirrors()
        let urls = ["https://a.example/1.js", "https://b.example/1.js", "https://c.example/1.js"]
        let first = await mirrors.ordered(urls)
        XCTAssertEqual(first, urls)
        await mirrors.failed("https://a.example/9.js")
        let second = await mirrors.ordered(urls)
        XCTAssertEqual(second, ["https://b.example/1.js", "https://c.example/1.js", "https://a.example/1.js"])
    }

    func testRemovingAPageLeavesTheOthers() {
        let one = "<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0001.webp\" alt=\"page 1\"/></div>\n"
        let two = "<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0002.webp\" alt=\"page 2\"/></div>"
        XCTAssertEqual(NtkSite.withoutPages(one + two, names: ["images/0001_0001.webp"]), two)
    }

    func testImageBytesAreRecognised() {
        XCTAssertTrue(NtkSite.isImage(webp))
        XCTAssertTrue(NtkSite.isImage(Data([0xFF, 0xD8, 0xFF, 0xE0])))
        XCTAssertFalse(NtkSite.isImage(Data("<html>".utf8)))
        XCTAssertFalse(NtkSite.isImage(Data()))
    }
}
