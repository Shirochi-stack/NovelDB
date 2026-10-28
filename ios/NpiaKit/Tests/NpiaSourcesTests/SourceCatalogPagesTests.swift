import XCTest
import NpiaCore
@testable import NpiaSources

/// Which sites and pages the Catalog and Browser tabs treat as downloadable. Source domains are
/// kept as escapes so the names stay out of the repository text.
final class SourceCatalogPagesTests: XCTestCase {
    private let defaults = EngineSettings()
    private let npia = NpiaParser.host
    private let qdn = "\u{71}\u{69}\u{64}\u{69}\u{61}\u{6e}.com"
    /// Listed by the novel-downloader rules only: no built-in scraper, not a WebToEpub site.
    private let rulesOnly = "www.sudugu.com"
    /// Listed by WebToEpub only.
    private let parsersOnly = "moonbunnycafe.com"

    private func off(_ engines: SiteEngine...) -> EngineSettings {
        EngineSettings(disabled: engines)
    }

    private func page(_ address: String, _ engines: EngineSettings) -> Bool {
        SourceCatalog.downloadablePage(URL(string: address)!, engines: engines)
    }

    private func site(_ address: String, _ engines: EngineSettings) -> Bool {
        SourceCatalog.supportedSite(URL(string: address)!, engines: engines)
    }

    func testBuiltInScrapersListTheirSites() {
        // The adapters ported so far. Others (the hard sources) may list their sites as they arrive;
        // `reader` takes any page the script engines list, so it lists none; maru is recognised by a name pattern.
        let ported: Set<SourceID> = [.npia, .globalNpia, .kpage, .sfc, .nweb, .jara, .floo, .fenrir, .xiyuwx, .yeduji, .kobo]
        let listed = Set(SourceCatalog.adapters.filter { !$0.sites.isEmpty }.map { $0.id })
        XCTAssertTrue(ported.isSubset(of: listed), "missing: \(ported.subtracting(listed))")
        XCTAssertFalse(listed.contains(.reader))
        for adapter in SourceCatalog.adapters {
            for host in adapter.sites {
                XCTAssertEqual(host, host.lowercased())
                XCTAssertFalse(host.contains("/"), "\(host) has a scheme or path")
            }
        }
        XCTAssertTrue(MaruAdapter.ownsSite("marumaru123.com"))
        XCTAssertTrue(MaruAdapter.ownsSite("www.marumaru.me"))
        XCTAssertFalse(MaruAdapter.ownsSite("example.com"))
    }

    func testASiteCoversItsSubdomains() {
        XCTAssertTrue(NpiaAdapter.ownsSite(npia))
        XCTAssertTrue(NpiaAdapter.ownsSite("images." + npia))
        XCTAssertFalse(NpiaAdapter.ownsSite("not" + npia))
        XCTAssertFalse(NpiaAdapter.ownsSite("example.com"))
        XCTAssertTrue(FlooAdapter.ownsSite("b." + FlooParser.domain))
        XCTAssertTrue(KoboAdapter.ownsSite("www.kobo.com"))
        XCTAssertTrue(KoboAdapter.ownsSite("books.rakuten.co.jp"))
    }

    func testBuiltInBookPagesAreDownloadable() {
        XCTAssertTrue(page("https://global.\(npia)/novel/123", defaults))
        XCTAssertTrue(page("https://\(npia)/novel/4567", defaults))
        XCTAssertTrue(page("https://fenrirealm.com/series/some-title", defaults))
        XCTAssertTrue(page("https://yeduji.com/book/12", defaults))
    }

    func testGlobalNpiaNovelPageCountsButItsHomePageDoesNot() {
        let host = "global." + npia
        XCTAssertTrue(site("https://\(host)/", defaults))
        XCTAssertTrue(page("https://\(host)/novel/987", defaults))
        XCTAssertFalse(page("https://\(host)/", defaults))
        XCTAssertFalse(page("https://\(host)", defaults))
        XCTAssertFalse(page("https://\(host)/?lang=en", defaults))
    }

    func testRulesBookPagesCountThroughTheHeader() {
        XCTAssertTrue(site("https://www.\(qdn)/", defaults))
        XCTAssertTrue(page("https://book.\(qdn)/info/1035420986/", defaults))
        XCTAssertTrue(page("https://www.\(qdn)/book/1035420986/", defaults))
        XCTAssertFalse(page("https://www.\(qdn)/", defaults))
        XCTAssertFalse(page("https://www.\(qdn)/rank/", defaults))
        XCTAssertFalse(page("https://www.\(qdn)/all/", defaults))
    }

