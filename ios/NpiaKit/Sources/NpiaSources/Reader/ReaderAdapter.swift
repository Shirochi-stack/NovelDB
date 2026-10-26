import Foundation
import NpiaCore

/// Generic sites driven by the desktop's `rules-lib.js` (novel-downloader rules).
///
/// The rules run in JavaScriptCore (`ReaderRuntime`) against a small DOM built from the fetched
/// page, the way `external_scraper.py` runs them in a browser page through `bridge.js`. A book
/// whose page the rules cannot read without the site's own scripts falls back to a web view
/// (`ReaderWebFallback`).
public struct ReaderAdapter: SourceAdapter {
    public static let id: SourceID = .reader
    public static let tier: SourceTier = .http

    public init() {}

    /// Asks the rules bundle whether a rule exists for the URL's host.
    public static func matches(_ url: URL) -> Bool {
        ReaderRules.shared.supports(url)
    }

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        try await ReaderSessions.shared.open(url, context: context).book
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let session = try await ReaderSessions.shared.session(for: book, context: context)
        return try await session.chapter(ref)
    }
}

// MARK: - Rule lookup

/// A shared runtime that only answers "is there a rule for this host?" and converts HTML to
/// blocks. It never touches the network.
final class ReaderRules: @unchecked Sendable {
    static let shared = ReaderRules()

    private let lock = NSLock()
    private var runtime: ReaderRuntime?
    private var unavailable = false
    private var hosts: [String: Bool] = [:]

    func supports(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host?.lowercased(), !host.isEmpty
        else { return false }
        lock.lock()
        defer { lock.unlock() }
        if let known = hosts[host] { return known }
        guard let runtime = loadedRuntime() else { return false }
        let supported = (try? runtime.callSync("hasRule", [url.absoluteString])) == "true"
        hosts[host] = supported
        return supported
    }

    /// Splits chapter HTML into text and image blocks (`_reader_html_blocks`).
    func blocks(html: String, baseURL: URL, attachments: [String: String] = [:]) throws -> [ReaderBlock] {
        lock.lock()
        defer { lock.unlock() }
        guard let runtime = loadedRuntime() else { throw ReaderRuntime.RuntimeError.loadFailed("rules unavailable") }
        let json = try runtime.callSync("htmlBlocks", [html, baseURL.absoluteString, attachments])
        return try ReaderBlock.decodeList(json)
    }

    /// Lock held.
    private func loadedRuntime() -> ReaderRuntime? {
        if let runtime { return runtime }
        if unavailable { return nil }
        do {
            runtime = try ReaderRuntime(http: OfflineHTTP())
        } catch {
            unavailable = true
        }
        return runtime
    }

    private struct OfflineHTTP: HTTPFetching {
        func send(_ request: HTTPRequest) async throws -> HTTPResponse {
            throw URLError(.notConnectedToInternet)
        }
    }
}

// MARK: - Sessions

/// Open books. Rule modules keep the book page they loaded with, so each book keeps its own
/// runtime for its chapters.
actor ReaderSessions {
    static let shared = ReaderSessions()

    private var sessions: [String: ReaderBookSession] = [:]
    private var recent: [ReaderBookSession] = []
    private var opening: [String: Task<ReaderBookSession, Error>] = [:]
    private let limit = 2

    func open(_ url: URL, context: SourceContext) async throws -> ReaderBookSession {
        let key = url.absoluteString
        let task = Task { try await ReaderBookSession.open(url, context: context) }
        opening[key] = task
        defer { if opening[key] == task { opening[key] = nil } }
        let session = try await task.value
        store(session, keys: [key, session.book.url.absoluteString])
        return session
    }

    func session(for book: BookInfo, context: SourceContext) async throws -> ReaderBookSession {
        let key = book.url.absoluteString
        if let session = sessions[key] { return session }
        if let task = opening[key] { return try await task.value }
        return try await open(book.url, context: context)
    }

    private func store(_ session: ReaderBookSession, keys: [String]) {
        for key in Set(keys) {
            if let old = sessions[key], old !== session { retire(old) }
            sessions[key] = session
        }
        recent.removeAll { $0 === session }
        recent.append(session)
        while recent.count > limit { retire(recent[0]) }
    }

    private func retire(_ session: ReaderBookSession) {
        sessions = sessions.filter { $0.value !== session }
        recent.removeAll { $0 === session }
        session.close()
    }
}

/// One opened book: its metadata, the rule's chapter list, and the engine that parsed it.
final class ReaderBookSession: @unchecked Sendable {
    enum Engine {
        case script(ReaderRuntime)
        case web(ReaderWebFallback)
    }

