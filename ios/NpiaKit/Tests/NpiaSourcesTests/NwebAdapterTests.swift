import CoreGraphics
import ImageIO
import XCTest
import NpiaCore
@testable import NpiaSources

final class NwebAdapterTests: XCTestCase {
    let domain = NwebAdapter.domain

    func listHTML(page: Int) -> String {
        let rows = (page == 1 ? [3, 2] : [1]).map { volume in
            """
            <li class="volumeComment"><a class="list_item" href="/webnovel/detail?novelId=55&amp;volumeNo=\(volume)">
              <p class="subj"><span class="bullet_wrap"><em>UP</em></span>\(volume)화  제목 \(volume)</p><span class="date">24.01.0\(volume)</span></a></li>
            """
        }.joined()
        return """
        <html><head><meta property="og:image" content="https://img.example.com/og.jpg"></head><body>
        <div class="section_area_info">
          <div class="thumbnail"><img src="//img.example.com/cover.jpg"></div>
          <h2 class="title">시험 작품</h2>
          <div class="info_top"><div class="info_group"><span class="item">판타지</span></div></div>
          <a href="/search?target=author&query=x">글쓴이</a>
          <span class="bullet_comp">완결</span>
          <p class="summary">첫 줄<br>둘째 줄 <a href="#">더보기</a></p>
        </div>
        <div class="end_tag_area"><div class="tag_collection"><a class="tag">#회귀</a><a class="tag">#판타지</a></div></div>
        <div class="cont_sub"><div class="component_head"><h3 class="title">작품 회차 (3)</h3></div>
        <ul>\(rows)</ul></div>
        </body></html>
        """
    }

    func episodeHTML(_ text: String) -> String {
        "<div class=\"detail_view_content\"><p>\(text)</p><p>둘째 문단</p></div>"
    }

    func testMatches() {
        XCTAssertTrue(NwebAdapter.matches(URL(string: "https://novel.\(domain)/webnovel/list?novelId=55")!))
        XCTAssertTrue(NwebAdapter.matches(URL(string: "https://m.novel.\(domain)/best/detail?novelId=55&volumeNo=2")!))
        XCTAssertTrue(NwebAdapter.matches(URL(string: "https://series.\(domain)/novel/detail.series?productNo=99")!))
        XCTAssertFalse(NwebAdapter.matches(URL(string: "https://novel.\(domain)/webnovel/list?novelId=0")!))
        XCTAssertFalse(NwebAdapter.matches(URL(string: "https://novel.\(domain)/webnovel/weekday")!))
        XCTAssertEqual(NwebAdapter.novelParts(URL(string: "https://novel.\(domain)/challenge/detail?novelId=7&volumeNo=3")!)?.tier, "challenge")
    }

    func testParseBookReadsAllListPages() async throws {
        let http = RoutedHTTP()
        http.on("/webnovel/list?novelId=55", listHTML(page: 1))
        http.on("/webnovel/list?novelId=55&page=2", listHTML(page: 2))
        http.on("/webnovel/detail?novelId=55", episodeHTML("본문"))
        let book = try await NwebAdapter().parseBook(URL(string: "https://novel.\(domain)/webnovel/detail?novelId=55&volumeNo=2")!, context: makeContext(http))
        XCTAssertEqual(book.title, "시험 작품")
        XCTAssertEqual(book.author, "글쓴이")
        XCTAssertEqual(book.status, "완결")
        XCTAssertEqual(book.synopsis, "첫 줄\n둘째 줄")
        XCTAssertEqual(book.coverURL?.absoluteString, "https://img.example.com/cover.jpg")
        XCTAssertEqual(book.tags, ["판타지", "회귀"])
        XCTAssertEqual(book.chapters.map(\.title), ["1화 제목 1", "2화 제목 2", "3화 제목 3"])
        XCTAssertEqual(book.chapters.map(\.id), ["1", "2", "3"])
        XCTAssertEqual(book.chapters[0].url?.absoluteString, "https://novel.\(domain)/webnovel/detail?novelId=55&volumeNo=1")
    }

    func testLoginRedirect() async throws {
        let http = RoutedHTTP()
        http.on("/webnovel/list?novelId=55", "<html></html>", finalURL: URL(string: "https://nid.\(domain)/login")!)
        do {
            _ = try await NwebAdapter().parseBook(URL(string: "https://novel.\(domain)/webnovel/list?novelId=55")!, context: makeContext(http))
            XCTFail("expected login error")
        } catch let error as SourceError {
            XCTAssertEqual(error, .loginRequired(.nweb))
        }
        http.on("/webnovel/detail?novelId=55", "<html></html>", finalURL: URL(string: "https://nid.\(domain)/login")!)
        let ref = ChapterRef(id: "1", index: 1, title: "1화", url: URL(string: "https://novel.\(domain)/webnovel/detail?novelId=55&volumeNo=1"))
        let book = BookInfo(source: .nweb, id: "55", url: URL(string: "https://novel.\(domain)/webnovel/list?novelId=55")!, title: "t")
        XCTAssertTrue(isRefused(try await NwebAdapter().parseChapter(ref, book: book, context: makeContext(http))))
    }

