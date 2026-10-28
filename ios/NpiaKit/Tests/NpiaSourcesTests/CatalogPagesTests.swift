import XCTest
import NpiaCore
@testable import NpiaSources

/// The classes of page the Catalog tab tells apart, and how the app follows the page on screen.
/// Source domains are kept as escapes.
final class CatalogPagesTests: XCTestCase {
    private let engines = EngineSettings()
    private let npia = NpiaParser.host
    private let qdn = "\u{71}\u{69}\u{64}\u{69}\u{61}\u{6e}.com"
    private let rulesOnly = "www.sudugu.com"
    /// The name people use for the npia site, as it appears in page titles.
    private var siteName: String { String(npia.dropLast(4)).capitalized }

    private func url(_ text: String) -> URL { URL(string: text)! }

    private func mode(_ text: String, cartLinks: Bool = true, engines: EngineSettings? = nil) -> CatalogPageMode {
        CatalogPages.mode(for: url(text), engines: engines ?? self.engines, cartLinksSetting: cartLinks)
    }

    // MARK: - The three classes

    func testNoveldbAndNoveldbNextAreOneClass() {
        XCTAssertTrue(CatalogPages.isNovelDB(url(CatalogPages.defaultAddress)))
        XCTAssertTrue(CatalogPages.isNovelDB(url("https://shirochi-stack.github.io/NovelDB/?tag=x")))
        XCTAssertTrue(CatalogPages.isNovelDB(url("https://shirochi-stack.github.io/NovelDB")))
        XCTAssertTrue(CatalogPages.isNovelDB(url(CatalogPages.nextAddress)))
        XCTAssertTrue(CatalogPages.isNovelDB(url("https://noveldb.pages.dev/some/route")))
        XCTAssertTrue(CatalogPages.isNovelDB(url("https://abc123.noveldb.pages.dev/")))
        // Other pages of the same host are not NovelDB.
        XCTAssertFalse(CatalogPages.isNovelDB(url("https://shirochi-stack.github.io/other-project/")))
        XCTAssertFalse(CatalogPages.isNovelDB(url("https://shirochi-stack.github.io/NovelDBX/")))
        XCTAssertFalse(CatalogPages.isNovelDB(url("https://evil.example/noveldb.pages.dev/")))
        XCTAssertFalse(CatalogPages.isNovelDB(url("https://notnoveldb.pages.dev/")))
        XCTAssertFalse(CatalogPages.isNovelDB(url("mailto:someone@example.com")))
        for address in [CatalogPages.defaultAddress, CatalogPages.nextAddress, "https://noveldb.pages.dev/x"] {
            let found = mode(address)
            XCTAssertEqual(found.kind, .novelDB, address)
            XCTAssertFalse(found.cartLinks, "links are not renamed on \(address)")
            XCTAssertFalse(found.download, "no Download button on \(address)")
        }
    }

    func testGlobalNpiaHomeHasNoButtonButANovelPageHasOne() {
        let home = mode("https://global.\(npia)/")
        XCTAssertEqual(home.kind, .supported)
        XCTAssertFalse(home.cartLinks)
        XCTAssertFalse(home.download)

        let novel = mode("https://global.\(npia)/novel/4321")
        XCTAssertEqual(novel.kind, .supported)
        XCTAssertFalse(novel.cartLinks)
        XCTAssertTrue(novel.download)
    }

    func testEngineListedSitesAreSupportedPageByPage() {
        // Rules header: book pages only. The home page and rankings are not downloadable.
        XCTAssertTrue(mode("https://book.\(qdn)/info/1035420986/").download)
        XCTAssertTrue(mode("https://www.\(qdn)/book/1035420986/").download)
        XCTAssertEqual(CatalogPages.kind(of: url("https://www.\(qdn)/rank/")), .supported)
        XCTAssertFalse(mode("https://www.\(qdn)/rank/").download)
        XCTAssertFalse(mode("https://www.\(qdn)/").download)
        // The rules switched off: nobody lists the site any more.
        let noRules = EngineSettings(disabled: [.novelDownloader, .webtoepub])
        XCTAssertEqual(CatalogPages.kind(of: url("https://\(rulesOnly)/"), engines: engines), .supported)
        XCTAssertEqual(CatalogPages.kind(of: url("https://\(rulesOnly)/"), engines: noRules), .other)
    }

