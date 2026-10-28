import Foundation
import NpiaCore

/// Waits and limits of the rbooks scraper. The defaults are the desktop's; tests shrink them.
struct RbooksTiming: Sendable {
    var loadTimeout: TimeInterval = 45
    var productPolls = 20
    var libraryPolls = 15
    var apiPolls = 30
    /// One discovery call stays well inside a web view's patience.
    var discoverBudgetMillis = 20_000
    var discoverCalls = 600
    var apiRecoveries = 3
    /// The viewer must keep the same text length for this many polls before it counts as drawn.
    var contentPolls = 43
    var stablePolls = 2
    var contentAttempts = 3
    var poll: TimeInterval = 0.7
    var maxPages = 3
}

/// rbooks: webnovel episodes and ebook volumes the signed-in account can read, through the site's web
/// viewer in a real browser engine. The episode list comes from the book API, which only a real browser
/// navigation can reach. A chapter the account does not own (or that the site only opens in its own apps)
/// is refused; nothing is ever purchased. Comic pages come back as chapter images.
///
/// Port of `_rbooks_*` in the desktop scraper (without its Windows-only PC viewer export). This side
/// navigates, keeps the session, paces loads and builds results; the page logic is shared with Android.
public struct RbooksAdapter: SourceAdapter {
    public static let id: SourceID = .rbooks
    public static let tier: SourceTier = .heavyBrowser

    var timing: RbooksTiming
    /// Cookie access for tests; nil uses the web engine's.
    var cookieOverride: (any SessionCookieAccess)?

    public init() {
        self.init(timing: RbooksTiming(), cookies: nil)
    }

    init(timing: RbooksTiming, cookies: (any SessionCookieAccess)? = nil) {
        self.timing = timing
        self.cookieOverride = cookies
    }

    public static func matches(_ url: URL) -> Bool {
        RbooksSite.matches(url)
    }

    private func cookies(_ context: SourceContext) -> any SessionCookieAccess {
        cookieOverride ?? context.web.sessionCookies
    }

    private func sessions(_ context: SourceContext) -> SiteSessions {
        SiteSessions(secrets: context.secrets, cookies: cookies(context))
    }

    private func log(_ context: SourceContext, _ level: LogLevel, _ message: String) {
        context.logger.log(level, "[rbooks] " + message)
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let bookID = RbooksSite.bookID(url)
        if bookID.isEmpty { throw SourceError.unsupportedURL(url) }
        let sessions = self.sessions(context)
        let restored = await sessions.restoreOnce(RbooksSite.session)
        if restored > 0 { log(context, .info, "Restored \(restored) saved login cookie(s).") }

        let page = try await HardSupport.openPage(
            context.web, userAgent: RbooksSite.userAgent, width: RbooksSite.viewportWidth, height: RbooksSite.viewportHeight
        )
        do {
            let book = try await readBook(page, url: url, bookID: bookID, context: context, sessions: sessions)
            await HardSupport.closeQuietly(page)
            return book
        } catch {
            await HardSupport.closeQuietly(page)
            throw error
        }
    }

    private func readBook(_ page: any WebPageDriving, url: URL, bookID: String, context: SourceContext, sessions: SiteSessions) async throws -> BookInfo {
        var productID = bookID
        if RbooksSite.isLibrary(url) {
            switch try await libraryBook(page, url, bookID, context, sessions: sessions) {
            case .single(let book): return book
            case .product(let id): productID = id
            }
        }
        let book = try await productBook(page, productID, context)
        if !(await sessions.status(RbooksSite.session)).present.isEmpty { await sessions.snapshot(RbooksSite.session) }
        return book
    }

    private enum Library {
        case single(BookInfo)
        case product(String)
    }

