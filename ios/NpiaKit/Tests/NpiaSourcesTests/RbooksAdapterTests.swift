import XCTest
import NpiaCore
@testable import NpiaSources

private let origin = RbooksSite.origin
private let api = RbooksSite.apiOrigin

private let fast = RbooksTiming(
    loadTimeout: 1, productPolls: 3, libraryPolls: 3, apiPolls: 3, discoverBudgetMillis: 100, discoverCalls: 6,
    apiRecoveries: 2, contentPolls: 8, stablePolls: 2, contentAttempts: 3, poll: 0.01, maxPages: 2
)

private func view(_ id: String) -> String {
    origin + "/books/" + id + "/view"
}

private func quoted(_ values: [String]) -> String {
    values.map { "\"\($0)\"" }.joined(separator: ",")
}

private func productJSON(links: [String] = [], seriesID: String = "100", title: String = "A Series", titles: String = "\"100\":\"Opening\"") -> String {
    "{\"title\":\"\(title)\",\"author\":\"An Author\",\"synopsis\":\"About it\",\"cover\":\"https://img.example/c.jpg\",\"publisher\":\"A Press\",\"tags\":[\"fantasy\"],"
        + "\"links\":[\(quoted(links))],\"titleById\":{\(titles)},\"seriesId\":\"\(seriesID)\",\"diagnostics\":[\"Rendered episode links: \(links.count)\"]}"
}

private func discovery(_ ids: [String], done: Bool = true, stalled: Bool = false, error: String = "", state: String? = nil) -> String {
    let progress = state ?? "{\"phase\":\"chain\",\"ids\":[\(quoted(ids))],\"total\":\(ids.count),\"nextId\":\"x\"}"
    let titles = ids.map { "\"\($0)\":\"Episode title \($0)\"" }.joined(separator: ",")
    return "{\"done\":\(done),\"stalled\":\(stalled),\"state\":\(progress),\"isSerial\":true,\"error\":\"\(error)\","
        + "\"diagnostics\":[\"Chained episode links: \(ids.count)\"],\"titleById\":{\(titles)},\"links\":[\(quoted(ids.map(view)))]}"
}

private let viewerReady = "{\"complete\":true,\"found\":true,\"ready\":true,\"length\":2400,\"refused\":false,\"url\":\"\"}"
private let viewerBlank = "{\"complete\":true,\"found\":false,\"ready\":false,\"length\":0,\"refused\":false}"

private final class Responses: @unchecked Sendable {
    var productReady = "true"
    var product = productJSON()
    var libraryProbe = "{\"product\":\"\",\"unavailable\":false,\"adult\":false,\"title\":\"\",\"viewer\":false}"
    var apiState = "{\"json\":true,\"title\":\"\",\"url\":\"\(api)/books/100\",\"challenge\":false}"
    var discover: @Sendable (String) -> String = { _ in discovery(["100", "101", "102"]) }
    var contentState: @Sendable (Int) -> String = { _ in viewerReady }
    var accessWall = "{\"wall\":false,\"offViewer\":false,\"refused\":false,\"hits\":0}"
    var extract: @Sendable (String) -> String = { _ in
        "{\"title\":\"Episode One\",\"content\":\"<p>First.</p><p>Second.</p>\",\"contentText\":\"First.\\nSecond.\",\"images\":[]}"
    }
    var ownedIds: @Sendable () -> String = { "{\"owned\":[\"200\"]}" }
    private let lock = NSLock()
    private var contentPolled = 0

    var contentCalls: Int { lock.lock(); defer { lock.unlock() }; return contentPolled }

    private func nextContentPoll() -> Int {
        lock.lock(); defer { lock.unlock() }
        contentPolled += 1
        return contentPolled - 1
    }

