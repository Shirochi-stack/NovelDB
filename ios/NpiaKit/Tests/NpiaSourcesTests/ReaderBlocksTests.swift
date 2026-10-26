import XCTest
import NpiaCore
@testable import NpiaSources

final class ReaderBlocksTests: XCTestCase {
    func testHTMLBlocksSplitParagraphsBreaksNewlinesAndImages() throws {
        let html = """
        <div><p>First&nbsp;line<br>second line</p>
        <script>var x = 1;</script>
        <p>pre-line one
        pre-line two</p>
        <img data-src="/img/a.png" src="blank.gif"><span>tail <b>bold</b></span>
        <img src="file:///C:/local.png"><button>Like</button></div>
        """
        let blocks = try ReaderBlocks.fromHTML(html, baseURL: URL(string: "https://example.com/read/1"))
        XCTAssertEqual(blocks, [
            .text("First line"), .text("second line"),
            .text("pre-line one"), .text("pre-line two"),
            .image(URL(string: "https://example.com/img/a.png")!),
            .text("tail bold"),
        ])
    }

    func testPlainBlocksDropBlankLines() {
        XCTAssertEqual(ReaderBlocks.fromPlainText("  a \r\n\r\n\u{a0}b\n \n"), [.text("a"), .text("b")])
    }

    func testContentDropsRepeatedTitleAndNamesImages() {
        let ref = ChapterRef(id: "1", index: 3, title: "제 1 화")
        let image = URL(string: "https://cdn.example.com/x/pic.PNG?type=w800")!
        let content = ReaderBlocks.content([.text("제1화"), .text("A < B"), .image(image), .image(image)], ref: ref, cssClass: "c")
        XCTAssertEqual(content.images.map(\.filename), ["images/0003_01.png"])
        XCTAssertEqual(content.html, """
        <div class="c">
        <p>A &lt; B</p>
        <p class="img"><img src="images/0003_01.png" alt=""/></p>
        <p class="img"><img src="images/0003_01.png" alt=""/></p>
        </div>
        """)
    }

    func testUnescapeHTML() {
        XCTAssertEqual(HTTPSourceSupport.unescapeHTML("a &amp;lt; &#54620;&#xAE00; &quot;q&quot;"), "a &lt; 한글 \"q\"")
    }
}

final class MaruAdapterTests: XCTestCase {
    let site = URL(string: "https://marumaru123.com/novel/42")!

    func testMatches() {
        XCTAssertTrue(MaruAdapter.matches(site))
        XCTAssertTrue(MaruAdapter.matches(URL(string: "https://www.marumaru9.net/novel/42/77")!))
        XCTAssertFalse(MaruAdapter.matches(URL(string: "https://marumaru123.com/webtoon/42")!))
        XCTAssertFalse(MaruAdapter.matches(URL(string: "https://example.com/novel/42")!))
        XCTAssertEqual(MaruAdapter.parts(URL(string: "https://marumaru1.com/novel/42/77")!)?.episode, "77")
    }

    func testParseBookSortsEpisodesAndStripsWorkTitle() async throws {
        let http = RoutedHTTP()
        http.on("works/42", json: [
            "ok": true,
            "item": ["title": "작품", "author": "작가", "cover_url": "https://img.example.com/c.jpg", "genres": "판타지, 무협/로맨스", "status": "연재"],
            "synopsis": "줄거리\n둘째 줄",
            "episodes": [
                ["id": 12, "number": 2, "title": "작품 2화"],
                ["id": 11, "number": 1, "title": "작품  프롤로그"],
                ["id": 13, "number": 3, "title": ""],
            ],
        ])
        let book = try await MaruAdapter().parseBook(site, context: makeContext(http))
        XCTAssertEqual(book.title, "작품")
        XCTAssertEqual(book.author, "작가")
        XCTAssertEqual(book.tags, ["판타지", "무협", "로맨스"])
        XCTAssertEqual(book.chapters.map(\.title), ["프롤로그", "2화", "3화"])
        XCTAssertEqual(book.chapters.map(\.id), ["11", "12", "13"])
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "https://marumaru123.com/novel/42/11")
        XCTAssertNil(http.requests.first?.headers["Referer"])
    }

    func testChapterKinds() async throws {
        let ref = ChapterRef(id: "11", index: 1, title: "프롤로그", url: URL(string: "https://marumaru123.com/novel/42/11"))
        let book = BookInfo(source: .maru, id: "42", url: site, title: "작품")
        let http = RoutedHTTP()
        http.on("episodes/11", json: ["ok": true, "item": ["body": [
            ["kind": "text", "text": "첫 줄\n\n둘째 줄"],
            ["kind": "image", "url": "https://img.example.com/1.webp"],
        ]]])
        let result = try await MaruAdapter().parseChapter(ref, book: book, context: makeContext(http))
        let body = try XCTUnwrap(content(result))
        XCTAssertTrue(body.html.contains("<p>첫 줄</p>\n<p>둘째 줄</p>"))
        XCTAssertEqual(body.images.map(\.filename), ["images/0001_01.webp"])

        http.on("episodes/11", json: ["ok": true, "item": ["body": [["kind": "paid"]]]])
        XCTAssertTrue(isRefused(try await MaruAdapter().parseChapter(ref, book: book, context: makeContext(http))))

        http.on("episodes/11", json: ["ok": true, "item": ["ready": false, "body": []]])
        XCTAssertTrue(isRefused(try await MaruAdapter().parseChapter(ref, book: book, context: makeContext(http))))

        http.on("episodes/11", json: ["ok": true, "item": ["embed_url": "/files/11.txt", "body": []]])
        http.on("files/11.txt", "\u{feff}임베드 본문\n끝")
        let embedded = try XCTUnwrap(content(try await MaruAdapter().parseChapter(ref, book: book, context: makeContext(http))))
        XCTAssertTrue(embedded.html.contains("<p>임베드 본문</p>"))
    }
}

