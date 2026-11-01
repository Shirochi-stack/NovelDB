import Foundation
import NpiaCore

/// Waits and limits of the mpia scraper. The defaults are the desktop's; tests shrink them.
struct MpiaTiming: Sendable {
    var loadTimeout: TimeInterval = 45
    var detailPolls = 20
    var contentPolls = 60
    var poll: TimeInterval = 0.7
    /// Pause between chapter-list pages, like the desktop `_mpia_wait_list_interval`.
    var listInterval: TimeInterval = 0.5
    var maxListPages = 1000
    var maxPages = 3
}

/// mpia: the mpia site's webnovels the signed-in account can read, through the site's own web reader in a
/// real browser engine. The book and chapter list come from the site's JSON API; the chapter text is drawn
/// by the site's wasm reader onto a canvas and rebuilt from the reader's own output. A chapter the account
/// cannot read is refused; nothing is ever purchased.
///
/// Port of `_mpia_*` in the desktop scraper. This side navigates, paces and builds results; the page logic
/// is shared with Android in `hard-sources.js`.
public struct MpiaAdapter: SourceAdapter {
    public static let id: SourceID = .mpia
    public static let tier: SourceTier = .heavyBrowser
    public static let sites: Set<String> = [MpiaSite.domain]

    var timing: MpiaTiming

    public init() {
        self.init(timing: MpiaTiming())
    }

    init(timing: MpiaTiming) {
        self.timing = timing
    }

    public static func matches(_ url: URL) -> Bool {
        MpiaSite.matches(url)
    }

    /// A viewer or legacy link reads under the book-detail URL so it downloads like any other.
    public static func canonicalURL(_ url: URL) -> URL {
        let novelID = MpiaSite.novelID(url)
        return novelID.isEmpty ? url : MpiaSite.bookURL(novelID)
    }

    private func log(_ context: SourceContext, _ level: LogLevel, _ message: String) {
        context.logger.log(level, "[mpia] " + message)
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let novelID = MpiaSite.novelID(url)
        if novelID.isEmpty { throw SourceError.unsupportedURL(url) }
        let page = try await HardSupport.openPage(
            context.web, userAgent: MpiaSite.userAgent, width: MpiaSite.viewportWidth, height: MpiaSite.viewportHeight
        )
        do {
            let book = try await readBook(page, novelID: novelID, context: context)
            await HardSupport.closeQuietly(page)
            return book
        } catch {
            await HardSupport.closeQuietly(page)
            throw error
        }
    }

