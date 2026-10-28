import CryptoKit
import XCTest
import NpiaCore
import NpiaWebEngine
import WebKit
@testable import NpiaSources

/// The catalog bridge in a real web view (WebKit), wired like the Catalog tab wires it: the script
/// at document start, a message handler named `noveldb`, and the app's answers sent as script calls.
@MainActor
final class CatalogBridgeTests: XCTestCase {
    /// The bridge's messages, as the app's message handler receives them.
    @MainActor
    private final class Inbox: NSObject, WKScriptMessageHandler {
        private(set) var messages: [[String: Any]] = []

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let text = message.body as? String, let data = text.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            messages.append(object)
        }

        func of(_ type: String) -> [[String: Any]] {
            messages.filter { $0["type"] as? String == type }
        }

        func locations() -> [String] {
            of("location").compactMap { $0["url"] as? String }
        }
    }

    private let engines = EngineSettings()
    private let book = "https://catalog.test/book/1"

    private func makeView(_ inbox: Inbox) -> WKWebView {
        let controller = WKUserContentController()
        controller.addUserScript(WKUserScript(source: CatalogBridge.source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        controller.add(inbox, name: CatalogBridge.handlerName)
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        configuration.websiteDataStore = .nonPersistent()
        return WKWebView(frame: CGRect(x: 0, y: 0, width: 600, height: 800), configuration: configuration)
    }

    private func html(title: String, body: String = "") -> String {
        "<!doctype html><html><head><title>" + title + "</title></head><body>" + body + "<p>filler</p></body></html>"
    }

    /// A page that believes it lives at `address` (the web view has no network in these tests).
    private func openPage(_ address: String, title: String = "Book One - Catalog Test", body: String = "") async -> (WKWebView, Inbox) {
        let inbox = Inbox()
        let view = makeView(inbox)
        view.loadHTMLString(html(title: title, body: body), baseURL: URL(string: address))
        await waitUntil("the bridge to start") { !inbox.of("ready").isEmpty }
        return (view, inbox)
    }

    @discardableResult
    private func run(_ view: WKWebView, _ body: String) async -> String {
        do {
            let value = try await view.callAsyncJavaScript(body, arguments: [:], in: nil, contentWorld: .page)
            return (value as? String) ?? ""
        } catch {
            return "error: " + error.localizedDescription
        }
    }

    private func waitUntil(_ what: String, timeout: TimeInterval = 25, _ condition: () async -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() { return }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTFail("Timed out waiting for \(what)")
    }

    /// What the app does when the page reports itself: answer with the page's mode.
    private func send(_ view: WKWebView, _ mode: CatalogPageMode, download: CatalogDownloadPage? = nil) async {
        await run(view, "return String(" + CatalogPages.setModeScript(mode, download: download) + ");")
    }

    private func buttonText(_ view: WKWebView) async -> String {
        await run(view, "const b = document.querySelector('.noveldb-download'); return b ? b.textContent : '';")
    }

    private func linkText(_ view: WKWebView) async -> String {
        await run(view, "return document.getElementById('out').textContent;")
    }

    private func mode(_ address: String) -> CatalogPageMode {
        CatalogPages.mode(for: URL(string: address)!, engines: engines, cartLinksSetting: true)
    }

    // MARK: - The script file

    func testTheBridgeIsByteIdenticalToTheAndroidAndDesktopCopies() throws {
        let url = try XCTUnwrap(CatalogBridge.resourceURL, "catalog-bridge.js is missing from the bundle")
        let data = try Data(contentsOf: url)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        XCTAssertEqual(digest, "f58147eaf2f3123dbc9b659cb5168fd242974cdf5d87c7d9e3e00394dafca0d1")
        XCTAssertFalse(data.contains(13), "the bridge keeps LF line endings")
        XCTAssertEqual(CatalogBridge.source, String(decoding: data, as: UTF8.self))
    }

    // MARK: - What the bridge reports

    func testTheAddressIsReportedAtStartAndAfterEveryHistoryChange() async {
        let (view, inbox) = await openPage(book)
        await waitUntil("the first location") { inbox.locations().contains(book) }
        // The title settles after the address and is reported with it.
        await waitUntil("the page title") {
            inbox.of("location").contains { $0["title"] as? String == "Book One - Catalog Test" }
        }

        await run(view, "history.pushState({}, '', '/book/2'); return 'ok';")
        await waitUntil("the address after pushState") { inbox.locations().contains("https://catalog.test/book/2") }
        await run(view, "history.replaceState({}, '', '/book/3'); return 'ok';")
        await waitUntil("the address after replaceState") { inbox.locations().contains("https://catalog.test/book/3") }
        await run(view, "location.hash = 'part'; return 'ok';")
        await waitUntil("the address after the hash changed") { inbox.locations().contains("https://catalog.test/book/3#part") }
    }

    func testTheBridgeAlsoRunsOnADataURLPage() async {
        let inbox = Inbox()
        let view = makeView(inbox)
        let encoded = Data(html(title: "Data page").utf8).base64EncodedString()
        view.load(URLRequest(url: URL(string: "data:text/html;charset=utf-8;base64," + encoded)!))
        await waitUntil("the bridge to start") { !inbox.of("ready").isEmpty }
        await waitUntil("the first location") { inbox.locations().contains { $0.hasPrefix("data:text/html") } }
        // A data page may only change the fragment of its address; either way the page moved.
        await run(view, "try { history.pushState({}, '', '#two'); } catch (e) { location.hash = 'two'; } return 'ok';")
        await waitUntil("the address after the history change") { inbox.locations().contains { $0.hasSuffix("#two") } }
    }

    // MARK: - What the app answers

    func testADownloadModeShowsTheButtonAndAClickPostsCartAdd() async {
        let (view, inbox) = await openPage(book)
        let before = await buttonText(view)
        XCTAssertEqual(before, "", "nothing is offered before the app answers")

        let supported = CatalogPageMode(kind: .supported, cartLinks: false, download: true)
        await send(view, supported, download: CatalogDownloadPage(url: book, title: "Book One"))
        await waitUntil("the Download button") { await buttonText(view) == "🔗 Download" }

        // A click adds the page to the cart, and the button says so...
        await run(view, "document.querySelector('.noveldb-download').click(); return 'clicked';")
        await waitUntil("cartAdd") {
            inbox.of("cartAdd").contains { $0["url"] as? String == book && $0["title"] as? String == "Book One" }
        }
        await waitUntil("the button to read In cart") { await buttonText(view) == "✓ In cart" }

        // ...a second click takes it out again.
        await run(view, "document.querySelector('.noveldb-download').click(); return 'clicked';")
        await waitUntil("cartRemove") { inbox.of("cartRemove").contains { $0["url"] as? String == book } }
        await waitUntil("the button to read Download again") { await buttonText(view) == "🔗 Download" }
    }

    func testTheButtonLeavesWithTheAddressItWasMadeFor() async {
        let (view, inbox) = await openPage(book)
        let supported = CatalogPageMode(kind: .supported, cartLinks: false, download: true)
        await send(view, supported, download: CatalogDownloadPage(url: book, title: "Book One"))
        await waitUntil("the Download button") { await buttonText(view) == "🔗 Download" }

        // The page moves on: the button made for the old address goes away at once.
        await run(view, "history.pushState({}, '', '/book/2'); return 'ok';")
        await waitUntil("the button to go") { await buttonText(view) == "" }
        await waitUntil("the new address to be reported") { inbox.locations().contains("https://catalog.test/book/2") }

        // An answer that still names the old address does not bring it back.
        await send(view, supported, download: CatalogDownloadPage(url: book, title: "Book One"))
        try? await Task.sleep(nanoseconds: 1_500_000_000)
        let stale = await buttonText(view)
        XCTAssertEqual(stale, "")

        // One for the reported address does.
        await send(view, supported, download: CatalogDownloadPage(url: "https://catalog.test/book/2", title: "Book Two"))
        await waitUntil("the button for the new address") { await buttonText(view) == "🔗 Download" }
    }

    func testOutsideLinksAreOnlyRenamedOnOtherSites() async {
        let link = "<a id=\"out\" href=\"https://other.test/page\">Read more</a>"
        let (view, _) = await openPage(book, body: link)

        let noveldb = mode(CatalogPages.defaultAddress)
        let next = mode(CatalogPages.nextAddress)
        let supported = mode("https://global.\(NpiaParser.host)/novel/1")
        let other = mode("https://example.com/books/1")
        XCTAssertEqual([noveldb.kind, next.kind, supported.kind, other.kind], [.novelDB, .novelDB, .supported, .other])
        XCTAssertEqual([noveldb.cartLinks, next.cartLinks, supported.cartLinks, other.cartLinks], [false, false, false, true])

        // Another site: the link reads "Download" (and goes to the cart when tapped).
        await send(view, other)
        await waitUntil("the link to be renamed") { await linkText(view) == "🔗 Download" }

        // NovelDB and NovelDB Next: the link is given back.
        await send(view, noveldb)
        await waitUntil("the link to be restored") { await linkText(view) == "Read more" }
        await send(view, other)
        await waitUntil("the link to be renamed again") { await linkText(view) == "🔗 Download" }
        await send(view, next)
        await waitUntil("the link to be restored for NovelDB Next") { await linkText(view) == "Read more" }

        // A supported site: the link stays as written, and the page gets its button.
        await send(view, other)
        await waitUntil("the link to be renamed a third time") { await linkText(view) == "🔗 Download" }
        await send(view, supported, download: CatalogDownloadPage(url: book, title: "Book One"))
        await waitUntil("the link to be restored for a supported site") { await linkText(view) == "Read more" }
        await waitUntil("the Download button") { await buttonText(view) == "🔗 Download" }
        // Stays so a moment later (nothing renames it again).
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        let kept = await linkText(view)
        XCTAssertEqual(kept, "Read more")
    }

    func testTheOldModeMessageStillWorks() async {
        // The first version of the app sent only the cart setting.
        let (view, _) = await openPage(book, body: "<a id=\"out\" href=\"https://other.test/page\">Read more</a>")
        await run(view, "window.NovelDBApp.setMode({cartLinks: true}); return 'ok';")
        await waitUntil("the link to be renamed") { await linkText(view) == "🔗 Download" }
        await run(view, "window.NovelDBApp.setMode({cartLinks: false}); return 'ok';")
        await waitUntil("the link to be restored") { await linkText(view) == "Read more" }
        let button = await buttonText(view)
        XCTAssertEqual(button, "")
    }
}
