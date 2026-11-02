import Foundation
import NpiaCore

/// Waits and limits of the ntk scraper. The defaults are the desktop's; tests shrink them.
struct NtkTiming: Sendable {
    var loadTimeout: TimeInterval = 45
    var pageStatePolls = 30
    var chapterStatePolls = 20
    var poll: TimeInterval = 0.7
    var maxPages = 3
    /// Reloads of a chapter the site answered with "too many requests", each after a longer pause.
    var rateLimitRetries = 4
    var rateLimitPause: TimeInterval = 10
    /// How long one mirror host gets to serve a webtoon page before the next is tried.
    var mirrorTimeout: TimeInterval = 20
}

/// The site answered the reader's content request with "too many requests".
struct NtkRateLimited: Error {
    let message: String
}

/// When the site last rate-limited a page; every page waits until then before its next chapter.
actor NtkCooldown {
    private var until = Date.distantPast
    func remaining() -> TimeInterval { until.timeIntervalSinceNow }
    func extend(by seconds: TimeInterval) { until = max(until, Date().addingTimeInterval(seconds)) }
}

/// Which mirror hosts failed during one download. One mirror can reset every connection while another
/// serves the same file, so hosts that failed go last: a dead one costs a few short waits, not one per page.
actor NtkMirrors {
    private var failures: [String: Int] = [:]

    func ordered(_ urls: [String]) -> [String] {
        urls.enumerated()
            .sorted { (failures[Self.host($0.element)] ?? 0, $0.offset) < (failures[Self.host($1.element)] ?? 0, $1.offset) }
            .map(\.element)
    }

    func failed(_ url: String) {
        failures[Self.host(url), default: 0] += 1
    }

    private static func host(_ url: String) -> String {
        URL(string: url)?.host?.lowercased() ?? ""
    }
}

/// A once-then-give-up gate for the Cloudflare sign-in prompt, shared across the pool.
actor NtkPromptGate {
    static let shared = NtkPromptGate()
    private var seen = Set<String>()
    func once(_ key: String) -> Bool {
        if seen.contains(key) { seen.remove(key); return false }
        seen.insert(key)
        return true
    }
}

/// ntk: a free webtoon/novel aggregator on numbered mirror hosts, behind a Cloudflare / anti-bot gate.
/// The native side opens the sign-in browser so the user clears the check once; after that this reads the
/// book, the chapter list, and each chapter in the verified session. A novel chapter runs the site's own
/// ad-acknowledgement handshake and v3 decrypt in-page; a webtoon chapter takes its page list from the
/// reader's own request and downloads each page from whichever mirror host serves it.
/// Nothing is bought — the site has no VIP concept.
///
/// Port of `_ntk_*` (browser path) in the desktop scraper. This side navigates, paces and builds results;
/// the page logic is shared with Android in `hard-sources.js`.
public struct NtkAdapter: SourceAdapter {
    public static let id: SourceID = .ntk
    public static let tier: SourceTier = .heavyBrowser

    var timing: NtkTiming

    public init() {
        self.init(timing: NtkTiming())
    }

    init(timing: NtkTiming) {
        self.timing = timing
    }

    public static func matches(_ url: URL) -> Bool {
        NtkSite.matches(url)
    }

    /// The numbered mirror host keeps moving, so the site is recognised by its name pattern.
    public static func ownsSite(_ host: String) -> Bool {
        NtkSite.ownsSite(host)
    }

    /// A chapter link reads under the book-index URL so it downloads the whole book.
    public static func canonicalURL(_ url: URL) -> URL {
        matches(url) ? NtkSite.indexURL(url) : url
    }

