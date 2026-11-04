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
    /// The address a matching link is read (and kept in the queue and link cart) under. Sites that serve
    /// the same book from several hosts, such as a phone site next to the desktop one, answer with one of
    /// them, so a link found in the Browser tab downloads like any other. The default keeps the link.
    static func canonicalURL(_ url: URL) -> URL
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

    static func canonicalURL(_ url: URL) -> URL { url }

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

    /// Like `parseChapters`, but the rate-limit delay is between chapter starts across all tasks (one
    /// chapter begins per delay, whatever the threads): for sites no built-in scraper knows the limits of.
    func parseChaptersSpaced(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        let shared = context.shared(for: Self.id)
        let limit = max(1, shared.threads)
        let gate = ChapterGate()
        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var pending: [(offset: Int, element: ChapterRef)] = refs.enumerated().reversed()
        func start(_ item: (offset: Int, element: ChapterRef), in group: inout ThrowingTaskGroup<(Int, ChapterResult), Error>) {
            group.addTask {
                try await gate.wait(seconds: shared.chapterDelay())
                return try await self.chapterTask(item.offset, item.element, book: book, context: context, delay: nil)
            }
        }
        try await withThrowingTaskGroup(of: (Int, ChapterResult).self) { group in
            for _ in 0..<limit {
                guard let item = pending.popLast() else { break }
                start(item, in: &group)
            }
            while let (position, result) = try await group.next() {
                results[position] = result
                context.onChapter(result)
                if let item = pending.popLast() { start(item, in: &group) }
            }
        }
        return results.compactMap { $0 }
    }

    private func chapterTask(_ position: Int, _ ref: ChapterRef, book: BookInfo, context: SourceContext, delay: SharedSettings?) async throws -> (Int, ChapterResult) {
        if let delay { try await delay.sleepChapterDelay() }
        // A chapter that failed on the network is read again after a pause (5 s, then 15 s); the
        // end-of-book retry passes still follow for what fails here too.
        let waits: [UInt64] = [5, 15]
        var attempt = 0
        while true {
            let result: ChapterResult
            do {
                result = try await parseChapter(ref, book: book, context: context)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                result = .failed(ref, message: error.localizedDescription)
            }
            guard case .failed(_, let message) = result, attempt < waits.count, ChapterRetry.transient(message) else { return (position, result) }
            let wait = waits[attempt]
            attempt += 1
            context.logger.log(.warning, "Chapter \(ref.index): \(message.prefix(160)); retry \(attempt) of \(waits.count) in \(wait) s")
            try await Task.sleep(nanoseconds: wait * 1_000_000_000)
        }
    }
}

/// A failure the network may not repeat: a lost connection, a timeout, a busy server (429, 5xx).
enum ChapterRetry {
    static func transient(_ message: String) -> Bool {
        message.range(of: #"connection|timed? ?out|timeout|reset|refused|unreachable|broken pipe|\b(429|50[0234])\b"#,
                      options: [.regularExpression, .caseInsensitive]) != nil
    }
}

/// Hands out chapter start times one delay apart; each caller sleeps until its own.
actor ChapterGate {
    private var nextStart = Date.distantPast

    func reserve(seconds: Double) -> TimeInterval {
        let now = Date()
        let start = max(now, nextStart)
        nextStart = start.addingTimeInterval(seconds)
        return start.timeIntervalSince(now)
    }

    nonisolated func wait(seconds: Double) async throws {
        let wait = await reserve(seconds: seconds)
        if wait > 0 { try await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000)) }
    }
}

public extension URL {
    /// Lower-cased host without a leading `www.`.
    var bareHost: String {
        let host = (host ?? "").lowercased()
        return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }
}