    func page(_ index: Int = 0) -> HardScriptedPage {
        HardScriptedPage(name: "page\(index)") { _, call in
            if call.hasPrefix("rbooks.productReady") { return self.productReady }
            if call.hasPrefix("rbooks.productInfo") { return self.product }
            if call.hasPrefix("rbooks.libraryProbe") { return self.libraryProbe }
            if call.hasPrefix("rbooks.apiState") { return self.apiState }
            if call.hasPrefix("rbooks.discover") { return self.discover(call) }
            if call.hasPrefix("rbooks.contentState") { return self.contentState(self.nextContentPoll()) }
            if call.hasPrefix("rbooks.accessWall") { return self.accessWall }
            if call.hasPrefix("rbooks.extract") { return self.extract(call) }
            if call.hasPrefix("rbooks.ownedIds") { return self.ownedIds() }
            return nil
        }
    }
}

private func adapter(_ cookies: FakeCookies? = FakeCookies(), timing: RbooksTiming = fast) -> RbooksAdapter {
    RbooksAdapter(timing: timing, cookies: cookies)
}

private func web(_ responses: Responses, setup: @escaping @Sendable (HardScriptedPage) -> Void = { _ in }) -> HardScriptedWeb {
    HardScriptedWeb { index in
        let page = responses.page(index)
        setup(page)
        return page
    }
}

private func book(_ chapters: [ChapterRef], id: String = "100", title: String = "A Series") -> BookInfo {
    BookInfo(source: .rbooks, id: id, url: URL(string: origin + "/books/" + id)!, title: title, chapters: chapters)
}

private let chapter1 = ChapterRef(id: "101", index: 1, title: "Episode 1", url: URL(string: view("101")))
private let signedIn = [RbooksSite.accessCookie: "at", RbooksSite.refreshCookie: "rt"]

