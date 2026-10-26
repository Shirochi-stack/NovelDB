import Foundation

/// Everything an adapter may use while scraping one job.
public struct SourceContext: Sendable {
    public var http: any HTTPFetching
    public var web: any WebEngineProviding
    public var secrets: any SecretStore
    public var settings: AppSettings
    public var logger: any JobLogging

    public init(http: any HTTPFetching, web: any WebEngineProviding, secrets: any SecretStore, settings: AppSettings, logger: any JobLogging = NullLogger()) {
        self.http = http
        self.web = web
        self.secrets = secrets
        self.settings = settings
        self.logger = logger
    }

    public func shared(for source: SourceID) -> SharedSettings {
        settings.effective(for: source)
    }
}

/// One source. Mirrors `_<source>_parse_book` / `_parse_chapter` / `_parse_chapter_batch`
/// in the desktop `external_scraper.py` (and `downloader_core.py` for npia).
public protocol SourceAdapter: Sendable {
    static var id: SourceID { get }
    static var tier: SourceTier { get }
    init()
    static func matches(_ url: URL) -> Bool
    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo
    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult
    /// Defaults to `parseChapter` with `threads` concurrent tasks; override for batch APIs.
    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult]
}

public extension SourceAdapter {
    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        let limit = max(1, context.shared(for: Self.id).threads)
        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var pending: [(offset: Int, element: ChapterRef)] = refs.enumerated().reversed()
        try await withThrowingTaskGroup(of: (Int, ChapterResult).self) { group in
            for _ in 0..<limit {
                guard let item = pending.popLast() else { break }
                group.addTask { try await self.chapterTask(item.offset, item.element, book: book, context: context) }
            }
            while let (position, result) = try await group.next() {
                results[position] = result
                if let item = pending.popLast() {
                    group.addTask { try await self.chapterTask(item.offset, item.element, book: book, context: context) }
                }
            }
        }
        return results.compactMap { $0 }
    }

    private func chapterTask(_ position: Int, _ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> (Int, ChapterResult) {
        do {
            return (position, try await parseChapter(ref, book: book, context: context))
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            return (position, .failed(ref, message: error.localizedDescription))
        }
    }
}

public extension URL {
    /// Lower-cased host without a leading `www.`.
    var bareHost: String {
        let host = (host ?? "").lowercased()
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }
}