    struct ChapterInfo {
        var url: String
        var name: String
        var isVIP: Bool
        var isPaid: Bool?
    }

    let book: BookInfo
    private let engine: Engine
    private let chapters: [String: ChapterInfo]

    init(book: BookInfo, engine: Engine, chapters: [String: ChapterInfo]) {
        self.book = book
        self.engine = engine
        self.chapters = chapters
    }

    static func open(_ url: URL, context: SourceContext) async throws -> ReaderBookSession {
        let runtime = try ReaderRuntime(http: context.http, logger: context.logger)
        let scriptError: Error
        do {
            let json = try await runtime.call("openBook", [url.absoluteString, NSNull(), NSNull(), [String: Any]()], timeout: 900)
            let payload = try ReaderBookPayload.decode(json)
            if !payload.chapters.isEmpty {
                return try make(payload, requested: url, engine: .script(runtime))
            }
            scriptError = SourceError.parse("The reader rules found no chapters on \(url.absoluteString).")
        } catch is CancellationError {
            runtime.close()
            throw CancellationError()
        } catch {
            scriptError = error
        }
        runtime.close()
        context.logger.log(.info, "[Reader] Script engine failed (\(scriptError.localizedDescription)); trying a web view.")
        do {
            let (payload, web) = try await ReaderWebFallback.openBook(url, context: context)
            if !payload.chapters.isEmpty {
                return try make(payload, requested: url, engine: .web(web))
            }
            await web.close()
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            context.logger.log(.debug, "[Reader] Web view fallback failed: \(error.localizedDescription)")
        }
        throw Self.sourceError(scriptError)
    }

    static func make(_ payload: ReaderBookPayload, requested: URL, engine: Engine) throws -> ReaderBookSession {
        let pageURL = payload.pageUrl.flatMap { URL(string: $0) } ?? requested
        let bookURL = payload.bookUrl.flatMap { URL(string: $0, relativeTo: pageURL)?.absoluteURL } ?? requested
        var refs: [ChapterRef] = []
        var infos: [String: ChapterInfo] = [:]
        for (offset, chapter) in payload.chapters.enumerated() {
            let index = offset + 1
            let raw = chapter.url ?? ""
            var id = raw.isEmpty ? "\(index)" : raw
            if infos[id] != nil { id += "#\(index)" }
            let name = chapter.name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let title = name.isEmpty ? "Chapter \(index)" : name
            refs.append(ChapterRef(
                id: id,
                index: index,
                title: title,
                url: raw.isEmpty ? nil : URL(string: raw, relativeTo: pageURL)?.absoluteURL,
                isPaid: chapter.isVIP ?? false
            ))
            infos[id] = ChapterInfo(url: raw, name: name, isVIP: chapter.isVIP ?? false, isPaid: chapter.isPaid)
        }
        let book = BookInfo(
            source: .reader,
            id: bookURL.absoluteString,
            url: bookURL,
            title: payload.bookname?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty ?? bookURL.absoluteString,
            author: payload.author?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty,
            synopsis: payload.introduction?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty,
            coverURL: payload.coverUrl.flatMap { URL(string: $0, relativeTo: pageURL)?.absoluteURL },
            tags: payload.tags ?? [],
            language: payload.language?.nonEmpty ?? "zh",
            chapters: refs
        )
        return ReaderBookSession(book: book, engine: engine, chapters: infos)
    }

    func chapter(_ ref: ChapterRef) async throws -> ChapterResult {
        guard let info = chapters[ref.id] else {
            return .failed(ref, message: "Chapter \(ref.index) is not in the book's chapter list.")
        }
        let blocks: [ReaderBlock]
        do {
            switch engine {
            case .script(let runtime):
                let isPaid: Any = info.isPaid.map { $0 as Any } ?? NSNull()
                let json = try await runtime.call("parseChapter", [info.url, info.name, info.isVIP, isPaid], timeout: 300)
                blocks = try ReaderChapterPayload.decode(json).blocks
            case .web(let web):
                blocks = try await web.chapter(info)
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            if ref.isPaid {
                return .refused(ref, reason: "The site did not return this paid chapter for the signed-in account.")
            }
            throw Self.sourceError(error)
        }
        if let content = ReaderChapterBuilder.content(ref, blocks: blocks) {
            return .content(content)
        }
        if ref.isPaid {
            return .refused(ref, reason: "The site did not return this paid chapter for the signed-in account.")
        }
        return .failed(ref, message: "The chapter page had no text.")
    }

    func close() {
        switch engine {
        case .script(let runtime): runtime.close()
        case .web(let web): Task { await web.close() }
        }
    }

    static func sourceError(_ error: Error) -> Error {
        if error is SourceError { return error }
        let message = error.localizedDescription
        if message.hasPrefix("HTTP 401") || message.hasPrefix("HTTP 403") || message.hasPrefix("HTTP 429") {
            return SourceError.blocked(message)
        }
        return SourceError.parse(message)
    }
}

// MARK: - Payloads

/// `__ND_parseBook()` output from `bridge.js`, plus `pageUrl`/`charset` from `reader_host.js`.
struct ReaderBookPayload: Decodable {
    struct Chapter: Decodable {
        var url: String?
        var name: String?
        var number: Double?
        var sectionName: String?
        var isVIP: Bool?
        var isPaid: Bool?
    }

