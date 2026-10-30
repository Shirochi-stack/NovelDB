import XCTest
import NpiaCore
@testable import NpiaSources

private let kpageSite = "page.\u{6b}\u{61}\u{6b}\u{61}\u{6f}.com"

// MARK: - Stubs

private final class KpageRequestLog: @unchecked Sendable {
    private let lock = NSLock()
    private var urls: [String] = []

    func add(_ url: String) {
        lock.lock()
        urls.append(url)
        lock.unlock()
    }

    var all: [String] {
        lock.lock()
        defer { lock.unlock() }
        return urls
    }
}

private struct KpageRoute: Sendable {
    var contains: String
    var status: Int
    var contentType: String
    var body: String
}

private struct KpageStubHTTP: HTTPFetching {
    var routes: [KpageRoute] = []
    var log = KpageRequestLog()

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        log.add(request.url.absoluteString)
        for route in routes where request.url.absoluteString.contains(route.contains) {
            return HTTPResponse(
                url: request.url, status: route.status,
                headers: ["Content-Type": route.contentType], body: Data(route.body.utf8))
        }
        return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
    }
}

private final class KpageFakePage: WebPageDriving, @unchecked Sendable {
    let htmlText: String
    let tagsJSON: String

    init(html: String, tagsJSON: String = "[]") {
        self.htmlText = html
        self.tagsJSON = tagsJSON
    }

    func load(_ url: URL, until state: WebLoadState, timeout: TimeInterval) async throws {}

    func evaluateJSON(_ functionBody: String) async throws -> Data {
        if functionBody.contains("startsWith('#')") { return Data(tagsJSON.utf8) }
        return Data("true".utf8)
    }

    func addInitScript(_ source: String) async {}
    func currentURL() async -> URL? { nil }
    func html() async throws -> String { htmlText }
    func close() async {}
}

private struct KpageStubWeb: WebEngineProviding {
    var page: KpageFakePage

    func newPage(profile: String) async throws -> any WebPageDriving { page }
    func presentLogin(url: URL, profile: String, userAgentSuffix: String?) async throws {}
}

// MARK: - Fixtures

private let kpageBookHTML = """
<html><head>
<meta property="og:title" content="Test Novel - \u{c6f9}\u{c18c}\u{c124} | kpage"/>
<meta property="og:image" content="https://img.example.test/cover.jpg"/>
<meta property="og:description" content="A short synopsis."/>
</head><body><div><h2>Test Novel</h2><span>Author Name</span></div></body></html>
"""

