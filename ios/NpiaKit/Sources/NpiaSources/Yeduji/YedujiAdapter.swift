import Foundation
import NpiaCore

/// Chinese novel site with free and VIP chapters. Port of `_yeduji_*` in the desktop
/// `external_scraper.py`. VIP chapters only show a preview, so they are refused.
public struct YedujiAdapter: SourceAdapter {
    public static let id: SourceID = .yeduji
    public static let tier: SourceTier = .http

    static let headers = [
        "User-Agent": HTTPSourceSupport.desktopUserAgent,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    ]
    private static let dotAll: NSRegularExpression.Options = [.dotMatchesLineSeparators]
    static let vipNotice = "以下内容为VIP专属"
    static let promo = "夜读集由爱发电"

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        let host = (url.host ?? "").lowercased()
        return (host == "yeduji.com" || host == "www.yeduji.com")
            && url.path.range(of: "^/book/\\d+", options: .regularExpression) != nil
    }

    static func bookID(_ url: URL) -> String? {
        HTTPSourceSupport.firstMatch("/book/(\\d+)", in: url.path)
    }

    private static func fetch(_ url: URL, http: any HTTPFetching) async throws -> String {
        try await HTTPSourceSupport.get(url, headers: headers, http: http).text
    }

    static func stripTags(_ value: String) -> String {
        HTTPSourceSupport.unescapeHTML(value.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression))
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let bookID = Self.bookID(url) else { throw SourceError.unsupportedURL(url) }
        let origin = "\(url.scheme ?? "https")://\(url.host ?? "yeduji.com")"
        let indexURL = URL(string: "\(origin)/book/\(bookID)/")!
        let listURL = URL(string: "\(origin)/book/\(bookID)/list/")!
        let indexHTML = try await Self.fetch(indexURL, http: context.http)
        let listHTML = try await Self.fetch(listURL, http: context.http)
        return try Self.book(indexHTML: indexHTML, listHTML: listHTML, bookID: bookID, indexURL: indexURL, listURL: listURL)
    }

    static func book(indexHTML: String, listHTML: String, bookID: String, indexURL: URL, listURL: URL) throws -> BookInfo {
        var title = HTTPSourceSupport.firstMatch("<h1[^>]*>(.*?)</h1>", in: indexHTML, options: dotAll)
            .map { HTTPSourceSupport.unescapeHTML($0.trimmingCharacters(in: .whitespacesAndNewlines)) } ?? ""
        let info = HTTPSourceSupport.firstMatch("<div\\s+class=[\"']info[\"'][^>]*>(.*?)</div>", in: indexHTML, options: dotAll) ?? ""

        func field(_ label: String) -> String? {
            HTTPSourceSupport.firstMatch(
                "<dt>\\s*" + NSRegularExpression.escapedPattern(for: label) + "\\s*</dt>\\s*<dd>(.*?)</dd>",
                in: info, options: dotAll
            )
        }
        func links(_ label: String) -> [String] {
            guard let value = field(label) else { return [] }
            return HTTPSourceSupport.allMatches("<a[^>]*>(.*?)</a>", in: value, options: dotAll)
                .map { HTTPSourceSupport.unescapeHTML($0[0].trimmingCharacters(in: .whitespacesAndNewlines)) }
                .filter { !$0.isEmpty }
        }

        let pageTitle = HTTPSourceSupport.firstMatch("<title>(.*?)</title>", in: indexHTML, options: dotAll) ?? ""
        var author = field("作者").map(stripTags) ?? ""
        if author.isEmpty {
            author = HTTPSourceSupport.firstMatch("-\\s*(.+?)\\s*著\\s*-", in: pageTitle)?
                .trimmingCharacters(in: .whitespaces) ?? ""
        }
        if title.isEmpty {
            title = pageTitle.components(separatedBy: " - ").first?.trimmingCharacters(in: .whitespaces) ?? ""
        }
        let tags = links("标签")
        let category = links("分类")
        let status = field("状态").map(stripTags) ?? ""

        let cover = HTTPSourceSupport.firstMatch("<img[^>]+src=[\"']([^\"']*?/data/cover/[^\"']*)[\"'\\s]", in: indexHTML, options: .caseInsensitive)
            .flatMap { URL(string: $0, relativeTo: indexURL)?.absoluteURL }

        var synopsis = ""
        if var inner = HTTPSourceSupport.firstMatch("<div\\s+class=[\"']desc[\"'][^>]*>(.*?)</div>", in: indexHTML, options: dotAll) {
            inner = inner.replacingOccurrences(of: "<img[^>]*>", with: "", options: .regularExpression)
            if let content = HTTPSourceSupport.firstMatch(
                "<p[^>]*class=[\"'][^\"']*desc-content[^\"']*[\"'][^>]*>(.*?)</p>", in: inner, options: dotAll
            ) {
                inner = content
            }
            synopsis = stripTags(inner.replacingOccurrences(of: "<br\\s*/?>", with: "\n", options: [.regularExpression, .caseInsensitive]))
        }

        var chapters: [ChapterRef] = []
        let pattern = "<a\\s+data-chapterId=[\"']([^\"']*)[\"'\\s][^>]*href=[\"']([^\"']*?/book/\\d+/\\d+\\.html)[\"'\\s][^>]*>(.*?)</a>"
        for groups in HTTPSourceSupport.allMatches(pattern, in: listHTML, options: dotAll) {
            let chapterID = groups[0], href = groups[1], inner = groups[2]
            let name = HTTPSourceSupport.firstMatch("<h4[^>]*>(.*?)</h4>", in: inner, options: dotAll).map(stripTags) ?? "Chapter \(chapterID)"
            let isVIP = inner.range(of: "<small[^>]*>\\s*VIP\\s*</small>", options: [.regularExpression, .caseInsensitive]) != nil
            guard let chapterURL = URL(string: href, relativeTo: listURL)?.absoluteURL else { continue }
            chapters.append(ChapterRef(id: chapterID, index: chapters.count + 1, title: name, url: chapterURL, isPaid: isVIP))
        }
        guard !chapters.isEmpty else { throw SourceError.parse("no chapters found on the list page") }

        return BookInfo(
            source: id, id: bookID, url: indexURL, title: title.isEmpty ? "Book \(bookID)" : title,
            author: author.isEmpty ? nil : author, synopsis: synopsis.isEmpty ? nil : synopsis,
            coverURL: cover, tags: tags + category.filter { !tags.contains($0) },
            status: status.isEmpty ? nil : status, language: "zh", chapters: chapters
        )
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        if ref.isPaid {
            // The page only carries a preview for VIP chapters; never save it as the chapter.
            return .refused(ref, reason: "VIP chapter (preview only)")
        }
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        return Self.chapter(html: try await Self.fetch(url, http: context.http), ref: ref)
    }

    static func chapter(html: String, ref: ChapterRef) -> ChapterResult {
        guard let content = HTTPSourceSupport.firstMatch("<div\\s+class=[\"']content[\"'][^>]*>(.*?)</div>", in: html, options: dotAll) else {
            return .failed(ref, message: "no content block")
        }
        if content.contains(vipNotice) {
            return .refused(ref, reason: "VIP chapter (preview only)")
        }
        var parts: [String] = []
        for groups in HTTPSourceSupport.allMatches("<p[^>]*>(.*?)</p>", in: content, options: dotAll) {
            let raw = groups[0].trimmingCharacters(in: .whitespacesAndNewlines)
            let plain = stripTags(raw)
            if plain.isEmpty || plain.contains(promo) { continue }
            if plain.contains("请先"), plain.contains("登录"), plain.contains("后查看完整内容") { continue }
            parts.append("<p>\(ReaderBlocks.escape(plain, quote: false))</p>")
        }
        guard !parts.isEmpty else { return .failed(ref, message: "no text content") }
        return .content(ChapterContent(ref: ref, html: "<div class=\"yeduji-content\">\n" + parts.joined(separator: "\n") + "\n</div>"))
    }
}
