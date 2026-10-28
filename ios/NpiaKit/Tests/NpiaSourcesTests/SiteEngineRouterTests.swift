import XCTest
import NpiaCore
@testable import NpiaSources

final class SiteEngineRouterTests: XCTestCase {
    private let all: [SiteEngine] = [.native, .novelDownloader, .webtoepub]

    // MARK: - Which engines run

    func testANativeOwnerOnlyFollowsEnginesThatListTheSite() {
        // Nothing lists the site: only the built-in scraper runs.
        XCTAssertEqual(SiteEngineRouter.plan(order: all, nativeOwnsURL: true) { _ in false }, [.native])
        // WebToEpub lists it: it follows the built-in scraper.
        XCTAssertEqual(SiteEngineRouter.plan(order: all, nativeOwnsURL: true) { $0 == .webtoepub }, [.native, .webtoepub])
        XCTAssertEqual(SiteEngineRouter.plan(order: all, nativeOwnsURL: true) { _ in true }, all)
    }

    func testALinkWithoutANativeScraperTriesEveryJavaScriptEngine() {
        XCTAssertEqual(SiteEngineRouter.plan(order: all, nativeOwnsURL: false) { _ in false }, [.novelDownloader, .webtoepub])
    }

    func testTheUsersOrderIsKept() {
        XCTAssertEqual(
            SiteEngineRouter.plan(order: [.webtoepub, .native, .novelDownloader], nativeOwnsURL: true) { _ in true },
            [.webtoepub, .native, .novelDownloader]
        )
        XCTAssertEqual(
            SiteEngineRouter.plan(order: [.webtoepub, .native], nativeOwnsURL: false) { _ in false },
            [.webtoepub]
        )
    }

    func testWithTheBuiltInScrapersOffTheJavaScriptEnginesAlwaysRun() {
        // The user switched native off, so no scraper owns the link and nothing is filtered by the site lists.
        XCTAssertEqual(
            SiteEngineRouter.plan(order: [.novelDownloader, .webtoepub], nativeOwnsURL: true) { _ in false },
            [.novelDownloader, .webtoepub]
        )
    }

    func testRepeatedEnginesRunOnce() {
        XCTAssertEqual(
            SiteEngineRouter.plan(order: [.native, .native, .webtoepub], nativeOwnsURL: true) { _ in true },
            [.native, .webtoepub]
        )
    }

    // MARK: - Falling back

    func testTheFirstEngineWithChaptersWins() async throws {
        let script = Script([.native: .book(3)])
        let opened = try await script.run(all)
        XCTAssertEqual(opened.engine, .native)
        XCTAssertEqual(script.calls, [.native])
    }

    func testAnErrorOrNoChaptersMovesOnToTheNextEngine() async throws {
        let script = Script([
            .native: .fail(SourceError.parse("broken page")),
            .novelDownloader: .book(0),
            .webtoepub: .book(2),
        ])
        let opened = try await script.run(all)
        XCTAssertEqual(opened.engine, .webtoepub)
        XCTAssertEqual(opened.book.chapters.count, 2)
        XCTAssertEqual(script.calls, [.native, .novelDownloader, .webtoepub])
        XCTAssertTrue(script.lines.contains("novel-downloader rules found no chapters; trying WebToEpub parsers next."), script.lines.joined(separator: "\n"))
        XCTAssertTrue(script.lines.contains("Built-in scrapers: Could not read the page: broken page"), script.lines.joined(separator: "\n"))
    }

    func testABookWithoutChaptersIsKeptWhenNothingElseWorks() async throws {
        let script = Script([.native: .book(0), .novelDownloader: .fail(SourceError.parse("no")), .webtoepub: .fail(SourceError.parse("no"))])
        let opened = try await script.run(all)
        XCTAssertEqual(opened.engine, .native)
        XCTAssertTrue(opened.book.chapters.isEmpty)
        XCTAssertEqual(script.calls.count, 3)
    }

    func testTheFirstEmptyBookIsTheOneKept() async throws {
        let script = Script([.native: .book(0), .novelDownloader: .book(0)])
        let opened = try await script.run([.native, .novelDownloader])
        XCTAssertEqual(opened.engine, .native)
    }

