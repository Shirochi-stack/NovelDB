import XCTest
import NpiaCore
@testable import NpiaSources

final class ReaderAdapterTests: XCTestCase {
    // MARK: - Loading and matching

    func testRulesBundleLoadsInJavaScriptCore() throws {
        let runtime = try ReaderRuntime(http: FixtureHTTP([:]))
        XCTAssertEqual(runtime.evaluate("typeof window.__ND_getRule"), "function")
        XCTAssertEqual(runtime.evaluate("typeof window.__ND_parseBook"), "function")
        XCTAssertEqual(runtime.evaluate("window.__ND_READY === true"), "true")
        XCTAssertEqual(runtime.evaluate("typeof GM_xmlhttpRequest"), "function")
    }

    func testMatchesAsksTheRules() {
        XCTAssertTrue(ReaderAdapter.matches(URL(string: "https://www.sudugu.com/1001/")!))
        XCTAssertTrue(ReaderAdapter.matches(URL(string: "https://ncode.syosetu.com/n0000aa/")!))
        XCTAssertTrue(ReaderAdapter.matches(URL(string: "https://kakuyomu.jp/works/1")!))
        // Rules that match a host pattern rather than a fixed name.
        XCTAssertTrue(ReaderAdapter.matches(URL(string: "https://booktoki123.com/novel/1")!))
        XCTAssertFalse(ReaderAdapter.matches(URL(string: "https://example.com/book/1")!))
        XCTAssertFalse(ReaderAdapter.matches(URL(string: "ftp://www.sudugu.com/1001/")!))
        XCTAssertEqual(SourceCatalog.adapterType(for: URL(string: "https://www.sudugu.com/1001/")!)?.id, .reader)
    }

    // MARK: - End to end

    func testSuduguBookAndChapters() async throws {
        let context = makeContext(Fixtures.sudugu)
        let adapter = ReaderAdapter()
        let book = try await adapter.parseBook(URL(string: "https://www.sudugu.com/1001/")!, context: context)

        XCTAssertEqual(book.source, .reader)
        XCTAssertEqual(book.title, "测试之书")
        XCTAssertEqual(book.author, "张三")
        XCTAssertEqual(book.synopsis, "这是一段简介。\n\n第二行。")
        XCTAssertEqual(book.tags, ["玄幻", "连载"])
        XCTAssertEqual(book.coverURL?.absoluteString, "https://www.sudugu.com/cover/1001.jpg")
        XCTAssertEqual(book.chapters.map(\.title), ["第一章 开始", "第二章 继续", "第三章 结束"])
        XCTAssertEqual(book.chapters.map(\.index), [1, 2, 3])
        XCTAssertEqual(book.chapters[2].url?.absoluteString, "https://www.sudugu.com/1001/3.html")
        XCTAssertFalse(book.chapters.contains { $0.isPaid })

        // Two-page chapter with an image and a repeated title line.
        guard case .content(let first) = try await adapter.parseChapter(book.chapters[0], book: book, context: context) else {
            return XCTFail("chapter 1 should parse")
        }
        XCTAssertEqual(first.html, """
        <div class="reader-content">
        <p>第一段。</p>
        <p>第二段 文字。</p>
        <p class="img"><img src="images/0001_01.png" alt=""/></p>
        <p>第三段。</p>
        </div>
        """)
        XCTAssertEqual(first.images, [ImageRef(url: URL(string: "https://www.sudugu.com/img/a.png")!, filename: "images/0001_01.png")])

        let results = try await adapter.parseChapters(Array(book.chapters[1...]), book: book, context: context)
        guard results.count == 2, case .content(let second) = results[0], case .content(let third) = results[1] else {
            return XCTFail("chapters 2 and 3 should parse: \(results)")
        }
        XCTAssertTrue(second.html.contains("<p>甲行</p>\n<p>乙行</p>\n<p>丙行</p>"), second.html)
        XCTAssertTrue(third.html.contains("<p>完 &amp; 结</p>"), third.html)
    }

    func testTemplateRuleWithSections() async throws {
        let context = makeContext(Fixtures.syosetu)
        let adapter = ReaderAdapter()
        let book = try await adapter.parseBook(URL(string: "https://ncode.syosetu.com/n0000aa/")!, context: context)

        XCTAssertEqual(book.title, "試験の物語")
        XCTAssertEqual(book.language, "ja")
        XCTAssertEqual(book.synopsis, "あらすじです。\n二行目。")
        XCTAssertEqual(book.chapters.map(\.title), ["プロローグ", "第一話", "第二話"])
        XCTAssertEqual(book.chapters.map { $0.url?.absoluteString }, [
            "https://ncode.syosetu.com/n0000aa/1/",
            "https://ncode.syosetu.com/n0000aa/2/",
            "https://ncode.syosetu.com/n0000aa/3/",
        ])

        guard case .content(let content) = try await adapter.parseChapter(book.chapters[0], book: book, context: context) else {
            return XCTFail("chapter should parse")
        }
        XCTAssertEqual(content.html, """
        <div class="reader-content">
        <p>前書き</p>
        <p>本文一行目</p>
        <p>本文三行目</p>
        <p>後書き</p>
        </div>
        """)
    }

