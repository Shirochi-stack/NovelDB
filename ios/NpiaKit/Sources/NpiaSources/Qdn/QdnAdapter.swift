import Foundation
import NpiaCore

/// Waits and limits of the qdn scraper. The defaults are the desktop's; tests shrink them.
struct QdnTiming: Sendable {
    /// Spacing between chapter page loads across all workers: the firewall blocks bursts.
    var minGap: TimeInterval = 2.5
    /// Pauses after a firewall block, one per retry; when they run out the chapter fails.
    var firewallCooldowns: [TimeInterval] = [60, 180, 420, 900]
    var loadTimeout: TimeInterval = 45
    var bookPolls = 45
    var chapterPolls = 60
    var poll: TimeInterval = 1
    /// The reader decrypts a bought chapter in the page, which takes 10-60 s.
    var decryptPolls = 500
    var decryptPoll: TimeInterval = 0.3
    var extractPolls = 240
    var extractPoll: TimeInterval = 0.5
    var decodePolls = 600
    var decodePoll: TimeInterval = 0.5
    var referenceSlices = 200
    var referenceSliceMillis = 8_000
    /// Pages open at once, whatever `threads` says: each is a full desktop reader.
    var maxPages = 3
}

/// qdn: the chapter list and free chapters from the desktop reader page, and bought chapters with the
/// user's browser session. The reader encrypts a bought chapter and the site's own script decrypts it in a
/// real rendering engine (a web view is one); the text is then drawn with scrambled fonts, which
/// `hard-sources.js` reads off the screen and matches against a reference font. Chapters the account has
/// not bought are refused; nothing is ever purchased.
///
/// Port of `_qdn_*` and `qdn_font_decoder.py` in the desktop scraper. This side navigates, keeps the
/// session, paces loads and builds results; the page logic is shared with Android.
public struct QdnAdapter: SourceAdapter {
    public static let id: SourceID = .qdn
    public static let tier: SourceTier = .heavyBrowser

    var timing: QdnTiming
    /// Cookie access for tests; nil uses the web engine's.
    var cookieOverride: (any SessionCookieAccess)?
    private let pacer: HardPacer

    public init() {
        self.init(timing: QdnTiming(), cookies: nil)
    }

    init(timing: QdnTiming, cookies: (any SessionCookieAccess)? = nil) {
        self.timing = timing
        self.cookieOverride = cookies
        self.pacer = HardPacer(minGap: timing.minGap)
    }

    public static func matches(_ url: URL) -> Bool {
        QdnSite.matches(url)
    }

    public static func canonicalURL(_ url: URL) -> URL {
        matches(url) ? QdnSite.canonicalURL(url) : url
    }

    private func cookies(_ context: SourceContext) -> any SessionCookieAccess {
        cookieOverride ?? context.web.sessionCookies
    }

    private func sessions(_ context: SourceContext) -> SiteSessions {
        SiteSessions(secrets: context.secrets, cookies: cookies(context))
    }

    private func log(_ context: SourceContext, _ level: LogLevel, _ message: String) {
        context.logger.log(level, "[qdn] " + message)
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let bookID = QdnSite.bookID(url) else { throw SourceError.unsupportedURL(url) }
        let sessions = self.sessions(context)
        let restored = await sessions.restoreOnce(QdnSite.session)
        if restored > 0 { log(context, .info, "Restored \(restored) saved login cookie(s).") }

        let page = try await HardSupport.openPage(
            context.web, userAgent: QdnSite.userAgent, width: QdnSite.viewportWidth, height: QdnSite.viewportHeight
        )
        do {
            let book = try await readBook(page, bookID: bookID, context: context, sessions: sessions)
            await HardSupport.closeQuietly(page)
            return book
        } catch {
            await HardSupport.closeQuietly(page)
            throw error
        }
    }