    private func log(_ context: SourceContext, _ level: LogLevel, _ message: String) {
        context.logger.log(level, "[ntk] " + message)
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let novelID = NtkSite.novelID(url)
        if novelID.isEmpty { throw SourceError.unsupportedURL(url) }
        let kind = NtkSite.kind(url)
        let indexURL = NtkSite.indexURL(url)
        let page = try await HardSupport.openPage(
            context.web, userAgent: NtkSite.userAgent, width: NtkSite.viewportWidth, height: NtkSite.viewportHeight
        )
        do {
            let book = try await readBook(page, url: indexURL, novelID: novelID, kind: kind, context: context)
            await HardSupport.closeQuietly(page)
            return book
        } catch {
            await HardSupport.closeQuietly(page)
            throw error
        }
    }

    private func readBook(_ page: any WebPageDriving, url: URL, novelID: String, kind: String, context: SourceContext) async throws -> BookInfo {
        try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(context, .warning, $0) }
        let args = HardScripts.json(["novelId": novelID, "kind": kind])
        var ready = false
        for _ in 0..<timing.pageStatePolls {
            switch NtkPageState(try await page.hardObject("ntk.pageState(\(args))")).state {
            case "ready":
                ready = true
            case "verification":
                if await NtkPromptGate.shared.once("verify:\(novelID)") { throw SourceError.loginRequired(.ntk) }
                throw SourceError.blocked("the site's security check is still up; clear it in the Browser tab, then retry")
            case "blocked":
                throw SourceError.blocked("the site's firewall blocked the page; clear its security check in the Browser tab, then retry")
            default:
                break
            }
            if ready { break }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if !ready { throw SourceError.blocked("the book page did not render its chapter list") }

        let info = NtkBookInfo(try await page.hardObject("ntk.bookInfo(\(args))"))
        if info.blocked { throw SourceError.blocked("the book page was blocked by the site's security check") }

        let listArgs = HardScripts.json([["novelId": novelID, "kind": kind]])
        let listing = NtkChapterListResult(HardJSON(text: try await page.hardTask("ntk.chapterList", argsJSON: listArgs)))
        if !listing.ok || listing.chapters.isEmpty {
            throw SourceError.parse("no chapters were found for this book (\(listing.error.isEmpty ? "empty list" : listing.error))")
        }

        let chapters = listing.chapters.map { entry in
            ChapterRef(
                id: entry.episodeId.isEmpty ? entry.url : entry.episodeId, index: entry.number,
                title: entry.name.isEmpty ? "Chapter \(entry.number)" : entry.name, url: URL(string: entry.url)
            )
        }
        let coverURL = await pageCover(page, info.cover, context)
        let book = BookInfo(
            source: .ntk, id: novelID, url: url,
            title: info.title.isEmpty ? "\(kind) \(novelID)" : info.title, author: info.author.isEmpty ? nil : info.author,
            synopsis: info.synopsis.isEmpty ? nil : info.synopsis, coverURL: coverURL, tags: info.tags,
            language: "ko", chapters: chapters
        )
        log(context, .info, "Book: \(book.title) by \(book.author ?? "?") - \(chapters.count) \(kind) chapters")
        return book
    }

    /// The cover as a data: URL fetched by the page: its image host resets plain HTTP connections.
    /// Falls back to the address itself when the page cannot fetch it.
    private func pageCover(_ page: any WebPageDriving, _ cover: String, _ context: SourceContext) async -> URL? {
        guard !cover.isEmpty else { return nil }
        do {
            let args = HardScripts.json([["url": cover]])
            let result = HardJSON(text: try await page.hardTask("ntk.fetchBinary", argsJSON: args, maxPolls: 160))
            let data = result.string("data")
            if result.bool("ok"), !data.isEmpty {
                let declared = result.string("contentType").split(separator: ";").first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
                if let url = URL(string: "data:\(declared.isEmpty ? "image/jpeg" : declared);base64,\(data)") { return url }
            }
            log(context, .debug, "Cover fetch in the page failed (HTTP \(result.int("status")))")
        } catch {
            log(context, .debug, "Cover fetch in the page failed: \(error)")
        }
        return URL(string: cover)
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let run = NtkRun(context: context, timing: timing)
        do {
            let result = try await run.chapter(ref)
            await run.close()
            return result
        } catch {
            await run.close()
            throw error
        }
    }

