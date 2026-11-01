import XCTest
import NpiaCore
@testable import NpiaSources

private let origin = QdnSite.origin

private let fast = QdnTiming(
    minGap: 0, firewallCooldowns: [0.02, 0.02], loadTimeout: 1, bookPolls: 3, chapterPolls: 3, poll: 0.01,
    decryptPolls: 4, decryptPoll: 0.01, extractPolls: 3, extractPoll: 0.01, decodePolls: 3, decodePoll: 0.01,
    referenceSlices: 3, referenceSliceMillis: 10, maxPages: 2
)

private func chapterJSON(_ n: Int, vip: Bool) -> String {
    "{\"url\":\"\(origin)/chapter/77/\(1000 + n)/\",\"name\":\"Chapter \(n)\",\"isVip\":\(vip)}"
}

private func bookJSON(_ chapters: [String], total: Int = 0, catalogURL: String = "", title: String = "A Book") -> String {
    "{\"title\":\"\(title)\",\"author\":\"An Author\",\"coverUrl\":\"https://img.example/c.jpg\",\"description\":\"About it\",\"tags\":[\"x\",\"y\"],"
        + "\"chapters\":[\(chapters.joined(separator: ","))],\"bookUrl\":\"\(origin)/book/77/\",\"totalChapters\":\(total),\"catalogUrl\":\"\(catalogURL)\"}"
}

private let ready = "{\"state\":\"ready\",\"url\":\"\(origin)/book/77/\",\"title\":\"A Book\",\"text\":\"\",\"catalogCount\":3,\"viewport\":{\"width\":1280,\"height\":800}}"
private let plainChapter = "{\"ready\":true,\"blocked\":false,\"verification\":false,\"encrypted\":false}"
private let blockedChapter = "{\"ready\":false,\"blocked\":true,\"verification\":false,\"encrypted\":false}"

/// Responds to the qdn entry points; each test overrides what it cares about.
private final class Responses: @unchecked Sendable {
    var pageState = ready
    var book = bookJSON([chapterJSON(1, vip: false), chapterJSON(2, vip: false), chapterJSON(3, vip: true)])
    var userInfo = "{\"live\":null}"
    var catalogState = "{\"state\":\"ready\",\"count\":0}"
    var catalog = "{\"chapters\":[]}"
    var chapterState: @Sendable (String) -> String = { _ in plainChapter }
    var encryptionState: @Sendable () -> String = { "{\"encrypted\":false,\"rendered\":false,\"failed\":false,\"blocked\":false,\"words\":0}" }
    var read: @Sendable (String) -> String = { _ in "{\"lines\":[\"First paragraph.\",\"Second paragraph.\"]}" }
    var extract: @Sendable () -> String? = { "{\"paragraphs\":[]}" }
    var decoderStatus: @Sendable (String) -> String? = { _ in "{\"done\":true,\"built\":9800,\"total\":9800}" }
    var decode: @Sendable (String) -> String = { _ in "{\"lines\":[\"decoded one\",\"decoded two\"],\"stats\":{\"decoded\":10,\"verifiedGlyphs\":4,\"shapeMatchedGlyphs\":6}}" }
    var knownFonts = "[]"

    func page(_ index: Int = 0) -> HardScriptedPage {
        HardScriptedPage(name: "page\(index)") { page, call in
            let url = page.url?.absoluteString ?? ""
            if call.hasPrefix("qdn.pageState") { return self.pageState }
            if call.hasPrefix("qdn.bookInfo") { return self.book }
            if call.hasPrefix("qdn.userInfo") { return self.userInfo }
            if call.hasPrefix("qdn.catalogState") { return self.catalogState }
            if call.hasPrefix("qdn.catalogInfo") { return self.catalog }
            if call.hasPrefix("qdn.chapterState") { return self.chapterState(url) }
            if call.hasPrefix("qdn.encryptionState") { return self.encryptionState() }
            if call.hasPrefix("qdn.readChapter") { return self.read(url) }
            if call.hasPrefix("qdn.extract") { return self.extract() }
            if call.hasPrefix("qdn.decoder.putResource") { return "{\"complete\":true}" }
            if call.hasPrefix("qdn.decoder.prepare") { return self.decoderStatus(call) }
            if call.hasPrefix("qdn.decoder.knownFonts") { return self.knownFonts }
            if call.hasPrefix("qdn.decoder.decodeJson") { return self.decode(call) }
            return nil
        }
    }
}

private func web(_ responses: Responses, setup: @escaping @Sendable (HardScriptedPage) -> Void = { _ in }) -> HardScriptedWeb {
    HardScriptedWeb { index in
        let page = responses.page(index)
        setup(page)
        return page
    }
}

private func adapter(_ cookies: FakeCookies? = FakeCookies(), timing: QdnTiming = fast) -> QdnAdapter {
    QdnAdapter(timing: timing, cookies: cookies)
}

private func settings(skipPaid: Bool = false, from: Int? = nil, to: Int? = nil, threads: Int? = nil) -> AppSettings {
    var value = AppSettings()
    value.shared.skipPaid = skipPaid
    value.shared.rangeFrom = from
    value.shared.rangeTo = to
    if let threads { value.shared.threads = threads }
    return value
}