    private func readBook(_ page: any WebPageDriving, bookID: String, context: SourceContext, sessions: SiteSessions) async throws -> BookInfo {
        let bookURL = QdnSite.bookURL(bookID)
        let state = try await loadBook(page, bookURL, context)
        switch state.state {
        case "verification":
            throw verification(bookID, context)
        case "blocked":
            throw SourceError.blocked("qdn's firewall blocked the request; wait a while and try again")
        case "ready":
            log(context, .info, "Reading as a desktop browser (\(state.viewportWidth) x \(state.viewportHeight) px).")
        default:
            let shown = state.title.isEmpty ? state.url : state.title
            throw SourceError.parse("the book page did not render (\(shown): \(String(state.text.prefix(120))))")
        }

        var status = await sessions.status(QdnSite.session)
        if !status.present.isEmpty {
            // A remembered login can have expired; the site renews it from its remember-me cookie only when
            // these are absent, so expired copies must go.
            let reply = try await HardSupport.attempt { try await page.hardObject("qdn.userInfo()") }
            if reply?.optionalBool("live") == false {
                let access = cookies(context)
                for name in QdnSite.loginCookies {
                    await access.removeCookie(name: name, url: URL(string: QdnSite.origin + "/")!)
                }
                log(context, .warning, "The saved login has expired. Sign in again in Settings > Enter App if VIP chapters stay locked.")
                status = await sessions.status(QdnSite.session)
            }
        }

        let info = QdnBookPage(try await page.hardObject("qdn.bookInfo()"))
        if info.title.isEmpty { throw SourceError.parse("could not extract the book title") }
        if info.chapters.isEmpty { throw SourceError.parse("no chapters found") }
        var entries = info.chapters
        if info.totalChapters > entries.count, !info.catalogURL.isEmpty {
            // A long book's page lists only its first chapters; the catalog page lists them all.
            let all = try await fullCatalog(page, info.catalogURL, bookID, context)
            if let all, all.count > entries.count {
                log(context, .info, "Read the full catalog: \(all.count) chapters (the book page listed \(entries.count)).")
                entries = all
            } else {
                log(context, .warning, "The book has \(info.totalChapters) chapters but only \(entries.count) could be listed.")
            }
        }
        let chapters = entries.enumerated().map { offset, entry -> ChapterRef in
            let link = URL(string: entry.url)
            return ChapterRef(
                id: link.flatMap { QdnSite.chapterID($0) } ?? String(offset + 1),
                index: offset + 1, title: entry.name, url: link, isPaid: entry.isVip
            )
        }
        let book = BookInfo(
            source: .qdn, id: bookID, url: bookURL, title: info.title,
            author: info.author.isEmpty ? nil : info.author, synopsis: info.description.isEmpty ? nil : info.description,
            coverURL: URL(string: info.coverURL), tags: info.tags, language: "zh", chapters: chapters
        )
        log(context, .info, "Book: \(book.title) by \(book.author ?? "?") - \(chapters.count) chapters")

        let sessionKey = "qdn:session:" + bookID
        if status.usable {
            HardPrompts.shared.clear(sessionKey)
            await sessions.snapshot(QdnSite.session)
        } else if wantsBoughtChapters(chapters, context) {
            if HardPrompts.shared.once(sessionKey) {
                // Bought chapters need the user's login: let the app open its sign-in browser, then start over.
                log(context, .info, "Not signed in and VIP chapters are selected; asking for a sign-in.")
                throw SourceError.loginRequired(.qdn)
            }
            log(context, .warning, "Not signed in: VIP chapters will be skipped as previews.")
        }
        return book
    }

    /// Every chapter from the catalog page, or nil when it could not be read.
    private func fullCatalog(_ page: any WebPageDriving, _ catalogURL: String, _ bookID: String, _ context: SourceContext) async throws -> [QdnCatalogEntry]? {
        guard let url = URL(string: catalogURL) else { return nil }
        try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(context, .warning, "Catalog: " + $0) }
        for _ in 0..<timing.bookPolls {
            let reply = try await HardSupport.attempt { try await page.hardObject("qdn.catalogState()") }
            let state = reply.map(QdnCatalogState.init) ?? QdnCatalogState()
            switch state.state {
            case "ready":
                return try await page.hardObject("qdn.catalogInfo()").objects("chapters").map(QdnCatalogEntry.init)
            case "verification":
                throw verification(bookID, context)
            case "blocked":
                throw SourceError.blocked("qdn's firewall blocked the request; wait a while and try again")
            default:
                break
            }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        return nil
    }

    private func wantsBoughtChapters(_ chapters: [ChapterRef], _ context: SourceContext) -> Bool {
        let shared = context.shared(for: .qdn)
        let from = shared.rangeFrom ?? Int.min
        let to = shared.rangeTo ?? Int.max
        return !shared.skipPaid && chapters.contains { $0.isPaid && $0.index >= from && $0.index <= to }
    }

    private func verification(_ bookID: String, _ context: SourceContext) -> SourceError {
        log(context, .warning, "qdn asked for a verification (captcha).")
        // The sign-in browser shows the book page, where the user can complete the check.
        if HardPrompts.shared.once("qdn:verify:" + bookID) { return .loginRequired(.qdn) }
        return .blocked("qdn still asks for a verification: open qdn in the Browser tab, complete it, then try again")
    }