    /// A library URL is either the reader of an owned book (one chapter) or points at the product page.
    private func libraryBook(_ page: any WebPageDriving, _ url: URL, _ requestedID: String, _ context: SourceContext, sessions: SiteSessions) async throws -> Library {
        try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(context, .warning, $0) }
        if RbooksSite.isAccountGate(await page.currentURL()) { throw signInNeeded(requestedID, context, "the library needs a login") }
        if !(await sessions.status(RbooksSite.session)).usable {
            throw signInNeeded(requestedID, context, "library links need a signed-in session")
        }
        var probe = RbooksLibraryProbe()
        for _ in 0..<timing.libraryPolls {
            probe = RbooksLibraryProbe(try await page.hardObject("rbooks.libraryProbe()"))
            if probe.viewer || !probe.product.isEmpty { break }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if probe.viewer {
            let title = probe.title.isEmpty ? "rbooks \(requestedID)" : probe.title
            log(context, .info, "Library reader: \(title)")
            return .single(BookInfo(
                source: .rbooks, id: requestedID, url: url, title: title, language: "ko",
                chapters: [ChapterRef(id: requestedID, index: 1, title: title, url: url)]
            ))
        }
        if probe.product.isEmpty {
            throw SourceError.parse("the library page opened, but no readable book or product link was found; check the rbooks login in Settings > Enter App")
        }
        let productID = URL(string: probe.product).map { RbooksSite.bookID($0) } ?? ""
        guard !productID.isEmpty else { throw SourceError.parse("the library page pointed at a product link that could not be read") }
        log(context, .info, "Library book resolves to \(RbooksSite.bookURL(productID).absoluteString)")
        if probe.unavailable {
            log(context, .warning,
                "The library marks this title unavailable" + (probe.adult ? " as an adult (19+) title; complete or renew adult verification in the Browser tab." : "."))
        }
        return .product(productID)
    }

    private func productBook(_ page: any WebPageDriving, _ bookID: String, _ context: SourceContext) async throws -> BookInfo {
        let bookURL = RbooksSite.bookURL(bookID)
        log(context, .info, "Opening \(bookURL.absoluteString)")
        try await HardSupport.loadQuietly(page, bookURL, timeout: timing.loadTimeout) { log(context, .warning, $0) }
        var ready = false
        for _ in 0..<timing.productPolls {
            if RbooksSite.isAccountGate(await page.currentURL()) {
                throw signInNeeded(bookID, context, "the book page needs a login or adult verification")
            }
            if try await page.hardBool("rbooks.productReady()") {
                ready = true
                break
            }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if RbooksSite.isAccountGate(await page.currentURL()) {
            throw signInNeeded(bookID, context, "the book page needs a login or adult verification")
        }
        if !ready, try await titleOf(page).localizedCaseInsensitiveContains("just a moment") {
            throw verification(bookID, context)
        }
        let meta = RbooksProduct(try await page.hardObject("rbooks.productInfo()"))
        for line in meta.diagnostics { log(context, .info, line) }

        var links = meta.links
        var titleByID = meta.titleByID
        var discoveryError = ""
        if !meta.seriesID.isEmpty {
            let found = try await discoverEpisodes(context, seriesID: meta.seriesID, rendered: meta.links, bookID: bookID)
            for line in found.diagnostics { log(context, .info, line) }
            if found.links.count > links.count { links = found.links }
            for (id, title) in found.titleByID { titleByID[id] = title }
            discoveryError = found.error
            if !found.error.isEmpty { log(context, .warning, "Episode API warning: \(found.error)") }
        }

        var chapters: [ChapterRef] = []
        var seen = Set<String>()
        for (offset, link) in links.enumerated() {
            guard let chapterURL = ReaderBlocks.resolve(link, base: bookURL) else { continue }
            let chapterID = RbooksSite.bookID(chapterURL)
            if chapterID.isEmpty || !seen.insert(chapterID).inserted { continue }
            let title = titleByID[chapterID].flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 } ?? "Episode \(offset + 1)"
            // The catalog does not say whether this account owns an episode; the viewer decides when it opens.
            chapters.append(ChapterRef(id: chapterID, index: chapters.count + 1, title: title, url: chapterURL))
        }
        if chapters.isEmpty {
            if !discoveryError.isEmpty {
                throw SourceError.blocked("episode discovery failed (\(discoveryError)): complete rbooks' verification in the Browser tab and retry")
            }
            throw SourceError.parse("no webnovel episodes or linked ebook volumes were found on this book page")
        }
        let book = BookInfo(
            source: .rbooks, id: bookID, url: bookURL, title: meta.title.isEmpty ? "rbooks \(bookID)" : meta.title,
            author: meta.author.isEmpty ? nil : meta.author, synopsis: meta.synopsis.isEmpty ? nil : meta.synopsis,
            coverURL: URL(string: meta.cover), tags: meta.tags, language: "ko", chapters: chapters
        )
        log(context, .info, "Book: \(book.title) by \(book.author ?? "?") - \(chapters.count) chapters")
        return book
    }

