import XCTest
import NpiaCore
@testable import NpiaSources

final class SourceCatalogTests: XCTestCase {
    func testEverySourceHasExactlyOneAdapter() {
        let ids = SourceCatalog.adapters.map { $0.id }
        XCTAssertEqual(Set(ids), Set(SourceID.allCases))
        XCTAssertEqual(ids.count, SourceID.allCases.count)
    }

    func testReaderIsCheckedLast() {
        XCTAssertEqual(SourceCatalog.adapters.last?.id, .reader)
    }

    func testUnknownURLThrows() {
        XCTAssertThrowsError(try SourceCatalog.adapter(for: URL(string: "https://example.com/x")!))
    }

    func testDefaultParseChaptersKeepsOrderAndCapturesFailures() async throws {
        var settings = AppSettings()
        settings.shared.threads = 3
        settings.shared.intervalMin = 0
        settings.shared.intervalMax = 0
        let context = SourceContext(http: StubHTTP(), web: StubWeb(), secrets: InMemorySecretStore(), settings: settings)
        let refs = (1...10).map { ChapterRef(id: "\($0)", index: $0, title: "Chapter \($0)") }
        let book = BookInfo(source: .reader, id: "b", url: URL(string: "https://example.com")!, title: "Book", chapters: refs)

        let results = try await FakeAdapter().parseChapters(refs, book: book, context: context)
        XCTAssertEqual(results.map(\.ref.index), Array(1...10))
        if case .failed(let ref, _) = results[6] {
            XCTAssertEqual(ref.index, 7)
        } else {
            XCTFail("chapter 7 should fail")
        }
    }
}

private struct FakeAdapter: SourceAdapter {
    static let id: SourceID = .reader
    static let tier: SourceTier = .http
    static func matches(_ url: URL) -> Bool { true }

    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        throw SourceError.notImplemented(Self.id)
    }

    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        try await Task.sleep(nanoseconds: UInt64.random(in: 1_000_000...5_000_000))
        if ref.index == 7 { throw SourceError.parse("bad page") }
        return .content(ChapterContent(ref: ref, html: "<p>\(ref.index)</p>"))
    }
}

struct StubHTTP: HTTPFetching {
    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        HTTPResponse(url: request.url, status: 200, headers: [:], body: Data())
    }
}

struct StubWeb: WebEngineProviding {
    func newPage(profile: String) async throws -> any WebPageDriving { throw WebEngineError.unavailable }
    func presentLogin(url: URL, profile: String, userAgentSuffix: String?) async throws { throw WebEngineError.unavailable }
}