    private func loadBook(_ page: any WebPageDriving, _ url: URL, _ context: SourceContext) async throws -> QdnPageState {
        try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(context, .warning, $0) }
        var state = QdnPageState()
        for _ in 0..<timing.bookPolls {
            let reply = try await HardSupport.attempt { try await page.hardObject("qdn.pageState()") }
            state = reply.map(QdnPageState.init) ?? QdnPageState()
            if state.state != "waiting" { return state }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        return state
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let run = QdnRun(context: context, timing: timing, pacer: pacer)
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
        let run = QdnRun(context: context, timing: timing, pacer: pacer)
        let shared = context.shared(for: .qdn)
        do {
            let results = try await HardSupport.parseConcurrently(refs, limit: run.pageLimit, shared: shared, context: context) { ref in
                try await run.chapter(ref)
            }
            await run.close()
            await sessions(context).snapshot(QdnSite.session)
            return results
        } catch {
            await run.close()
            await sessions(context).snapshot(QdnSite.session)
            throw error
        }
    }
}

/// The pages and the decoder one download uses.
final class QdnRun: @unchecked Sendable {
    private let context: SourceContext
    private let timing: QdnTiming
    private let pacer: HardPacer
    private let pool: WebPagePool
    private let decoder: QdnDecoder
    let pageLimit: Int

    init(context: SourceContext, timing: QdnTiming, pacer: HardPacer) {
        self.context = context
        self.timing = timing
        self.pacer = pacer
        let limit = max(1, min(context.shared(for: .qdn).threads, timing.maxPages))
        self.pageLimit = limit
        self.pool = WebPagePool(engine: context.web, profile: WebProfile.external, size: limit) { page in
            await HardSupport.prepare(page, userAgent: QdnSite.userAgent, width: QdnSite.viewportWidth, height: QdnSite.viewportHeight)
        }
        self.decoder = QdnDecoder(web: context.web, logger: context.logger, timing: timing)
    }

    private func log(_ level: LogLevel, _ message: String) {
        context.logger.log(level, "[qdn] " + message)
    }

    func chapter(_ ref: ChapterRef) async throws -> ChapterResult {
        try await pool.withPage { page in try await self.read(page, ref) }
    }

    func close() async {
        await pool.closeAll()
        await decoder.close()
    }

