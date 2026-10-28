import CryptoKit
import XCTest
import NpiaCore
#if os(macOS)
import AppKit
#endif
@testable import NpiaSources
@testable import NpiaWebEngine

/// Where the shared fixtures are: the repository's `tests/fixtures`, which the Android side's Playwright
/// suite reads too.
enum RepoFixtures {
    static let root: URL = {
        var url = URL(fileURLWithPath: #filePath)
        // .../ios/NpiaKit/Tests/NpiaSourcesTests/<file> -> the repository root
        for _ in 0..<5 { url.deleteLastPathComponent() }
        return url
    }()

    static func data(_ path: String) throws -> Data {
        try Data(contentsOf: root.appendingPathComponent("tests/fixtures/" + path))
    }

    static func json(_ path: String) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: data(path)) as? [String: Any] else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return object
    }

    /// A saved page from `tests/fixtures/hard_sources` with the site names filled in.
    static func page(_ name: String, login: Int? = nil) throws -> String {
        var text = String(decoding: try data("hard_sources/" + name), as: UTF8.self)
        let prefix = "\u{72}\u{69}\u{64}\u{69}"
        text = text
            .replacingOccurrences(of: "{QDN}", with: QdnSite.domain)
            .replacingOccurrences(of: "{RBOOKS}", with: RbooksSite.domain)
            .replacingOccurrences(of: "{CDN}", with: prefix + "cdn.net")
            .replacingOccurrences(of: "{BORDER}", with: prefix + "border")
            .replacingOccurrences(of: "{CENTER}", with: prefix + "center")
        if let login { text = text.replacingOccurrences(of: "{LOGIN}", with: String(login)) }
        return text
    }

    /// The extraction payload of the captured chapter: one entry per painted character, in reading order.
    static func capturedPayload(_ name: String = "qdn_chapter76_capture.json") throws -> String {
        var payload = try json(name)
        let paragraphs = (payload["atoms"] as? [[[String: Any]]] ?? []).map { atoms in
            atoms.filter { ($0["src"] as? String) != "attr" }.map { atom -> [String: Any] in
                ["parts": [["text": atom["text"] ?? "", "fonts": atom["fonts"] ?? ""]], "mirrored": atom["mirrored"] ?? false]
            }
        }
        payload["paragraphs"] = paragraphs
        let text = HardScripts.json(payload)
        precondition(text != "null", "the captured payload could not be encoded")
        return text
    }

    static func text(_ path: String) throws -> String {
        String(decoding: try data(path), as: UTF8.self)
    }

    static func expectedLines() throws -> [String] {
        (try json("qdn_chapter76_expected.json")["paragraphs"] as? [String]) ?? []
    }
}

/// `hard-sources.js` in a real WebKit web view (the engine of the iOS app): the page-side logic that
/// Android runs in Chromium, and above all the glyph matching, which draws on a canvas and so depends on
/// the engine.
@MainActor
final class HardWebKitTests: XCTestCase {
    private let engine = WKWebEngine(persistent: false)
    private var opened: [any WebPageDriving] = []
    #if os(macOS)
    private var windows: [NSWindow] = []
    #endif

    /// One decoder page with the reference built (and no verified table, so every glyph is matched by shape),
    /// shared by the tests that need one: building the reference takes a while.
    private static let decoderEngine = WKWebEngine(persistent: false)
    private static var shapeDecoder: (any WebPageDriving)?

    override func tearDown() async throws {
        for page in opened { await page.close() }
        opened = []
        #if os(macOS)
        for window in windows { window.orderOut(nil) }
        windows = []
        #endif
    }

    private func sharedShapeDecoder() async throws -> any WebPageDriving {
        if let page = Self.shapeDecoder { return page }
        let page = try await decoderPage(verified: false, prepare: true, track: false)
        Self.shapeDecoder = page
        return page
    }

