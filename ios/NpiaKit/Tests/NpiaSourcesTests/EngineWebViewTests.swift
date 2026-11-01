import XCTest
import NpiaCore
import NpiaWebEngine
@testable import NpiaSources

/// The engine scripts in a real web view (WebKit), with the real message handler behind
/// `window.__W2E_transport`.
@MainActor
final class EngineWebViewTests: XCTestCase {
    private func openPage(_ profile: String, http: RoutedHTTP = RoutedHTTP()) async throws -> any WebPageDriving {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: profile)
        await page.addMessageHandler(EnginePage.transportName) { json in
            await EngineTransport.handle(json, http: http)
        }
        try await page.load(dataURL("<html><body><p>book page</p></body></html>"), until: .domContentLoaded, timeout: 30)
        return page
    }

    func testTheWebToEpubBundleLoadsInAWebView() async throws {
        let page = try await openPage("test-w2e-load")
        let bundle = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: .shared)
        XCTAssertFalse(bundle.updated)

        struct Info: Decodable { let api: Int; let sites: Int }
        let text = try await page.evaluate(String.self, "return window.__W2E_info();")
        let info = try JSONDecoder().decode(Info.self, from: Data(text.utf8))
        XCTAssertEqual(info.api, 1)
        XCTAssertGreaterThan(info.sites, 500)

        // Loading twice is harmless.
        _ = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: .shared)

        // A page no parser is registered for answers with an error, not an exception.
        let book = try await page.evaluate(String.self, "return await window.__W2E_parseBook();")
        XCTAssertTrue(book.contains("\"error\""), book)
        await page.close()
    }

    func testRequestsThePageCannotMakeGoThroughTheAppsHTTPClient() async throws {
        let http = RoutedHTTP()
        http.on("transport.test/hello", "hello world", headers: ["content-type": "text/plain; charset=utf-8"])
        http.on("transport.test/missing", "nothing here", status: 404)
        let page = try await openPage("test-w2e-transport", http: http)
        _ = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: .shared)

        // The page's own fetch is refused (another origin), so the bundle falls back to the transport.
        let text = try await page.evaluate(String.self, """
        const response = await window.__W2E_fetch("https://transport.test/hello");
        return response.status + ":" + response.headers.get("content-type") + ":" + (await response.text());
        """)
        XCTAssertEqual(text, "200:text/plain; charset=utf-8:hello world")

        let missing = try await page.evaluate(String.self, """
        const response = await window.__W2E_fetch("https://transport.test/missing");
        return response.status + ":" + (await response.text());
        """)
        XCTAssertEqual(missing, "404:nothing here")
        XCTAssertEqual(http.requested("transport.test/hello").count, 1)
        await page.close()
    }

    func testTheNovelDownloaderRulesLoadInAWebView() async throws {
        let page = try await openPage("test-nd-load")
        try await ReaderWebFallback.inject(into: page, logger: NullLogger(), store: .shared)
        let ready = try await page.evaluate(Bool.self, "return window.__ND_READY === true && window.__ND_BRIDGE_READY === true;")
        XCTAssertTrue(ready)
        // Injecting again does nothing.
        try await ReaderWebFallback.inject(into: page, logger: NullLogger(), store: .shared)
        await page.close()
    }

    func testAnUpdateThatFailsToLoadInAWebViewIsSetAside() async throws {
        let fixture = try EngineFixture()
        let broken = Data("throw new Error('broken update');".utf8)
        let manifest = EngineManifest(raw: [
            "engine": "webtoepub", "api": 1, "commit": "bad2", "shim": "s", "version": "9.9", "date": "2027-01-01T00:00:00Z",
            "built": "2027-01-01T00:00:00Z", "file": "webtoepub-lib.js", "size": broken.count, "sha256": EngineStore.sha256Hex(broken),
        ])
        _ = try fixture.store.install(.webtoepub, data: broken, manifest: manifest)

        // The shipped copy of this fixture is not a real bundle, so use the app's own as the fallback.
        let real = EngineStore.shared
        let store = EngineStore(shipped: { engine in real.shipped(engine)?.folder }, updatesRoot: fixture.root.appendingPathComponent("home/engines"))
        XCTAssertTrue(try XCTUnwrap(store.current(.webtoepub)).updated)

        let page = try await openPage("test-w2e-reject")
        let bundle = try await WebToEpubSession.inject(into: page, logger: NullLogger(), store: store)
        XCTAssertFalse(bundle.updated, "the shipped copy took over")
        XCTAssertEqual(store.rejectedKeys(.webtoepub), ["bad2-s"])
        XCTAssertFalse(try XCTUnwrap(store.current(.webtoepub)).updated)
        await page.close()
    }
}

private func dataURL(_ html: String) -> URL {
    let encoded = Data(html.utf8).base64EncodedString()
    return URL(string: "data:text/html;charset=utf-8;base64,\(encoded)")!
}