    func testChapterFromAnotherSessionReopensTheBook() async throws {
        let context = makeContext(Fixtures.sudugu)
        let book = try await ReaderBookSession.open(URL(string: "https://www.sudugu.com/1001/")!, context: context).book
        // A fresh BookInfo (e.g. restored after relaunch) still resolves its chapters.
        let result = try await ReaderAdapter().parseChapter(book.chapters[2], book: book, context: context)
        guard case .content(let content) = result else { return XCTFail("\(result)") }
        XCTAssertTrue(content.html.contains("完 &amp; 结"))
    }

    func testMissingPageFailsWithParseError() async {
        let context = makeContext([:])
        do {
            _ = try await ReaderAdapter().parseBook(URL(string: "https://www.sudugu.com/404/")!, context: context)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("HTTP 404 for https://www.sudugu.com/404/"))
        }
    }

    // MARK: - Chapter HTML

    func testChapterBuilderDropsRepeatedTitleAndEscapes() {
        let ref = ChapterRef(id: "x", index: 12, title: "Chapter  ONE")
        let content = ReaderChapterBuilder.content(ref, blocks: [
            .text("chapter one"),
            .text("a < b & \"c\""),
            .image(URL(string: "https://img.example/p/1.webp?x=1")!),
            .image(URL(string: "https://img.example/p/1.webp?x=1")!),
            .image(URL(string: "https://img.example/p/2")!),
        ])
        XCTAssertEqual(content?.html, """
        <div class="reader-content">
        <p>a &lt; b &amp; &quot;c&quot;</p>
        <p class="img"><img src="images/0012_01.webp" alt=""/></p>
        <p class="img"><img src="images/0012_01.webp" alt=""/></p>
        <p class="img"><img src="images/0012_02.jpg" alt=""/></p>
        </div>
        """)
        XCTAssertEqual(content?.images.map(\.filename), ["images/0012_01.webp", "images/0012_02.jpg"])
        XCTAssertNil(ReaderChapterBuilder.content(ref, blocks: [.text("Chapter one")]))
    }

    func testPaidChapterWithoutTextIsRefused() async throws {
        let ref = ChapterRef(id: "/1001/9.html", index: 9, title: "VIP", url: URL(string: "https://www.sudugu.com/1001/9.html"), isPaid: true)
        let session = ReaderBookSession(
            book: BookInfo(source: .reader, id: "b", url: URL(string: "https://www.sudugu.com/1001/")!, title: "b", chapters: [ref]),
            engine: .script(try ReaderRuntime(http: FixtureHTTP([:]))),
            chapters: [ref.id: .init(url: ref.id, name: "VIP", isVIP: true, isPaid: false)]
        )
        // No book is open in the runtime, so the rule call fails; a paid chapter is refused, not failed.
        let result = try await session.chapter(ref)
        guard case .refused(let refused, _) = result else { return XCTFail("\(result)") }
        XCTAssertEqual(refused.index, 9)
    }

    // MARK: - JavaScript environment

    func testDOMShimFollowsBrowserParsing() throws {
        let runtime = try ReaderRuntime(http: FixtureHTTP([:]))
        let script = """
        (function () {
          var d = new DOMParser().parseFromString('<table><tr><td>a</td><td>b</td></tr></table><p>x<p>y &amp; z<ul><li>1<li>2</ul><div id="c" class="k m"><span>s</span> t</div>', 'text/html');
          return JSON.stringify([
            d.querySelectorAll('table > tbody > tr > td').length,
            d.querySelectorAll('body > p').length,
            d.querySelector('p:nth-of-type(2)').textContent,
            d.querySelectorAll('ul > li:last-child')[0].textContent,
            d.querySelector('div.k.m > span + *') === null,
            d.querySelector('#c').innerHTML,
            d.querySelector('div:has(> span)').id,
            d.querySelectorAll('li:not(:first-child)').length,
            new URL('../b?q=1#h', 'https://a.example/x/y/z').href
          ]);
        })()
        """
        XCTAssertEqual(runtime.evaluate(script), #"[2,2,"y & z","2",true,"<span>s</span> t","c",1,"https://a.example/x/b?q=1#h"]"#)
    }

    func testTextDecoderUsesSwiftCharsets() throws {
        let runtime = try ReaderRuntime(http: FixtureHTTP([:]))
        let gbk = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))
        let bytes = try XCTUnwrap("第一章 中文".data(using: gbk)).base64EncodedString()
        XCTAssertEqual(runtime.evaluate("new TextDecoder('gbk').decode(__npia_env.base64ToBytes('\(bytes)'))"), "第一章 中文")
        XCTAssertEqual(ReaderText.decode(Data("é".utf8), charset: "utf-8"), "é")
    }

    // MARK: - Helpers

    private func makeContext(_ pages: [String: String]) -> SourceContext {
        SourceContext(http: FixtureHTTP(pages), web: StubWeb(), secrets: InMemorySecretStore(), settings: AppSettings())
    }
}