private func ref(_ n: Int) -> ChapterRef {
    ChapterRef(id: String(1000 + n), index: n, title: "Chapter \(n)", url: URL(string: "\(origin)/chapter/77/\(1000 + n)/")!)
}

private func bookInfo(_ chapters: [ChapterRef]) -> BookInfo {
    BookInfo(source: .qdn, id: "77", url: URL(string: origin + "/book/77/")!, title: "A Book", chapters: chapters)
}

private func expectError<T>(_ file: StaticString = #filePath, line: UInt = #line, _ body: () async throws -> T) async -> Error? {
    do {
        _ = try await body()
        XCTFail("expected an error", file: file, line: line)
        return nil
    } catch {
        return error
    }
}

final class QdnAdapterTests: XCTestCase {
    override func setUp() {
        SiteSessions.resetProcessState()
        HardPrompts.shared.reset()
    }

    private func parseBook(_ qdn: QdnAdapter, link: String = origin + "/book/77/", web: HardScriptedWeb,
                           secrets: InMemorySecretStore = InMemorySecretStore(), settings: AppSettings = settings(skipPaid: true),
                           logger: CollectingLogger = CollectingLogger()) async throws -> BookInfo {
        try await qdn.parseBook(URL(string: link)!, context: hardContext(web, secrets: secrets, settings: settings, logger: logger))
    }

    private func parseBook(link: String = origin + "/book/77/", web: HardScriptedWeb,
                           secrets: InMemorySecretStore = InMemorySecretStore(), settings: AppSettings = settings(skipPaid: true),
                           logger: CollectingLogger = CollectingLogger()) async throws -> BookInfo {
        try await parseBook(adapter(), link: link, web: web, secrets: secrets, settings: settings, logger: logger)
    }

    // MARK: URLs

    func testRecognisesBookAndChapterUrlsOfTheSiteAndItsSubdomains() {
        XCTAssertTrue(QdnAdapter.matches(hardURL(origin + "/book/1049836819/")))
        XCTAssertTrue(QdnAdapter.matches(hardURL("https://m." + QdnSite.domain + "/book/1")))
        XCTAssertTrue(QdnAdapter.matches(hardURL(origin + "/chapter/1049836819/12345/")))
        XCTAssertTrue(QdnAdapter.matches(hardURL("https://" + QdnSite.domain + "/book/5?x=1")))
        XCTAssertFalse(QdnAdapter.matches(hardURL(origin + "/rank/")))
        XCTAssertFalse(QdnAdapter.matches(hardURL("https://example.com/book/12")))
        XCTAssertFalse(QdnAdapter.matches(hardURL("https://not" + QdnSite.domain + "/book/12")))
    }

    func testTheMobileHostIsReadLikeTheDesktopOne() {
        let mobile = "https://m." + QdnSite.domain
        let desktop = "https://www." + QdnSite.domain
        // Book pages and the chapter pages under them match on both hosts, with or without tracking parameters.
        for host in [mobile, desktop] {
            XCTAssertTrue(QdnAdapter.matches(hardURL(host + "/book/1010868264/")), host)
            XCTAssertTrue(QdnAdapter.matches(hardURL(host + "/book/1010868264")), host)
            XCTAssertTrue(QdnAdapter.matches(hardURL(host + "/book/1010868264/?source=m_jump")), host)
            XCTAssertTrue(QdnAdapter.matches(hardURL(host + "/chapter/1010868264/402733549/")), host)
        }
        // Everything else on the site does not: home, rankings, search, listings, a book path without an id.
        for host in [mobile, desktop, "https://" + QdnSite.domain] {
            for path in ["", "/", "/rank/yuepiao/", "/search?kw=abc", "/all/", "/finish/", "/book/", "/book/abc/", "/author/1/", "/bookstore/12/"] {
                XCTAssertFalse(QdnAdapter.matches(hardURL(host + path)), host + path)
            }
        }
    }

    func testEveryBookOrChapterLinkIsKeptUnderTheDesktopBookAddress() {
        let book = origin + "/book/1010868264/"
        for link in [
            "https://m." + QdnSite.domain + "/book/1010868264/",
            "https://m." + QdnSite.domain + "/book/1010868264/?source=m_jump#intro",
            "https://m." + QdnSite.domain + "/chapter/1010868264/402733549/",
            "https://www." + QdnSite.domain + "/book/1010868264",
            "https://www." + QdnSite.domain + "/chapter/1010868264/402733549/",
            "https://" + QdnSite.domain + "/book/1010868264/catalog/",
        ] {
            XCTAssertEqual(QdnAdapter.canonicalURL(hardURL(link)).absoluteString, book, link)
            XCTAssertEqual(SourceCatalog.canonicalURL(hardURL(link)).absoluteString, book, link)
        }
        // Links the adapter does not own are left alone.
        let rank = "https://m." + QdnSite.domain + "/rank/"
        XCTAssertEqual(QdnAdapter.canonicalURL(hardURL(rank)).absoluteString, rank)
        XCTAssertEqual(SourceCatalog.canonicalURL(hardURL("https://example.com/book/1")).absoluteString, "https://example.com/book/1")
    }

