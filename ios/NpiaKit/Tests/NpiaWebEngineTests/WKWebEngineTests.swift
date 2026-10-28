import XCTest
import NpiaCore
@testable import NpiaWebEngine

@MainActor
final class WKWebEngineTests: XCTestCase {
    func testLoadDataURLAndEvaluateJSON() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-eval")
        try await page.load(dataURL("<html><head><title>Hello</title></head><body><p id='x'>42</p></body></html>"),
                            until: .load, timeout: 30)
        let title = try await page.evaluate(String.self, "return document.title;")
        XCTAssertEqual(title, "Hello")
        struct Payload: Decodable, Equatable { let value: Int; let list: [String] }
        let payload = try await page.evaluate(Payload.self, """
            await new Promise((resolve) => setTimeout(resolve, 10));
            return { value: Number(document.getElementById('x').textContent), list: ['a', 'b'] };
            """)
        XCTAssertEqual(payload, Payload(value: 42, list: ["a", "b"]))
        let html = try await page.html()
        XCTAssertTrue(html.contains("<p id=\"x\">42</p>"))
        await page.close()
    }

    func testDOMContentLoadedWait() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-dcl")
        try await page.load(dataURL("<p>ready</p>"), until: .domContentLoaded, timeout: 30)
        let state = try await page.evaluate(String.self, "return document.readyState;")
        XCTAssertNotEqual(state, "loading")
        await page.close()
    }

    func testScriptErrorsThrow() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-error")
        try await page.load(dataURL("<p>x</p>"), until: .load, timeout: 30)
        do {
            _ = try await page.evaluateJSON("throw new Error('boom');")
            XCTFail("expected a script error")
        } catch let error as WebEngineError {
            guard case .scriptFailed = error else { return XCTFail("unexpected \(error)") }
        }
        await page.close()
    }

    func testInitScriptRunsBeforePageScripts() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-init")
        await page.addInitScript("window.__injected = 'yes';")
        try await page.load(dataURL("<script>window.__seen = window.__injected || 'no';</script>"), until: .load, timeout: 30)
        let seen = try await page.evaluate(String.self, "return window.__seen;")
        XCTAssertEqual(seen, "yes")
        await page.close()
    }

    func testMessageHandlerRepliesToThePage() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-message")
        await page.addMessageHandler("echo") { text in "echo:" + text }
        try await page.load(dataURL("<p>message</p>"), until: .load, timeout: 30)
        let reply = try await page.evaluate(String.self, "return await window.webkit.messageHandlers.echo.postMessage('hi');")
        XCTAssertEqual(reply, "echo:hi")
        // Handlers can wait: the page's promise stays pending until the reply arrives.
        await page.addMessageHandler("slow") { text in
            try? await Task.sleep(nanoseconds: 100_000_000)
            return text.uppercased()
        }
        try await page.load(dataURL("<p>again</p>"), until: .load, timeout: 30)
        let slow = try await page.evaluate(String.self, "return await window.webkit.messageHandlers.slow.postMessage('wait');")
        XCTAssertEqual(slow, "WAIT")
        await page.close()
    }

    func testFileURLLoads() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("page.html")
        try "<html><body><h1>File page</h1></body></html>".write(to: file, atomically: true, encoding: .utf8)

        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-file")
        try await page.load(file, until: .load, timeout: 30)
        let heading = try await page.evaluate(String.self, "return document.querySelector('h1').textContent;")
        XCTAssertEqual(heading, "File page")
        let current = await page.currentURL()
        XCTAssertEqual(current?.lastPathComponent, "page.html")
        await page.close()
    }

    func testNetworkCaptureRecordsFetchAndXHR() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-capture")
        await page.startCapture(patterns: ["application/json"])
        try await page.load(dataURL("<p>capture</p>"), until: .load, timeout: 30)
        _ = try await page.evaluateJSON("""
            const json = 'data:application/json,' + encodeURIComponent(JSON.stringify({ chapter: 7 }));
            await (await fetch(json)).text();
            await new Promise((resolve) => {
              const xhr = new XMLHttpRequest();
              xhr.onloadend = () => resolve();
              xhr.open('GET', json + '#xhr');
              xhr.send();
            });
            await (await fetch('data:text/plain,ignored')).text();
            return true;
            """)
        let found = try await page.waitForResponse(containing: "application/json", timeout: 10)
        XCTAssertNotNil(found)
        let responses = try await page.capturedResponses()
        XCTAssertFalse(responses.contains { $0.url.hasPrefix("data:text/plain") })
        XCTAssertGreaterThanOrEqual(responses.count, 1)
        struct Body: Decodable { let chapter: Int }
        XCTAssertEqual(try responses[0].json(Body.self).chapter, 7)
        await page.close()
    }

    func testWaitForPollsUntilTrue() async throws {
        let engine = WKWebEngine(persistent: false)
        let page = try await engine.newPage(profile: "test-wait")
        try await page.load(dataURL("<script>setTimeout(() => { window.done = true; }, 200);</script>"), until: .load, timeout: 30)
        let done = try await page.waitFor("window.done === true", timeout: 10)
        XCTAssertTrue(done)
        await page.close()
    }

    func testCookiesFlowFromWebViewToSharedStorage() async throws {
        let engine = WKWebEngine(persistent: false)
        let store = WKWebEngine.dataStore(for: "test-cookies", persistent: false)
        let name = "npia_test_\(UUID().uuidString.prefix(8))"
        let cookie = try XCTUnwrap(HTTPCookie(properties: [
            .domain: "cookie-test.example", .path: "/", .name: name, .value: "web",
        ]))
        await store.httpCookieStore.setCookie(cookie)
        await engine.syncCookies(profile: "test-cookies")
        let jar = HTTPCookieStorage.shared.cookies(for: URL(string: "https://cookie-test.example/")!) ?? []
        XCTAssertTrue(jar.contains { $0.name == name && $0.value == "web" })

        let shared = try XCTUnwrap(HTTPCookie(properties: [
            .domain: "cookie-test.example", .path: "/", .name: name + "_net", .value: "net",
        ]))
        HTTPCookieStorage.shared.setCookie(shared)
        _ = try await engine.newPage(profile: "test-cookies")
        let webCookies = await store.httpCookieStore.allCookies()
        XCTAssertTrue(webCookies.contains { $0.name == name + "_net" && $0.value == "net" })
        HTTPCookieStorage.shared.deleteCookie(cookie)
        HTTPCookieStorage.shared.deleteCookie(shared)
    }

    func testLoginWithoutPresenterIsUnavailable() async throws {
        let engine = WKWebEngine(persistent: false, loginPresenter: WebLoginCoordinator())
        do {
            try await engine.presentLogin(url: URL(string: "https://example.com/login")!, profile: "test-login")
            XCTFail("expected unavailable")
        } catch let error as WebEngineError {
            XCTAssertEqual(error, .unavailable)
        }
    }

    func testLoginReturnsWhenUserFinishes() async throws {
        let presenter = RecordingPresenter()
        let engine = WKWebEngine(persistent: false, loginPresenter: presenter)
        presenter.onPresent = { session in
            XCTAssertEqual(session.profile, "test-login-done")
            session.finish()
        }
        try await engine.presentLogin(url: dataURL("<p>login</p>"), profile: "test-login-done")
        XCTAssertEqual(presenter.presented, 1)
        XCTAssertEqual(presenter.dismissed, 1)
    }

    func testLoginCancelThrows() async throws {
        let presenter = RecordingPresenter()
        let engine = WKWebEngine(persistent: false, loginPresenter: presenter)
        presenter.onPresent = { $0.cancel() }
        do {
            try await engine.presentLogin(url: dataURL("<p>login</p>"), profile: "test-login-cancel")
            XCTFail("expected cancellation")
        } catch is CancellationError {}
        XCTAssertEqual(presenter.dismissed, 1)
    }

    func testPagePoolHonoursSize() async throws {
        let engine = WKWebEngine(persistent: false)
        let pool = WebPagePool(engine: engine, profile: "test-pool", size: 2)
        let active = ActiveCounter()
        try await withThrowingTaskGroup(of: Void.self) { group in
            for index in 0..<6 {
                group.addTask {
                    try await pool.withPage { page in
                        await active.enter()
                        try await page.load(dataURL("<p>\(index)</p>"), until: .load, timeout: 30)
                        let text = try await page.evaluate(String.self, "return document.body.textContent;")
                        XCTAssertEqual(text, "\(index)")
                        await active.leave()
                    }
                }
            }
            try await group.waitForAll()
        }
        let created = await pool.pageCount
        let peak = await active.peak
        XCTAssertEqual(created, 2)
        XCTAssertLessThanOrEqual(peak, 2)
        await pool.closeAll()
    }

    func testProfileUUIDIsStable() {
        XCTAssertEqual(WKWebEngine.profileUUID("external"), WKWebEngine.profileUUID("external"))
        XCTAssertNotEqual(WKWebEngine.profileUUID("external"), WKWebEngine.profileUUID("npia"))
    }
}

private func dataURL(_ html: String) -> URL {
    let encoded = Data(html.utf8).base64EncodedString()
    return URL(string: "data:text/html;charset=utf-8;base64,\(encoded)")!
}

@MainActor
private final class RecordingPresenter: WebLoginPresenting {
    var presented = 0
    var dismissed = 0
    var onPresent: (@MainActor (WebLoginSession) -> Void)?
    var canPresent: Bool { true }

    func present(_ session: WebLoginSession) {
        presented += 1
        let handler = onPresent
        Task { @MainActor in handler?(session) }
    }

    func dismiss(_ session: WebLoginSession) {
        dismissed += 1
    }
}

private actor ActiveCounter {
    private(set) var peak = 0
    private var current = 0

    func enter() {
        current += 1
        peak = max(peak, current)
    }

    func leave() {
        current -= 1
    }
}