    var bookUrl: String?
    var bookname: String?
    var author: String?
    var introduction: String?
    var coverUrl: String?
    var tags: [String]?
    var language: String?
    var pageUrl: String?
    var chapters: [Chapter]

    static func decode(_ json: String) throws -> ReaderBookPayload {
        do {
            return try JSONDecoder().decode(ReaderBookPayload.self, from: Data(json.utf8))
        } catch {
            throw SourceError.parse("Unexpected book data from the reader rules: \(error.localizedDescription)")
        }
    }
}

struct ReaderChapterPayload: Decodable {
    var chapterName: String?
    var blocks: [ReaderBlock]

    static func decode(_ json: String) throws -> ReaderChapterPayload {
        try JSONDecoder().decode(ReaderChapterPayload.self, from: Data(json.utf8))
    }
}

/// One `('text'|'img', value)` pair from `reader_host.js`.
enum ReaderBlock: Decodable, Equatable {
    case text(String)
    case image(URL)

    init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        let kind = try container.decode(String.self)
        let value = try container.decode(String.self)
        if kind == "img", let url = URL(string: value) {
            self = .image(url)
        } else {
            self = .text(value)
        }
    }

    static func decodeList(_ json: String) throws -> [ReaderBlock] {
        try JSONDecoder().decode([ReaderBlock].self, from: Data(json.utf8))
    }
}

// MARK: - Chapter HTML

/// Builds chapter XHTML the way `_reader_chapter_result` does on the desktop.
enum ReaderChapterBuilder {
    static let cssClass = "reader-content"

    static func content(_ ref: ChapterRef, blocks: [ReaderBlock]) -> ChapterContent? {
        var blocks = blocks
        if case .text(let first)? = blocks.first, normalizedTitle(first) == normalizedTitle(ref.title) {
            blocks.removeFirst()
        }
        var parts: [String] = []
        var images: [ImageRef] = []
        var filenames: [URL: String] = [:]
        var hasText = false
        for block in blocks {
            switch block {
            case .text(let text):
                hasText = true
                parts.append("<p>\(escape(text))</p>")
            case .image(let url):
                let filename: String
                if let known = filenames[url] {
                    filename = known
                } else {
                    filename = imageFilename(url, chapter: ref.index, number: images.count + 1)
                    filenames[url] = filename
                    images.append(ImageRef(url: url, filename: filename))
                }
                parts.append("<p class=\"img\"><img src=\"\(escape(filename))\" alt=\"\"/></p>")
            }
        }
        guard hasText || !images.isEmpty else { return nil }
        let html = "<div class=\"\(cssClass)\">\n" + parts.joined(separator: "\n") + "\n</div>"
        return ChapterContent(ref: ref, html: html, images: images)
    }

    /// `_normalize_title_for_match`: NFKC, no whitespace, case-folded.
    static func normalizedTitle(_ value: String) -> String {
        value.precomposedStringWithCompatibilityMapping
            .components(separatedBy: .whitespacesAndNewlines).joined()
            .folding(options: [.caseInsensitive], locale: nil)
    }

    static func imageFilename(_ url: URL, chapter: Int, number: Int) -> String {
        let ext = url.pathExtension.lowercased()
        let known = ["jpg", "jpeg", "png", "gif", "webp", "avif", "bmp"].contains(ext) ? ext : "jpg"
        return String(format: "images/%04d_%02d.%@", chapter, number, known)
    }

    static func escape(_ text: String) -> String {
        var out = ""
        out.reserveCapacity(text.count)
        for character in text {
            switch character {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            default: out.append(character)
            }
        }
        return out
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
