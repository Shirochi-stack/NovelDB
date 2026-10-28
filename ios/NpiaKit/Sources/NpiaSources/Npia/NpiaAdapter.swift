import Foundation
import NpiaCore

/// npia over plain HTTP, ported from `downloader_core.py` (`fetch_metadata`,
/// `fetch_chapter_list`, `download_chapter_content`, `fetch_notice_ids`) and `npia_auth.py`.
///
/// TODO(Phase 5): `settings.npia.useBrowserFallback` (the desktop viewer-page fallback for
/// chapters the HTTP payload cannot deliver) needs the web engine and device testing.
public struct NpiaAdapter: SourceAdapter {
    public static let id: SourceID = .npia
    public static let tier: SourceTier = .http
    public static let sites: Set<String> = [NpiaParser.host]

    var cookies: any NpiaCookieSetting
    /// Seconds to wait before retry `attempt` (1-based); tests pass zero.
    var retryDelay: @Sendable (Int) -> Double

    public init() {
        self.init(cookies: SharedCookieStorageJar())
    }

    public init(cookies: any NpiaCookieSetting, retryDelay: @escaping @Sendable (Int) -> Double = { min(8, pow(2, Double($0 - 1))) }) {
        self.cookies = cookies
        self.retryDelay = retryDelay
    }

    public static func matches(_ url: URL) -> Bool {
        NpiaParser.novelID(from: url) != nil
    }

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let novelID = NpiaParser.novelID(from: url) else { throw SourceError.unsupportedURL(url) }
        try await NpiaAuth.prepare(context: context, cookies: cookies)

        let pageURL = NpiaParser.novelURL(novelID)
        let page = try await context.http.send(HTTPRequest(url: pageURL, headers: NpiaAuth.headers(), timeout: 15))
        guard page.status == 200 else { throw HTTPError.status(page.status, pageURL) }
        guard !page.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw SourceError.parse("empty novel page")
        }
        let meta = NpiaParser.parseMetadata(page.text, novelID: novelID)
        context.logger.log(.info, "Metadata acquired: \(meta.title) by \(meta.author)")

        var notices: [ChapterRef] = []
        if context.settings.npia.includeNotices {
            notices = NpiaParser.parseNotices(page.text).enumerated().map { offset, notice in
                ChapterRef(id: notice.id, index: offset + 1, title: notice.title, url: NpiaParser.viewerURL(notice.id), isNotice: true)
            }
        }

        let chapters = try await fetchChapterList(novelID, context: context)
        guard !chapters.isEmpty else { throw SourceError.parse("no chapters found") }

        return BookInfo(
            source: .npia, id: novelID, url: pageURL, title: meta.title, author: meta.author,
            synopsis: meta.synopsis.isEmpty ? nil : meta.synopsis, coverURL: meta.coverURL,
            tags: meta.tags, status: meta.status, language: "ko", chapters: chapters, notices: notices
        )
    }

    func fetchChapterList(_ novelID: String, context: SourceContext) async throws -> [ChapterRef] {
        let url = URL(string: "https://\(NpiaParser.host)/proc/episode_list")!
        let headers = NpiaAuth.headers(referer: NpiaParser.novelURL(novelID).absoluteString, form: true)
        var chapters: [ChapterRef] = []
        var seen = Set<String>()
        var page = 0
        // The desktop loop runs until a page adds nothing; the cap only guards against a server
        // that keeps returning new ids forever.
        while page < 5000 {
            try Task.checkCancellation()
            let body = NpiaAuth.formBody([("novel_no", novelID), ("sort", "DOWN"), ("page", String(page))])
            let response = try await context.http.send(HTTPRequest(url: url, method: "POST", headers: headers, body: body, timeout: 30))
            guard response.status == 200 else {
                throw SourceError.parse("chapter list page \(page) returned HTTP \(response.status); stopping to avoid an incomplete book")
            }
            if response.text.contains("Authentication required") {
                if chapters.isEmpty { throw SourceError.loginRequired(.npia) }
                context.logger.log(.warning, "Authentication required during chapter scan; keeping \(chapters.count) chapters.")
                break
            }
            let rows = NpiaParser.parseEpisodePage(response.text)
            if rows.isEmpty { break }
            var added = 0
            for row in rows where seen.insert(row.id).inserted {
                chapters.append(ChapterRef(id: row.id, index: chapters.count + 1, title: row.title, url: NpiaParser.viewerURL(row.id), isPaid: row.isPaid))
                added += 1
            }
            if added == 0 && page > 0 { break }
            page += 1
        }
        context.logger.log(.info, "Found \(chapters.count) chapters.")
        return chapters
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let url = URL(string: "https://\(NpiaParser.host)/proc/viewer_data/\(ref.id)")!
        let headers = NpiaAuth.headers(referer: NpiaParser.viewerURL(ref.id).absoluteString, form: true)
        let attempts = max(1, min(context.shared(for: .npia).maxRetries, 5))
        var lastProblem = ""

        for attempt in 1...attempts {
            try Task.checkCancellation()
            if attempt > 1 {
                let delay = retryDelay(attempt - 1)
                if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
            }
            let response: HTTPResponse
            do {
                response = try await context.http.send(HTTPRequest(url: url, method: "POST", headers: headers, body: Data(), timeout: 15))
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                lastProblem = error.localizedDescription
                continue
            }
            if response.status == 429 { return .failed(ref, message: "HTTP 429 rate limit") }
            guard response.status == 200 else {
                lastProblem = "HTTP \(response.status)"
                continue
            }
            let text = response.text
            if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                // The viewer answers unpurchased PLUS chapters with an empty body.
                if ref.isPaid { return .refused(ref, reason: "not purchased or no PLUS subscription") }
                lastProblem = "empty response body"
                continue
            }
            switch NpiaParser.validatePayload(text) {
            case .failure(.blocked(let reason)):
                return .refused(ref, reason: reason)
            case .failure(.invalid(let reason)):
                if ref.isPaid, attempt == attempts { return .refused(ref, reason: reason) }
                lastProblem = reason
                continue
            case .success(let segments):
                let options = NpiaParser.RenderOptions(
                    stripLeadingSpaces: context.settings.text.stripLeadingSpaces,
                    removeNewlines: context.settings.text.removeNewlines,
                    saveImageURLsOnly: context.settings.images.saveImageURLsOnly,
                    fontMapping: FontMapping.load(),
                    imagePrefix: (ref.isNotice ? "n" : "") + String(format: "%04d", ref.index)
                )
                let rendered = NpiaParser.renderSegments(segments, options: options)
                return .content(ChapterContent(ref: ref, html: rendered.html, images: rendered.images))
            }
        }
        return .failed(ref, message: "failed after \(attempts) attempts (\(lastProblem))")
    }
}