    private func read(_ page: any WebPageDriving, _ ref: ChapterRef) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        guard let loaded = try await loadChapter(page, ref, url) else {
            return .failed(ref, message: "blocked by qdn's firewall; wait a while, then download the rest")
        }
        if !loaded.ready {
            guard try await waitReady(page) else { return .failed(ref, message: "timed out waiting for the chapter") }
        }
        let encryption = QdnEncryptionState(try await page.hardObject("qdn.encryptionState()"))
        if encryption.encrypted { return try await decodeEncrypted(page, ref, words: encryption.words) }
        return fromRead(ref, QdnRead(try await page.hardObject("qdn.readChapter()")))
    }

    /// Loads the chapter, waiting out the firewall. Nil when the cooldowns are used up.
    private func loadChapter(_ page: any WebPageDriving, _ ref: ChapterRef, _ url: URL) async throws -> QdnChapterState? {
        var attempt = 0
        while true {
            try await pacer.pace()
            try await HardSupport.loadQuietly(page, url, timeout: timing.loadTimeout) { log(.warning, $0) }
            let state = QdnChapterState(try await page.hardObject("qdn.chapterState()"))
            if !state.blocked { return state }
            guard attempt < timing.firewallCooldowns.count else {
                log(.warning, "Still blocked by qdn's firewall; giving up on \(ref.title).")
                return nil
            }
            let pause = timing.firewallCooldowns[attempt]
            log(.warning, "qdn's firewall is blocking requests. Pausing \(Int(pause / 60)) min before retrying \(ref.title) (\(attempt + 1)/\(timing.firewallCooldowns.count)).")
            try await HTTPSourceSupport.sleep(seconds: pause)
            attempt += 1
        }
    }

    private func waitReady(_ page: any WebPageDriving) async throws -> Bool {
        for _ in 0..<timing.chapterPolls {
            let state = QdnChapterState(try await page.hardObject("qdn.chapterState()"))
            if state.ready { return true }
            try await HTTPSourceSupport.sleep(seconds: timing.poll)
        }
        return false
    }

    private func fromRead(_ ref: ChapterRef, _ read: QdnRead) -> ChapterResult {
        guard let error = read.error else {
            if read.lines.isEmpty { return .failed(ref, message: "empty content") }
            return .content(ReaderBlocks.content(read.lines.map { ReaderBlock.text($0) }, ref: ref, cssClass: "qdn-content"))
        }
        switch error {
        case "locked":
            let detail: String
            switch read.reason {
            case "login": detail = "preview only: the app is not signed in to qdn (Settings > Enter App)"
            case "purchase": detail = "preview only: this account has not bought the chapter"
            default: detail = "locked, or sign-in required"
            }
            let counts = read.expected > 0 ? " (\(read.chars)/\(read.expected) characters)" : ""
            return .refused(ref, reason: detail + counts)
        case "verification":
            return .failed(ref, message: "qdn asks for a verification: open qdn in the Browser tab, complete it, then retry")
        default:
            return .failed(ref, message: error)
        }
    }

    /// A bought chapter: wait for the reader to decrypt it, read what is painted, decode the glyphs.
    private func decodeEncrypted(_ page: any WebPageDriving, _ ref: ChapterRef, words: Int) async throws -> ChapterResult {
        // The reference takes a few seconds to build; do it while the reader decrypts.
        async let warm: String? = warmDecoder()
        var state = QdnEncryptionState()
        for _ in 0..<timing.decryptPolls {
            state = QdnEncryptionState(try await page.hardObject("qdn.encryptionState()"))
            if state.rendered || state.failed || state.blocked { break }
            try await HTTPSourceSupport.sleep(seconds: timing.decryptPoll)
        }
        let failure = await warm
        if state.blocked { return .failed(ref, message: "blocked by qdn's firewall") }
        if state.failed {
            return .failed(ref, message: "the reader could not decrypt this chapter (章节加载失败); retry, or open it once in the Browser tab")
        }
        if !state.rendered { return .failed(ref, message: "timed out decrypting the chapter") }
        if let failure { return .failed(ref, message: "could not prepare the glyph decoder: \(failure)") }

        var decoded: QdnDecoded
        do {
            decoded = try await extractAndDecode(page)
            if decoded.issues > 0 {
                log(.info, "Capture unstable or fonts missing; retrying once.")
                if let retry = try await HardSupport.attempt({ try await self.extractAndDecode(page) }), quality(retry) >= quality(decoded) {
                    decoded = retry
                }
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            return .failed(ref, message: "could not decode the chapter: \(error.localizedDescription)")
        }
        let lines = decoded.lines
        if lines.isEmpty { return .failed(ref, message: "no decoded content") }
        let stats = decoded.stats
        let textLength = lines.reduce(0) { $0 + $1.count } + lines.count - 1
        let warnings: [(String, Int)] = [
            ("uncertain_glyphs", stats.lowConfidence),
            ("suspicious_passthrough", stats.suspiciousPassthrough),
            ("missing_fonts", stats.missingFonts),
            ("unstable_paragraphs", stats.unstableParagraphs),
            ("incomplete", words >= 500 && Double(textLength) < Double(words) * 0.8 ? 1 : 0),
        ].filter { $0.1 > 0 }
        if !warnings.isEmpty {
            log(.warning, "Saving best available text for \(ref.title): " + warnings.map { "\($0.0)=\($0.1)" }.joined(separator: ", "))
        }
        var summary = "Decoded font-encrypted chapter: \(lines.count) paragraphs, \(stats.decoded) glyphs (\(stats.verifiedGlyphs) verified, \(stats.shapeMatchedGlyphs) shape matched"
        if stats.lowConfidence > 0 { summary += ", \(stats.lowConfidence) uncertain" }
        log(.info, summary + ").")
        return .content(ReaderBlocks.content(lines.map { ReaderBlock.text($0) }, ref: ref, cssClass: "qdn-content"))
    }

    private func warmDecoder() async -> String? {
        do {
            try await decoder.prepare()
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    /// Higher is better: something decoded, then fewer capture problems, then more text.
    private func quality(_ decoded: QdnDecoded) -> Int {
        (decoded.lines.isEmpty ? 0 : 1_000_000_000) - decoded.issues * 1_000_000 + decoded.lines.reduce(0) { $0 + $1.count }
    }

    private func extractAndDecode(_ page: any WebPageDriving) async throws -> QdnDecoded {
        let known = try await decoder.knownFonts().map { HardScripts.literal($0) }.joined(separator: ",")
        let payload = try await page.hardTask(
            "qdn.extract", argsJSON: "[{\"known\":[\(known)]}]",
            pollSeconds: timing.extractPoll, maxPolls: timing.extractPolls
        )
        return try await decoder.decode(payload)
    }
}
