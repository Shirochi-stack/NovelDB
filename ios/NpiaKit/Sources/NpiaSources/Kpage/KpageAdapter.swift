import Foundation
import NpiaCore

/// Hosts and endpoints for the kpage source (escaped so the provider name stays out of the repo).
enum KpageHosts {
    static let siteHost = "page.\u{6b}\u{61}\u{6b}\u{61}\u{6f}.com"
    static let apiHost = "bff-page.\u{6b}\u{61}\u{6b}\u{61}\u{6f}.com"
    static let site = "https://" + siteHost
    static let api = "https://" + apiHost

    static let userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    static func apiHeaders() -> [String: String] {
        [
            "User-Agent": userAgent,
            "Referer": site + "/",
            "Origin": site,
            "Accept": "application/json, text/plain, */*",
        ]
    }
}

private struct KpageBookPage: Sendable {
    var html: String
    var tags: [String]
}

private struct KpageBrowserFetch: Decodable {
    var status: Int
    var text: String
}

/// kpage: metadata from the rendered content page (web engine), episode list and viewer data
/// from the site's JSON gateway (plain HTTP, browser fallback), text chunks as JSON paragraph
/// lists, and image-only episodes as image pages. Paid episodes the account cannot open are
/// refused; nothing is ever purchased or rented.
public struct KpageAdapter: SourceAdapter {
    public static let id: SourceID = .kpage
    public static let tier: SourceTier = .lightBrowser

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return false }
        guard (url.host ?? "").lowercased() == KpageHosts.siteHost else { return false }
        return WebRegex.matches("^/content/\\d+", in: url.path)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let seriesId = KpageParsing.seriesID(from: url.absoluteString) else {
            throw SourceError.unsupportedURL(url)
        }
        context.logger.log(.info, "[kpage] Navigating to: \(url.absoluteString)")

        let aboutURL: URL? = {
            var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
            components?.query = "tab_type=about"
            components?.fragment = nil
            return components?.url
        }()

        let loaded: KpageBookPage = try await context.web.withPage(profile: WebProfile.external) { page in
            try await page.load(url, until: .domContentLoaded, timeout: 30)
            // The page is a SPA; wait for the title block to render.
            _ = try? await page.waitFor(
                "document.querySelector('h2') && document.querySelector('meta[property=\"og:title\"]')",
                timeout: 12)
            try await Task.sleep(nanoseconds: 1_000_000_000)
            let html = try await page.html()

            var tags: [String] = []
            if let about = aboutURL {
                do {
                    try await page.load(about, until: .domContentLoaded, timeout: 15)
                    try await Task.sleep(nanoseconds: 1_500_000_000)
                    tags = try await page.evaluate([String].self, Self.tagScript)
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    tags = []
                }
            }
            return KpageBookPage(html: html, tags: tags)
        }

        let meta = KpageParsing.parseMetadata(html: loaded.html)
        if meta.title.isEmpty { throw SourceError.parse("Could not extract the title") }
        context.logger.log(.info, "[kpage] Title: \(meta.title), author: \(meta.author)")

        // Episode list: one request returns every episode.
        guard let listURL = URL(string: "\(KpageHosts.api)/api/gateway/api/v2/content/product/list?series_id=\(seriesId)&cursor_index=0&cursor_direction=ANCHOR&window_size=10000&sort_opt=asc") else {
            throw SourceError.parse("Bad episode list URL")
        }
        let listing = try await fetchAPI(listURL, contentPage: url, context: context)
        let episodes = KpageParsing.parseEpisodes(json: listing.body)
        if episodes.isEmpty {
            context.logger.log(.warning, "[kpage] No episodes found (HTTP \(listing.status)).")
        }

        var chapters: [ChapterRef] = []
        for (offset, episode) in episodes.enumerated() {
            let viewer = URL(string: "\(KpageHosts.site)/content/\(seriesId)/viewer/\(episode.productId)")
            let title = episode.fullName.isEmpty ? episode.shortName : episode.fullName
            chapters.append(ChapterRef(
                id: String(episode.productId), index: offset + 1, title: title,
                url: viewer, isPaid: !episode.isAccessible))
        }
        let rentedOrBought = episodes.filter { $0.isAccessible && !$0.isFree }.count
        if rentedOrBought > 0 {
            context.logger.log(.info, "[kpage] Detected \(rentedOrBought) rented/purchased episode(s) as accessible.")
        }

        var coverText = meta.cover
        if coverText.hasPrefix("//") { coverText = "https:" + coverText }
        return BookInfo(
            source: Self.id, id: seriesId, url: url, title: meta.title,
            author: meta.author.isEmpty ? nil : meta.author,
            synopsis: meta.description.isEmpty ? nil : meta.description,
            coverURL: coverText.isEmpty ? nil : URL(string: coverText),
            tags: loaded.tags, language: "ko", chapters: chapters)
    }

    private static let tagScript = """
    const tags = [];
    const nodes = document.querySelectorAll('a, span, div');
    for (let i = 0; i < nodes.length; i++) {
      const t = (nodes[i].innerText || '').trim();
      if (t.startsWith('#') && t.length > 1 && t.length < 30) {
        const tag = t.substring(1);
        if (tags.indexOf(tag) === -1) tags.push(tag);
      }
    }
    return tags;
    """

    // MARK: Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        try await readChapter(ref, context: context, leadingImageBytes: 0)
    }

    /// Concurrent like the default, plus the optional cross-chapter duplicate image removal.
    public func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        let shared = context.shared(for: Self.id)
        let limit = max(1, shared.threads)
        let options = context.settings.sources.kpage
        let leading = options.dedupeImages ? max(1, min(10, options.dedupeLeadingImages)) : 0

        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var pending: [(offset: Int, element: ChapterRef)] = refs.enumerated().reversed()
        try await withThrowingTaskGroup(of: (Int, ChapterResult).self) { group in
            for _ in 0..<limit {
                guard let item = pending.popLast() else { break }
                group.addTask { try await self.chapterTask(item.offset, item.element, context: context, leading: leading, delay: nil) }
            }
            while let (position, result) = try await group.next() {
                results[position] = result
                context.onChapter(result)
                if let item = pending.popLast() {
                    group.addTask { try await self.chapterTask(item.offset, item.element, context: context, leading: leading, delay: shared) }
                }
            }
        }
        var ordered: [ChapterResult] = results.compactMap { $0 }
        if leading > 0 {
            var contents: [ChapterContent] = []
            for result in ordered {
                if case .content(let content) = result { contents.append(content) }
            }
            let deduped = KpageParsing.removeDuplicateLeadingImages(contents, maxPositions: leading)
            var cursor = 0
            var rebuilt: [ChapterResult] = []
            for result in ordered {
                if case .content = result, cursor < deduped.count {
                    rebuilt.append(.content(deduped[cursor]))
                    cursor += 1
                } else {
                    rebuilt.append(result)
                }
            }
            ordered = rebuilt
        }
        return ordered
    }

    private func chapterTask(_ position: Int, _ ref: ChapterRef, context: SourceContext, leading: Int, delay: SharedSettings?) async throws -> (Int, ChapterResult) {
        if let delay { try await delay.sleepChapterDelay() }
        do {
            return (position, try await readChapter(ref, context: context, leadingImageBytes: leading))
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            return (position, .failed(ref, message: error.localizedDescription))
        }
    }

    private func readChapter(_ ref: ChapterRef, context: SourceContext, leadingImageBytes: Int) async throws -> ChapterResult {
        if context.shared(for: Self.id).skipPaid && ref.isPaid {
            return .refused(ref, reason: "Paid episode skipped (skip paid is on).")
        }
        guard let urlText = ref.url?.absoluteString, let ids = KpageParsing.viewerIDs(from: urlText) else {
            return .failed(ref, message: "Invalid viewer URL.")
        }
        guard let viewerURL = URL(string: "\(KpageHosts.api)/api/gateway/api/v1/viewer/data?series_id=\(ids.series)&product_id=\(ids.product)"),
              let contentPage = URL(string: "\(KpageHosts.site)/content/\(ids.series)") else {
            return .failed(ref, message: "Invalid viewer URL.")
        }

        let response = try await fetchAPI(viewerURL, contentPage: contentPage, context: context)
        let outcome = KpageParsing.parseViewer(response.body, status: response.status)

        switch outcome {
        case .locked(let reason):
            return .refused(ref, reason: "Not purchased or rentable with this account (\(reason)). Nothing was bought.")

        case .unavailable(let reason):
            if response.status == 401 || response.status == 403 {
                return .refused(ref, reason: "Access denied (HTTP \(response.status)). Sign in with an account that owns this episode.")
            }
            return .failed(ref, message: "Viewer data unavailable: \(reason) (HTTP \(response.status)).")

        case .images(let files):
            guard var built = KpageParsing.buildImageChapter(
                files, chapterName: ref.title, chapterIndex: ref.index, baseURL: contentPage) else {
                return .failed(ref, message: "No images in this episode.")
            }
            if leadingImageBytes > 0 {
                for position in 0..<min(leadingImageBytes, built.images.count) {
                    if let bytes = await fetchImageBytes(built.images[position].url, context: context) {
                        built.images[position].data = bytes
                    }
                }
            }
            return .content(ChapterContent(ref: ref, html: built.html, images: built.images))

        case .text(let baseURL, let secureURLs):
            var chunks: [Data] = []
            for secure in secureURLs {
                guard let chunkURL = URL(string: baseURL + secure) else { continue }
                do {
                    let chunk = try await context.http.get(chunkURL, headers: [
                        "User-Agent": KpageHosts.userAgent,
                        "Referer": KpageHosts.site + "/",
                    ])
                    let contentType = chunk.headers.first { $0.key.lowercased() == "content-type" }?.value ?? ""
                    guard (200..<300).contains(chunk.status), contentType.lowercased().contains("json") else { continue }
                    chunks.append(chunk.body)
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    continue
                }
            }
            let options = context.settings.sources.kpage
            let paragraphs = KpageParsing.extractParagraphs(
                chunks, skipLastPage: options.skipLastPage, keepFiller: options.keepFiller)
            if paragraphs.isEmpty {
                return .failed(ref, message: "No readable text in this episode.")
            }
            let output = KpageParsing.buildOutput(paragraphs)
            var titled = ref
            titled.title = KpageParsing.headingTitle(paragraphs, fallback: ref.title)
            return .content(ChapterContent(ref: titled, html: output.html))
        }
    }

    private func fetchImageBytes(_ url: URL, context: SourceContext) async -> Data? {
        do {
            let response = try await context.http.get(url, headers: [
                "User-Agent": KpageHosts.userAgent,
                "Referer": KpageHosts.site + "/",
            ])
            guard (200..<300).contains(response.status), !response.body.isEmpty else { return nil }
            return response.body
        } catch {
            return nil
        }
    }

    // MARK: Gateway requests

    private static func looksLikeJSON(_ data: Data) -> Bool {
        let text = String(decoding: data.prefix(64), as: UTF8.self)
        let first = text.trimmingCharacters(in: .whitespacesAndNewlines).first
        return first == "{" || first == "["
    }

    /// GET a gateway JSON endpoint with plain HTTP; when that is refused or not JSON (the gateway
    /// can reject requests without a page origin), repeat the request from inside the content page.
    private func fetchAPI(_ url: URL, contentPage: URL, context: SourceContext) async throws -> (status: Int, body: Data) {
        var status = 0
        var body = Data()
        do {
            let response = try await context.http.get(url, headers: KpageHosts.apiHeaders())
            status = response.status
            body = response.body
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            status = 0
        }
        let needsFallback = status == 0 || status == 401 || status == 403 || !Self.looksLikeJSON(body)
        if needsFallback {
            if let browser = try? await browserFetch(url, contentPage: contentPage, context: context),
               Self.looksLikeJSON(Data(browser.text.utf8)) {
                return (browser.status, Data(browser.text.utf8))
            }
        }
        return (status, body)
    }

    private func browserFetch(_ url: URL, contentPage: URL, context: SourceContext) async throws -> (status: Int, text: String) {
        let literal = Self.jsLiteral(url.absoluteString)
        let script = """
        const response = await fetch(\(literal), { credentials: 'include' });
        const text = await response.text();
        return { status: response.status, text: text };
        """
        return try await context.web.withPage(profile: WebProfile.external) { page in
            try await page.load(contentPage, until: .domContentLoaded, timeout: 30)
            let result = try await page.evaluate(KpageBrowserFetch.self, script)
            return (result.status, result.text)
        }
    }

    static func jsLiteral(_ text: String) -> String {
        guard let data = try? JSONEncoder().encode([text]) else { return "\"\"" }
        let encoded = String(decoding: data, as: UTF8.self)
        // `["..."]` -> `"..."`
        return String(encoded.dropFirst().dropLast())
    }
}