private let kpageEpisodesJSON = #"""
{"result":{"list":[
 {"item":{"product_id":103,"title":"Test Novel 3\#u{D654}","order_value":3,"is_free":false,"rental":{"owned":"yes_rented"}}},
 {"item":{"product_id":101,"title":"Test Novel 1\#u{D654}","order_value":1,"is_free":true}},
 {"item":{"product_id":102,"title":"Test Novel 2\#u{D654}","order_value":2,"is_free":false,"ticket_count":5,"price":100}},
 {"item":{"product_id":104,"title":"Test Novel 4\#u{D654}","order_value":0,"is_free":false}}
]}}
"""#

private let kpageViewerText = #"""
{"result":{"viewerData":{"atsServerUrl":"http://ats.example.test","contentsList":[{"secureUrl":"/chunk1"},{"secureUrl":"/chunk2"}]}}}
"""#

private let kpageChunk1 = #"""
{"contentInfo":{"contentId":0,"paragraphList":[
 {"id":1,"type":"H1","text":"Heading"},
 {"id":2,"type":"P","text":"Hello &amp; <world>"},
 {"id":3,"type":"P","childParagraphList":[{"type":"SPAN","text":"Bold","style":{"bold":true}},{"type":"BR"}]}
]}}
"""#

private let kpageChunk2 = #"""
{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"Second page"}]}}
"""#

private let kpageViewerImages = #"""
{"result":{"viewerData":{"imageDownloadData":{"files":[
 {"no":2,"secureUrl":"https://img.example.test/b?filename=b%20x.png","width":100,"height":200},
 {"no":1,"secureUrl":"https://img.example.test/a?filename=a.jpg"}
]}}}}
"""#

private func kpPara(_ plain: String, _ type: String = "P", chunk: Int = 0, html: String? = nil) -> KpageParagraph {
    KpageParagraph(
        plain: plain, html: html ?? plain, type: type, style: [:], attrs: [:],
        chunkIndex: chunk, contentId: 0, paragraphId: 0)
}

private func kpContext(http: KpageStubHTTP, web: KpageStubWeb = KpageStubWeb(page: KpageFakePage(html: "")),
                       settings: AppSettings = AppSettings()) -> SourceContext {
    SourceContext(http: http, web: web, secrets: InMemorySecretStore(), settings: settings)
}

private func kpRef(_ product: String, index: Int, paid: Bool = false) -> ChapterRef {
    ChapterRef(
        id: product, index: index, title: "Episode \(index)",
        url: URL(string: "https://\(kpageSite)/content/42/viewer/\(product)"), isPaid: paid)
}

private func kpBook() -> BookInfo {
    BookInfo(source: .kpage, id: "42", url: URL(string: "https://\(kpageSite)/content/42")!, title: "Test Novel")
}

// MARK: - Tests

final class KpageAdapterTests: XCTestCase {
    // URL matching

    func testMatchesContentURLs() {
        XCTAssertTrue(KpageAdapter.matches(URL(string: "https://\(kpageSite)/content/123")!))
        XCTAssertTrue(KpageAdapter.matches(URL(string: "http://\(kpageSite)/content/123?tab_type=about")!))
        XCTAssertTrue(KpageAdapter.matches(URL(string: "https://\(kpageSite)/content/123/viewer/456")!))
    }

    func testRejectsOtherURLs() {
        XCTAssertFalse(KpageAdapter.matches(URL(string: "https://\(kpageSite)/home")!))
        XCTAssertFalse(KpageAdapter.matches(URL(string: "https://\(kpageSite)/content/abc")!))
        XCTAssertFalse(KpageAdapter.matches(URL(string: "https://example.com/content/123")!))
        XCTAssertFalse(KpageAdapter.matches(URL(string: "https://www.\(kpageSite)/content/123")!))
        XCTAssertFalse(KpageAdapter.matches(URL(string: "https://example.com/novel/1")!))
        XCTAssertFalse(KpageAdapter.matches(URL(string: "ftp://\(kpageSite)/content/123")!))
    }

    func testViewerIDs() {
        let ids = KpageParsing.viewerIDs(from: "https://\(kpageSite)/content/12/viewer/345")
        XCTAssertEqual(ids?.series, "12")
        XCTAssertEqual(ids?.product, "345")
        XCTAssertNil(KpageParsing.viewerIDs(from: "https://\(kpageSite)/content/12"))
        XCTAssertEqual(KpageParsing.seriesID(from: "https://\(kpageSite)/content/12?x=1"), "12")
    }

    // Metadata and episodes

    func testParseMetadata() {
        let meta = KpageParsing.parseMetadata(html: kpageBookHTML)
        XCTAssertEqual(meta.title, "Test Novel")
        XCTAssertEqual(meta.author, "Author Name")
        XCTAssertEqual(meta.cover, "https://img.example.test/cover.jpg")
        XCTAssertEqual(meta.description, "A short synopsis.")
    }

    func testParseEpisodesSortsAndFlagsAccess() {
        let episodes = KpageParsing.parseEpisodes(json: Data(kpageEpisodesJSON.utf8))
        XCTAssertEqual(episodes.map { $0.productId }, [101, 102, 103, 104])
        XCTAssertEqual(episodes.map { $0.isAccessible }, [true, false, true, false])
        XCTAssertEqual(episodes[0].shortName, "1\u{d654}")
        XCTAssertTrue(episodes[0].isFree)
    }

    func testHasUserAccessFlag() throws {
        func check(_ json: String) throws -> Bool {
            let object = try JSONSerialization.jsonObject(with: Data(json.utf8))
            return KpageParsing.hasUserAccessFlag(object)
        }
        XCTAssertTrue(try check(#"{"a":{"purchased":true}}"#))
        XCTAssertTrue(try check(#"{"pass":1}"#))
        XCTAssertTrue(try check(#"{"rented":"yes_rented"}"#))
        XCTAssertFalse(try check(#"{"ticket_count":3}"#))
        XCTAssertFalse(try check(#"{"usable":"no"}"#))
        XCTAssertFalse(try check(#"{"x":{"rented":"expired"}}"#))
        XCTAssertFalse(try check(#"{"price_pass":true}"#))
        XCTAssertFalse(try check(#"{"rented":false}"#))
    }

    func testSortKeyFallsBackToTitleNumberThenLast() {
        let numbered = KpageEpisode(productId: 5, order: 0, shortName: "", fullName: "Title 7\u{d654}", isFree: false, isAccessible: false, slideType: "")
        let unnumbered = KpageEpisode(productId: 1, order: 0, shortName: "", fullName: "Prologue", isFree: false, isAccessible: false, slideType: "")
        let ordered = KpageParsing.sortEpisodes([unnumbered, numbered])
        XCTAssertEqual(ordered.map { $0.productId }, [5, 1])
        XCTAssertEqual(KpageParsing.sortKey(numbered).0, 7)
        XCTAssertEqual(KpageParsing.titleNumber("12\u{d654}"), 12)
        XCTAssertEqual(KpageParsing.titleNumber("none"), 0)
    }

    // Viewer data

    func testViewerOutcomes() {
        XCTAssertEqual(
            KpageParsing.parseViewer(Data(kpageViewerText.utf8), status: 200),
            .text(baseURL: "https://ats.example.test", secureURLs: ["/chunk1", "/chunk2"]))
        XCTAssertEqual(
            KpageParsing.parseViewer(Data(#"{"message":"not_purchased"}"#.utf8), status: 200),
            .locked(reason: "not_purchased"))
        XCTAssertEqual(
            KpageParsing.parseViewer(Data("{}".utf8), status: 403),
            .locked(reason: "access denied"))
        XCTAssertEqual(
            KpageParsing.parseViewer(Data("<html>".utf8), status: 200),
            .unavailable(reason: "non-json response"))
        XCTAssertEqual(
            KpageParsing.parseViewer(Data(#"{"result_code":7}"#.utf8), status: 200),
            .unavailable(reason: "api returned no viewer resources (code 7)"))
        if case .images(let files) = KpageParsing.parseViewer(Data(kpageViewerImages.utf8), status: 200) {
            XCTAssertEqual(files.count, 2)
            XCTAssertEqual(files[0].filename, "b%20x.png")
        } else {
            XCTFail("expected images")
        }
    }

    // Paragraph extraction and output

    func testExtractAndBuildOutput() {
        let paragraphs = KpageParsing.extractParagraphs(
            [Data(kpageChunk1.utf8), Data(kpageChunk2.utf8)], skipLastPage: false, keepFiller: false)
        XCTAssertEqual(paragraphs.map { $0.plain }, ["Heading", "Hello & <world>", "Bold", "Second page"])
        let output = KpageParsing.buildOutput(paragraphs)
        let expected = [
            "<h1 class=\"kpage-source-heading\">Heading</h1>",
            KpageParsing.pageBreak,
            "<p>Hello &amp; &lt;world&gt;</p>",
            "<p><b>Bold</b><br /></p>",
            KpageParsing.pageBreak,
            "<p>Second page</p>",
        ].joined(separator: "\n")
        XCTAssertEqual(output.html, expected)
        XCTAssertEqual(output.text, "Heading\nHello & <world>\nBold\nSecond page")
        XCTAssertEqual(KpageParsing.headingTitle(paragraphs, fallback: "x"), "Heading")
        XCTAssertEqual(KpageParsing.headingTitle([kpPara("body")], fallback: "fallback"), "fallback")
    }

    func testSkipLastPageAndKeepFiller() {
        let chunks = [Data(kpageChunk1.utf8), Data(kpageChunk2.utf8)]
        let skipped = KpageParsing.extractParagraphs(chunks, skipLastPage: true, keepFiller: false)
        XCTAssertFalse(skipped.contains { $0.plain == "Second page" })
        let kept = KpageParsing.extractParagraphs(chunks, skipLastPage: true, keepFiller: true)
        XCTAssertTrue(kept.contains { $0.plain == "Second page" })
    }

    func testColophonChunkIsDropped() {
        let colophon = #"{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"ISBN 978 \#u{BC1C}\#u{D589}\#u{C778} Someone \#u{BE14}\#u{B85C}\#u{ADF8}"}]}}"#
        let chunks = [Data(kpageChunk1.utf8), Data(colophon.utf8)]
        let dropped = KpageParsing.extractParagraphs(chunks, skipLastPage: false, keepFiller: false)
        XCTAssertFalse(dropped.contains { $0.plain.contains("ISBN") })
        let kept = KpageParsing.extractParagraphs(chunks, skipLastPage: false, keepFiller: true)
        XCTAssertTrue(kept.contains { $0.plain.contains("ISBN") })
        XCTAssertFalse(KpageParsing.isColophonChunk("Just a story with an ISBN mention"))
    }

    func testImageOnlyParagraphsAreDropped() {
        let chunk = #"{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","childParagraphList":[{"type":"IMG","text":""}]},{"id":2,"type":"P","text":"Kept"}]}}"#
        let paragraphs = KpageParsing.extractParagraphs([Data(chunk.utf8)], skipLastPage: false, keepFiller: false)
        XCTAssertEqual(paragraphs.map { $0.plain }, ["Kept"])
    }

    func testStyleIsRendered() {
        let chunk = #"{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"X","style":{"textAlign":"CENTER","color":"ff0000","fontSize":14}}]}}"#
        let paragraphs = KpageParsing.extractParagraphs([Data(chunk.utf8)], skipLastPage: false, keepFiller: false)
        let output = KpageParsing.buildOutput(paragraphs)
        XCTAssertTrue(output.html.contains("color:#ff0000"))
        XCTAssertTrue(output.html.contains("text-align:center"))
        XCTAssertTrue(output.html.contains("font-size:14px"))
    }

    func testStripHeadings() {
        let stripped = KpageParsing.stripHeadings([kpPara("\u{c81c}12\u{d654}"), kpPara("&nbsp;"), kpPara("  "), kpPara("Text"), kpPara("\u{c81c}3\u{d654}")])
        XCTAssertEqual(stripped.map { $0.plain }, ["Text", "\u{c81c}3\u{d654}"])
    }

    func testBlankBreakParagraphIsKept() {
        let output = KpageParsing.buildOutput([kpPara("", "P", html: "<br />"), kpPara("Text")])
        XCTAssertEqual(output.html, "<p><br /></p>\n<p>Text</p>")
    }

    // Image chapters

    func testBuildImageChapter() throws {
        guard case .images(let files) = KpageParsing.parseViewer(Data(kpageViewerImages.utf8), status: 200) else {
            return XCTFail("expected images")
        }
        let built = try XCTUnwrap(KpageParsing.buildImageChapter(files, chapterName: "Ep 5", chapterIndex: 5))
        XCTAssertEqual(built.images.map { $0.filename }, ["images/0005_01.jpg", "images/0005_02.png"])
        XCTAssertEqual(built.images[0].url.absoluteString, "https://img.example.test/a?filename=a.jpg")
        XCTAssertTrue(built.html.contains("src=\"images/0005_01.jpg\""))
        XCTAssertTrue(built.html.contains("width=\"100\" height=\"200\""))
        XCTAssertNil(KpageParsing.buildImageChapter([], chapterName: "x", chapterIndex: 1))
    }

    func testRemoveDuplicateLeadingImages() throws {
        func chapter(_ index: Int, _ bytes: [UInt8]) throws -> ChapterContent {
            let file = KpageImageFile(no: 1, url: "https://img.example.test/\(index)", width: 0, height: 0, filename: "p.jpg")
            let built = try XCTUnwrap(KpageParsing.buildImageChapter([file], chapterName: "c", chapterIndex: index))
            var images = built.images
            images[0].data = Data(bytes)
            return ChapterContent(ref: ChapterRef(id: "\(index)", index: index, title: "c"), html: built.html, images: images)
        }
        let cleaned = KpageParsing.removeDuplicateLeadingImages(
            [try chapter(1, [1]), try chapter(2, [1]), try chapter(3, [2])], maxPositions: 2)
        XCTAssertEqual(cleaned[0].images.count, 1)
        XCTAssertEqual(cleaned[1].images.count, 0)
        XCTAssertFalse(cleaned[1].html.contains("<img"))
        XCTAssertEqual(cleaned[2].images.count, 1)
        XCTAssertTrue(cleaned[2].html.contains("<img"))
    }

    func testJSLiteralEscapes() {
        XCTAssertEqual(KpageAdapter.jsLiteral("a\"b"), "\"a\\\"b\"")
    }

    // Adapter flows

    func testParseBookEndToEnd() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/product/list", status: 200, contentType: "application/json", body: kpageEpisodesJSON),
        ])
        let web = KpageStubWeb(page: KpageFakePage(html: kpageBookHTML, tagsJSON: #"["tagone","tagtwo"]"#))
        let book = try await KpageAdapter().parseBook(
            URL(string: "https://\(kpageSite)/content/42")!, context: kpContext(http: http, web: web))
        XCTAssertEqual(book.source, .kpage)
        XCTAssertEqual(book.id, "42")
        XCTAssertEqual(book.title, "Test Novel")
        XCTAssertEqual(book.author, "Author Name")
        XCTAssertEqual(book.tags, ["tagone", "tagtwo"])
        XCTAssertEqual(book.coverURL?.absoluteString, "https://img.example.test/cover.jpg")
        XCTAssertEqual(book.chapters.map { $0.index }, [1, 2, 3, 4])
        XCTAssertEqual(book.chapters.map { $0.id }, ["101", "102", "103", "104"])
        XCTAssertEqual(book.chapters.map { $0.isPaid }, [false, true, false, true])
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "https://\(kpageSite)/content/42/viewer/101")
    }

    func testParseTextChapter() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/viewer/data", status: 200, contentType: "application/json", body: kpageViewerText),
            KpageRoute(contains: "ats.example.test/chunk1", status: 200, contentType: "application/json", body: kpageChunk1),
            KpageRoute(contains: "ats.example.test/chunk2", status: 200, contentType: "application/json", body: kpageChunk2),
        ])
        let result = try await KpageAdapter().parseChapter(kpRef("105", index: 5), book: kpBook(), context: kpContext(http: http))
        guard case .content(let content) = result else { return XCTFail("expected content, got \(result)") }
        XCTAssertEqual(content.ref.title, "Heading")
        XCTAssertTrue(content.html.contains("<p>Hello &amp; &lt;world&gt;</p>"))
        XCTAssertTrue(content.images.isEmpty)
    }

    func testParseImageChapterWithDedupeFetchesLeadingBytes() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/viewer/data", status: 200, contentType: "application/json", body: kpageViewerImages),
            KpageRoute(contains: "img.example.test", status: 200, contentType: "image/jpeg", body: "bytes"),
        ])
        var settings = AppSettings()
        settings.sources.kpage.dedupeImages = true
        settings.sources.kpage.dedupeLeadingImages = 1
        let ref = kpRef("106", index: 6)
        let results = try await KpageAdapter().parseChapters([ref], book: kpBook(), context: kpContext(http: http, settings: settings))
        guard case .content(let content) = results[0] else { return XCTFail("expected content") }
        XCTAssertEqual(content.images.count, 2)
        XCTAssertNotNil(content.images[0].data)
        XCTAssertNil(content.images[1].data)
    }

    func testLockedChapterIsRefused() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/viewer/data", status: 200, contentType: "application/json", body: #"{"message":"not_purchased"}"#),
        ])
        let result = try await KpageAdapter().parseChapter(kpRef("107", index: 7, paid: true), book: kpBook(), context: kpContext(http: http))
        guard case .refused(let ref, let reason) = result else { return XCTFail("expected refused, got \(result)") }
        XCTAssertEqual(ref.id, "107")
        XCTAssertTrue(reason.contains("not_purchased"))
    }

    func testForbiddenStatusIsRefused() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/viewer/data", status: 403, contentType: "application/json", body: "{}"),
        ])
        let result = try await KpageAdapter().parseChapter(kpRef("108", index: 8), book: kpBook(), context: kpContext(http: http))
        guard case .refused = result else { return XCTFail("expected refused, got \(result)") }
    }

    func testSkipPaidDoesNotTouchTheNetwork() async throws {
        var settings = AppSettings()
        settings.shared.skipPaid = true
        let http = KpageStubHTTP()
        let result = try await KpageAdapter().parseChapter(
            kpRef("109", index: 9, paid: true), book: kpBook(), context: kpContext(http: http, settings: settings))
        guard case .refused = result else { return XCTFail("expected refused, got \(result)") }
        XCTAssertTrue(http.log.all.isEmpty)
    }

    func testEmptyTextIsFailedNotContent() async throws {
        let http = KpageStubHTTP(routes: [
            KpageRoute(contains: "/viewer/data", status: 200, contentType: "application/json", body: kpageViewerText),
        ])
        let result = try await KpageAdapter().parseChapter(kpRef("110", index: 10), book: kpBook(), context: kpContext(http: http))
        guard case .failed = result else { return XCTFail("expected failed, got \(result)") }
    }
}