    /// A page that shows `html` as if it came from `address`, with the script installed as a
    /// document-start script the way the app does it.
    private func open(_ html: String, at address: String, inWindow: Bool = false) async throws -> any WebPageDriving {
        let page = try await engine.newPage(profile: "hard-web-test")
        opened.append(page)
        await page.emulateDesktop(userAgent: QdnSite.userAgent, width: 1280, height: 800)
        await page.addMainFrameInitScript(HardScripts.source)
        let web = try XCTUnwrap(page as? WKWebPage)
        #if os(macOS)
        // A web view that is not in a window is not visible, so it gets no rendering updates (the reader's
        // intersection observers would never fire). The iOS app keeps its pages in a hidden window.
        if inWindow {
            _ = NSApplication.shared
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 800), styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = web.webView
            window.orderFrontRegardless()
            windows.append(window)
        }
        #endif
        web.webView.loadHTMLString(html, baseURL: URL(string: address))
        let loaded = try await page.waitFor("location.href === \(HardScripts.literal(address)) && document.readyState === 'complete'", timeout: 30)
        XCTAssertTrue(loaded, "the page at \(address) did not load")
        return page
    }

    /// The object a JavaScript expression evaluates to in the page.
    private func object(_ page: any WebPageDriving, _ expression: String) async throws -> HardJSON {
        HardJSON(data: try await page.evaluateJSON("return \(expression);"))
    }

    private func decoderPage(verified: Bool, prepare: Bool, track: Bool = true) async throws -> any WebPageDriving {
        let page = try await (track ? engine : Self.decoderEngine).newPage(profile: "hard-web-decoder")
        if track { opened.append(page) }
        try await page.load(URL(string: "about:blank")!, until: .domContentLoaded, timeout: 20)
        try await page.ensureHard()
        try await QdnDecoder.push(page, name: "font", resource: HardScripts.qdnFont)
        try await QdnDecoder.push(page, name: "frequency", resource: HardScripts.qdnFrequency)
        if verified { try await QdnDecoder.push(page, name: "verified", resource: HardScripts.qdnVerified) }
        if prepare {
            let started = Date()
            while true {
                let state = QdnPrepareState(try await page.hardObject("qdn.decoder.prepare(10000)"))
                if state.done {
                    XCTAssertGreaterThan(state.built, 9000)
                    print("WebKit built the glyph reference (\(state.built) characters) in \(Int(Date().timeIntervalSince(started) * 1000)) ms")
                    break
                }
            }
        }
        return page
    }

    /// Compares decoded paragraphs one by one, so a failure names the paragraphs instead of printing both texts.
    private func assertSameLines(_ actual: [String], _ expected: [String], _ label: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(actual.count, expected.count, "\(label): paragraph count", file: file, line: line)
        var wrong = 0
        for (position, text) in expected.enumerated() where position < actual.count && actual[position] != text {
            wrong += 1
            if wrong <= 5 { XCTFail("\(label): paragraph \(position) is \(actual[position]) but should be \(text)", file: file, line: line) }
        }
        XCTAssertEqual(wrong, 0, "\(label): paragraphs that differ", file: file, line: line)
    }

    private func decode(_ page: any WebPageDriving, _ payload: String) async throws -> QdnDecoded {
        let started = Date()
        let text = try await page.hardTask(
            "qdn.decoder.decodeJson", argsJSON: "[\(HardScripts.literal(payload))]", pollSeconds: 0.25, maxPolls: 2400
        )
        let decoded = QdnDecoded(HardJSON(text: text))
        print("WebKit decoded a payload in \(Int(Date().timeIntervalSince(started) * 1000)) ms: \(decoded.stats)")
        return decoded
    }

    // MARK: The script itself

    func testTheScriptExposesTheDocumentedEntryPoints() async throws {
        let page = try await open("<p>blank</p>", at: QdnSite.origin + "/")
        let info = try await object(page, """
        (() => {
          const h = window.__npiaHard;
          return {version: h.version, qdn: Object.keys(h.qdn).sort(), decoder: Object.keys(h.qdn.decoder).sort(),
                  rbooks: Object.keys(h.rbooks).sort(), tasks: Object.keys(h.tasks).sort()};
        })()
        """)
        XCTAssertEqual(info.int("version"), 1)
        XCTAssertTrue(Set(["pageState", "bookInfo", "chapterState", "encryptionState", "readChapter", "extract", "userInfo",
                           "installFontHook", "decoder", "catalogState", "catalogInfo"]).isSubset(of: info.strings("qdn")))
        XCTAssertTrue(Set(["putResource", "prepare", "decode", "decodeJson", "knownFonts", "status", "resourceStatus"]).isSubset(of: info.strings("decoder")))
        XCTAssertTrue(Set(["productReady", "productInfo", "libraryProbe", "discover", "apiState", "contentState", "accessWall",
                           "extract", "ownedIds"]).isSubset(of: info.strings("rbooks")))
        XCTAssertEqual(info.strings("tasks"), ["poll", "start"])
    }

    func testTheDocumentStartScriptIsInstalledBeforeThePageAndSafeToLoadAgain() async throws {
        let page = try await open("<p>blank</p>", at: QdnSite.origin + "/")
        _ = try await page.evaluateJSON("window.__npiaHard.marker = 7; return true;")
        // The app's fallback evaluate must not replace the copy that is already there.
        try await page.ensureHard()
        _ = try await page.evaluateJSON("(0, eval)(\(HardScripts.literal(HardScripts.source))); return true;")
        let marker = try await page.evaluate(Int.self, "return window.__npiaHard.marker;")
        XCTAssertEqual(marker, 7)
    }

    func testTheFontHookRecordsScriptBuiltFacesAndKeepsFontFaceWorking() async throws {
        let page = try await open("<p>blank</p>", at: QdnSite.origin + "/")
        let result = try await object(page, """
        (() => {
          const bytes = new Uint8Array([0, 1, 0, 0]);
          const face = new FontFace('probe', bytes.buffer, {unicodeRange: 'U+4E00-9FA5'});
          const records = window.__npiaFontFaces;
          return {hooked: window.FontFace.__npiaHooked === true, isInstance: face instanceof FontFace,
                  count: records.length, family: records[0].family, size: records[0].source.byteLength,
                  range: records[0].descriptors.unicodeRange, text: String(window.FontFace)};
        })()
        """)
        XCTAssertTrue(result.bool("hooked"))
        XCTAssertTrue(result.bool("isInstance"))
        XCTAssertEqual(result.int("count"), 1)
        XCTAssertEqual(result.string("family"), "probe")
        XCTAssertEqual(result.int("size"), 4)
        XCTAssertEqual(result.string("range"), "U+4E00-9FA5")
        XCTAssertEqual(result.string("text"), "function FontFace() { [native code] }")
    }

    func testTasksRunLongOperationsToCompletionAndReportErrors() async throws {
        let page = try await open("<p>blank</p>", at: QdnSite.origin + "/")
        _ = try await page.evaluateJSON("""
        window.__npiaHard.test = {
          slow: async (value) => { await window.__npiaHard.util.sleep(120); return {value}; },
          broken: async () => { throw new Error('boom'); },
        };
        return true;
        """)
        let slow = try await page.hardTask("test.slow", argsJSON: "[7]", pollSeconds: 0.05, maxPolls: 100)
        XCTAssertEqual(slow, "{\"value\":7}")
        do {
            _ = try await page.hardTask("test.broken", argsJSON: "[]", pollSeconds: 0.05, maxPolls: 100)
            XCTFail("the task should fail")
        } catch WebEngineError.scriptFailed(let message) {
            XCTAssertEqual(message, "boom")
        }
        do {
            _ = try await page.hardTask("test.nothing", argsJSON: "[]", pollSeconds: 0.05, maxPolls: 2)
            XCTFail("an unknown entry point should fail")
        } catch WebEngineError.scriptFailed(let message) {
            XCTAssertTrue(message.contains("unknown entry point"), message)
        }
    }

    func testSha256AndBase64HelpersAgreeWithCryptoKit() async throws {
        let page = try await open("<p>blank</p>", at: QdnSite.origin + "/")
        for size in [0, 1, 55, 56, 57, 64, 1000, 100_001] {
            let data = Data((0..<size).map { UInt8(truncatingIfNeeded: $0 * 31 + 7) })
            let encoded = data.base64EncodedString()
            let result = try await object(page, """
            (() => {
              const u = window.__npiaHard.util;
              const bytes = u.base64ToBytes(\(HardScripts.literal(encoded)));
              return {plain: u.sha256Plain(bytes), size: bytes.length, again: u.bytesToBase64(bytes)};
            })()
            """)
            let expected = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            XCTAssertEqual(result.int("size"), size)
            XCTAssertEqual(result.string("plain"), expected, "size \(size)")
            XCTAssertEqual(result.string("again"), encoded, "size \(size)")
            let subtle = try await page.evaluate(String.self, "return await window.__npiaHard.util.sha256Hex(window.__npiaHard.util.base64ToBytes(\(HardScripts.literal(encoded))));")
            XCTAssertEqual(subtle, expected, "size \(size)")
        }
    }

    // MARK: The glyph decoder, which draws on a canvas

    func testUnicodeRangesAndTheFontTableAreReadTheSameWay() async throws {
        let page = try await decoderPage(verified: false, prepare: false)
        func ranges(_ value: String) async throws -> [[Int]] {
            let data = try await page.evaluateJSON("return window.__npiaHard.qdn.decoder.parseUnicodeRange(\(HardScripts.literal(value)));")
            return (try JSONSerialization.jsonObject(with: data) as? [[Int]]) ?? []
        }
        let single = try await ranges("U+4E00-9FA5")
        XCTAssertEqual(single, [[0x4E00, 0x9FA5]])
        let two = try await ranges("U+3400-4DB5, U+20")
        XCTAssertEqual(two, [[0x3400, 0x4DB5], [0x20, 0x20]])
        let wild = try await ranges("U+4??")
        XCTAssertEqual(wild, [[0x400, 0x4FF]])
        let empty = try await ranges("")
        XCTAssertEqual(empty, [[0, 0x10FFFF]])

        // The reference font's character table has about 9,850 hanzi.
        let font = try HardScripts.resource(HardScripts.qdnFont).base64EncodedString()
        let count = try await page.evaluate(Int.self, "return window.__npiaHard.qdn.decoder.parseCmap(window.__npiaHard.util.base64ToBytes(\(HardScripts.literal(font)))).length;")
        XCTAssertGreaterThan(count, 9000)
    }

    /// The main open question of the port: does canvas glyph matching give the desktop's answers in WebKit?
    func testShapeMatchingDecodesTheCapturedChapterAndTheScreenshotExcerptInWebKit() async throws {
        let page = try await sharedShapeDecoder()
        let expected = try RepoFixtures.expectedLines()
        XCTAssertFalse(expected.isEmpty)

        // 1,729 glyphs of a real chapter, matched against the reference without the verified table.
        let result = try await decode(page, try RepoFixtures.capturedPayload())
        assertSameLines(result.lines, expected, "shape matching")
        XCTAssertEqual(result.stats.decoded, 1729)
        XCTAssertEqual(result.stats.shapeMatchedGlyphs, 1729)
        XCTAssertEqual(result.stats.verifiedGlyphs, 0)
        XCTAssertEqual(result.stats.missingFonts, 0)
        XCTAssertEqual(result.stats.suspiciousPassthrough, 90)
        print("WebKit shape matching: \(result.stats.lowConfidence) uncertain of \(result.stats.decoded) glyphs")

        // Glyphs checked against a screenshot.
        let sample = try RepoFixtures.json("qdn_screenshot_excerpt.json")
        let excerpt = try await decode(page, HardScripts.json(sample["payload"] ?? [:]))
        assertSameLines(excerpt.lines, sample["expected"] as? [String] ?? [], "screenshot excerpt")
        XCTAssertGreaterThan(excerpt.stats.decoded, 0)
    }

    func testVerifiedGlyphsNeverNeedTheReferenceInWebKit() async throws {
        let page = try await decoderPage(verified: true, prepare: false)
        let result = try await decode(page, try RepoFixtures.capturedPayload())
        assertSameLines(result.lines, try RepoFixtures.expectedLines(), "verified glyphs")
        XCTAssertEqual(result.stats.decoded, 1729)
        XCTAssertEqual(result.stats.verifiedGlyphs, 1729)
        XCTAssertEqual(result.stats.shapeMatchedGlyphs, 0)
        XCTAssertEqual(result.stats.lowConfidence, 0)
        let status = try await page.hardObject("qdn.decoder.status()")
        print("WebKit decoder status after verified glyphs: \(status.raw)")
        XCTAssertFalse(status.bool("prepared"))
    }

    func testTheProductionDecoderBuildsTheReferenceOnceAndDecodes() async throws {
        let decoder = QdnDecoder(web: engine, logger: NullLogger(), timing: QdnTiming())
        try await decoder.prepare()
        try await decoder.prepare() // a second call does nothing
        let result = try await decoder.decode(try RepoFixtures.capturedPayload())
        print("WebKit production decoder stats: \(result.stats)")
        assertSameLines(result.lines, try RepoFixtures.expectedLines(), "production decoder")
        // The cipher fonts it has seen are named by digest, so the next extraction can leave them out.
        let known = try await decoder.knownFonts()
        XCTAssertFalse(known.isEmpty)
        XCTAssertTrue(known.allSatisfy { $0.count == 64 })
        await decoder.close()
        let afterClose = try await decoder.knownFonts()
        XCTAssertTrue(afterClose.isEmpty)
    }

    // MARK: Extraction from a reader with every protection layer

    /// `tests/fixtures/hard_sources/qdn_synthetic_reader.html` is built by `build_reader()` in
    /// `tests/hard_sources_support.py`: cipher fonts (one in a stylesheet, one built in script), mirrored
    /// glyphs, characters in random tags ordered by CSS `order`, `::after{content: attr()}`, hidden decoys, and
    /// poisoned paragraphs that swap to the real text only near the viewport.
    func testASyntheticReaderWithEveryLayerIsExtractedAndDecodedInWebKit() async throws {
        let sample = try RepoFixtures.json("hard_sources/qdn_synthetic_reader_lines.json")
        let lines = sample["lines"] as? [String] ?? []
        XCTAssertEqual(lines.count, 42)
        let page = try await open(try RepoFixtures.text("hard_sources/qdn_synthetic_reader.html"), at: QdnSite.origin + "/chapter/1/2/", inWindow: true)

        let chapter = QdnChapterState(try await page.hardObject("qdn.chapterState()"))
        XCTAssertTrue(chapter.ready && chapter.encrypted)
        var encryption = QdnEncryptionState()
        for _ in 0..<80 {
            encryption = QdnEncryptionState(try await page.hardObject("qdn.encryptionState()"))
            if encryption.rendered { break }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        XCTAssertTrue(encryption.encrypted && encryption.rendered, "the synthetic reader did not render")
        XCTAssertEqual(encryption.words, lines.reduce(0) { $0 + $1.unicodeScalars.count })

        let started = Date()
        let payloadText = try await page.hardTask("qdn.extract", argsJSON: "[{\"known\":[]}]", pollSeconds: 0.25, maxPolls: 480)
        print("WebKit extracted the synthetic reader in \(Int(Date().timeIntervalSince(started) * 1000)) ms")
        let payload = HardJSON(text: payloadText)
        XCTAssertFalse(payload.has("error"), payload.string("error"))
        XCTAssertEqual((payload.any("unstableParagraphs") as? [Any])?.count, 0)
        XCTAssertEqual(payload.int("scriptedCount"), 1) // the blob face, seen through the FontFace hook
        XCTAssertEqual(payload.objects("faces").map { $0.string("family") }, ["cipherA", "cipherB"])
        XCTAssertGreaterThan(payload.int("dropped"), 0)

        let result = try await decode(try await sharedShapeDecoder(), payloadText)
        XCTAssertEqual(result.lines.count, lines.count)
        for (position, line) in lines.enumerated() where position < result.lines.count {
            XCTAssertEqual(result.lines[position], line, "paragraph \(position)")
        }
        XCTAssertEqual(result.stats.missingFonts, 0)
        XCTAssertEqual(result.stats.unstableParagraphs, 0)
    }

    // MARK: qdn pages

    private var bookAddress: String { QdnSite.origin + "/book/1049836819/" }

    func testTheBookPageIsReadyAndDescribesTheBook() async throws {
        let page = try await open(try RepoFixtures.page("qdn_book.html"), at: bookAddress)
        let state = try await page.hardObject("qdn.pageState()")
        XCTAssertEqual(state.string("state"), "ready")
        XCTAssertEqual(state.int("catalogCount"), 7)
        XCTAssertTrue(state.bool("bookName"))
        let viewport = try XCTUnwrap(state.object("viewport"))
        print("WebKit reports a layout viewport of \(viewport.int("width")) x \(viewport.int("height")) CSS px")

        let info = QdnBookPage(try await page.hardObject("qdn.bookInfo()"))
        XCTAssertEqual(info.title, "示例之书")
        XCTAssertEqual(info.author, "张三")
        XCTAssertEqual(info.coverURL, "https://bookcover.yuewen.com/qdbimg/349573/1049836819/180")
        XCTAssertEqual(info.description, "清晨的小镇，古老的高塔。 一把生锈的钥匙。")
        XCTAssertEqual(info.tags, ["玄幻", "热血"])
        XCTAssertEqual(info.chapters.map(\.name), ["第一章 清晨", "第二章 高塔", "第三章 钥匙", "第四章 铁门", "第五章 希望之地", "第六章 钥匙", "第七章 免费"])
        XCTAssertEqual(info.chapters.map(\.isVip), [false, false, true, true, true, true, false])
        XCTAssertEqual(
            info.chapters.map(\.url),
            [1001, 1002, 1003, 1004, 2001, 2002, 3001].map { "https://www.\(QdnSite.domain)/chapter/1049836819/\($0)/" }
        )
        // A long book lists only its first chapters and links the page with every chapter.
        XCTAssertEqual(info.totalChapters, 1418)
        XCTAssertEqual(info.catalogURL, "https://www.\(QdnSite.domain)/book/1049836819/catalog/")
    }

    func testTheFullCatalogPageListsEveryChapterWithItsVolumesVipFlag() async throws {
        let page = try await open(try RepoFixtures.page("qdn_catalog.html"), at: bookAddress + "catalog/")
        let state = QdnCatalogState(try await page.hardObject("qdn.catalogState()"))
        XCTAssertEqual(state.state, "ready")
        XCTAssertEqual(state.count, 6)
        let info = try await page.hardObject("qdn.catalogInfo()")
        let chapters = info.objects("chapters").map(QdnCatalogEntry.init)
        XCTAssertEqual(chapters.map(\.name), ["第一章 清晨", "第二章 高塔", "第一章 希望之地", "第二章 钥匙", "第三章 铁门"])
        XCTAssertEqual(chapters.map(\.isVip), [false, false, true, true, true])
        XCTAssertEqual(chapters.compactMap { $0.url.split(separator: "/").last.map(String.init) }, ["1001", "1002", "2001", "2002", "2003"])
        let volumes = info.objects("volumes")
        XCTAssertEqual(volumes.map { $0.int("count") }, [2, 3])
        XCTAssertEqual(volumes.map { $0.bool("isVip") }, [false, true])
    }

    func testCaptchaAndFirewallPagesAreToldApartFromASlowPage() async throws {
        let captcha = try await open(try RepoFixtures.page("qdn_verification.html"), at: QdnSite.origin + "/verify/")
        let state1 = QdnPageState(try await captcha.hardObject("qdn.pageState()"))
        XCTAssertEqual(state1.state, "verification")
        let state2 = QdnCatalogState(try await captcha.hardObject("qdn.catalogState()"))
        XCTAssertEqual(state2.state, "verification")
        let state3 = QdnChapterState(try await captcha.hardObject("qdn.chapterState()"))
        XCTAssertTrue(state3.verification)
        let state4 = QdnRead(try await captcha.hardObject("qdn.readChapter()"))
        XCTAssertEqual(state4.error, "verification")

        let firewall = try await open(try RepoFixtures.page("qdn_waf.html"), at: QdnSite.origin + "/waf/")
        let state5 = QdnPageState(try await firewall.hardObject("qdn.pageState()"))
        XCTAssertEqual(state5.state, "blocked")
        let state6 = QdnCatalogState(try await firewall.hardObject("qdn.catalogState()"))
        XCTAssertEqual(state6.state, "blocked")
        let state7 = QdnChapterState(try await firewall.hardObject("qdn.chapterState()"))
        XCTAssertTrue(state7.blocked)

        let slow = try await open("<h1>loading</h1>", at: QdnSite.origin + "/slow/")
        let state8 = QdnPageState(try await slow.hardObject("qdn.pageState()"))
        XCTAssertEqual(state8.state, "waiting")
        let state9 = QdnCatalogState(try await slow.hardObject("qdn.catalogState()"))
        XCTAssertEqual(state9.state, "waiting")
    }

    func testFreeChapterTextDropsReviewsAndStrayNumbers() async throws {
        let page = try await open(try RepoFixtures.page("qdn_chapter_free.html"), at: QdnSite.origin + "/chapter/1049836819/1001/")
        let state = QdnChapterState(try await page.hardObject("qdn.chapterState()"))
        XCTAssertTrue(state.ready)
        XCTAssertFalse(state.blocked || state.verification || state.encrypted)
        let read = QdnRead(try await page.hardObject("qdn.readChapter()"))
        XCTAssertNil(read.error)
        XCTAssertEqual(read.lines.count, 4)
        XCTAssertTrue(read.lines.first?.hasPrefix("清晨的风吹过") == true)
        XCTAssertTrue(read.lines.allSatisfy { !$0.contains("12") })
    }

    func testAPlainBoughtChapterReadsWithoutDecoding() async throws {
        let page = try await open(try RepoFixtures.page("qdn_chapter_plain_bought.html"), at: QdnSite.origin + "/chapter/1049836819/1003/")
        let read = QdnRead(try await page.hardObject("qdn.readChapter()"))
        XCTAssertTrue(read.lines.first?.hasPrefix("老人接过信") == true)
    }

    func testAnUnboughtVipChapterIsRefusedWithTheReason() async throws {
        for (login, reason) in [(0, "login"), (1, "purchase")] {
            let page = try await open(try RepoFixtures.page("qdn_chapter_vip_guest.html", login: login), at: QdnSite.origin + "/chapter/1049836819/1003/")
            let read = QdnRead(try await page.hardObject("qdn.readChapter()"))
            XCTAssertEqual(read.error, "locked")
            XCTAssertEqual(read.reason, reason)
            XCTAssertEqual(read.expected, 2600)
            XCTAssertGreaterThan(read.chars, 0)
        }
    }

    // MARK: rbooks pages

    private var productAddress: String { RbooksSite.origin + "/books/6251000001" }

    func testTheProductPageHasItsMetadataAndRenderedEpisodes() async throws {
        let page = try await open(try RepoFixtures.page("rbooks_product.html"), at: productAddress)
        let ready = try await page.hardBool("rbooks.productReady()")
        XCTAssertTrue(ready)
        let info = RbooksProduct(try await page.hardObject("rbooks.productInfo()"))
        let cdn = "\u{72}\u{69}\u{64}\u{69}cdn.net"
        XCTAssertEqual(info.title, "예시 소설")
        XCTAssertEqual(info.author, "홍길동")
        XCTAssertEqual(info.publisher, "예시 출판")
        XCTAssertEqual(info.cover, "https://img.\(cdn)/cover/6251000001/xxlarge")
        XCTAssertTrue(info.synopsis.contains("용사는 탑으로 향한다"))
        XCTAssertFalse(info.synopsis.contains("가입하면"))
        XCTAssertEqual(info.seriesID, "6251000001")
        XCTAssertEqual(info.links, [RbooksSite.origin + "/books/6251000001/view", RbooksSite.origin + "/books/6251000002/view"])
        XCTAssertEqual(info.titleByID["6251000002"], "예시 소설 2화")
        XCTAssertEqual(info.tags, ["판타지", "모험"])
        XCTAssertTrue(info.diagnostics.contains("Rendered episode links: 3"))

        let loading = try await open("<div>loading</div>", at: RbooksSite.origin + "/books/1")
        let notReady = try await loading.hardBool("rbooks.productReady()")
        XCTAssertFalse(notReady)
    }

    func testTheLibraryPageResolvesTheProductAndReportsUnavailableTitles() async throws {
        let library = try await open(try RepoFixtures.page("rbooks_library.html"), at: RbooksSite.origin + "/library/books/8706175/")
        let probe = RbooksLibraryProbe(try await library.hardObject("rbooks.libraryProbe()"))
        XCTAssertEqual(probe.product, RbooksSite.origin + "/books/6121000538") // the lowest volume id
        XCTAssertTrue(probe.unavailable)
        XCTAssertTrue(probe.adult)
        XCTAssertFalse(probe.viewer)

        let viewer = try await open(try RepoFixtures.page("rbooks_viewer.html"), at: RbooksSite.origin + "/books/6251000001/view")
        let reading = RbooksLibraryProbe(try await viewer.hardObject("rbooks.libraryProbe()"))
        XCTAssertTrue(reading.viewer)
        XCTAssertTrue(reading.product.hasSuffix("/books/6251000001/view"))
    }

    func testViewerContentIsXHTMLWithLocalImageNames() async throws {
        let page = try await open(try RepoFixtures.page("rbooks_viewer.html"), at: RbooksSite.origin + "/books/6251000001/view")
        let state = RbooksContentState(try await page.hardObject("rbooks.contentState()"))
        XCTAssertTrue(state.found && state.ready)
        XCTAssertFalse(state.refused)
        XCTAssertGreaterThan(state.length, 500)
        let state10 = RbooksWall(try await page.hardObject("rbooks.accessWall()"))
        XCTAssertFalse(state10.wall)

        let before = try await page.evaluate(String.self, "return document.body.innerHTML;")
        let reply = try await page.hardOptionalObject("rbooks.extract({\"fallbackTitle\":\"fallback\",\"chapterIndex\":7})")
        let result = RbooksExtract(try XCTUnwrap(reply))
        // Reading must leave the live page alone.
        let after = try await page.evaluate(String.self, "return document.body.innerHTML;")
        XCTAssertEqual(before, after)

        let cdn = "\u{72}\u{69}\u{64}\u{69}cdn.net"
        XCTAssertEqual(result.title, "1화 시작")
        XCTAssertEqual(result.images.map(\.url), ["https://img.\(cdn)/pages/6251000001/001.jpg?cache=1", RbooksSite.origin + "/pages/002.png"])
        XCTAssertEqual(result.images.map(\.name), ["images/0007_01.jpg", "images/0007_02.png"])
        let content = result.content
        XCTAssertEqual(content.components(separatedBy: "src=\"images/0007_01.jpg\"").count - 1, 2)
        XCTAssertTrue(content.contains("src=\"images/0007_02.png\""))
        for forbidden in ["class=", "style=", "data-", "<script", "content_footer", "다음 화로", "<hr", "&nbsp;", "\u{200B}", "id="] {
            XCTAssertFalse(content.contains(forbidden), forbidden)
        }
        XCTAssertTrue(content.contains("<blockquote>"))
        XCTAssertTrue(content.contains("align=\"center\""))
        XCTAssertTrue(content.contains("<br />"))
        XCTAssertTrue(result.contentText.components(separatedBy: "\n").first?.hasPrefix("첫 번째 문단입니다.") == true)
        // The EPUB needs well-formed XML.
        let problems = try await page.evaluate(Int.self, """
        const doc = new DOMParser().parseFromString('<div>' + \(HardScripts.literal(content)) + '</div>', 'application/xml');
        return doc.getElementsByTagName('parsererror').length;
        """)
        XCTAssertEqual(problems, 0)
    }

    func testViewerRefusalsAndPurchaseWallsAreClassified() async throws {
        let refused = try await open(try RepoFixtures.page("rbooks_viewer_refused.html"), at: RbooksSite.origin + "/books/6251000009/view")
        let state11 = RbooksContentState(try await refused.hardObject("rbooks.contentState()"))
        XCTAssertTrue(state11.refused)
        let refusedWall = RbooksWall(try await refused.hardObject("rbooks.accessWall()"))
        XCTAssertTrue(refusedWall.wall)
        XCTAssertTrue(refusedWall.refused)
        let nothing = try await refused.hardOptionalObject("rbooks.extract({\"fallbackTitle\":\"x\",\"chapterIndex\":1})")
        XCTAssertNil(nothing)

        let wall = try await open(try RepoFixtures.page("rbooks_wall.html"), at: RbooksSite.origin + "/books/6251000008/view")
        let purchase = RbooksWall(try await wall.hardObject("rbooks.accessWall()"))
        XCTAssertTrue(purchase.wall)
        XCTAssertFalse(purchase.refused)
        XCTAssertGreaterThanOrEqual(purchase.hits, 2)

        let product = try await open(try RepoFixtures.page("rbooks_wall.html"), at: RbooksSite.origin + "/books/6251000008")
        let state12 = RbooksWall(try await product.hardObject("rbooks.accessWall()"))
        XCTAssertTrue(state12.offViewer)
    }
}