    func testUnknownSitesKeepTodaysBehaviour() {
        let on = mode("https://example.com/books/1", cartLinks: true)
        XCTAssertEqual(on.kind, .other)
        XCTAssertTrue(on.cartLinks)
        XCTAssertFalse(on.download)
        XCTAssertFalse(mode("https://example.com/", cartLinks: false).cartLinks)
    }

    // MARK: - Addresses and titles

    func testAddressesAreComparedLikeTheBridgeDoes() {
        XCTAssertTrue(CatalogPages.sameAddress("https://a.test/x/", "https://a.test/x"))
        XCTAssertTrue(CatalogPages.sameAddress("https://a.test/x?y=1#", "https://a.test/x?y=1"))
        XCTAssertTrue(CatalogPages.sameAddress("https://a.test/", "https://a.test"))
        XCTAssertFalse(CatalogPages.sameAddress("https://a.test/x", "https://a.test/y"))
        XCTAssertFalse(CatalogPages.sameAddress("https://a.test/x#a", "https://a.test/x#b"))
    }

    func testTitlesLoseTheSiteName() {
        let page = url("https://global.\(npia)/novel/1")
        XCTAssertEqual(CatalogPages.cleanTitle("Solo Leveling - \(siteName)", for: page), "Solo Leveling")
        XCTAssertEqual(CatalogPages.cleanTitle("  Solo   Leveling | \(siteName) Global ", for: page), "Solo Leveling")
        XCTAssertEqual(CatalogPages.cleanTitle("Home - Hello World", for: url("https://www.example.com/a")), "Hello World")
        XCTAssertEqual(CatalogPages.cleanTitle(siteName, for: url("https://global.\(npia)/")), siteName)
        XCTAssertEqual(CatalogPages.cleanTitle("A - B", for: url("https://www.example.com/a")), "A - B")
        XCTAssertEqual(CatalogPages.cleanTitle("  ", for: url("https://example.com")), "")
        XCTAssertEqual(CatalogPages.cleanTitle(String(repeating: "x", count: 500), for: url("https://example.com")).count, 200)
        XCTAssertEqual(CatalogPages.siteLabel(url("https://www.shop.example.co.uk/")), "example")
        XCTAssertEqual(CatalogPages.siteLabel(url("https://example.com")), "example")
    }

    // MARK: - The page's mode as a script