    func testNoEnginesLeftIsAnError() async {
        let script = Script([:])
        do {
            _ = try await script.run([])
            XCTFail("should throw")
        } catch {
            XCTAssertEqual((error as? SourceError), .parse("No site engine could read this page."))
        }
        XCTAssertEqual(script.calls, [])
    }

    func testABuiltInScraperAbortSignalStopsTheFallback() async {
        for abort in [SourceError.blocked("human check"), SourceError.loginRequired(.kpage)] {
            let script = Script([.native: .fail(abort), .webtoepub: .book(1)])
            do {
                _ = try await script.run(all)
                XCTFail("should throw")
            } catch {
                XCTAssertEqual(error as? SourceError, abort)
            }
            XCTAssertEqual(script.calls, [.native], "nothing after the abort ran")
        }
    }

    func testAnotherEnginesBlockDoesNotStopTheFallback() async throws {
        let script = Script([.novelDownloader: .fail(SourceError.blocked("HTTP 403")), .webtoepub: .book(1)])
        let opened = try await script.run([.novelDownloader, .webtoepub])
        XCTAssertEqual(opened.engine, .webtoepub)
        XCTAssertEqual(script.calls, [.novelDownloader, .webtoepub])
    }

    func testCancellationStopsEverything() async {
        let script = Script([.native: .fail(CancellationError()), .webtoepub: .book(1)])
        do {
            _ = try await script.run(all)
            XCTFail("should throw")
        } catch {
            XCTAssertTrue(error is CancellationError)
        }
        XCTAssertEqual(script.calls, [.native])
    }

    func testRealFailuresOutrankNoMatchAnswers() async {
        let script = Script([
            .novelDownloader: .fail(EngineNoMatch(message: "no rule")),
            .webtoepub: .fail(SourceError.parse("HTTP 500")),
        ])
        do {
            _ = try await script.run([.novelDownloader, .webtoepub])
            XCTFail("should throw")
        } catch {
            XCTAssertEqual(error as? SourceError, .parse("HTTP 500"))
        }
        let onlyNoMatch = Script([.novelDownloader: .fail(EngineNoMatch(message: "no rule")), .webtoepub: .fail(EngineNoMatch(message: "no parser"))])
        do {
            _ = try await onlyNoMatch.run([.novelDownloader, .webtoepub])
            XCTFail("should throw")
        } catch {
            XCTAssertEqual((error as? EngineNoMatch)?.message, "no rule")
        }
    }

    // MARK: - The catalog

    func testNativeOwnersExcludeTheRuleEnginesAdapter() {
        // The rule engines' adapter claims sites the rules or parsers list, but it is not a built-in scraper.
        let url = URL(string: "https://www.sudugu.com/1001/")!
        XCTAssertEqual(SourceCatalog.adapterType(for: url)?.id, .reader)
        XCTAssertNil(SourceCatalog.nativeAdapterType(for: url))
        XCTAssertNil(SourceCatalog.nativeAdapterType(for: URL(string: "https://example.com/")!))
    }

    func testWebToEpubSitesAreSupportedLinks() {
        let url = URL(string: "https://www.fictionpress.com/s/1")!
        XCTAssertTrue(ReaderAdapter.matches(url))
        XCTAssertEqual(SourceCatalog.adapterType(for: url)?.id, .reader)
        XCTAssertFalse(ReaderAdapter.matches(URL(string: "ftp://www.fictionpress.com/s/1")!))
        XCTAssertFalse(ReaderAdapter.matches(URL(string: "https://unknown.example/book")!))
    }

    // MARK: - Which links can be tried

    func testAWebPageNoSiteListNamesIsTriedByTheScriptEngines() {
        // WebToEpub also recognises some sites by their page, so an unlisted page is still worth a try.
        let url = URL(string: "https://unlisted.example/book/1")!
        XCTAssertNil(SourceCatalog.adapterType(for: url))
        XCTAssertEqual(SiteEngineRouter.plan(for: url, engines: EngineSettings()), [.novelDownloader, .webtoepub])
        XCTAssertTrue(SiteEngineRouter.canTry(url, engines: EngineSettings()))
        XCTAssertFalse(SiteEngineRouter.canTry(URL(string: "ftp://unlisted.example/book/1")!, engines: EngineSettings()))
    }

