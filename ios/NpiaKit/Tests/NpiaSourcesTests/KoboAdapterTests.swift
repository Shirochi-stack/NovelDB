import XCTest
import NpiaCore
@testable import NpiaSources

final class KoboAdapterTests: XCTestCase {
    // MARK: URL matching

    func testMatchesKoboProductPages() {
        XCTAssertTrue(KoboAdapter.matches(URL(string: "https://www.kobo.com/us/en/ebook/some-book")!))
        XCTAssertTrue(KoboAdapter.matches(URL(string: "https://kobo.com/ebook/some-book/")!))
        XCTAssertTrue(KoboAdapter.matches(URL(string: "http://www.kobo.com/jp/ja/ebook/abc123")!))
    }

    func testMatchesRakutenBooks() {
        XCTAssertTrue(KoboAdapter.matches(URL(string: "https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/")!))
        XCTAssertTrue(KoboAdapter.matches(URL(string: "https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef")!))
    }

    func testRejectsOtherURLs() {
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://www.kobo.com/us/en/audiobook/some-book")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://www.kobo.com/us/en/ebook/a/b")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://www.kobo.com/")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://books.rakuten.co.jp/rk/xyz/")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "http://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://example.com/ebook/some-book")!))
        XCTAssertFalse(KoboAdapter.matches(URL(string: "https://readnow.kobo.com/ebook/some-book")!))
    }

    // MARK: Owned read URL

    func testOwnedReadURLRequiresLibraryAndReaderHost() {
        func page(_ config: String) -> String {
            let escaped = config.replacingOccurrences(of: "\"", with: "&quot;")
            return "<div data-kobo-gizmo-config=\"\(escaped)\"></div>"
        }
        XCTAssertEqual(
            KoboParsing.ownedReadURL(page("{\"isInLibrary\":true,\"readNowUrl\":\"https://readnow.kobo.com/book/1\"}")),
            "https://readnow.kobo.com/book/1")
        XCTAssertEqual(KoboParsing.ownedReadURL(page("{\"isInLibrary\":false,\"readNowUrl\":\"https://readnow.kobo.com/book/1\"}")), "")
        XCTAssertEqual(KoboParsing.ownedReadURL(page("{\"isInLibrary\":true,\"readNowUrl\":\"https://evil.example/book/1\"}")), "")
        XCTAssertEqual(KoboParsing.ownedReadURL(page("{\"isInLibrary\":true,\"readNowUrl\":\"http://readnow.kobo.com/book/1\"}")), "")
        XCTAssertEqual(KoboParsing.ownedReadURL(page("not json")), "")
        XCTAssertEqual(KoboParsing.ownedReadURL("<p>nothing</p>"), "")
    }

    // MARK: Book