    /// The episode list from the book API: open the API as a top-level page (the only thing its bot check
    /// lets through), then let the page script walk the series in slices.
    private func discoverEpisodes(_ context: SourceContext, seriesID: String, rendered: [String], bookID: String) async throws -> RbooksDiscovery {
        let api = try await HardSupport.openPage(
            context.web, userAgent: RbooksSite.userAgent, width: RbooksSite.viewportWidth, height: RbooksSite.viewportHeight
        )
        do {
            let result = try await walkEpisodes(api, context, seriesID: seriesID, rendered: rendered, bookID: bookID)
            await HardSupport.closeQuietly(api)
            return result
        } catch {
            await HardSupport.closeQuietly(api)
            throw error
        }
    }

    private func walkEpisodes(_ api: any WebPageDriving, _ context: SourceContext, seriesID: String, rendered: [String], bookID: String) async throws -> RbooksDiscovery {
        var recoveries = 0
        var state: Any?
        var start = seriesID
        var reopen = true
        var result = RbooksDiscovery()
        for _ in 0..<timing.discoverCalls {
            if reopen {
                try await openAPI(api, start, bookID, context)
                reopen = false
            }
            var input: [String: Any] = ["seriesId": seriesID, "renderedLinks": rendered, "budgetMs": timing.discoverBudgetMillis]
            if let state { input["state"] = state }
            result = RbooksDiscovery(try await api.hardObject("rbooks.discover(\(HardScripts.json(input)))"))
            if result.done { return result }
            if let next = result.state { state = next }
            if let (found, total) = Self.progress(state) {
                log(context, .info, "Episode list: \(found) of \(total > 0 ? String(total) : "?") read so far.")
            }
            if !result.stalled { continue }
            recoveries += 1
            if recoveries > timing.apiRecoveries {
                // The API keeps failing: keep what was found.
                var giveUp: [String: Any] = ["giveUp": true]
                if let state { giveUp["state"] = state }
                return RbooksDiscovery(try await api.hardObject("rbooks.discover(\(HardScripts.json(giveUp)))"))
            }
            log(context, .warning, "The episode API stopped answering; reopening it (\(recoveries)/\(timing.apiRecoveries)).")
            let next = ((state as? [String: Any])?["nextId"]).map { HTTPSourceSupport.string($0) } ?? ""
            start = next.isEmpty ? seriesID : next
            reopen = true
            try await HTTPSourceSupport.sleep(seconds: timing.poll * 3)
        }
        result.error = result.error.isEmpty ? "the episode list did not finish" : result.error
        return result
    }

    /// (episodes found, episodes announced) from a discovery state, for the progress line.
    private static func progress(_ state: Any?) -> (Int, Int)? {
        guard let values = state as? [String: Any], let ids = values["ids"] as? [Any] else { return nil }
        return (ids.count, HTTPSourceSupport.int(values["total"]))
    }

    private func openAPI(_ api: any WebPageDriving, _ id: String, _ bookID: String, _ context: SourceContext) async throws {
        try await HardSupport.loadQuietly(api, URL(string: RbooksSite.apiOrigin + "/books/" + id)!, timeout: timing.loadTimeout) {
            log(context, .warning, $0)
        }
        var state = RbooksApiState()
        for _ in 0..<timing.apiPolls {
            state = RbooksApiState(try await api.hardObject("rbooks.apiState()"))
            if state.json { return }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        if state.challenge { throw verification(bookID, context) }
        throw SourceError.blocked("the rbooks book API did not return JSON (\(state.title.isEmpty ? state.url : state.title))")
    }

    private func signInNeeded(_ bookID: String, _ context: SourceContext, _ why: String) -> SourceError {
        log(context, .warning, "Sign-in needed: \(why).")
        if HardPrompts.shared.once("rbooks:login:" + bookID) { return .loginRequired(.rbooks) }
        return .blocked("rbooks still asks you to sign in or verify your age: do it in Settings > Enter App, then retry")
    }

    private func verification(_ bookID: String, _ context: SourceContext) -> SourceError {
        log(context, .warning, "rbooks asked for a browser verification (Cloudflare).")
        if HardPrompts.shared.once("rbooks:verify:" + bookID) { return .loginRequired(.rbooks) }
        return .blocked("rbooks still asks for a verification: open the book in the Browser tab, complete it, then retry")
    }

    private func titleOf(_ page: any WebPageDriving) async throws -> String {
        let data = try await HardSupport.attempt { try await page.evaluateJSON("return document.title;") }
        guard let data else { return "" }
        return (try? JSONDecoder().decode(String.self, from: data)) ?? ""
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let run = RbooksRun(context: context, book: book, timing: timing, sessions: sessions(context))
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
        let sessions = self.sessions(context)
        let run = RbooksRun(context: context, book: book, timing: timing, sessions: sessions)
        let shared = context.shared(for: .rbooks)
        do {
            let results = try await HardSupport.parseConcurrently(refs, limit: run.pageLimit, shared: shared, context: context) { ref in
                try await run.chapter(ref)
            }
            await run.close()
            await sessions.snapshot(RbooksSite.session)
            return results
        } catch {
            await run.close()
            await sessions.snapshot(RbooksSite.session)
            throw error
        }
    }
}

/// The pages one download uses.
final class RbooksRun: @unchecked Sendable {
    private let context: SourceContext
    private let book: BookInfo
    private let timing: RbooksTiming
    private let sessions: SiteSessions
    private let pool: WebPagePool
    let pageLimit: Int