/// Serves fixture pages by absolute URL; anything else is a 404.
struct FixtureHTTP: HTTPFetching {
    let pages: [String: String]

    init(_ pages: [String: String]) {
        self.pages = pages
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        guard let page = pages[request.url.absoluteString] else {
            return HTTPResponse(url: request.url, status: 404, headers: [:], body: Data())
        }
        return HTTPResponse(url: request.url, status: 200, headers: ["content-type": "text/html; charset=utf-8"], body: Data(page.utf8))
    }
}

/// Small hand-made pages shaped like the sites the rules expect.
enum Fixtures {
    static let sudugu: [String: String] = [
        "https://www.sudugu.com/1001/": """
        <!DOCTYPE html><html><head><meta charset="utf-8"><title>测试之书</title></head><body>
        <div class="item"><img src="/cover/1001.jpg"></div>
        <div class="itemtxt"><h1><a href="/1001/">测试之书</a></h1><p><a href="/author/1">作者：张三</a> <span>玄幻</span><span>连载</span></p></div>
        <div class="des"><p>这是一段简介。</p><p>第二行。</p></div>
        <div id="list"><ul><li><a href="/1001/1.html">第一章 开始</a></li><li><a href="/1001/2.html">第二章 继续</a></li></ul></div>
        <div id="pages"><a href="/1001/">上一页</a><a href="/1001/p-2.html">下一页</a></div>
        </body></html>
        """,
        "https://www.sudugu.com/1001/p-2.html": """
        <html><body><div id="list"><ul><li><a href="/1001/3.html">第三章 结束</a></li></ul></div>
        <div id="pages"><a href="/1001/">上一页</a></div></body></html>
        """,
        "https://www.sudugu.com/1001/1.html": """
        <html><body><div class="con"><p>第一章 开始</p><p>第一段。</p><p>第二段&nbsp;文字。</p><p><img src="/img/a.png"></p></div>
        <div class="prenext"><span><a href="/1001/">目录</a></span><span><a href="/1001/1_2.html">下一页</a></span></div></body></html>
        """,
        "https://www.sudugu.com/1001/1_2.html": """
        <html><body><div class="con"><p>第三段。</p></div>
        <div class="prenext"><span><a href="/1001/2.html">下一章</a></span></div></body></html>
        """,
        "https://www.sudugu.com/1001/2.html": """
        <html><body><div class="con">甲行<br>乙行<br><br>丙行</div></body></html>
        """,
        "https://www.sudugu.com/1001/3.html": """
        <html><body><div class="con"><p>完 &amp; 结</p><script>ad()</script></div></body></html>
        """,
    ]

    static let syosetu: [String: String] = [
        "https://ncode.syosetu.com/n0000aa/": """
        <!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><title>試験の物語</title></head><body>
        <div class="l-container"><main><article>
        <h1 class="p-novel__title">試験の物語</h1>
        <div class="p-novel__author">作者：<a href="/u/1/">山田</a></div>
        <div id="novel_ex" class="p-novel__summary">あらすじです。<br>二行目。</div>
        <div class="p-eplist">
        <div class="p-eplist__chapter-title">第一部</div>
        <div class="p-eplist__sublist"><a href="/n0000aa/1/" class="p-eplist__subtitle">プロローグ</a></div>
        <div class="p-eplist__sublist"><a href="/n0000aa/2/" class="p-eplist__subtitle">第一話</a></div>
        <div class="p-eplist__chapter-title">第二部</div>
        <div class="p-eplist__sublist"><a href="/n0000aa/3/" class="p-eplist__subtitle">第二話</a></div>
        </div></article></main></div></body></html>
        """,
        "https://ncode.syosetu.com/n0000aa/1/": """
        <html><body><div class="p-novel__body">
        <div class="p-novel__text p-novel__text--preface"><p>前書き</p></div>
        <div class="p-novel__text"><p id="L1">本文一行目</p><p id="L2"><br></p><p id="L3">本文三行目</p></div>
        <div class="p-novel__text p-novel__text--afterword"><p>後書き</p></div>
        </div></body></html>
        """,
        "https://ncode.syosetu.com/n0000aa/2/": "<html><body><div class=\"p-novel__text\"><p>二話です。</p></div></body></html>",
        "https://ncode.syosetu.com/n0000aa/3/": "<html><body><div class=\"p-novel__text\"><p>三話です。</p></div></body></html>",
    ]
}
