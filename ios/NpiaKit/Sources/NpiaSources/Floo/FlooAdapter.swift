import Foundation
import NpiaCore

/// floo: free chapters over HTTP; chapters that need the signed-in account (VIP) are read
/// through the web engine's profile, and bought VIP chapters, which the site serves as
/// server-drawn images, come back as chapter images. Chapters the account has not bought
/// are refused; nothing is ever purchased. Port of `_floo_*` in the desktop external scraper.
///
/// The desktop additionally turns the VIP images back into text with `floo_image_reader`
/// (template matching against glyphs rendered by Windows GDI+ with a system font, using
/// numpy and PowerShell). None of that exists on iOS, so the images are returned as is.
public struct FlooAdapter: SourceAdapter {
    public static let id: SourceID = .floo
    public static let tier: SourceTier = .lightBrowser

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        FlooParser.matches(url)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let bookID = FlooParser.bookID(url)
        guard !bookID.isEmpty, let canonical = FlooParser.canonicalBookURL(bookID) else {
            throw SourceError.unsupportedURL(url)
        }
        var candidates: [URL] = [canonical]
        if url != canonical { candidates.append(url) }

        for target in candidates {
            do {
                let response = try await Self.fetch(target, http: context.http)
                if let book = FlooParser.book(fromHTML: FlooParser.decodings(response.body), pageURL: response.url,
                                              bookID: bookID, canonical: canonical) {
                    context.logger.log(.info, "[floo] Book: \(book.title) - \(book.chapters.count) chapters")
                    return book
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                context.logger.log(.warning, "[floo] Book request failed: \(error.localizedDescription)")
            }
        }

        // The HTTP response can lack the catalog while the browser version renders it.
        do {
            let found: BookInfo? = try await context.web.withPage(profile: WebProfile.external) { page in
                for target in candidates {
                    try await page.load(target, until: .domContentLoaded, timeout: 30)
                    var html = try await page.html()
                    var pageURL = await page.currentURL() ?? target
                    var book = FlooParser.book(fromHTML: [html], pageURL: pageURL, bookID: bookID, canonical: canonical)
                    if book == nil {
                        _ = try? await page.waitFor(
                            "document.querySelector('div.C-Fo-Zuo div.DivTable a, .chapter-list a, .chapterList a, .catalog a')",
                            timeout: 10)
                        html = try await page.html()
                        pageURL = await page.currentURL() ?? target
                        book = FlooParser.book(fromHTML: [html], pageURL: pageURL, bookID: bookID, canonical: canonical)
                    }
                    if let book { return book }
                }
                return nil
            }
            if let found { return found }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            context.logger.log(.warning, "[floo] Browser book request failed: \(error.localizedDescription)")
        }
        throw SourceError.parse("floo: book title or chapter catalog was not found.")
    }

    // MARK: Chapter

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let shared = context.shared(for: Self.id)
        if ref.isPaid && shared.skipPaid {
            return .refused(ref, reason: "Paid chapter skipped (skip paid is on).")
        }
        guard let url = ref.url else { return .failed(ref, message: "Chapter has no URL.") }

        var outcome: FlooChapterOutcome = .none
        // A guest preview of a paid chapter can look like full text, so paid chapters are
        // always read in the signed-in web profile.
        if !ref.isPaid, let response = try? await Self.fetch(url, http: context.http) {
            outcome = FlooParser.chapter(fromHTML: FlooParser.decodings(response.body), pageURL: response.url)
        }
        if Self.needsBrowser(outcome) {
            if let rendered = await Self.browserOutcome(url, context: context) { outcome = rendered }
        }
        // The chapter loop rests the per-chapter delay between chapters.

        switch outcome {
        case .text(let lines):
            return .content(ChapterContent(ref: ref, html: FlooParser.chapterHTML(lines)))
        case .images(let urls):
            return await Self.imageChapter(ref, urls: urls, referer: url, http: context.http)
        case .locked(let reason):
            if reason == "login" {
                return .refused(ref, reason: "Sign in to floo in the app to read this chapter.")
            }
            return .refused(ref, reason: "This chapter has not been bought by the signed-in account.")
        case .none:
            return .failed(ref, message: "Could not read this chapter.")
        }
    }

    // MARK: Fetching

    static func headers() -> [String: String] {
        [
            "User-Agent": FlooParser.userAgent,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://\(FlooParser.bookHost)/",
        ]
    }

    /// GET that follows the site's `C3VK` cookie challenge (up to three requests in all).
    static func fetch(_ url: URL, http: any HTTPFetching) async throws -> HTTPResponse {
        var response = try await http.fetchOK(url, headers: headers())
        for _ in 0..<2 {
            guard let token = FlooParser.challengeToken(in: response.body) else { break }
            storeChallengeCookie(token)
            response = try await http.fetchOK(url, headers: headers())
        }
        return response
    }

    private static func storeChallengeCookie(_ value: String) {
        let properties: [HTTPCookiePropertyKey: Any] = [
            .name: "C3VK", .value: value, .domain: FlooParser.bookHost, .path: "/",
        ]
        if let cookie = HTTPCookie(properties: properties) {
            HTTPCookieStorage.shared.setCookie(cookie)
        }
    }

    static func needsBrowser(_ outcome: FlooChapterOutcome) -> Bool {
        switch outcome {
        case .none, .locked: return true
        case .text, .images: return false
        }
    }

    /// Renders the chapter in the signed-in web profile. Nil when the page could not be loaded.
    static func browserOutcome(_ url: URL, context: SourceContext) async -> FlooChapterOutcome? {
        do {
            return try await context.web.withPage(profile: WebProfile.external) { page in
                try await page.load(url, until: .domContentLoaded, timeout: 30)
                _ = try? await page.waitFor("document.querySelector('\(FlooParser.contentSelector)')", timeout: 8)
                let html = try await page.html()
                let finalURL = await page.currentURL() ?? url
                return FlooParser.chapter(fromHTML: [html], pageURL: finalURL)
            }
        } catch {
            context.logger.log(.warning, "[floo] Browser chapter failed: \(error.localizedDescription)")
            return nil
        }
    }

    /// Downloads a bought VIP chapter's images. A non-image answer means the account cannot read it.
    static func imageChapter(_ ref: ChapterRef, urls: [URL], referer: URL, http: any HTTPFetching) async -> ChapterResult {
        var images: [ImageRef] = []
        for (offset, url) in urls.enumerated() {
            var requestHeaders = headers()
            requestHeaders["Referer"] = referer.absoluteString
            requestHeaders["Accept"] = "image/gif,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5"
            let data: Data
            do {
                data = try await http.fetchOK(url, headers: requestHeaders).body
            } catch HTTPError.status(let code, _) where code == 401 || code == 403 {
                return .refused(ref, reason: "The signed-in account cannot read this chapter's images.")
            } catch {
                return .failed(ref, message: "Could not download chapter image \(offset + 1): \(error.localizedDescription)")
            }
            guard let ext = FlooParser.imageExtension(data) else {
                return .refused(ref, reason: "This chapter has not been bought by the signed-in account.")
            }
            let filename = FlooParser.imageFilename(chapterIndex: ref.index, number: offset + 1, ext: ext)
            images.append(ImageRef(url: url, filename: filename, data: data))
        }
        let html = FlooParser.imageHTML(images.map { $0.filename })
        return .content(ChapterContent(ref: ref, html: html, images: images))
    }
}