    func testAWebToEpubOnlySiteCountsOnEveryPageButTheHomePage() {
        XCTAssertTrue(site("https://\(parsersOnly)/", defaults))
        XCTAssertFalse(page("https://\(parsersOnly)/", defaults))
        XCTAssertFalse(page("https://\(parsersOnly)", defaults))
        XCTAssertFalse(page("https://\(parsersOnly)/?s=query", defaults))
        XCTAssertTrue(page("https://\(parsersOnly)/some-novel/", defaults))
        XCTAssertTrue(page("https://www.\(parsersOnly)/some-novel/chapter-1", defaults))
    }

    func testUnknownSitesAreNeitherSupportedNorDownloadable() {
        XCTAssertFalse(site("https://example.com/", defaults))
        XCTAssertFalse(page("https://example.com/book/1", defaults))
        XCTAssertFalse(page("not a url", defaults))
        XCTAssertFalse(page("ftp://\(parsersOnly)/a/b", defaults))
        XCTAssertFalse(site("ftp://\(parsersOnly)/a/b", defaults))
    }

    func testSwitchedOffEnginesDoNotCount() {
        let book = "https://\(rulesOnly)/1001/"
        XCTAssertTrue(page(book, defaults))
        XCTAssertFalse(page(book, off(.novelDownloader)))
        XCTAssertTrue(page(book, off(.native, .webtoepub)))
        XCTAssertTrue(site("https://\(rulesOnly)/", defaults))
        XCTAssertFalse(site("https://\(rulesOnly)/", off(.novelDownloader, .webtoepub)))

        let toon = "https://\(parsersOnly)/some-novel/"
        XCTAssertFalse(page(toon, off(.webtoepub)))
        XCTAssertTrue(page(toon, off(.native, .novelDownloader)))
    }

    func testWithTheBuiltInScrapersOffABuiltInPageNeedsAnEngineToListIt() {
        let novel = "https://global.\(npia)/novel/987"
        // WebToEpub lists this host (any page but the home page); the rules do not.
        XCTAssertTrue(page(novel, off(.native)))
        XCTAssertFalse(page(novel, off(.native, .webtoepub)))
        XCTAssertTrue(page(novel, off(.novelDownloader, .webtoepub)))
        // The site stays a supported site while the scrapers are off, whatever the engines say.
        XCTAssertTrue(site("https://global.\(npia)/", off(.native, .webtoepub)))
    }

    func testTheAddScreenStillTakesAnyWebLink() {
        XCTAssertTrue(SiteEngineRouter.canTry(URL(string: "https://example.com/anything")!, engines: defaults))
    }

    func testAnUpdatedBundleIsTheOneThatDecides() throws {
        // A downloaded rules bundle with its own header replaces the shipped one.
        let fixture = try EngineFixture()
        let real = EngineStore.shared
        let store = EngineStore(shipped: { engine in real.shipped(engine)?.folder }, updatesRoot: fixture.root.appendingPathComponent("home/engines"))
        let header = "// ==UserScript==\n// @version 99.0.0\n// @match *://only.test/book/*\n// ==/UserScript==\nswitch(e){case\"only.test\":{break}throw new Error(\"Not Found Rule!\")}"
        let data = Data(header.utf8)
        let manifest = EngineManifest(raw: [
            "engine": "novel_downloader", "api": 1, "commit": "page1", "shim": "s", "version": "99.0.0",
            "date": "2030-01-01T00:00:00Z", "built": "2030-01-01T00:00:00Z", "file": "rules-lib.js",
            "size": data.count, "sha256": EngineStore.sha256Hex(data),
        ])
        _ = try store.install(.novelDownloader, data: data, manifest: manifest)
        let book = URL(string: "https://only.test/book/1")!
        let home = URL(string: "https://only.test/")!
        XCTAssertTrue(SourceCatalog.downloadablePage(book, engines: off(.native, .webtoepub), store: store))
        XCTAssertFalse(SourceCatalog.downloadablePage(home, engines: off(.native, .webtoepub), store: store))
        XCTAssertTrue(SourceCatalog.supportedSite(home, engines: off(.native, .webtoepub), store: store))
        // The shipped rules know nothing of that site.
        XCTAssertFalse(SourceCatalog.downloadablePage(book, engines: off(.native, .webtoepub), store: real))
    }
}