private func episodes(_ count: Int) -> [ChapterRef] {
    (1...count).map { ChapterRef(id: String(100 + $0), index: $0, title: "Episode \($0)", url: URL(string: view(String(100 + $0)))) }
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

final class RbooksAdapterTests: XCTestCase {
    override func setUp() {
        SiteSessions.resetProcessState()
        HardPrompts.shared.reset()
    }

    private func parseBook(_ rbooks: RbooksAdapter, _ link: String, web: HardScriptedWeb, secrets: InMemorySecretStore = InMemorySecretStore(),
                           logger: CollectingLogger = CollectingLogger()) async throws -> BookInfo {
        try await rbooks.parseBook(URL(string: link)!, context: hardContext(web, secrets: secrets, logger: logger))
    }

    private func parseBook(_ link: String, web: HardScriptedWeb, secrets: InMemorySecretStore = InMemorySecretStore(),
                           logger: CollectingLogger = CollectingLogger()) async throws -> BookInfo {
        try await parseBook(adapter(), link, web: web, secrets: secrets, logger: logger)
    }

    private func parseChapter(_ responses: Responses, adapter rbooks: RbooksAdapter = adapter(), settings: AppSettings = AppSettings(),
                              http: any HTTPFetching = RecordingHTTP(), logger: CollectingLogger = CollectingLogger(),
                              setup: @escaping @Sendable (HardScriptedPage) -> Void = { _ in }) async throws -> (ChapterResult, HardScriptedWeb) {
        let scripted = web(responses, setup: setup)
        let result = try await rbooks.parseChapter(chapter1, book: book([chapter1]), context: hardContext(scripted, settings: settings, http: http, logger: logger))
        return (result, scripted)
    }

    // MARK: URLs

    func testRecognisesProductViewerAndLibraryUrls() {
        func matches(_ text: String) -> Bool { RbooksAdapter.matches(hardURL(text)) }
        XCTAssertTrue(matches(origin + "/books/6251000001"))
        XCTAssertTrue(matches(origin + "/books/6251000001/"))
        XCTAssertTrue(matches(origin + "/books/6251000001/view"))
        XCTAssertTrue(matches("https://www." + RbooksSite.domain + "/books/12/view?from=library"))
        XCTAssertTrue(matches(origin + "/library/books/8706175/"))
        XCTAssertFalse(matches(origin + "/category/books/100"))
        XCTAssertFalse(matches(origin + "/"))
        XCTAssertFalse(matches(origin + "/books/"))
        XCTAssertFalse(matches("https://example.com/books/1234567890/view"))
        XCTAssertEqual(RbooksSite.bookID(hardURL(origin + "/books/6251000001/view")), "6251000001")
        XCTAssertTrue(RbooksSite.isViewerURL(hardURL(origin + "/books/1/view")))
        XCTAssertFalse(RbooksSite.isViewerURL(hardURL(origin + "/books/1")))
        XCTAssertFalse(RbooksSite.isViewerURL(nil))
        XCTAssertTrue(RbooksSite.isAccountGate(hardURL(origin + "/account/login?state=x")))
        XCTAssertTrue(RbooksSite.isAccountGate(hardURL(origin + "/account/verify-adult")))
        XCTAssertTrue(RbooksSite.isAccountGate(hardURL(RbooksSite.accountOrigin + "/anything")))
        XCTAssertFalse(RbooksSite.isAccountGate(hardURL(origin + "/books/1")))
    }

    func testTheCatalogHandsRbooksLinksToTheRbooksAdapter() {
        for link in [origin + "/books/100", origin + "/books/100/view", origin + "/library/books/8706175/"] {
            XCTAssertTrue(SourceCatalog.adapterType(for: hardURL(link)) == RbooksAdapter.self, link)
            XCTAssertTrue(SiteEngineRouter.canTry(hardURL(link), engines: AppSettings().engines), link)
            // Links are read as they are; only qdn has a phone site to fold into the desktop one.
            XCTAssertEqual(SourceCatalog.canonicalURL(hardURL(link)).absoluteString, link)
        }
    }

    // MARK: Book

    func testReadsTheBookAndItsEpisodeListFromTheBookApi() async throws {
        let responses = Responses()
        responses.product = productJSON(links: [view("100")])
        responses.discover = { _ in discovery(["100", "101", "102"]) }
        let scripted = web(responses)
        let log = CollectingLogger()
        let result = try await parseBook(origin + "/books/100", web: scripted, logger: log)

        XCTAssertEqual(result.title, "A Series")
        XCTAssertEqual(result.author, "An Author")
        XCTAssertEqual(result.language, "ko")
        XCTAssertEqual(result.url.absoluteString, origin + "/books/100")
        XCTAssertEqual(result.chapters.map(\.id), ["100", "101", "102"])
        XCTAssertEqual(result.chapters.compactMap { $0.url?.absoluteString }, [view("100"), view("101"), view("102")])
        XCTAssertEqual(result.chapters.map(\.index), [1, 2, 3])
        // Titles from the API replace the ones read off the page (as on the desktop).
        XCTAssertEqual(result.chapters.map(\.title), ["Episode title 100", "Episode title 101", "Episode title 102"])
        // A product page and an API page, each read as a desktop browser, both closed.
        XCTAssertEqual(scripted.pages.count, 2)
        let product = scripted.pages[0]
        let apiPage = scripted.pages[1]
        XCTAssertEqual(product.loads.map(\.absoluteString), [origin + "/books/100"])
        XCTAssertEqual(apiPage.loads.map(\.absoluteString), [api + "/books/100"])
        XCTAssertEqual(product.userAgent, RbooksSite.userAgent)
        XCTAssertEqual(product.viewport?.0, 1280)
        XCTAssertEqual(product.viewport?.1, 900)
        XCTAssertTrue(scripted.pages.allSatisfy { $0.closed && $0.documentStartScripts.count == 1 })
        XCTAssertTrue(log.lines.contains { $0.contains("[rbooks] Book: A Series by An Author - 3 chapters") })
    }

    func testTheLongerOfTheRenderedListAndTheApiListIsKept() async throws {
        let responses = Responses()
        responses.product = productJSON(links: (1...5).map { view(String(100 + $0)) })
        responses.discover = { _ in discovery(["100", "101"]) }
        let result = try await parseBook(origin + "/books/100", web: web(responses))
        XCTAssertEqual(result.chapters.map(\.id), ["101", "102", "103", "104", "105"])
        // Titles from the API where it knew the episode, numbered ones elsewhere.
        XCTAssertEqual(result.chapters.map(\.title), ["Episode title 101", "Episode 2", "Episode 3", "Episode 4", "Episode 5"])
    }

    func testEpisodesWithoutATitleAreNumbered() async throws {
        let responses = Responses()
        responses.product = productJSON(links: [view("500"), view("501"), view("500")], titles: "")
        responses.apiState = "{\"json\":true}"
        responses.discover = { _ in discovery([], done: true) }
        let result = try await parseBook(origin + "/books/500", web: web(responses))
        XCTAssertEqual(result.chapters.map(\.id), ["500", "501"])
        XCTAssertEqual(result.chapters.map(\.title), ["Episode 1", "Episode 2"])
    }

    func testTheEpisodeApiIsWalkedInSlicesAndProgressIsLogged() async throws {
        let calls = Collected<Int>()
        let responses = Responses()
        responses.discover = { _ in
            calls.add(1)
            switch calls.all.count {
            case 1: return discovery(["100", "101"], done: false)
            case 2: return discovery(["100", "101", "102"], done: false)
            default: return discovery(["100", "101", "102", "103"])
            }
        }
        let log = CollectingLogger()
        let scripted = web(responses)
        let result = try await parseBook(origin + "/books/100", web: scripted, logger: log)
        XCTAssertEqual(result.chapters.count, 4)
        XCTAssertEqual(calls.all.count, 3)
        XCTAssertEqual(log.lines.filter { $0.contains("Episode list:") }.count, 2)
        // The API page is opened once for the whole walk.
        XCTAssertEqual(scripted.pages.filter { $0.loads.map(\.absoluteString) == [api + "/books/100"] }.count, 1)
    }

    func testTheDiscoveryCallCarriesTheStateTheScriptHandedBack() async throws {
        let seen = Collected<String>()
        let responses = Responses()
        responses.discover = { call in
            seen.add(call)
            return seen.all.count == 1 ? discovery(["100"], done: false) : discovery(["100", "101"])
        }
        _ = try await parseBook(origin + "/books/100", web: web(responses))
        XCTAssertEqual(seen.all.count, 2)
        XCTAssertFalse(seen.all[0].contains("\"state\""))
        XCTAssertTrue(seen.all[0].contains("\"seriesId\":\"100\""))
        XCTAssertTrue(seen.all[0].contains("\"budgetMs\":100"))
        XCTAssertTrue(seen.all[1].contains("\"state\":{"))
        XCTAssertTrue(seen.all[1].contains("\"phase\":\"chain\""))
    }

    func testAStalledApiIsReopenedWhereItStoppedThenGivenUpOnWithWhatWasFound() async throws {
        let responses = Responses()
        responses.discover = { call in
            if call.contains("\"giveUp\":true") { return discovery(["100", "101"]) }
            return discovery(["100", "101"], done: false, stalled: true, state: "{\"ids\":[\"100\",\"101\"],\"total\":9,\"nextId\":\"205\"}")
        }
        let log = CollectingLogger()
        let scripted = web(responses)
        let result = try await parseBook(origin + "/books/100", web: scripted, logger: log)
        XCTAssertEqual(result.chapters.map(\.id), ["100", "101"])
        // The API was reopened at the id it stopped at, twice, before giving up.
        let apiPages = scripted.pages.filter { $0.loads.first?.absoluteString.hasPrefix(api) == true }
        XCTAssertEqual(apiPages.count, 1)
        XCTAssertEqual(apiPages[0].loads.map(\.absoluteString), [api + "/books/100", api + "/books/205", api + "/books/205"])
        XCTAssertEqual(log.lines.filter { $0.contains("stopped answering") }.count, 2)
    }

    func testABookWithNoEpisodesIsAnError() async {
        let empty = Responses()
        empty.product = productJSON(seriesID: "")
        let none = await expectError { try await parseBook(origin + "/books/100", web: web(empty)) }
        guard case .parse(let message)? = none as? SourceError else { return XCTFail("expected a parse error, got \(String(describing: none))") }
        XCTAssertTrue(message.contains("no webnovel episodes"))

        let failed = Responses()
        failed.product = productJSON()
        failed.discover = { _ in discovery([], done: true, error: "book API did not return JSON") }
        let blocked = await expectError { try await parseBook(origin + "/books/100", web: web(failed)) }
        guard case .blocked(let why)? = blocked as? SourceError else { return XCTFail("expected blocked, got \(String(describing: blocked))") }
        XCTAssertTrue(why.contains("book API did not return JSON"))
    }

    func testASlowProductPageLoadIsOnlyAWarning() async throws {
        let log = CollectingLogger()
        let slow = hardURL(origin + "/books/100")
        let scripted = web(Responses()) { $0.loadFailures[slow.absoluteString] = WebEngineError.timeout(slow) }
        let result = try await parseBook(origin + "/books/100", web: scripted, logger: log)
        XCTAssertEqual(result.chapters.count, 3)
        XCTAssertTrue(log.lines.contains { $0.contains("Page load warning") })
    }

    // MARK: Sign-in

    func testALoginOrAdultCheckRedirectAsksForTheSignInBrowserOnceThenGivesUp() async {
        let rbooks = adapter()
        let gate = hardURL(origin + "/books/100")
        let scripted = web(Responses()) { page in
            page.redirect = { $0 == gate ? hardURL(origin + "/account/login?state=x") : $0 }
        }
        let first = await expectError { try await parseBook(rbooks, origin + "/books/100", web: scripted) }
        XCTAssertEqual(first as? SourceError, .loginRequired(.rbooks))
        let second = await expectError { try await parseBook(rbooks, origin + "/books/100", web: scripted) }
        guard case .blocked(let message)? = second as? SourceError else { return XCTFail("expected blocked, got \(String(describing: second))") }
        XCTAssertTrue(message.contains("Settings > Enter App"))
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testACloudflareCheckOnTheProductPageAsksForTheBrowser() async {
        let responses = Responses()
        responses.productReady = "false"
        let scripted = web(responses) { $0.titleText = "Just a moment..." }
        let error = await expectError { try await parseBook(origin + "/books/100", web: scripted) }
        XCTAssertEqual(error as? SourceError, .loginRequired(.rbooks))
    }

    func testACloudflareCheckOnTheBookApiAsksForTheBrowser() async {
        let responses = Responses()
        responses.apiState = "{\"json\":false,\"title\":\"Just a moment...\",\"url\":\"\(api)/books/100\",\"challenge\":true}"
        let error = await expectError { try await parseBook(origin + "/books/100", web: web(responses)) }
        XCTAssertEqual(error as? SourceError, .loginRequired(.rbooks))
    }

    func testAnApiThatNeverAnswersIsBlocked() async {
        let responses = Responses()
        responses.apiState = "{\"json\":false,\"title\":\"\",\"url\":\"\(api)/books/100\",\"challenge\":false}"
        let error = await expectError { try await parseBook(origin + "/books/100", web: web(responses)) }
        guard case .blocked(let message)? = error as? SourceError else { return XCTFail("expected blocked, got \(String(describing: error))") }
        XCTAssertTrue(message.contains("book API"))
    }

    // MARK: Library links

    func testALibraryLinkNeedsASignedInBrowser() async {
        let error = await expectError { try await parseBook(adapter(FakeCookies()), origin + "/library/books/8706175/", web: web(Responses())) }
        XCTAssertEqual(error as? SourceError, .loginRequired(.rbooks))
    }

    func testAnOwnedBooksReaderIsOneChapter() async throws {
        let responses = Responses()
        responses.libraryProbe = "{\"product\":\"\",\"unavailable\":false,\"adult\":false,\"title\":\"Owned Book\",\"viewer\":true}"
        let url = origin + "/library/books/8706175/"
        let result = try await parseBook(adapter(FakeCookies(signedIn)), url, web: web(responses))
        XCTAssertEqual(result.title, "Owned Book")
        XCTAssertEqual(result.chapters.count, 1)
        XCTAssertEqual(result.chapters.first?.url?.absoluteString, url)
        XCTAssertEqual(result.id, "8706175")
    }

    func testALibraryLinkThatPointsAtTheProductReadsTheProductBook() async throws {
        let responses = Responses()
        responses.libraryProbe = "{\"product\":\"\(origin)/books/6121000538\",\"unavailable\":true,\"adult\":true,\"title\":\"x\",\"viewer\":false}"
        responses.product = productJSON(seriesID: "6121000538")
        responses.discover = { _ in discovery(["6121000538", "6121000539"]) }
        let scripted = web(responses)
        let log = CollectingLogger()
        let result = try await parseBook(adapter(FakeCookies(signedIn)), origin + "/library/books/8706175/", web: scripted, logger: log)
        XCTAssertEqual(result.id, "6121000538")
        XCTAssertEqual(result.chapters.count, 2)
        XCTAssertEqual(scripted.pages.first?.loads.map(\.absoluteString), [origin + "/library/books/8706175/", origin + "/books/6121000538"])
        XCTAssertTrue(log.lines.contains { $0.contains("adult (19+)") })
    }

    func testALibraryPageWithNothingReadableIsAnError() async {
        let error = await expectError { try await parseBook(adapter(FakeCookies(signedIn)), origin + "/library/books/8706175/", web: web(Responses())) }
        guard case .parse(let message)? = error as? SourceError else { return XCTFail("expected a parse error, got \(String(describing: error))") }
        XCTAssertTrue(message.contains("no readable book"))
    }

    func testTheLoginIsSavedAfterABookIsRead() async throws {
        let secrets = InMemorySecretStore()
        _ = try await parseBook(adapter(FakeCookies(signedIn)), origin + "/books/100", web: web(Responses()), secrets: secrets)
        XCTAssertTrue((secrets.string(for: SecretKey.source(.rbooks, "sessionCookies")) ?? "").contains("at"))
    }

    // MARK: Chapters

    func testReadsAnEpisodeFromTheViewer() async throws {
        let (result, scripted) = try await parseChapter(Responses())
        let chapter = try XCTUnwrap(content(result))
        XCTAssertTrue(chapter.html.hasPrefix("<div class=\"rbooks-content\">"))
        XCTAssertTrue(chapter.html.contains("<p>First.</p>"))
        // The viewer's own title is used, the catalog's would otherwise be.
        XCTAssertEqual(chapter.ref.title, "Episode One")
        XCTAssertEqual(scripted.pages.first?.loads.map(\.absoluteString), [view("101")])
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testASeriesTitleIsNeverUsedAsAnEpisodeTitle() async throws {
        let responses = Responses()
        responses.extract = { _ in "{\"title\":\" a  series \",\"content\":\"<p>Text</p>\",\"contentText\":\"Text\",\"images\":[]}" }
        let scripted = web(responses)
        let result = try await adapter().parseChapter(chapter1, book: book([chapter1], title: "A Series"), context: hardContext(scripted))
        XCTAssertEqual(content(result)?.ref.title, "Episode 1")
    }

    func testTheViewerHasToKeepTheSameLengthBeforeItCounts() async throws {
        let responses = Responses()
        responses.contentState = { n in
            let length = n < 3 ? 600 + n * 100 : 1200
            return "{\"complete\":true,\"found\":true,\"ready\":true,\"length\":\(length),\"refused\":false}"
        }
        let (result, _) = try await parseChapter(responses)
        XCTAssertNotNil(content(result))
        // Growing for three polls, then the same length twice: five polls at least.
        XCTAssertGreaterThanOrEqual(responses.contentCalls, 5)
    }

    func testComicPagesAreDownloadedWithTheBookAsReferer() async throws {
        let png = Data([0x89, 0x50, 0x4E, 0x47, 1, 2, 3])
        let http = RecordingHTTP { request in
            request.url.absoluteString.hasSuffix("broken.jpg")
                ? HTTPResponse(url: request.url, status: 403, headers: [:], body: Data())
                : HTTPResponse(url: request.url, status: 200, headers: [:], body: png)
        }
        let responses = Responses()
        responses.extract = { _ in
            "{\"title\":\"T\",\"content\":\"<p><img src=\\\"images/0001_01.jpg\\\" /></p>\",\"contentText\":\"x\",\"images\":["
                + "{\"url\":\"https://img.example/p/1.jpg\",\"name\":\"images/0001_01.jpg\"},"
                + "{\"url\":\"https://img.example/p/broken.jpg\",\"name\":\"images/0001_02.jpg\"},"
                + "{\"url\":\"data:image/png;base64,iVBORw0KGgo=\",\"name\":\"images/0001_03.png\"}]}"
        }
        let (result, _) = try await parseChapter(responses, http: http)
        let images = try XCTUnwrap(content(result)).images
        XCTAssertEqual(images.map(\.filename), ["images/0001_01.jpg", "images/0001_02.jpg", "images/0001_03.png"])
        XCTAssertEqual(images[0].data, png)
        // A page that could not be fetched keeps its URL, so the image stage tries again.
        XCTAssertNil(images[1].data)
        XCTAssertEqual(images[1].url.absoluteString, "https://img.example/p/broken.jpg")
        XCTAssertEqual(images[2].data, Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
        let first = http.requests.first { $0.url.absoluteString.hasSuffix("1.jpg") }
        XCTAssertEqual(first?.headers["Referer"], origin + "/books/100")
        XCTAssertEqual(first?.headers["User-Agent"], RbooksSite.userAgent)
    }

    func testSavingImageUrlsOnlyDownloadsNothing() async throws {
        let http = RecordingHTTP()
        let responses = Responses()
        responses.extract = { _ in
            "{\"title\":\"T\",\"content\":\"<p>x</p>\",\"contentText\":\"x\",\"images\":[{\"url\":\"https://img.example/p/1.jpg\",\"name\":\"images/0001_01.jpg\"}]}"
        }
        var value = AppSettings()
        value.images.saveImageURLsOnly = true
        let (result, _) = try await parseChapter(responses, settings: value, http: http)
        let image = try XCTUnwrap(content(result)).images.first
        XCTAssertNil(image?.data)
        XCTAssertEqual(image?.url.absoluteString, "https://img.example/p/1.jpg")
        XCTAssertTrue(http.requests.isEmpty)
    }

    func testARedirectToTheLoginPageMeansSignedOut() async throws {
        let (result, _) = try await parseChapter(Responses()) { $0.redirect = { _ in hardURL(origin + "/account/login?state=x") } }
        guard case .refused(_, let reason) = result else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("Enter App"))
    }

    func testARedirectAwayFromTheViewerMeansNotBought() async throws {
        let (result, _) = try await parseChapter(Responses()) { $0.redirect = { _ in hardURL(origin + "/books/101") } }
        guard case .refused(_, let reason) = result else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("not bought"))
    }

    func testATimedOutLoadIsFineWhenTheViewerItselfCommitted() async throws {
        let viewer = hardURL(view("101"))
        let (result, _) = try await parseChapter(Responses()) { $0.loadFailures[viewer.absoluteString] = WebEngineError.timeout(viewer) }
        XCTAssertNotNil(content(result))
        // ... but not when the page is somewhere else.
        let (failed, _) = try await parseChapter(Responses()) { page in
            page.loadFailures[viewer.absoluteString] = WebEngineError.timeout(viewer)
            page.redirect = { _ in hardURL(origin + "/books/101") }
        }
        guard case .failed(_, let message) = failed else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("timed out"))
    }

    func testAPurchaseWallIsRefusedWithoutRetrying() async throws {
        let responses = Responses()
        responses.contentState = { _ in viewerBlank }
        responses.accessWall = "{\"wall\":true,\"offViewer\":false,\"refused\":false,\"hits\":3}"
        let (result, scripted) = try await parseChapter(responses)
        guard case .refused(_, let reason) = result else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("not bought"))
        XCTAssertEqual(scripted.pages.first?.loads.count, 1)
    }

    func testAViewerThatNeverDrawsIsReloadedThenFails() async throws {
        let responses = Responses()
        responses.contentState = { _ in viewerBlank }
        let log = CollectingLogger()
        let (result, scripted) = try await parseChapter(responses, logger: log)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("did not finish"))
        // One load, then a reload before each of the other two attempts.
        XCTAssertEqual(scripted.pages.first?.loads.count, 3)
        XCTAssertEqual(log.lines.filter { $0.contains("Content validation failed") }.count, 3)
    }

    func testAnEmptyExtractionIsAFailureUnlessAWallExplainsIt() async throws {
        let responses = Responses()
        responses.extract = { _ in "null" }
        let (result, _) = try await parseChapter(responses)
        guard case .failed(_, let message) = result else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("no chapter content"))
    }

    // MARK: Titles the viewer refuses

    private func refusing(_ owned: @escaping @Sendable () -> String) -> Responses {
        let responses = Responses()
        responses.contentState = { _ in "{\"complete\":true,\"found\":false,\"ready\":false,\"length\":0,\"refused\":true}" }
        responses.accessWall = "{\"wall\":true,\"offViewer\":false,\"refused\":true,\"hits\":0}"
        responses.ownedIds = owned
        return responses
    }

    func testARefusedTitleThatIsNotInTheLibraryHasNotBeenBought() async throws {
        let (result, scripted) = try await parseChapter(refusing { "{\"owned\":[\"999\"]}" }, adapter: adapter(FakeCookies(signedIn)))
        guard case .refused(_, let reason) = result else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("not in this account's library"))
        // The library was asked from its own page.
        XCTAssertTrue(scripted.pages.contains { $0.loads.map(\.absoluteString).contains(RbooksSite.libraryOrigin + "/") })
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testARefusedTitleTheAccountOwnsIsAppOnlyAndCannotBeExported() async throws {
        let (result, _) = try await parseChapter(refusing { "{\"owned\":[\"101\"]}" }, adapter: adapter(FakeCookies(signedIn)))
        guard case .refused(_, let reason) = result else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("owned") && reason.contains("cannot be exported on a phone"), reason)
    }

    func testAnUnreachableLibraryIsALoginProblemWhenSignedOutAndRetryableOtherwise() async throws {
        let (signedOut, _) = try await parseChapter(refusing { "{\"owned\":null}" }, adapter: adapter(FakeCookies()))
        guard case .refused(_, let reason) = signedOut else { return XCTFail("expected a refusal") }
        XCTAssertTrue(reason.contains("Enter App"))
        let (unsure, _) = try await parseChapter(refusing { "{\"owned\":null}" }, adapter: adapter(FakeCookies(signedIn)))
        guard case .failed(_, let message) = unsure else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("retry"))
    }

    func testTheLibraryIsAskedOnceForAWholeBatch() async throws {
        let asked = Collected<Int>()
        let responses = refusing {
            asked.add(1)
            return "{\"owned\":[\"102\"]}"
        }
        let refs = episodes(3)
        let results = try await adapter(FakeCookies(signedIn)).parseChapters(refs, book: book(refs), context: hardContext(web(responses)))
        XCTAssertEqual(results.filter { if case .refused = $0 { return true } else { return false } }.count, 3)
        XCTAssertEqual(asked.all.count, 1)
    }

    // MARK: Batches

    func testABatchRunsEveryEpisodeAndKeepsToTheMostPages() async throws {
        let seen = Collected<Int>()
        let scripted = web(Responses())
        let refs = episodes(5)
        let results = try await adapter().parseChapters(refs, book: book(refs), context: hardContext(scripted) { _ in seen.add(1) })
        XCTAssertEqual(results.filter { content($0) != nil }.count, 5)
        XCTAssertEqual(seen.all.count, 5)
        XCTAssertLessThanOrEqual(scripted.pages.count, fast.maxPages)
        XCTAssertTrue(scripted.pages.allSatisfy(\.closed))
    }

    func testTheLoginIsSavedAgainWhenABatchEnds() async throws {
        let secrets = InMemorySecretStore()
        _ = try await adapter(FakeCookies(signedIn)).parseChapters([chapter1], book: book([chapter1]), context: hardContext(web(Responses()), secrets: secrets))
        XCTAssertTrue((secrets.string(for: SecretKey.source(.rbooks, "sessionCookies")) ?? "").contains("rt"))
    }
}