    public func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        if refs.isEmpty { return [] }
        let run = NtkRun(context: context, timing: timing)
        let shared = context.shared(for: .ntk)
        do {
            let results = try await HardSupport.parseConcurrently(refs, limit: run.pageLimit, shared: shared, context: context) { ref in
                try await run.chapter(ref)
            }
            await run.close()
            return results
        } catch {
            await run.close()
            throw error
        }
    }
}

/// The pages one download uses.
final class NtkRun: @unchecked Sendable {
    private let context: SourceContext
    private let timing: NtkTiming
    private let pool: WebPagePool
    let pageLimit: Int

    init(context: SourceContext, timing: NtkTiming) {
        self.context = context
        self.timing = timing
        let limit = max(1, min(context.shared(for: .ntk).threads, timing.maxPages))
        self.pageLimit = limit
        self.pool = WebPagePool(engine: context.web, profile: WebProfile.external, size: limit) { page in
            await HardSupport.prepare(page, userAgent: NtkSite.userAgent, width: NtkSite.viewportWidth, height: NtkSite.viewportHeight)
        }
    }

    private let cooldown = NtkCooldown()
    private let mirrors = NtkMirrors()

    func chapter(_ ref: ChapterRef) async throws -> ChapterResult {
        var attempt = 0
        while true {
            let wait = await cooldown.remaining()
            if wait > 0 { try await HTTPSourceSupport.sleep(seconds: wait) }
            do {
                return try await pool.withPage { page in try await self.read(page, ref) }
            } catch let limited as NtkRateLimited {
                if attempt >= timing.rateLimitRetries { return .failed(ref, message: limited.message) }
                attempt += 1
                let pause = timing.rateLimitPause * Double(1 << (attempt - 1))
                await cooldown.extend(by: pause)
                context.logger.log(.info, "[ntk] Chapter \(ref.index): the site asked to slow down; trying again in \(Int(pause)) s")
            }
        }
    }

    func close() async {
        await pool.closeAll()
    }

    private func read(_ page: any WebPageDriving, _ ref: ChapterRef) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        let kind = NtkSite.kind(url)
        do {
            try await page.load(url, until: .domContentLoaded, timeout: timing.loadTimeout)
        } catch WebEngineError.timeout(_) {
            let landed = await page.currentURL()
            if let landed, NtkSite.novelID(landed) != NtkSite.novelID(url) {
                return .failed(ref, message: "page load timed out")
            }
        }

        var sawReady = false
        for _ in 0..<timing.chapterStatePolls {
            let state = NtkChapterState(try await page.hardObject("ntk.chapterState()"))
            if state.blocked {
                return .failed(ref, message: "the site's security check blocked this chapter; clear it in the Browser tab, then retry")
            }
            if state.ready { sawReady = true; break }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if !sawReady { return .failed(ref, message: "the chapter page did not finish loading") }

        let entry = kind == "webtoon" ? "ntk.webtoon" : "ntk.chapter"
        let args = HardScripts.json([["chapterUrl": url.absoluteString, "name": ref.title, "index": ref.index] as [String: Any]])
        let result = NtkChapterResult(HardJSON(text: try await page.hardTask(entry, argsJSON: args)))
        if !result.ok {
            if result.blocked {
                return .failed(ref, message: "the site's security check blocked this chapter; clear it in the Browser tab, then retry")
            }
            if result.stage == "rate-limit" {
                throw NtkRateLimited(message: result.error.isEmpty ? "too many requests" : result.error)
            }
            let reason = result.error.isEmpty ? (result.stage.isEmpty ? "the chapter could not be read" : result.stage) : result.error
            return .failed(ref, message: reason)
        }
        if result.contentHtml.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && result.images.isEmpty {
            return .failed(ref, message: "no chapter content was extracted")
        }
        return await buildResult(ref, url: url, result)
    }