    private func readBook(_ page: any WebPageDriving, novelID: String, context: SourceContext) async throws -> BookInfo {
        try await HardSupport.loadQuietly(page, MpiaSite.bookURL(novelID), timeout: timing.loadTimeout) { log(context, .warning, $0) }
        var detail: MpiaDetail?
        for _ in 0..<timing.detailPolls {
            let answer = MpiaDetail(try await page.hardObject("mpia.detail(\(HardScripts.literal(novelID)))"))
            if answer.ok { detail = answer; break }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        guard let info = detail else {
            throw SourceError.blocked("the mpia book page did not return its details; sign in or clear the site's security check in the Browser tab, then retry")
        }
        log(context, .info, info.login
            ? "Signed in: free chapters and the ones you own are included."
            : "Not signed in: only free chapters download. Sign in in Settings > Enter App.")

        var rows: [MpiaChapterRow] = []
        var seen = Set<String>()
        var pageNo = 1
        while pageNo <= timing.maxListPages {
            let listing = MpiaChapterList(try await page.hardObject("mpia.chapters(\(HardScripts.literal(novelID)), \(pageNo))"))
            if !listing.ok {
                if rows.isEmpty {
                    throw SourceError.parse("the mpia chapter list could not be read (\(listing.error.isEmpty ? "page \(pageNo)" : listing.error))")
                }
                log(context, .warning, "Stopped reading the chapter list at page \(pageNo): \(listing.error)")
                break
            }
            var added = 0
            for row in listing.list where !row.id.isEmpty && seen.insert(row.id).inserted {
                rows.append(row)
                added += 1
            }
            if added == 0 || listing.list.isEmpty || (listing.total > 0 && rows.count >= listing.total) { break }
            pageNo += 1
            try await HTTPSourceSupport.sleep(seconds: timing.listInterval)
        }
        if rows.isEmpty { throw SourceError.parse("no chapters were found for this mpia book") }

        let sorted = rows.sorted { a, b in
            a.order != b.order ? a.order < b.order : (Int64(a.id) ?? 0) < (Int64(b.id) ?? 0)
        }
        let chapters = sorted.enumerated().map { offset, row in
            ChapterRef(
                id: row.id, index: offset + 1,
                title: row.title.isEmpty ? "Chapter \(row.order)" : row.title, url: URL(string: row.url), isPaid: row.isPaid
            )
        }
        let book = BookInfo(
            source: .mpia, id: novelID, url: MpiaSite.bookURL(novelID),
            title: info.title.isEmpty ? "mpia \(novelID)" : info.title, author: info.author.isEmpty ? nil : info.author,
            synopsis: info.synopsis.isEmpty ? nil : info.synopsis, coverURL: URL(string: info.cover), tags: info.tags,
            status: info.finished ? "Completed" : "Ongoing", language: "ko", chapters: chapters
        )
        log(context, .info, "Book: \(book.title) by \(book.author ?? "?") - \(chapters.count) chapters")
        return book
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let run = MpiaRun(context: context, timing: timing)
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
        let run = MpiaRun(context: context, timing: timing)
        let shared = context.shared(for: .mpia)
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
final class MpiaRun: @unchecked Sendable {
    private let context: SourceContext
    private let timing: MpiaTiming
    private let pool: WebPagePool
    let pageLimit: Int

    init(context: SourceContext, timing: MpiaTiming) {
        self.context = context
        self.timing = timing
        let limit = max(1, min(context.shared(for: .mpia).threads, timing.maxPages))
        self.pageLimit = limit
        self.pool = WebPagePool(engine: context.web, profile: WebProfile.external, size: limit) { page in
            await HardSupport.prepare(page, userAgent: MpiaSite.userAgent, width: MpiaSite.viewportWidth, height: MpiaSite.viewportHeight)
        }
    }

    private func log(_ level: LogLevel, _ message: String) {
        context.logger.log(level, "[mpia] " + message)
    }

    func chapter(_ ref: ChapterRef) async throws -> ChapterResult {
        try await pool.withPage { page in try await self.read(page, ref) }
    }

    func close() async {
        await pool.closeAll()
    }

    private func read(_ page: any WebPageDriving, _ ref: ChapterRef) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        do {
            try await page.load(url, until: .domContentLoaded, timeout: timing.loadTimeout)
        } catch WebEngineError.timeout(_) {
            // A timeout can come after the viewer committed; go on only if this is the requested chapter.
            let landed = await page.currentURL()
            if let landed, MpiaSite.novelID(landed) != MpiaSite.novelID(url) {
                return .failed(ref, message: "page load timed out")
            }
        }

        var ready = false
        for _ in 0..<timing.contentPolls {
            let state = MpiaReaderState(try await page.hardObject("mpia.readerState()"))
            if state.denied {
                return .refused(ref, reason: "locked or not bought: this mpia chapter needs a purchase, rental, or sign-in")
            }
            if state.ready { ready = true; break }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if !ready { return .failed(ref, message: "the mpia reader did not finish drawing the chapter") }

        let args = HardScripts.json([["chapterIndex": ref.index]])
        let extract = MpiaExtract(HardJSON(text: try await page.hardTask("mpia.extract", argsJSON: args)))
        if extract.locked { return .refused(ref, reason: "locked or not bought (the mpia reader denied this chapter)") }
        if extract.contentHtml.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && extract.images.isEmpty {
            return .failed(ref, message: extract.error.isEmpty ? "no chapter content was extracted" : extract.error)
        }
        return await buildResult(ref, extract)
    }

    private func buildResult(_ ref: ChapterRef, _ extract: MpiaExtract) async -> ChapterResult {
        let images: [ImageRef]
        if context.settings.images.saveImageURLsOnly {
            images = extract.images.compactMap { image in URL(string: image.url).map { ImageRef(url: $0, filename: image.name) } }
        } else {
            images = await downloadImages(extract.images, referer: MpiaSite.wwwOrigin, workers: context.shared(for: .mpia).imageWorkers)
        }
        var named = ref
        let shown = extract.chapterName
        if !shown.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           ReaderBlocks.normalizeTitle(shown) != ReaderBlocks.normalizeTitle(ref.title) {
            named.title = shown
        }
        return .content(ChapterContent(ref: named, html: extract.contentHtml, images: images))
    }

    private func downloadImages(_ images: [MpiaImage], referer: String, workers: Int) async -> [ImageRef] {
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

    private func imageRef(_ image: MpiaImage, referer: String) async -> ImageRef? {
        guard let url = URL(string: image.url) else { return nil }
        return ImageRef(url: url, filename: image.name, data: await fetchImage(image.url, url: url, referer: referer))
    }

    private func fetchImage(_ text: String, url: URL, referer: String) async -> Data? {
        if text.lowercased().hasPrefix("data:") {
            guard let comma = text.range(of: "base64,") else { return nil }
            return Data(base64Encoded: String(text[comma.upperBound...]), options: .ignoreUnknownCharacters)
        }
        do {
            let response = try await context.http.send(HTTPRequest(url: url, headers: [
                "User-Agent": MpiaSite.userAgent,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            ]))
            return (200..<300).contains(response.status) && !response.body.isEmpty ? response.body : nil
        } catch {
            return nil
        }
    }
}