    func testTheModeIsSentToThePageAsOneCall() throws {
        let supported = CatalogPageMode(kind: .supported, cartLinks: false, download: true)
        let script = CatalogPages.setModeScript(supported, download: CatalogDownloadPage(url: "https://a.test/x?y=1", title: "Quote \" here"))
        let prefix = "window.NovelDBApp && window.NovelDBApp.setMode({cartLinks: false, download: "
        XCTAssertTrue(script.hasPrefix(prefix), script)
        XCTAssertTrue(script.hasSuffix("})"), script)
        // The page gets the address and title as JSON, whatever characters they hold.
        let json = String(script.dropFirst(prefix.count).dropLast(2))
        let object = try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: String]
        XCTAssertEqual(object, ["url": "https://a.test/x?y=1", "title": "Quote \" here"])
        // No page, or a mode that offers no download: null.
        XCTAssertEqual(
            CatalogPages.setModeScript(supported, download: nil),
            "window.NovelDBApp && window.NovelDBApp.setMode({cartLinks: false, download: null})"
        )
        let other = CatalogPageMode(kind: .other, cartLinks: true, download: false)
        XCTAssertEqual(
            CatalogPages.setModeScript(other, download: CatalogDownloadPage(url: "https://a.test/", title: "t")),
            "window.NovelDBApp && window.NovelDBApp.setMode({cartLinks: true, download: null})"
        )
    }

    // MARK: - Following the page on screen

    func testALocationReportMovesThePageAndOffersADownload() {
        let book = "https://global.\(npia)/novel/55"
        var tracker = CatalogPageTracker(pageURL: url("https://global.\(npia)/"))
        XCTAssertNil(tracker.download(for: mode(book)))
        XCTAssertTrue(tracker.located(url: book, title: "Solo Leveling - \(siteName)", shown: url(book)))
        XCTAssertEqual(tracker.pageURL, url(book))
        let download = tracker.download(for: mode(book))
        XCTAssertEqual(download?.url, book)
        XCTAssertEqual(download?.title, "Solo Leveling")
        // A mode that offers no download (the home page of the same site) offers none.
        XCTAssertNil(tracker.download(for: mode("https://global.\(npia)/")))
    }

    func testOnlyTheSiteTheViewShowsCanReportAPage() {
        var tracker = CatalogPageTracker(pageURL: url("https://a.test/"))
        // Another site's report (a frame, a late message from the page the view left) is ignored.
        XCTAssertFalse(tracker.located(url: "https://b.test/x", title: "B", shown: url("https://a.test/x")))
        XCTAssertNil(tracker.reported)
        XCTAssertEqual(tracker.pageURL, url("https://a.test/"))
        // www. does not make another site.
        XCTAssertTrue(tracker.located(url: "https://www.a.test/x", title: "A", shown: url("https://a.test/x")))
        // Only http and https addresses count.
        XCTAssertFalse(tracker.located(url: "javascript:alert(1)", title: "", shown: nil))
        XCTAssertFalse(tracker.located(url: "data:text/html,hi", title: "", shown: nil))
        // With nothing shown yet, a report is taken.
        var fresh = CatalogPageTracker(pageURL: url("https://a.test/"))
        XCTAssertTrue(fresh.located(url: "https://c.test/", title: "", shown: nil))
        XCTAssertTrue(fresh.located(url: "https://c.test/x", title: "", shown: url("about:blank")))
    }

    func testMovingOnDropsTheOldReport() {
        let first = "https://global.\(npia)/novel/1"
        let second = "https://global.\(npia)/novel/2"
        var tracker = CatalogPageTracker(pageURL: url(first))
        tracker.located(url: first, title: "One", shown: url(first))
        XCTAssertNotNil(tracker.download(for: mode(first)))
        // The same address (a trailing slash, an empty fragment) keeps the report.
        tracker.navigated(to: url(first + "/"))
        XCTAssertNotNil(tracker.reported)
        // Another address: the report was made for the page the view left.
        tracker.navigated(to: url(second))
        XCTAssertNil(tracker.reported)
        XCTAssertNil(tracker.download(for: mode(second)))
        tracker.located(url: second, title: "Two", shown: url(second))
        XCTAssertEqual(tracker.download(for: mode(second))?.title, "Two")
    }

    func testAReportIsOnlyOfferedForThePageOnScreen() {
        let first = "https://global.\(npia)/novel/1"
        let second = "https://global.\(npia)/novel/2"
        var tracker = CatalogPageTracker(pageURL: url(first))
        tracker.located(url: first, title: "One", shown: url(first))
        // The view moves on before the old report is replaced: no button for the page that is gone.
        tracker.navigated(to: url(second))
        XCTAssertNil(tracker.download(for: CatalogPageMode(kind: .supported, cartLinks: false, download: true)))
    }

    func testChangingTheSiteStartsOver() {
        var tracker = CatalogPageTracker(pageURL: url("https://a.test/"))
        tracker.located(url: "https://a.test/x", title: "x", shown: nil)
        tracker.reset(to: url("https://b.test/"))
        XCTAssertNil(tracker.reported)
        XCTAssertEqual(tracker.pageURL, url("https://b.test/"))
    }
}