    func testSwitchedOffEnginesDecideWhichLinksCanBeTried() {
        let nativeOnly = EngineSettings(disabled: [.novelDownloader, .webtoepub])
        // Only the script engines read this site, so with them off nothing can.
        let listed = URL(string: "https://www.fictionpress.com/s/1")!
        XCTAssertFalse(SiteEngineRouter.canTry(listed, engines: nativeOnly))
        XCTAssertFalse(SiteEngineRouter.canTry(URL(string: "https://unlisted.example/book/1")!, engines: nativeOnly))
        XCTAssertEqual(SiteEngineRouter.plan(for: listed, engines: EngineSettings(disabled: [.novelDownloader])), [.webtoepub])

        // A site a built-in scraper owns.
        let owned = URL(string: "https://xiyuwx.com/book/123/")!
        XCTAssertNotNil(SourceCatalog.nativeAdapterType(for: owned))
        XCTAssertEqual(SiteEngineRouter.plan(for: owned, engines: nativeOnly), [.native])
        XCTAssertEqual(SiteEngineRouter.plan(for: owned, engines: EngineSettings()).first, .native)
        // With the built-in scrapers off, the script engines try it like any other web page.
        XCTAssertEqual(SiteEngineRouter.plan(for: owned, engines: EngineSettings(disabled: [.native])), [.novelDownloader, .webtoepub])
    }

    func testALinkNoEngineCanTryIsUnsupported() async {
        let context = SourceContext(http: StubHTTP(), web: StubWeb(), secrets: InMemorySecretStore(), settings: AppSettings())
        do {
            _ = try await SiteEngineRouter.open(URL(string: "ftp://unlisted.example/book/1")!, context: context)
            XCTFail("should throw")
        } catch {
            guard case .unsupportedURL? = error as? SourceError else { return XCTFail("\(error)") }
        }
    }

    func testEverySiteEngineSwitchedOffIsAnError() async {
        var settings = AppSettings()
        settings.engines.disabled = SiteEngine.allCases  // the settings UI never allows this
        let context = SourceContext(http: StubHTTP(), web: StubWeb(), secrets: InMemorySecretStore(), settings: settings)
        do {
            _ = try await SiteEngineRouter.open(URL(string: "https://www.sudugu.com/1001/")!, context: context)
            XCTFail("should throw")
        } catch {
            XCTAssertEqual((error as? SourceError)?.errorDescription?.contains("switched off"), true)
        }
    }
}

// MARK: - Test doubles

private struct StubReaderAdapter: SourceAdapter {
    static let id: SourceID = .reader
    static let tier: SourceTier = .http
    init() {}
    static func matches(_ url: URL) -> Bool { false }
    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo { throw SourceError.notImplemented(Self.id) }
    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        .failed(ref, message: "stub")
    }
}

/// Scripted engines: what each returns, and what was asked in what order.
private final class Script: @unchecked Sendable {
    enum Outcome {
        case book(Int)
        case fail(Error)
    }

    private let outcomes: [SiteEngine: Outcome]
    private let lock = NSLock()
    private var asked: [SiteEngine] = []
    private var logged: [String] = []

    init(_ outcomes: [SiteEngine: Outcome]) {
        self.outcomes = outcomes
    }

    var calls: [SiteEngine] {
        lock.lock(); defer { lock.unlock() }
        return asked
    }

    var lines: [String] {
        lock.lock(); defer { lock.unlock() }
        return logged
    }

    func run(_ steps: [SiteEngine]) async throws -> SiteEngineRouter.Opened {
        try await SiteEngineRouter.run(steps: steps, log: { self.record(line: $0) }) { engine in
            try self.attempt(engine)
        }
    }

    private func record(line: String) {
        lock.lock(); defer { lock.unlock() }
        logged.append(line)
    }

    private func attempt(_ engine: SiteEngine) throws -> SiteEngineRouter.Opened {
        lock.lock()
        asked.append(engine)
        lock.unlock()
        switch outcomes[engine] ?? .fail(SourceError.parse("not scripted")) {
        case .fail(let error):
            throw error
        case .book(let count):
            let chapters = (0..<count).map { ChapterRef(id: "\($0)", index: $0 + 1, title: "Chapter \($0 + 1)") }
            let book = BookInfo(source: .reader, id: "b", url: URL(string: "https://book.test/")!, title: "Book", chapters: chapters)
            return SiteEngineRouter.Opened(adapter: StubReaderAdapter(), book: book, engine: engine)
        }
    }
}
