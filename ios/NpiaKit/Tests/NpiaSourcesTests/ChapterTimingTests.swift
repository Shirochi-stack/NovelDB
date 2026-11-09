import Foundation
import XCTest
import NpiaCore
@testable import NpiaSources

private struct TimedAdapter: SourceAdapter {
    static let id: SourceID = .reader
    static let tier: SourceTier = .http
    init() {}
    static func matches(_ url: URL) -> Bool { true }
    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        BookInfo(source: .reader, id: "test", url: url, title: "Test")
    }
    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        try await Task.sleep(nanoseconds: ref.index == 1 ? 200_000_000 : 10_000_000)
        return .content(ChapterContent(ref: ref, html: "<p>Text</p>"))
    }
}

private final class TimingRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: TimeInterval] = [:]
    private var completions: [String] = []
    func timing(_ ref: ChapterRef, _ seconds: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        values[ref.id] = seconds
    }
    func completed(_ result: ChapterResult) {
        lock.lock(); defer { lock.unlock() }
        completions.append(result.ref.id)
    }
    var snapshot: ([String: TimeInterval], [String]) {
        lock.lock(); defer { lock.unlock() }
        return (values, completions)
    }
}

final class ChapterTimingTests: XCTestCase {
    func testConcurrentChapterClocksAndOptionalSetting() async throws {
        for enabled in [false, true] {
            var settings = AppSettings()
            settings.shared.chapterTiming = enabled
            settings.shared.threads = 2
            settings.shared.intervalMin = 0
            settings.shared.intervalMax = 0
            var context = makeContext(RoutedHTTP(), settings: settings)
            let recorder = TimingRecorder()
            context.onChapterTiming = { ref, seconds in recorder.timing(ref, seconds) }
            context.onChapter = { result in recorder.completed(result) }
            let refs = [ChapterRef(id: "slow", index: 1, title: "Slow"),
                        ChapterRef(id: "fast", index: 2, title: "Fast")]
            let book = BookInfo(source: .reader, id: "test", url: URL(string: "https://example.com/book")!, title: "Test")
            let results = try await TimedAdapter().parseChapters(refs, book: book, context: context)
            XCTAssertEqual(results.map { $0.ref.id }, ["slow", "fast"])
            let (timings, completions) = recorder.snapshot
            XCTAssertEqual(completions, ["fast", "slow"])
            if enabled {
                XCTAssertEqual(Set(timings.keys), ["slow", "fast"])
                XCTAssertGreaterThan(timings["slow"] ?? 0, 0.19)
                XCTAssertGreaterThan(timings["fast"] ?? 0, 0)
                XCTAssertGreaterThan(timings["slow"] ?? 0, timings["fast"] ?? 0)
            } else {
                XCTAssertTrue(timings.isEmpty)
            }
        }
    }

    func testHardAdapterHelperAlsoReportsTimings() async throws {
        var settings = AppSettings()
        settings.shared.chapterTiming = true
        settings.shared.intervalMin = 0
        settings.shared.intervalMax = 0
        var context = makeContext(RoutedHTTP(), settings: settings)
        let recorder = TimingRecorder()
        context.onChapterTiming = { ref, seconds in recorder.timing(ref, seconds) }
        context.onChapter = { result in recorder.completed(result) }
        let ref = ChapterRef(id: "1", index: 1, title: "Test")
        _ = try await HardSupport.parseConcurrently([ref], limit: 1, shared: settings.shared, context: context) { ref in
            .refused(ref, reason: "Test refusal")
        }
        XCTAssertNotNil(recorder.snapshot.0["1"])
        XCTAssertEqual(recorder.snapshot.1, ["1"])
    }
}