    // The library page and what it said, one caller at a time.
    private let libraryGate = HardLock()
    private var libraryPage: (any WebPageDriving)?
    private var owned: Set<String>?
    private var ownedAsked = false

    init(context: SourceContext, book: BookInfo, timing: RbooksTiming, sessions: SiteSessions) {
        self.context = context
        self.book = book
        self.timing = timing
        self.sessions = sessions
        let limit = max(1, min(context.shared(for: .rbooks).threads, timing.maxPages))
        self.pageLimit = limit
        self.pool = WebPagePool(engine: context.web, profile: WebProfile.external, size: limit) { page in
            await HardSupport.prepare(page, userAgent: RbooksSite.userAgent, width: RbooksSite.viewportWidth, height: RbooksSite.viewportHeight)
        }
    }

    private func log(_ level: LogLevel, _ message: String) {
        context.logger.log(level, "[rbooks] " + message)
    }

    func chapter(_ ref: ChapterRef) async throws -> ChapterResult {
        try await pool.withPage { page in try await self.read(page, ref) }
    }

    func close() async {
        await pool.closeAll()
        let open: (any WebPageDriving)? = await libraryGate.run {
            let page = self.libraryPage
            self.libraryPage = nil
            return page
        }
        await open?.close()
    }

    private func read(_ page: any WebPageDriving, _ ref: ChapterRef) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        do {
            try await page.load(url, until: .domContentLoaded, timeout: timing.loadTimeout)
        } catch WebEngineError.timeout(_) {
            // A timeout can come after the viewer itself committed. Go on only if this is the requested viewer.
            let landed = await page.currentURL()
            guard let landed, RbooksSite.isViewerURL(landed), RbooksSite.bookID(landed) == RbooksSite.bookID(url) else {
                return .failed(ref, message: "page load timed out")
            }
        }
        let current = await page.currentURL()
        if let current, !RbooksSite.isViewerURL(current) {
            if RbooksSite.isAccountGate(current) {
                return .refused(ref, reason: "signed out of rbooks: sign in in Settings > Enter App, then download again")
            }
            return .refused(ref, reason: "locked or not bought (rbooks redirected to \(current.host ?? "another page"))")
        }

        var ready = false
        for attempt in 1...max(1, timing.contentAttempts) {
            if try await waitForContent(page) {
                ready = true
                break
            }
            let wall = RbooksWall(try await page.hardObject("rbooks.accessWall()"))
            if wall.wall && wall.refused { return try await refused(ref) }
            if wall.wall { return .refused(ref, reason: "locked or not bought (purchase or sign-in markers on the page)") }
            log(.warning, "Content validation failed for \(ref.title) (attempt \(attempt)/\(timing.contentAttempts)).")
            if attempt < timing.contentAttempts {
                try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(.warning, "Reload warning for \(ref.title): " + $0) }
            }
        }
        if !ready { return .failed(ref, message: "the viewer did not finish drawing") }