    func testTheCatalogHandsQdnLinksToTheQdnAdapter() {
        let mobile = hardURL("https://m." + QdnSite.domain + "/book/42/")
        XCTAssertTrue(SourceCatalog.adapterType(for: mobile) == QdnAdapter.self)
        XCTAssertTrue(SourceCatalog.nativeAdapterType(for: mobile) == QdnAdapter.self)
        XCTAssertTrue(SourceCatalog.adapterType(for: .qdn) == QdnAdapter.self)
        XCTAssertTrue(SourceCatalog.adapterType(for: .rbooks) == RbooksAdapter.self)
        // Both are the ones the Add screen's engine check lets through.
        XCTAssertTrue(SiteEngineRouter.canTry(mobile, engines: AppSettings().engines))
    }

    func testReadsTheBookAndChapterIdsOutOfTheUrl() {
        XCTAssertEqual(QdnSite.bookID(hardURL(origin + "/chapter/1049836819/12345/")), "1049836819")
        XCTAssertEqual(QdnSite.chapterID(hardURL(origin + "/chapter/1049836819/12345/")), "12345")
        XCTAssertNil(QdnSite.chapterID(hardURL(origin + "/book/1049836819/")))
        XCTAssertNil(QdnSite.bookID(hardURL("https://example.com/book/1")))
    }

    // MARK: Book

    func testAMobileBookLinkIsParsedFromTheDesktopBookPage() async throws {
        let scripted = web(Responses())
        let book = try await parseBook(link: "https://m." + QdnSite.domain + "/book/77/?source=m_jump", web: scripted)
        XCTAssertEqual(book.url.absoluteString, origin + "/book/77/")
        XCTAssertEqual(scripted.pages.first?.loads.map(\.absoluteString), [origin + "/book/77/"])
    }

    func testALoginMadeOnTheMobileHostCountsAndIsSavedLikeAnyOther() async throws {
        // The phone site's login cookies are on the parent domain, so the store answers for either host.
        let cookies = FakeCookies(["ywkey": "m1", "ywguid": "m2"])
        let secrets = InMemorySecretStore()
        let book = try await parseBook(adapter(cookies), link: "https://m." + QdnSite.domain + "/book/77/", web: web(Responses()), secrets: secrets)
        XCTAssertEqual(book.chapters.count, 3)
        XCTAssertTrue((secrets.string(for: SecretKey.source(.qdn, "sessionCookies")) ?? "").contains("m1"))
    }