final class YedujiAdapterTests: XCTestCase {
    let index = """
    <html><head><title>夜之书 - 某某 著 - 夜读集</title></head><body>
    <h1>夜之书</h1>
    <img class="cover" src="/data/cover/1/77.jpg" />
    <div class="info"><dl><dt>作者</dt><dd><a href="/author/1">某某</a></dd></dl>
    <dl><dt>分类</dt><dd><a href="/c/1">玄幻</a></dd></dl>
    <dl><dt>标签</dt><dd><a href="/t/1">热血</a><a href="/t/2">系统</a></dd></dl>
    <dl><dt>状态</dt><dd>连载中</dd></dl></div>
    <div class="desc"><img src="/more.png"><p class="desc-content">第一段<br/>第二段 &amp; 更多</p></div>
    </body></html>
    """
    let list = """
    <ul>
    <li><a data-chapterId="901" href="/book/77/901.html" class="c"><h4>第一章 开始</h4><small class="text-muted">免费</small></a></li>
    <li><a data-chapterId="902" href="/book/77/902.html" class="c"><h4>第二章 &amp; 继续</h4><small class="text-muted">VIP</small></a></li>
    </ul>
    """

    func testMatches() {
        XCTAssertTrue(YedujiAdapter.matches(URL(string: "https://www.yeduji.com/book/77/")!))
        XCTAssertFalse(YedujiAdapter.matches(URL(string: "https://yeduji.com/author/77")!))
    }

    func testParseBook() async throws {
        let http = RoutedHTTP()
        http.on("/book/77/", index)
        http.on("/book/77/list/", list)
        let book = try await YedujiAdapter().parseBook(URL(string: "https://yeduji.com/book/77/901.html")!, context: makeContext(http))
        XCTAssertEqual(book.title, "夜之书")
        XCTAssertEqual(book.author, "某某")
        XCTAssertEqual(book.tags, ["热血", "系统", "玄幻"])
        XCTAssertEqual(book.status, "连载中")
        XCTAssertEqual(book.synopsis, "第一段\n第二段 & 更多")
        XCTAssertEqual(book.coverURL?.absoluteString, "https://yeduji.com/data/cover/1/77.jpg")
        XCTAssertEqual(book.chapters.map(\.title), ["第一章 开始", "第二章 & 继续"])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, true])
        XCTAssertEqual(book.chapters[1].url?.absoluteString, "https://yeduji.com/book/77/902.html")
    }

    func testChapterStripsPromoAndRefusesVIP() async throws {
        let ref = ChapterRef(id: "901", index: 1, title: "第一章", url: URL(string: "https://yeduji.com/book/77/901.html"))
        let html = """
        <h1 class="title">第一章 开始</h1>
        <div class="content"><p>正文一</p><p>夜读集由爱发电支持</p><p><b>正文二</b></p><p> </p></div>
        """
        let body = try XCTUnwrap(content(YedujiAdapter.chapter(html: html, ref: ref)))
        XCTAssertEqual(body.html, "<div class=\"yeduji-content\">\n<p>正文一</p>\n<p>正文二</p>\n</div>")

        let preview = "<div class=\"content\"><p>预览</p><p>以下内容为VIP专属</p></div>"
        XCTAssertTrue(isRefused(YedujiAdapter.chapter(html: preview, ref: ref)))

        var paid = ref
        paid.isPaid = true
        let http = RoutedHTTP()
        let book = BookInfo(source: .yeduji, id: "77", url: URL(string: "https://yeduji.com/book/77/")!, title: "t")
        XCTAssertTrue(isRefused(try await YedujiAdapter().parseChapter(paid, book: book, context: makeContext(http))))
        XCTAssertTrue(http.requests.isEmpty)
    }
}
