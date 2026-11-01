import Foundation
import NpiaCore

/// xiyuwx and its yywenxuan mirror. HTTP first (GB18030-safe decoding, multi-page chapters,
/// the `_ii_rr` shuffle decoder); the web engine is the fallback when HTTP fails or the site
/// shows a verification page. Port of `_xiyuwx_*` in the desktop external scraper.
public struct XiyuwxAdapter: SourceAdapter {
    public static let id: SourceID = .xiyuwx
    public static let tier: SourceTier = .lightBrowser
    public static let sites: Set<String> = XiyuwxParser.hosts

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        XiyuwxParser.matches(url)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let bookID = XiyuwxParser.bookID(url)
        guard !bookID.isEmpty else { throw SourceError.unsupportedURL(url) }
        let site = XiyuwxParser.siteName(url)
        let bookURL = XiyuwxParser.canonicalBookURL(url, bookID: bookID)
        var candidates: [URL] = [url]
        if bookURL != url { candidates.append(bookURL) }

        for target in candidates {
            do {
                let page = try await Self.fetch(target, referer: bookURL, http: context.http)
                if let book = XiyuwxParser.book(fromHTML: page.html, pageURL: page.url, bookURL: bookURL, bookID: bookID) {
                    context.logger.log(.info, "[\(site)] Book: \(book.title) - \(book.chapters.count) chapters")
                    return book
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                context.logger.log(.warning, "[\(site)] Book request failed: \(error.localizedDescription)")
            }
        }

        var blocked = false
        do {
            let found: BookInfo? = try await context.web.withPage(profile: WebProfile.external) { page in
                for target in candidates {
                    let rendered = try await Self.renderedHTML(page, target)
                    if XiyuwxParser.isVerificationPage(rendered.html) { return nil }
                    if let book = XiyuwxParser.book(fromHTML: rendered.html, pageURL: rendered.url, bookURL: bookURL, bookID: bookID) {
                        return book
                    }
                }
                return nil
            }
            if let found { return found }
        } catch is CancellationError {
            throw CancellationError()
        } catch SourceError.blocked {
            blocked = true
        } catch {
            context.logger.log(.warning, "[\(site)] Browser book request failed: \(error.localizedDescription)")
        }
        if blocked {
            throw SourceError.blocked("\(site) is showing a security verification page.")
        }
        throw SourceError.parse("\(site): book title or chapter catalog was not found.")
    }

    // MARK: Chapter

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let shared = context.shared(for: Self.id)
        if ref.isPaid && shared.skipPaid {
            return .refused(ref, reason: "Paid chapter skipped (skip paid is on).")
        }
        guard let url = ref.url else { return .failed(ref, message: "Chapter has no URL.") }
        let site = XiyuwxParser.siteName(url)

        var lines = await Self.httpChapter(url, referer: book.url, context: context, shared: shared)
        if lines == nil {
            lines = await Self.browserChapter(url, context: context, shared: shared)
        }
        // The chapter loop rests the per-chapter delay between chapters.
        guard let lines else {
            if ref.isPaid { return .refused(ref, reason: "This chapter is locked for the signed-in account.") }
            context.logger.log(.warning, "[\(site)] Could not read \(ref.title)")
            return .failed(ref, message: "Could not read this chapter.")
        }
        return .content(ChapterContent(ref: ref, html: XiyuwxParser.chapterHTML(lines)))
    }

    // MARK: Fetching

    struct FetchedPage {
        var html: String
        var url: URL
    }

    static func headers(referer: URL) -> [String: String] {
        [
            "User-Agent": XiyuwxParser.userAgent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer.absoluteString,
        ]
    }

    static func fetch(_ url: URL, referer: URL, http: any HTTPFetching) async throws -> FetchedPage {
        let response = try await http.fetchOK(url, headers: headers(referer: referer))
        return FetchedPage(html: XiyuwxParser.decodePage(response.body), url: response.url)
    }

    /// Loads `url` in the page; waits briefly while a verification interstitial clears.
    static func renderedHTML(_ page: any WebPageDriving, _ url: URL) async throws -> FetchedPage {
        try await page.load(url, until: .domContentLoaded, timeout: 30)
        var html = try await page.html()
        var attempts = 0
        while XiyuwxParser.isVerificationPage(html) && attempts < 40 {
            try await Task.sleep(nanoseconds: 500_000_000)
            html = try await page.html()
            attempts += 1
        }
        if XiyuwxParser.isVerificationPage(html) {
            throw SourceError.blocked("verification page")
        }
        let final = await page.currentURL() ?? url
        return FetchedPage(html: html, url: final)
    }

    /// Reads every page of a chapter over HTTP. Nil when any page cannot be read.
    static func httpChapter(_ start: URL, referer: URL, context: SourceContext, shared: SharedSettings) async -> [String]? {
        var all: [String] = []
        var seen = Set<String>()
        var current: URL? = start
        for index in 0..<XiyuwxParser.maxPages {
            guard let url = current, !seen.contains(url.absoluteString), !Task.isCancelled else { return nil }
            if index > 0 { await pause(shared) }
            seen.insert(url.absoluteString)
            guard let page = try? await fetch(url, referer: referer, http: context.http),
                  let parsed = XiyuwxParser.chapterPage(fromHTML: page.html, pageURL: page.url) else { return nil }
            all.append(contentsOf: parsed.lines)
            if parsed.nextPage == nil { return all }
            current = parsed.nextPage
        }
        context.logger.log(.warning, "[\(XiyuwxParser.siteName(start))] Chapter has too many pages")
        return nil
    }

    /// Same as `httpChapter` but rendered by the web engine (passes JS verification).
    static func browserChapter(_ start: URL, context: SourceContext, shared: SharedSettings) async -> [String]? {
        do {
            return try await context.web.withPage(profile: WebProfile.external) { page in
                var all: [String] = []
                var seen = Set<String>()
                var current: URL? = start
                for index in 0..<XiyuwxParser.maxPages {
                    guard let url = current, !seen.contains(url.absoluteString) else { return nil }
                    if index > 0 { await pause(shared) }
                    seen.insert(url.absoluteString)
                    let rendered = try await renderedHTML(page, url)
                    guard let parsed = XiyuwxParser.chapterPage(fromHTML: rendered.html, pageURL: rendered.url) else { return nil }
                    all.append(contentsOf: parsed.lines)
                    if parsed.nextPage == nil { return all }
                    current = parsed.nextPage
                }
                return nil
            }
        } catch {
            context.logger.log(.warning, "[\(XiyuwxParser.siteName(start))] Browser chapter failed: \(error.localizedDescription)")
            return nil
        }
    }

    /// `_sleep_interval`: a random pause between `intervalMin` and `intervalMax` seconds.
    static func pause(_ shared: SharedSettings) async {
        try? await shared.sleepChapterDelay()
    }
}