    func testReadsTheBookFromTheDesktopReaderPage() async throws {
        let scripted = web(Responses())
        let log = CollectingLogger()
        let book = try await parseBook(link: origin + "/chapter/77/1001/", web: scripted, logger: log)

        XCTAssertEqual(book.title, "A Book")
        XCTAssertEqual(book.author, "An Author")
        XCTAssertEqual(book.language, "zh")
        XCTAssertEqual(book.url.absoluteString, origin + "/book/77/")
        XCTAssertEqual(book.tags, ["x", "y"])
        XCTAssertEqual(book.chapters.map(\.id), ["1001", "1002", "1003"])
        XCTAssertEqual(book.chapters.map(\.index), [1, 2, 3])
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, false, true])
        XCTAssertEqual(book.chapters.first?.url?.absoluteString, origin + "/chapter/77/1001/")
        let page = try XCTUnwrap(scripted.pages.first)
        XCTAssertEqual(scripted.pages.count, 1)
        // A chapter URL is turned into the book page, read as a desktop browser with the shared script.
        XCTAssertEqual(page.loads.map(\.absoluteString), [origin + "/book/77/"])
        XCTAssertEqual(page.userAgent, QdnSite.userAgent)
        XCTAssertEqual(page.viewport?.0, 1280)
        XCTAssertEqual(page.viewport?.1, 800)
        XCTAssertEqual(page.documentStartScripts.count, 1)
        XCTAssertTrue(page.documentStartScripts[0].contains("__npiaHard"))
        XCTAssertTrue(page.initScripts.isEmpty)
        XCTAssertTrue(page.closed)
        XCTAssertTrue(log.lines.contains { $0.contains("[qdn] Book: A Book") })
        XCTAssertTrue(log.lines.contains { $0.contains("Reading as a desktop browser (1280 x 800 px)") })
    }

    func testTheBookPageHasToRenderEventually() async {
        let responses = Responses()
        responses.pageState = "{\"state\":\"waiting\",\"url\":\"\(origin)/book/77/\",\"title\":\"Loading\",\"text\":\"nothing yet\"}"
        let error = await expectError { try await parseBook(web: web(responses)) }
        guard case .parse(let message)? = error as? SourceError else { return XCTFail("expected a parse error, got \(String(describing: error))") }
        XCTAssertTrue(message.contains("did not render"))
    }

    func testACaptchaAsksForTheSignInBrowserOnceThenGivesUp() async {
        let responses = Responses()
        responses.pageState = "{\"state\":\"verification\",\"url\":\"\(origin)/book/77/\"}"
        let qdn = adapter()
        let scripted = web(responses)
        let first = await expectError { try await parseBook(qdn, web: scripted) }
        XCTAssertEqual(first as? SourceError, .loginRequired(.qdn))
        // After the user was shown the page, the same book does not ask again.
        let second = await expectError { try await parseBook(qdn, web: scripted) }
        guard case .blocked(let message)? = second as? SourceError else { return XCTFail("expected blocked, got \(String(describing: second))") }
        XCTAssertTrue(message.contains("verification"))
        // A later download asks afresh.
        let third = await expectError { try await parseBook(qdn, web: scripted) }
        XCTAssertEqual(third as? SourceError, .loginRequired(.qdn))
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testAFirewallBlockIsNotSomethingTheUserCanFixInTheBrowser() async {
        let responses = Responses()
        responses.pageState = "{\"state\":\"blocked\",\"url\":\"\(origin)/book/77/\"}"
        let error = await expectError { try await parseBook(web: web(responses)) }
        guard case .blocked(let message)? = error as? SourceError else { return XCTFail("expected blocked, got \(String(describing: error))") }
        XCTAssertTrue(message.contains("firewall"))
    }

    func testASlowPageLoadIsOnlyAWarning() async throws {
        let responses = Responses()
        let slow = hardURL(origin + "/book/77/")
        let scripted = web(responses) { $0.loadFailures[slow.absoluteString] = WebEngineError.timeout(slow) }
        let log = CollectingLogger()
        let book = try await parseBook(web: scripted, logger: log)
        XCTAssertEqual(book.chapters.count, 3)
        XCTAssertTrue(log.lines.contains { $0.contains("Page load warning") })
    }

    func testALongBookIsReadFromItsFullCatalogPage() async throws {
        let all = (1...6).map { "{\"url\":\"\(origin)/chapter/77/\(2000 + $0)/\",\"name\":\"Full \($0)\",\"isVip\":\($0 > 4)}" }
        let responses = Responses()
        responses.book = bookJSON([chapterJSON(1, vip: false), chapterJSON(2, vip: false)], total: 6, catalogURL: origin + "/book/77/catalog/")
        responses.catalog = "{\"chapters\":[\(all.joined(separator: ","))]}"
        let scripted = web(responses)
        let log = CollectingLogger()
        let book = try await parseBook(web: scripted, logger: log)
        XCTAssertEqual(book.chapters.count, 6)
        XCTAssertEqual(book.chapters.first?.title, "Full 1")
        XCTAssertEqual(book.chapters.map(\.isPaid), [false, false, false, false, true, true])
        XCTAssertEqual(scripted.pages.first?.loads.map(\.absoluteString), [origin + "/book/77/", origin + "/book/77/catalog/"])
        XCTAssertTrue(log.lines.contains { $0.contains("full catalog: 6 chapters") })
    }

    func testACatalogPageThatCannotBeReadKeepsTheBookPageList() async throws {
        let responses = Responses()
        responses.book = bookJSON([chapterJSON(1, vip: false), chapterJSON(2, vip: false)], total: 6, catalogURL: origin + "/book/77/catalog/")
        responses.catalogState = "{\"state\":\"waiting\",\"count\":0}"
        let log = CollectingLogger()
        let book = try await parseBook(web: web(responses), settings: settings(), logger: log)
        XCTAssertEqual(book.chapters.count, 2)
        XCTAssertTrue(log.lines.contains { $0.contains("6 chapters but only 2") })
    }

    func testACaptchaOnTheCatalogPageAsksForTheBrowserToo() async {
        let responses = Responses()
        responses.book = bookJSON([chapterJSON(1, vip: false)], total: 6, catalogURL: origin + "/book/77/catalog/")
        responses.catalogState = "{\"state\":\"verification\",\"count\":0}"
        let error = await expectError { try await parseBook(web: web(responses), settings: settings()) }
        XCTAssertEqual(error as? SourceError, .loginRequired(.qdn))
    }

    func testABookWithoutChaptersOrTitleIsAParseError() async {
        let empty = Responses()
        empty.book = bookJSON([])
        let noChapters = await expectError { try await parseBook(web: web(empty)) }
        guard case .parse(let first)? = noChapters as? SourceError else { return XCTFail("expected a parse error") }
        XCTAssertTrue(first.contains("no chapters"))

        let untitled = Responses()
        untitled.book = bookJSON([chapterJSON(1, vip: false)], title: "")
        let noTitle = await expectError { try await parseBook(web: web(untitled)) }
        guard case .parse(let second)? = noTitle as? SourceError else { return XCTFail("expected a parse error") }
        XCTAssertTrue(second.contains("title"))
    }

    func testNotAQdnUrlIsRejected() async {
        let error = await expectError { try await parseBook(link: "https://example.com/book/1", web: web(Responses())) }
        guard case .unsupportedURL? = error as? SourceError else { return XCTFail("expected unsupportedURL, got \(String(describing: error))") }
    }

    // MARK: Sign-in prompt for bought chapters

    func testBoughtChaptersWithoutASessionOpenTheSignInBrowserOnceThenGoOn() async throws {
        let qdn = adapter()
        let scripted = web(Responses())
        let first = await expectError { try await parseBook(qdn, web: scripted, settings: settings()) }
        XCTAssertEqual(first as? SourceError, .loginRequired(.qdn))
        // The pipeline starts over after the sign-in browser closes, with a new adapter: now the book is read,
        // signed in or not.
        let book = try await parseBook(adapter(), web: scripted, settings: settings())
        XCTAssertEqual(book.chapters.count, 3)
        // A new download asks again.
        let again = await expectError { try await parseBook(adapter(), web: scripted, settings: settings()) }
        XCTAssertEqual(again as? SourceError, .loginRequired(.qdn))
    }

    func testNoPromptWhenTheRangeHasOnlyFreeChapters() async throws {
        let book = try await parseBook(web: web(Responses()), settings: settings(from: 1, to: 2))
        XCTAssertEqual(book.chapters.count, 3)
    }

    func testNoPromptWhenPaidChaptersAreSkipped() async throws {
        let book = try await parseBook(web: web(Responses()), settings: settings(skipPaid: true))
        XCTAssertEqual(book.chapters.count, 3)
    }

    func testNoPromptWhenTheBrowserIsSignedInAndTheLoginIsSaved() async throws {
        let cookies = FakeCookies(["ywkey": "k1", "ywguid": "g1"])
        let secrets = InMemorySecretStore()
        let book = try await parseBook(adapter(cookies), web: web(Responses()), secrets: secrets, settings: settings())
        XCTAssertEqual(book.chapters.count, 3)
        let saved = try XCTUnwrap(secrets.string(for: SecretKey.source(.qdn, "sessionCookies")))
        XCTAssertTrue(saved.contains("k1") && saved.contains("g1"))
    }

    func testHalfALoginIsNotASession() async {
        let error = await expectError { try await parseBook(adapter(FakeCookies(["ywguid": "g1"])), web: web(Responses()), settings: settings()) }
        XCTAssertEqual(error as? SourceError, .loginRequired(.qdn))
    }

    func testAnExpiredRememberedLoginIsRemovedSoTheSiteCanRenewIt() async {
        let cookies = FakeCookies(["ywkey": "old", "ywguid": "old"])
        let responses = Responses()
        responses.userInfo = "{\"live\":false}"
        let log = CollectingLogger()
        let error = await expectError { try await parseBook(adapter(cookies), web: web(responses), settings: settings(), logger: log) }
        // The session is gone, and a VIP chapter is in range.
        XCTAssertEqual(error as? SourceError, .loginRequired(.qdn))
        XCTAssertEqual(Set(cookies.removed), ["ywkey", "ywguid", "ywopenid"])
        XCTAssertTrue(cookies.values.isEmpty)
        XCTAssertTrue(log.lines.contains { $0.contains("has expired") })
    }

    func testASavedLoginComesBackAfterTheCookieStoreLostIt() async throws {
        let secrets = InMemorySecretStore()
        // First run: signed in, so the cookies are saved.
        _ = try await parseBook(adapter(FakeCookies(["ywkey": "k1", "ywguid": "g1"])), web: web(Responses()), secrets: secrets, settings: settings())
        // The app restarted and the web view dropped its session cookies.
        SiteSessions.resetProcessState()
        let cookies = FakeCookies()
        let book = try await parseBook(adapter(cookies), web: web(Responses()), secrets: secrets, settings: settings())
        XCTAssertEqual(book.chapters.count, 3)
        XCTAssertEqual(cookies.values, ["ywkey": "k1", "ywguid": "g1"])
    }

    // MARK: Chapters

    private func readChapter(_ responses: Responses, logger: CollectingLogger = CollectingLogger(), secrets: InMemorySecretStore = InMemorySecretStore(),
                             settings: AppSettings = AppSettings(), adapter qdn: QdnAdapter = adapter()) async throws -> (ChapterResult, HardScriptedWeb) {
        let scripted = web(responses)
        let result = try await qdn.parseChapter(ref(1), book: bookInfo([ref(1)]), context: hardContext(scripted, secrets: secrets, settings: settings, logger: logger))
        return (result, scripted)
    }

    func testReadsAFreeChapterAsParagraphs() async throws {
        let (result, scripted) = try await readChapter(Responses())
        let chapter = try XCTUnwrap(content(result))
        XCTAssertEqual(chapter.ref, ref(1))
        XCTAssertTrue(chapter.html.contains("<p>First paragraph.</p>"))
        XCTAssertTrue(chapter.html.contains("class=\"qdn-content\""))
        XCTAssertEqual(scripted.pages.first?.loads.map(\.absoluteString), [origin + "/chapter/77/1001/"])
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
        // The chapter page is a desktop reader with the script installed before it runs.
        XCTAssertEqual(scripted.pages.first?.userAgent, QdnSite.userAgent)
        XCTAssertEqual(scripted.pages.first?.documentStartScripts.count, 1)
    }

    func testAnUnboughtChapterIsRefusedWithTheReason() async throws {
        for (reason, expected) in [("login", "not signed in"), ("purchase", "not bought")] {
            let responses = Responses()
            responses.read = { _ in "{\"error\":\"locked\",\"reason\":\"\(reason)\",\"chars\":137,\"expected\":3094}" }
            let (result, _) = try await readChapter(responses)
            guard case .refused(_, let why) = result else { return XCTFail("expected a refusal") }
            XCTAssertTrue(why.contains(expected), why)
            XCTAssertTrue(why.contains("137/3094"), why)
        }
    }

    func testALockedChapterWithoutAReasonIsRefusedToo() async throws {
        let responses = Responses()
        responses.read = { _ in "{\"error\":\"locked\"}" }
        let (result, _) = try await readChapter(responses)
        guard case .refused(_, let why) = result else { return XCTFail("expected a refusal") }
        XCTAssertEqual(why, "locked, or sign-in required")
    }

    func testAVerificationPageIsAFailureTheUserCanRetry() async throws {
        let responses = Responses()
        responses.read = { _ in "{\"error\":\"verification\"}" }
        let (result, _) = try await readChapter(responses)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("verification"))
    }

    func testAnEmptyChapterAndUnknownErrorsFail() async throws {
        let empty = Responses()
        empty.read = { _ in "{\"lines\":[]}" }
        let (emptyResult, _) = try await readChapter(empty)
        guard case .failed = emptyResult else { return XCTFail("expected a failure") }

        let odd = Responses()
        odd.read = { _ in "{\"error\":\"missing content\"}" }
        let (oddResult, _) = try await readChapter(odd)
        guard case .failed(_, let message) = oddResult else { return XCTFail("expected a failure") }
        XCTAssertEqual(message, "missing content")
    }

    func testTheFirewallIsWaitedOutBeforeAChapterIsRetried() async throws {
        let loads = Collected<Int>()
        let responses = Responses()
        responses.chapterState = { _ in
            loads.add(1)
            return loads.all.count <= 2 ? blockedChapter : plainChapter
        }
        let log = CollectingLogger()
        let (result, scripted) = try await readChapter(responses, logger: log)
        XCTAssertNotNil(content(result))
        XCTAssertEqual(scripted.pages.first?.loads.count, 3)
        XCTAssertEqual(log.lines.filter { $0.contains("firewall is blocking") }.count, 2)
    }

    func testAFirewallThatNeverLiftsFailsTheChapterAfterTheCooldowns() async throws {
        let responses = Responses()
        responses.chapterState = { _ in blockedChapter }
        let (result, scripted) = try await readChapter(responses)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("firewall"))
        // The first try plus one per cooldown.
        XCTAssertEqual(scripted.pages.first?.loads.count, 3)
    }

    func testAChapterThatNeverGetsReadyTimesOut() async throws {
        let responses = Responses()
        responses.chapterState = { _ in "{\"ready\":false,\"blocked\":false,\"verification\":false,\"encrypted\":false}" }
        let (result, _) = try await readChapter(responses)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("timed out"))
    }

    // MARK: Bought chapters

    private func encrypted(words: Int = 0, rendered: @escaping @Sendable () -> Bool = { true }, failed: Bool = false, blocked: Bool = false) -> Responses {
        let responses = Responses()
        responses.chapterState = { _ in "{\"ready\":true,\"blocked\":false,\"verification\":false,\"encrypted\":true}" }
        responses.encryptionState = {
            "{\"encrypted\":true,\"rendered\":\(rendered()),\"failed\":\(failed),\"blocked\":\(blocked),\"words\":\(words)}"
        }
        return responses
    }

    func testABoughtChapterIsExtractedOnItsPageAndDecodedOnTheDecoderPage() async throws {
        let polls = Collected<Int>()
        let responses = encrypted(words: 20, rendered: { polls.add(1); return polls.all.count > 2 })
        responses.knownFonts = "[\"abc\"]"
        let log = CollectingLogger()
        let (result, scripted) = try await readChapter(responses, logger: log)
        let chapter = try XCTUnwrap(content(result))
        XCTAssertTrue(chapter.html.contains("<p>decoded one</p>"))
        XCTAssertEqual(scripted.pages.count, 2)
        let chapterPage = scripted.pages[0]
        let decoderPage = scripted.pages[1]
        XCTAssertTrue(chapterPage.calls.contains { $0.hasPrefix("qdn.extract") && $0.contains("\"known\":[\"abc\"]") })
        XCTAssertEqual(decoderPage.loads.map(\.absoluteString), ["about:blank"])
        // The resources (the font in chunks), then the reference, then the decode.
        XCTAssertGreaterThanOrEqual(decoderPage.calls.filter { $0.hasPrefix("qdn.decoder.putResource") }.count, 3)
        XCTAssertTrue(decoderPage.calls.contains { $0.hasPrefix("qdn.decoder.prepare") })
        XCTAssertTrue(decoderPage.calls.contains { $0.hasPrefix("qdn.decoder.decodeJson") })
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
        XCTAssertTrue(log.lines.contains { $0.contains("Decoded font-encrypted chapter: 2 paragraphs, 10 glyphs") })
    }

    func testTheResourcesReachTheDecoderPageInChunksTheScriptAccepts() async throws {
        let (_, scripted) = try await readChapter(encrypted())
        let decoderPage = try XCTUnwrap(scripted.pages.last)
        let puts = decoderPage.calls.filter { $0.hasPrefix("qdn.decoder.putResource") }
        // font (12 chunks of about 192 KB), frequency and verified maps (one each).
        XCTAssertEqual(puts.filter { $0.contains("\"font\"") }.count, 12)
        XCTAssertEqual(puts.filter { $0.contains("\"frequency\"") }.count, 1)
        XCTAssertEqual(puts.filter { $0.contains("\"verified\"") }.count, 1)
        // Every font chunk but the last is a multiple of 4 base64 characters.
        let font = puts.filter { $0.contains("\"font\"") }
        for call in font.dropLast() {
            let literal = call.components(separatedBy: ", ").last ?? ""
            let chunk = literal.replacingOccurrences(of: "\")", with: "").replacingOccurrences(of: "\"", with: "").replacingOccurrences(of: "\\", with: "")
            XCTAssertEqual(chunk.count % 4, 0)
        }
    }

    func testTheDecoderIsBuiltOnceForAWholeDownload() async throws {
        let scripted = web(encrypted())
        let refs = (1...4).map(ref)
        let results = try await adapter().parseChapters(refs, book: bookInfo(refs), context: hardContext(scripted))
        XCTAssertEqual(results.filter { content($0) != nil }.count, 4)
        let decoderPages = scripted.pages.filter { $0.loads.map(\.absoluteString) == ["about:blank"] }
        XCTAssertEqual(decoderPages.count, 1)
        let decoderPage = try XCTUnwrap(decoderPages.first)
        XCTAssertEqual(decoderPage.calls.filter { $0.hasPrefix("qdn.decoder.prepare") }.count, 1)
        XCTAssertEqual(decoderPage.calls.filter { $0.hasPrefix("qdn.decoder.decodeJson") }.count, 4)
    }

    func testAReaderThatCannotDecryptFailsTheChapter() async throws {
        let (result, _) = try await readChapter(encrypted(failed: true))
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("could not decrypt"))
    }

    func testAFirewallDuringTheDecryptionFailsTheChapter() async throws {
        let (result, _) = try await readChapter(encrypted(blocked: true))
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("firewall"))
    }

    func testADecryptionThatNeverFinishesTimesOut() async throws {
        let (result, _) = try await readChapter(encrypted(rendered: { false }))
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("timed out decrypting"))
    }

    func testAnUnstableCaptureIsReadAgainAndTheBetterOneKept() async throws {
        let decodes = Collected<Int>()
        let responses = encrypted()
        responses.decode = { _ in
            decodes.add(1)
            if decodes.all.count == 1 { return "{\"lines\":[\"bad\"],\"stats\":{\"decoded\":1,\"unstableParagraphs\":[0,1]}}" }
            return "{\"lines\":[\"good one\",\"good two\"],\"stats\":{\"decoded\":2}}"
        }
        let log = CollectingLogger()
        let (result, _) = try await readChapter(responses, logger: log)
        let chapter = try XCTUnwrap(content(result))
        XCTAssertEqual(decodes.all.count, 2)
        XCTAssertTrue(chapter.html.contains("good one"))
        XCTAssertFalse(chapter.html.contains("bad"))
        XCTAssertTrue(log.lines.contains { $0.contains("retrying once") })
    }

    func testAWorseSecondCaptureIsNotKept() async throws {
        let decodes = Collected<Int>()
        let responses = encrypted()
        responses.decode = { _ in
            decodes.add(1)
            if decodes.all.count == 1 { return "{\"lines\":[\"first\",\"capture\"],\"stats\":{\"decoded\":2,\"missingFonts\":[{\"family\":\"f\"}]}}" }
            return "{\"lines\":[\"x\"],\"stats\":{\"decoded\":1,\"unstableParagraphs\":[0],\"missingFonts\":[{\"family\":\"f\"}]}}"
        }
        let (result, _) = try await readChapter(responses)
        let chapter = try XCTUnwrap(content(result))
        XCTAssertTrue(chapter.html.contains("first"))
    }

    func testWarningsAreLoggedButTheBestTextIsStillSaved() async throws {
        let responses = encrypted(words: 5000)
        responses.decode = { _ in "{\"lines\":[\"short\"],\"stats\":{\"decoded\":3,\"lowConfidence\":2,\"suspiciousPassthrough\":1}}" }
        let log = CollectingLogger()
        let (result, _) = try await readChapter(responses, logger: log)
        XCTAssertNotNil(content(result))
        let warnings = log.lines.filter { $0.contains("Saving best available text") }
        XCTAssertEqual(warnings.count, 1)
        let warning = warnings.first ?? ""
        XCTAssertTrue(warning.contains("uncertain_glyphs=2") && warning.contains("suspicious_passthrough=1") && warning.contains("incomplete=1"), warning)
    }

    func testADecoderThatFailsFailsTheChapterNotTheDownload() async throws {
        let responses = encrypted()
        responses.decoderStatus = { _ in nil }
        let (result, _) = try await readChapter(responses)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("glyph decoder"), message)
    }

    // MARK: A batch

    func testABatchRunsEveryChapterAndReportsEachOne() async throws {
        let seen = Collected<Int>()
        let responses = Responses()
        responses.read = { url in
            url.contains("1002") ? "{\"error\":\"locked\",\"reason\":\"purchase\",\"chars\":10,\"expected\":900}" : "{\"lines\":[\"Text \(url)\"]}"
        }
        let scripted = web(responses)
        let refs = (1...3).map(ref)
        let results = try await adapter().parseChapters(refs, book: bookInfo(refs), context: hardContext(scripted) { _ in seen.add(1) })
        XCTAssertEqual(results.map { content($0) != nil }, [true, false, true])
        if case .refused = results[1] {} else { XCTFail("chapter 2 should be refused") }
        XCTAssertEqual(seen.all.count, 3)
        // At most `maxPages` reader pages, all closed at the end.
        XCTAssertLessThanOrEqual(scripted.pages.count, fast.maxPages)
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testAPageThatFailsToLoadFailsOnlyItsChapter() async throws {
        let broken = hardURL(origin + "/chapter/77/1002/")
        let scripted = web(Responses()) { $0.loadFailures[broken.absoluteString] = WebEngineError.navigationFailed("net::ERR_FAILED") }
        let refs = (1...2).map(ref)
        let results = try await adapter().parseChapters(refs, book: bookInfo(refs), context: hardContext(scripted))
        XCTAssertNotNil(content(results[0]))
        guard case .failed(_, let message) = results[1] else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("ERR_FAILED"), message)
    }

    func testTheLoginIsSavedAgainWhenABatchEnds() async throws {
        let cookies = FakeCookies(["ywkey": "k2", "ywguid": "g2"])
        let secrets = InMemorySecretStore()
        _ = try await adapter(cookies).parseChapters([ref(1)], book: bookInfo([ref(1)]), context: hardContext(web(Responses()), secrets: secrets))
        XCTAssertTrue((secrets.string(for: SecretKey.source(.qdn, "sessionCookies")) ?? "").contains("k2"))
    }

    func testPacingKeepsLoadsApart() async throws {
        let now = Collected<Double>()
        now.add(0)
        let pacer = HardPacer(minGap: 0.2) { now.all.last ?? 0 }
        try await pacer.pace()
        let started = Date()
        now.add(0.05)
        try await pacer.pace()
        // The second load waited for the rest of the gap.
        let waited = Date().timeIntervalSince(started)
        XCTAssertGreaterThan(waited, 0.12)
        XCTAssertLessThan(waited, 0.5)
    }

    func testWorkersReachingThePacerTogetherLeaveOneGapApart() async {
        // The clock stands still: three workers arrive at once and are given turns one gap apart.
        let pacer = HardPacer(minGap: 2.5) { 100 }
        let first = await pacer.reserve()
        let second = await pacer.reserve()
        let third = await pacer.reserve()
        XCTAssertEqual([first, second, third], [0, 2.5, 5.0])

        // A worker that arrives later only waits for what is left of the last turn.
        let later = HardPacer(minGap: 2.5) { 100 }
        _ = await later.reserve()
        let clock = Collected<Double>()
        clock.add(101)
        let resting = HardPacer(minGap: 2.5) { clock.all.last ?? 0 }
        _ = await resting.reserve()
        clock.add(101.5)
        let left = await resting.reserve()
        XCTAssertEqual(left, 2.0, accuracy: 0.0001)
    }

    func testRealWorkersAreSpacedOutByTheGap() async throws {
        let pacer = HardPacer(minGap: 0.1)
        let times = Collected<Double>()
        try await withThrowingTaskGroup(of: Void.self) { group in
            for _ in 0..<3 {
                group.addTask {
                    try await pacer.pace()
                    times.add(ProcessInfo.processInfo.systemUptime)
                }
            }
            try await group.waitForAll()
        }
        let sorted = times.all.sorted()
        XCTAssertEqual(sorted.count, 3)
        print("WebKit-free pacer times: \(sorted.map { $0 - sorted[0] })")
        // The last worker waited about two gaps for its turn.
        XCTAssertGreaterThan(sorted[2] - sorted[0], 0.15)
    }

    func testChapterWorkersAreCappedWhateverThreadsSays() async throws {
        let scripted = web(Responses())
        let refs = (1...6).map(ref)
        _ = try await adapter().parseChapters(refs, book: bookInfo(refs), context: hardContext(scripted, settings: settings(threads: 16)))
        XCTAssertLessThanOrEqual(scripted.pages.count, fast.maxPages)
    }

    func testOverridesForQdnApplyToItsRun() async throws {
        var value = AppSettings()
        var override = SharedOverrides()
        override.threads = 1
        value.overrides["qdn"] = override
        let scripted = web(Responses())
        let refs = (1...3).map(ref)
        _ = try await adapter().parseChapters(refs, book: bookInfo(refs), context: hardContext(scripted, settings: value))
        XCTAssertEqual(scripted.pages.count, 1)
    }

    func testAnEmptyBatchOpensNothing() async throws {
        let scripted = web(Responses())
        let results = try await adapter().parseChapters([], book: bookInfo([]), context: hardContext(scripted))
        XCTAssertTrue(results.isEmpty)
        XCTAssertTrue(scripted.pages.isEmpty)
    }
}