    private func buildResult(_ ref: ChapterRef, url: URL, _ result: NtkChapterResult) async -> ChapterResult {
        if result.images.isEmpty {
            return .content(ChapterContent(ref: ref, html: result.contentHtml))
        }
        if context.settings.images.saveImageURLsOnly {
            let images = result.images.compactMap { image in URL(string: image.url).map { ImageRef(url: $0, filename: image.name) } }
            return .content(ChapterContent(ref: ref, html: result.contentHtml, images: images))
        }
        let images = await downloadImages(result.images, referer: NtkSite.origin(url), workers: context.shared(for: .ntk).imageWorkers)
        if images.isEmpty { return .failed(ref, message: "no mirror served any page of this chapter") }
        let kept = Set(images.map(\.filename))
        let missing = result.images.map(\.name).filter { !kept.contains($0) }
        if !missing.isEmpty {
            context.logger.log(.warning, "[ntk] Chapter \(ref.index): \(missing.count) page(s) could not be downloaded from any mirror and were left out")
        }
        return .content(ChapterContent(ref: ref, html: NtkSite.withoutPages(result.contentHtml, names: missing), images: images))
    }

    private func downloadImages(_ images: [NtkImage], referer: String, workers: Int) async -> [ImageRef] {
        let limit = min(8, max(1, workers))
        var results = [ImageRef?](repeating: nil, count: images.count)
        await withTaskGroup(of: (Int, ImageRef?).self) { group in
            var next = 0
            while next < min(limit, images.count) {
                let position = next
                group.addTask { (position, await self.imageRef(images[position], referer: referer)) }
                next += 1
            }
            while let (position, ref) = await group.next() {
                results[position] = ref
                if next < images.count {
                    let upcoming = next
                    group.addTask { (upcoming, await self.imageRef(images[upcoming], referer: referer)) }
                    next += 1
                }
            }
        }
        return results.compactMap { $0 }
    }

    private func imageRef(_ image: NtkImage, referer: String) async -> ImageRef? {
        guard let url = URL(string: image.url) else { return nil }
        if image.candidates.isEmpty {
            return ImageRef(url: url, filename: image.name, data: await fetchImage(image.url, url: url, referer: referer))
        }
        guard let data = await fetchMirrored(image.candidates, referer: referer) else { return nil }
        return ImageRef(url: url, filename: image.name, data: data)
    }

    /// The bytes from the first host that serves the page, each host tried once; nil when none does.
    private func fetchMirrored(_ urls: [String], referer: String) async -> Data? {
        for text in await mirrors.ordered(urls) {
            if Task.isCancelled { return nil }
            guard let url = URL(string: text) else { continue }
            let data = await withTaskGroup(of: Data?.self) { group -> Data? in
                group.addTask {
                    await self.fetchImage(text, url: url, referer: referer, timeout: self.timing.mirrorTimeout, quietStatuses: NtkSite.mirrorErrors)
                }
                group.addTask {
                    try? await HTTPSourceSupport.sleep(seconds: self.timing.mirrorTimeout)
                    return nil
                }
                let first = await group.next() ?? nil
                group.cancelAll()
                return first
            }
            if let data, NtkSite.isImage(data) { return data }
            await mirrors.failed(text)
        }
        return nil
    }

    private func fetchImage(_ text: String, url: URL, referer: String, timeout: TimeInterval = 0, quietStatuses: Set<Int> = []) async -> Data? {
        if text.lowercased().hasPrefix("data:") {
            guard let comma = text.range(of: "base64,") else { return nil }
            return Data(base64Encoded: String(text[comma.upperBound...]), options: .ignoreUnknownCharacters)
        }
        do {
            let response = try await context.http.send(HTTPRequest(url: url, headers: [
                "User-Agent": NtkSite.userAgent,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            ], timeout: timeout, quietStatuses: quietStatuses))
            return (200..<300).contains(response.status) && !response.body.isEmpty ? response.body : nil
        } catch {
            return nil
        }
    }
}