    func testChapterDropsRepeatedTitle() async throws {
        let http = RoutedHTTP()
        http.on("/webnovel/detail?novelId=55", episodeHTML("1화 제목 1"))
        let ref = ChapterRef(id: "1", index: 1, title: "1화 제목 1", url: URL(string: "https://novel.\(domain)/webnovel/detail?novelId=55&volumeNo=1"))
        let book = BookInfo(source: .nweb, id: "55", url: URL(string: "https://novel.\(domain)/webnovel/list?novelId=55")!, title: "t")
        let body = try XCTUnwrap(content(try await NwebAdapter().parseChapter(ref, book: book, context: makeContext(http))))
        XCTAssertEqual(body.html, "<div class=\"nweb-novel-content\">\n<p>둘째 문단</p>\n</div>")
    }

    func testSeriesResolvesToWebEdition() {
        let page = "<a href=\"//novel.\(domain)/best/list?novelId=321\">웹소설 보기</a> totalCount=40"
        XCTAssertEqual(NwebAdapter.seriesWebEdition(page)?.absoluteString, "https://novel.\(domain)/best/list?novelId=321")
        XCTAssertNil(NwebAdapter.seriesWebEdition("<a href=\"/other\">x</a>"))
    }

    // MARK: - Logo banners

    static func png(width: Int, height: Int, mark: CGRect) -> Data {
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        context.fill(mark)
        let image = context.makeImage()!
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, "public.png" as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, image, nil)
        CGImageDestinationFinalize(destination)
        return data as Data
    }

    func testSignatureFromImage() throws {
        let banner = try XCTUnwrap(NwebImageSignature(imageData: Self.png(width: 400, height: 100, mark: CGRect(x: 20, y: 20, width: 100, height: 60))))
        XCTAssertEqual(banner.aspect, 4, accuracy: 0.001)
        XCTAssertTrue(banner.isBanner)
        XCTAssertEqual(banner.pixels.count, 64 * 8)
        XCTAssertTrue(banner.pixels.contains { $0 < 100 })
        let same = try XCTUnwrap(NwebImageSignature(imageData: Self.png(width: 800, height: 200, mark: CGRect(x: 40, y: 40, width: 200, height: 120))))
        XCTAssertTrue(banner.matches(same))
        let other = try XCTUnwrap(NwebImageSignature(imageData: Self.png(width: 400, height: 100, mark: CGRect(x: 280, y: 20, width: 100, height: 60))))
        XCTAssertFalse(banner.matches(other))
        let tall = try XCTUnwrap(NwebImageSignature(imageData: Self.png(width: 100, height: 150, mark: .zero)))
        XCTAssertFalse(tall.isBanner)
        XCTAssertFalse(banner.matches(tall))
    }

    func testLogoBannerIsDetectedAndDropped() async throws {
        let logo = Self.png(width: 400, height: 100, mark: CGRect(x: 20, y: 20, width: 100, height: 60))
        let http = RoutedHTTP()
        http.on("/webnovel/list?novelId=55", listHTML(page: 1))
        http.on("/webnovel/list?novelId=55&page=2", listHTML(page: 2))
        for volume in 1...3 {
            http.on("/webnovel/detail?novelId=55&volumeNo=\(volume)",
                    "<div class=\"detail_view_content\"><p>본문 \(volume)</p><img src=\"https://img.example.com/\(volume)/illust.png\"><p>끝</p><img src=\"https://img.example.com/\(volume)/logo_end.png?type=w1\"></div>")
            http.on("img.example.com/\(volume)/logo_end.png", data: logo)
            http.on("img.example.com/\(volume)/illust.png", data: Self.png(width: 100, height: 150, mark: CGRect(x: 10, y: 10, width: 50, height: 50)))
        }
        let adapter = NwebAdapter()
        let context = makeContext(http)
        let book = try await adapter.parseBook(URL(string: "https://novel.\(domain)/webnovel/list?novelId=55")!, context: context)
        let body = try XCTUnwrap(content(try await adapter.parseChapter(book.chapters[1], book: book, context: context)))
        XCTAssertEqual(body.images.map(\.url.lastPathComponent), ["illust.png"])
        XCTAssertFalse(body.html.contains("logo_end"))
    }
}
