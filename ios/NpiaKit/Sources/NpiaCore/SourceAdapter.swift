import Foundation

/// Everything an adapter may use while scraping one job.
public struct SourceContext: Sendable {
    public var http: any HTTPFetching
    public var web: any WebEngineProviding
    public var secrets: any SecretStore
    public var settings: AppSettings
    public var logger: any JobLogging
    /// Called once per chapter as soon as its result is known, from any task. Adapters that
    /// override `parseChapters` must call it too, or progress jumps from 0 to done at the end.
    public var onChapter: @Sendable (ChapterResult) -> Void

    public init(http: any HTTPFetching, web: any WebEngineProviding, secrets: any SecretStore, settings: AppSettings,
                logger: any JobLogging = NullLogger(), onChapter: @escaping @Sendable (ChapterResult) -> Void = { _ in }) {
        self.http = http
        self.web = web
        self.secrets = secrets
        self.settings = settings
        self.logger = logger
        self.onChapter = onChapter
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
    /// Hosts of the sites this adapter downloads from, as a person browses them (no API or image
    /// hosts), lower case. A host covers its subdomains. Empty for `reader` (it takes any page the
    /// script engines list) and for sources that are not ported yet.
    static var sites: Set<String> { get }
    init()
    static func matches(_ url: URL) -> Bool
    /// True when `host` (lower case, no port) belongs to one of this adapter's sites. Adapters whose
    /// domain keeps moving override it with a name pattern.
    static func ownsSite(_ host: String) -> Bool
    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo
    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult
    /// Defaults to `parseChapter` with `threads` concurrent tasks; override for batch APIs.
    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult]
}

public extension SourceAdapter {
    static var sites: Set<String> { [] }

    static func ownsSite(_ host: String) -> Bool {
        sites.contains { host == $0 || host.hasSuffix("." + $0) }
    }

    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        let shared = context.shared(for: Self.id)
        let limit = max(1, shared.threads)
        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var pending: [(offset: Int, element: ChapterRef)] = refs.enumerated().reversed()
        try await withThrowingTaskGroup(of: (Int, ChapterResult).self) { group in
            for _ in 0..<limit {
                guard let item = pending.popLast() else { break }
                group.addTask { try await self.chapterTask(item.offset, item.element, book: book, context: context, delay: nil) }
            }
            while let (position, result) = try await group.next() {
                results[position] = result
                context.onChapter(result)
                if let item = pending.popLast() {
                    // This worker slot rests one chapter delay before its next chapter.
                    group.addTask { try await self.chapterTask(item.offset, item.element, book: book, context: context, delay: shared) }
                }
            }
        }
        return results.compactMap { $0 }
    }

    private func chapterTask(_ position: Int, _ ref: ChapterRef, book: BookInfo, context: SourceContext, delay: SharedSettings?) async throws -> (Int, ChapterResult) {
        if let delay { try await delay.sleepChapterDelay() }
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