        let options: [String: Any] = ["fallbackTitle": ref.title, "chapterIndex": ref.index]
        let reply = try await page.hardOptionalObject("rbooks.extract(\(HardScripts.json(options)))")
        guard let reply else { return try await emptyExtraction(page, ref) }
        let extract = RbooksExtract(reply)
        if extract.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return try await emptyExtraction(page, ref) }
        return await buildResult(ref, extract)
    }

    private func emptyExtraction(_ page: any WebPageDriving, _ ref: ChapterRef) async throws -> ChapterResult {
        let wall = RbooksWall(try await page.hardObject("rbooks.accessWall()"))
        return wall.wall ? .refused(ref, reason: "locked or not bought") : .failed(ref, message: "no chapter content was extracted")
    }

    /// The viewer's text must be long enough and keep its length: it is drawn in pieces.
    private func waitForContent(_ page: any WebPageDriving) async throws -> Bool {
        var last = -1
        var stable = 0
        for _ in 0..<timing.contentPolls {
            let state = RbooksContentState(try await page.hardObject("rbooks.contentState()"))
            if state.refused { return false }
            if state.ready && state.length >= 500 {
                if state.length == last {
                    stable += 1
                    if stable >= timing.stablePolls { return true }
                } else {
                    stable = 0
                    last = state.length
                }
            } else {
                stable = 0
                last = -1
            }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        return false
    }

    private func buildResult(_ ref: ChapterRef, _ extract: RbooksExtract) async -> ChapterResult {
        let referer = book.url.absoluteString
        let images: [ImageRef]
        if context.settings.images.saveImageURLsOnly {
            images = extract.images.compactMap { image in URL(string: image.url).map { ImageRef(url: $0, filename: image.name) } }
        } else {
            images = await downloadImages(extract.images, referer: referer, workers: context.shared(for: .rbooks).imageWorkers)
        }
        // A series title is never a usable episode title: keep the catalog's.
        var named = ref
        let shown = extract.title
        if !shown.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
           ReaderBlocks.normalizeTitle(shown) != ReaderBlocks.normalizeTitle(book.title) {
            named.title = shown
        }
        return .content(ChapterContent(ref: named, html: "<div class=\"rbooks-content\">" + extract.content + "</div>", images: images))
    }

    /// The viewer refused to open the title: the library says whether the account owns it.
    private func refused(_ ref: ChapterRef) async throws -> ChapterResult {
        let answer = try await ownedIDs()
        let signedIn = await sessions.status(RbooksSite.session).usable
        guard let owned = answer else {
            if !signedIn {
                return .refused(ref, reason: "the rbooks library could not confirm a session: sign in in Settings > Enter App, then download again")
            }
            return .failed(ref, message: "the rbooks library could not verify access to this volume; retry")
        }
        if !owned.contains(ref.id) {
            return .refused(ref, reason: "not in this account's library: buy or rent it on rbooks first")
        }
        return .refused(
            ref,
            reason: "owned, but rbooks opens it only in its own apps or PC viewer (the web viewer says it is unsupported); it cannot be exported on a phone"
        )
    }

    /// Volumes of this book the signed-in account owns, asked from the library page; nil when it could not be asked.
    private func ownedIDs() async throws -> Set<String>? {
        try await libraryGate.run {
            if self.ownedAsked { return self.owned }
            self.ownedAsked = true
            var ids: [String] = []
            var seen = Set<String>()
            for id in self.book.chapters.map(\.id) + [self.book.id] where seen.insert(id).inserted { ids.append(id) }
            ids = Array(ids.prefix(500))
            do {
                let page: any WebPageDriving
                if let open = self.libraryPage {
                    page = open
                } else {
                    page = try await HardSupport.openPage(
                        self.context.web, userAgent: RbooksSite.userAgent, width: RbooksSite.viewportWidth, height: RbooksSite.viewportHeight
                    )
                    self.libraryPage = page
                }
                try await HardSupport.loadQuietly(page, URL(string: RbooksSite.libraryOrigin + "/")!, timeout: self.timing.loadTimeout) {
                    self.log(.warning, $0)
                }
                let reply = try await page.hardObject("rbooks.ownedIds(\(HardScripts.literal(ids.joined(separator: ","))).split(','))")
                if let list = reply.any("owned") as? [Any] {
                    self.owned = Set(list.map { HTTPSourceSupport.string($0) })
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                self.log(.warning, "Library ownership lookup warning: \(error.localizedDescription)")
            }
            return self.owned
        }
    }

    // MARK: Images

    private func downloadImages(_ images: [RbooksImage], referer: String, workers: Int) async -> [ImageRef] {
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

    private func imageRef(_ image: RbooksImage, referer: String) async -> ImageRef? {
        guard let url = URL(string: image.url) else { return nil }
        return ImageRef(url: url, filename: image.name, data: await fetchImage(image.url, url: url, referer: referer))
    }

    /// The bytes, or nil (the image stage tries again without a referer).
    private func fetchImage(_ text: String, url: URL, referer: String) async -> Data? {
        if text.lowercased().hasPrefix("data:") {
            guard let comma = text.range(of: "base64,") else { return nil }
            return Data(base64Encoded: String(text[comma.upperBound...]), options: .ignoreUnknownCharacters)
        }
        do {
            let response = try await context.http.send(HTTPRequest(url: url, headers: [
                "User-Agent": RbooksSite.userAgent,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            ]))
            return (200..<300).contains(response.status) && !response.body.isEmpty ? response.body : nil
        } catch {
            return nil
        }
    }
}