    func testMakeBookOwned() {
        var meta = KoboMeta()
        meta.title = "A Book"
        meta.author = "An Author"
        meta.cover = "https://cdn.example/cover.jpg"
        meta.description = "About it"
        meta.language = "ja-JP"
        meta.readUrl = "https://readnow.kobo.com/book/1"
        let url = URL(string: "https://www.kobo.com/us/en/ebook/a-book")!
        let book = KoboParsing.makeBook(url: url, meta: meta)
        XCTAssertEqual(book.source, .kobo)
        XCTAssertEqual(book.id, "a-book")
        XCTAssertEqual(book.title, "A Book")
        XCTAssertEqual(book.author, "An Author")
        XCTAssertEqual(book.language, "ja")
        XCTAssertEqual(book.chapters.count, 1)
        XCTAssertFalse(book.chapters[0].isPaid)
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "https://readnow.kobo.com/book/1")
    }

    func testMakeBookNotOwnedMarksChapterPaid() {
        let url = URL(string: "https://www.kobo.com/us/en/ebook/a-book")!
        let book = KoboParsing.makeBook(url: url, meta: KoboMeta())
        XCTAssertEqual(book.title, "Kobo ebook")
        XCTAssertEqual(book.language, "en")
        XCTAssertTrue(book.chapters[0].isPaid)
        XCTAssertEqual(book.chapters[0].url, url)
    }

    func testMetaDecodingToleratesMissingAndWrongTypes() throws {
        let json = "{\"title\":\"T\",\"cover\":{\"x\":1},\"readUrl\":null}"
        let meta = try JSONDecoder().decode(KoboMeta.self, from: Data(json.utf8))
        XCTAssertEqual(meta.title, "T")
        XCTAssertEqual(meta.cover, "")
        XCTAssertEqual(meta.readUrl, "")
    }

    // MARK: Path and order helpers

    func testResolvePath() {
        XCTAssertEqual(KoboParsing.resolvePath("ch1.xhtml#a", base: "OEBPS/Text/toc.xhtml"), "OEBPS/Text/ch1.xhtml")
        XCTAssertEqual(KoboParsing.resolvePath("../Images/x.xhtml", base: "OEBPS/Text/toc.xhtml"), "OEBPS/Images/x.xhtml")
        XCTAssertEqual(KoboParsing.resolvePath("OEBPS/Text/c.xhtml", base: "OPS/t.xhtml"), "OEBPS/Text/c.xhtml")
        XCTAssertEqual(KoboParsing.resolvePath("a%20b.xhtml", base: "OEBPS/Text/toc.xhtml"), "OEBPS/Text/a b.xhtml")
        XCTAssertEqual(KoboParsing.resolvePath("https://x.example/a.xhtml"), "")
        XCTAssertEqual(KoboParsing.resolvePath("#frag"), "")
    }

    func testAnchorIsStable() {
        let first = KoboParsing.anchor("OEBPS/Text/ch1.xhtml")
        XCTAssertEqual(first, KoboParsing.anchor("OEBPS/Text/ch1.xhtml"))
        XCTAssertTrue(first.hasPrefix("kobo-"))
        XCTAssertEqual(first.count, 17)
        XCTAssertNotEqual(first, KoboParsing.anchor("OEBPS/Text/ch2.xhtml"))
    }

    func testOrderedPathsMergesOverlappingWindows() throws {
        let order = try KoboParsing.orderedPaths([["a", "b", "c"], ["b", "c", "d"], ["d", "e"]])
        XCTAssertEqual(order, ["a", "b", "c", "d", "e"])
    }

    func testOrderedPathsRejectsCycles() {
        XCTAssertThrowsError(try KoboParsing.orderedPaths([["a", "b"], ["b", "a"]]))
    }

    func testFontEncodedDetection() {
        let garbled = String(repeating: "\u{1F70}", count: 20) + "abc"
        XCTAssertTrue(KoboParsing.isFontEncoded(garbled, entryTitles: ["Chapter 1"]))
        XCTAssertFalse(KoboParsing.isFontEncoded(garbled, entryTitles: ["Prologue"]))
        XCTAssertFalse(KoboParsing.isFontEncoded("plain text", entryTitles: ["Chapter 1"]))
    }

    func testDataURLHelpers() {
        XCTAssertEqual(KoboParsing.imageExtension(dataURL: "data:image/png;base64,AAAA"), "png")
        XCTAssertEqual(KoboParsing.imageExtension(dataURL: "data:image/svg+xml;base64,AAAA"), "svg")
        XCTAssertEqual(KoboParsing.imageExtension(dataURL: ""), "jpg")
        XCTAssertEqual(KoboParsing.imageBytes(dataURL: "data:image/png;base64,AQID"), Data([1, 2, 3]))
        XCTAssertNil(KoboParsing.imageBytes(dataURL: "blob:abc"))
    }

    // MARK: Chapter assembly

    private func makeRef() -> ChapterRef {
        ChapterRef(id: "kobo-1", index: 1, title: "A Book", url: URL(string: "https://readnow.kobo.com/book/1"), isPaid: false)
    }

    func testBuildContentCleansAndLinksSections() throws {
        let one = KoboSnapshotSection(
            path: "OEBPS/Text/c1.xhtml",
            html: "<p onclick=\"x()\">One</p><script>bad()</script><a href=\"c2.xhtml#t\">next</a><img src=\"blob:abc\">",
            images: [KoboSnapshotImage(url: "blob:abc", data: "data:image/png;base64,AQID")])
        let two = KoboSnapshotSection(path: "OEBPS/Text/c2.xhtml", html: "<p>Two</p>", images: [])
        let entries: [(title: String, path: String)] = [
            (title: "One", path: "OEBPS/Text/c1.xhtml"), (title: "Two", path: "OEBPS/Text/c2.xhtml"),
        ]
        let content = try KoboParsing.buildContent(
            ref: makeRef(), entries: entries,
            collected: [one.path: one, two.path: two],
            snapshots: [[one.path, two.path]], horizontalLayout: true)
        XCTAssertFalse(content.html.contains("onclick"))
        XCTAssertFalse(content.html.contains("bad()"))
        XCTAssertTrue(content.html.contains("href=\"#\(KoboParsing.anchor(two.path))\""))
        XCTAssertTrue(content.html.contains("writing-mode:horizontal-tb"))
        XCTAssertTrue(content.html.contains("id=\"\(KoboParsing.anchor(one.path))\""))
        XCTAssertEqual(content.images.count, 1)
        XCTAssertEqual(content.images[0].filename, "images/0001_001.png")
        XCTAssertEqual(content.images[0].data, Data([1, 2, 3]))
        XCTAssertTrue(content.html.contains("src=\"images/0001_001.png\""))
        // Section order follows the spine.
        let first = content.html.range(of: "One")!.lowerBound
        let second = content.html.range(of: "Two")!.lowerBound
        XCTAssertLessThan(first, second)
    }

    func testBuildContentHonoursVerticalLayoutSetting() throws {
        let section = KoboSnapshotSection(path: "OEBPS/Text/c1.xhtml", html: "<p>One</p>", images: [])
        let content = try KoboParsing.buildContent(
            ref: makeRef(), entries: [(title: "One", path: section.path)],
            collected: [section.path: section], snapshots: [[section.path]], horizontalLayout: false)
        XCTAssertFalse(content.html.contains("writing-mode"))
    }

    func testBuildContentFailsOnMissingSectionOrBlobImage() {
        let section = KoboSnapshotSection(
            path: "OEBPS/Text/c1.xhtml", html: "<p>One</p>",
            images: [KoboSnapshotImage(url: "blob:xyz", data: nil)])
        XCTAssertThrowsError(try KoboParsing.buildContent(
            ref: makeRef(), entries: [(title: "One", path: section.path), (title: "Two", path: "OEBPS/Text/c2.xhtml")],
            collected: [section.path: section], snapshots: [[section.path]], horizontalLayout: true))
        XCTAssertThrowsError(try KoboParsing.buildContent(
            ref: makeRef(), entries: [(title: "One", path: section.path)],
            collected: [section.path: section], snapshots: [[section.path]], horizontalLayout: true))
    }

    // MARK: Refusals (no network, no web page)

    private func makeContext() -> SourceContext {
        SourceContext(http: KoboStubHTTP(), web: KoboStubEngine(), secrets: InMemorySecretStore(), settings: AppSettings())
    }

    func testRakutenBookIsListedButChapterRefused() async throws {
        let url = URL(string: "https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/")!
        let book = try await KoboAdapter().parseBook(url, context: makeContext())
        XCTAssertEqual(book.chapters.count, 1)
        let result = try await KoboAdapter().parseChapter(book.chapters[0], book: book, context: makeContext())
        guard case .refused(_, let reason) = result else { return XCTFail("expected refused") }
        XCTAssertFalse(reason.isEmpty)
    }

    func testUnownedBookChapterIsRefusedWithoutWebPage() async throws {
        let url = URL(string: "https://www.kobo.com/us/en/ebook/a-book")!
        let book = KoboParsing.makeBook(url: url, meta: KoboMeta())
        // KoboStubEngine throws if a page is requested, so reaching `.refused` proves none was opened.
        let result = try await KoboAdapter().parseChapter(book.chapters[0], book: book, context: makeContext())
        guard case .refused = result else { return XCTFail("expected refused") }
    }

    func testForeignReaderURLIsRefused() async throws {
        let ref = ChapterRef(id: "kobo-1", index: 1, title: "x", url: URL(string: "https://evil.example/read"), isPaid: false)
        let book = BookInfo(source: .kobo, id: "x", url: URL(string: "https://www.kobo.com/ebook/x")!, title: "x", chapters: [ref])
        let result = try await KoboAdapter().parseChapter(ref, book: book, context: makeContext())
        guard case .refused = result else { return XCTFail("expected refused") }
    }
}

// MARK: - Stubs

private struct KoboStubHTTP: HTTPFetching {
    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
    }
}

private struct KoboStubEngine: WebEngineProviding {
    func newPage(profile: String) async throws -> any WebPageDriving { throw WebEngineError.unavailable }
    func presentLogin(url: URL, profile: String) async throws {}
}
