import Foundation
import NpiaCore
import SwiftSoup

/// One catalog entry found on a book page.
struct XiyuwxLink: Equatable {
    var url: URL
    var name: String
    var paid: Bool
}

/// One parsed chapter page (a chapter can span several pages).
struct XiyuwxChapterPage: Equatable {
    var lines: [String]
    var nextPage: URL?
}

/// Pure parsing for xiyuwx and its yywenxuan mirror (`_xiyuwx_*` in the desktop scraper).
enum XiyuwxParser {
    static let hosts: Set<String> = ["xiyuwx.com", "www.xiyuwx.com", "yywenxuan.com", "www.yywenxuan.com"]
    static let userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    static let maxPages = 30

    // MARK: URLs

    static func bookID(_ url: URL) -> String {
        guard let host = url.host?.lowercased(), hosts.contains(host) else { return "" }
        let path = rawPath(url)
        let groups = WebRegex.firstMatch("^/(?:book/)?([1-9][0-9]*)(?:/|$)", in: path, options: [.caseInsensitive])
        guard let groups, groups.count > 1, let id = groups[1] else { return "" }
        return id
    }

    static func matches(_ url: URL) -> Bool { !bookID(url).isEmpty }

    static func siteName(_ url: URL) -> String {
        (url.host?.lowercased() ?? "").hasSuffix("yywenxuan.com") ? "YY Wenxuan" : "Xiyuwx"
    }

    /// Path with its trailing slash preserved.
    static func rawPath(_ url: URL) -> String {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?.path ?? url.path
    }

    /// `/<id>/` or `/book/<id>/` on the same host as `url`.
    static func canonicalBookURL(_ url: URL, bookID: String) -> URL {
        let prefix = rawPath(url).hasPrefix("/book/") ? "book/" : ""
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false) ?? URLComponents()
        components.path = "/\(prefix)\(bookID)/"
        components.query = nil
        components.fragment = nil
        return components.url ?? url
    }

    // MARK: Decoding

    /// Some chapters contain invalid bytes despite declaring UTF-8; decode those lossily
    /// instead of falling through to another charset.
    static func decodePage(_ data: Data) -> String {
        let head = String(decoding: data.prefix(4096), as: UTF8.self)
        if let name = WebRegex.capture("charset\\s*=\\s*[\"']?([a-zA-Z0-9_-]+)", in: head, options: [.caseInsensitive]) {
            let lowered = name.lowercased()
            if lowered == "utf-8" || lowered == "utf8" {
                return String(decoding: data, as: UTF8.self)
            }
        }
        return WebHTML.decode(data)
    }

    /// The `_ii_rr` ordering map: base64 of comma separated integers.
    static func decodePositions(_ encoded: String) -> [Int]? {
        var padded = encoded.trimmingCharacters(in: .whitespacesAndNewlines)
        while padded.count % 4 != 0 { padded += "=" }
        guard let data = Data(base64Encoded: padded), let text = String(data: data, encoding: .utf8) else { return nil }
        var values: [Int] = []
        for part in text.split(separator: ",", omittingEmptySubsequences: false) {
            guard let value = Int(part.trimmingCharacters(in: .whitespaces)) else { return nil }
            values.append(value)
        }
        return values.isEmpty ? nil : values
    }

    /// Reorders shuffled paragraphs: `positions[0]` is the offset, every following value
    /// gives the offset position of the paragraph to emit next. Nil unless it is a permutation.
    static func unshuffle(_ pieces: [String], positions: [Int]) -> [String]? {
        guard let offset = positions.first else { return nil }
        let indexes: [Int] = positions.dropFirst().map { $0 - offset }
        guard indexes.count == pieces.count, indexes.sorted() == Array(0..<pieces.count) else { return nil }
        return indexes.map { pieces[$0] }
    }

    /// Splits `raw` on `<br><br>` pairs (any spacing and self-closing form).
    static func splitParagraphs(_ raw: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: "<br\\s*/?>\\s*<br\\s*/?>", options: [.caseInsensitive]) else { return [raw] }
        var pieces: [String] = []
        var cursor = raw.startIndex
        for match in regex.matches(in: raw, range: NSRange(raw.startIndex..., in: raw)) {
            guard let range = Range(match.range, in: raw) else { continue }
            pieces.append(String(raw[cursor..<range.lowerBound]))
            cursor = range.upperBound
        }
        pieces.append(String(raw[cursor...]))
        return pieces
    }

    /// Replaces only the first match (`re.sub(..., count=1)`).
    static func replaceFirst(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              let range = Range(match.range, in: text) else { return text }
        return text.replacingCharacters(in: range, with: "")
    }

    // MARK: Documents

    static func parse(_ html: String, baseURL: URL?) -> Document? {
        guard let document = try? WebHTML.parse(html, baseURL: baseURL) else { return nil }
        _ = document.outputSettings().prettyPrint(pretty: false)
        return document
    }

    static func isVerificationPage(_ html: String) -> Bool {
        guard let document = parse(html, baseURL: nil) else { return false }
        let title = (try? document.title()) ?? ""
        return WebRegex.matches("Just a moment|Checking your browser", in: title, options: [.caseInsensitive])
    }

    // MARK: Book

    static func chapterLinks(_ document: Document, baseURL: URL, bookID: String) -> [XiyuwxLink] {
        let escapedID = NSRegularExpression.escapedPattern(for: bookID)
        let prefixMatch = WebRegex.firstMatch("^/(?:book/)?\(escapedID)/", in: rawPath(baseURL), options: [.caseInsensitive])
        var prefix = "/\(bookID)/"
        if let groups = prefixMatch, let whole = groups.first, let text = whole { prefix = text }
        let selectors = [
            "#list a[href]", "#chapterlist a[href]", ".chapterlist a[href]",
            ".chapter-list a[href]", ".chapter_list a[href]",
            ".listmain a[href]", ".list-chapter a[href]",
            ".catalog a[href]", ".directory a[href]", "dl dd a[href]",
        ]
        var links: [Element] = []
        var catalogFound = false
        // The template puts a reversed "latest chapters" preview before the full catalog;
        // read the links after the last body heading to keep reading order.
        let headings = WebHTML.select("#list dt", in: document)
        let bodyMarker = "\u{6b63}\u{6587}"
        if let heading = headings.reversed().first(where: { WebHTML.text($0).contains(bodyMarker) }) {
            var sibling: Element? = try? heading.nextElementSibling()
            while let node = sibling {
                let tag = node.tagName().lowercased()
                if tag == "dt" { break }
                if tag == "a", !WebHTML.attr(node, "href").isEmpty { links.append(node) }
                sibling = try? node.nextElementSibling()
            }
            catalogFound = !links.isEmpty
        }
        for selector in selectors {
            if !links.isEmpty { break }
            links = WebHTML.select(selector, in: document)
            if !links.isEmpty { catalogFound = true; break }
        }
        if links.isEmpty { links = WebHTML.select("a[href]", in: document) }

        let chapterLike = "\u{7b2c}.{1,12}[\u{7ae0}\u{8282}\u{56de}\u{5377}]|\u{5e8f}\u{7ae0}|\u{6954}\u{5b50}|\u{524d}\u{8a00}|\u{540e}\u{8bb0}|\u{756a}\u{5916}|\u{6b63}\u{6587}"
        let navigation = "^(?:\u{4e0a}\u{4e00}\u{7ae0}|\u{4e0b}\u{4e00}\u{7ae0}|\u{8fd4}\u{56de}\u{76ee}\u{5f55}|\u{7ae0}\u{8282}\u{76ee}\u{5f55}|\u{5168}\u{90e8}\u{7ae0}\u{8282}|\u{76ee}\u{5f55})$"
        let lockMarkup = "\\bvip\\b|locked|paywall|\u{4ed8}\u{8d39}|\u{6536}\u{8d39}|\u{1f512}"

        var chapters: [XiyuwxLink] = []
        var seen = Set<String>()
        for link in links {
            let name = WebHTML.text(link)
            guard let joined = WebHTML.join(baseURL, WebHTML.attr(link, "href")),
                  let host = joined.host?.lowercased(), hosts.contains(host) else { continue }
            let path = rawPath(joined)
            if !path.hasPrefix(prefix) || stripTrailingSlash(path) == stripTrailingSlash(prefix) { continue }
            if !WebRegex.matches("\\.(?:html?|shtml)$", in: path, options: [.caseInsensitive]) { continue }
            // The mirror hard-codes the main host in its links; fetch from the serving host.
            var components = URLComponents(url: joined, resolvingAgainstBaseURL: false) ?? URLComponents()
            components.scheme = baseURL.scheme
            components.host = baseURL.host
            components.port = baseURL.port
            guard let href = components.url else { continue }
            if name.isEmpty || seen.contains(href.absoluteString) { continue }
            if !catalogFound, !WebRegex.matches(chapterLike, in: name) { continue }
            if WebRegex.matches(navigation, in: name) { continue }
            seen.insert(href.absoluteString)
            var row: Element? = link.parent()
            while let candidate = row {
                let tag = candidate.tagName().lowercased()
                if tag == "dd" || tag == "li" || tag == "tr" { break }
                row = candidate.parent()
            }
            let linkClasses: String = (try? link.className()) ?? ""
            var rowClasses = ""
            if let row { rowClasses = (try? row.className()) ?? "" }
            let marker = [linkClasses, rowClasses, WebHTML.attr(link, "data-vip"), WebHTML.attr(link, "data-paid")]
                .joined(separator: " ")
            // Titles such as a "please subscribe" note are ordinary free chapters here;
            // only explicit lock markup is paid.
            let paid = WebRegex.matches(lockMarkup, in: marker, options: [.caseInsensitive])
            chapters.append(XiyuwxLink(url: href, name: name, paid: paid))
        }
        return chapters
    }

    private static func stripTrailingSlash(_ path: String) -> String {
        var value = path
        while value.hasSuffix("/") { value.removeLast() }
        return value
    }

    /// Nil while the page is a verification page or lacks a title or catalog.
    static func book(fromHTML html: String, pageURL: URL, bookURL: URL, bookID: String) -> BookInfo? {
        guard let document = parse(html, baseURL: pageURL) else { return nil }
        let pageTitle = (try? document.title()) ?? ""
        if WebRegex.matches("Just a moment|Checking your browser", in: pageTitle, options: [.caseInsensitive]) { return nil }

        var name = WebHTML.text(WebHTML.selectFirst("#info h1, .book-info h1, .bookinfo h1, .info h1, h1", in: document))
        if name.isEmpty {
            name = WebHTML.attr(WebHTML.selectFirst("meta[property=og:novel:book_name], meta[property=og:title]", in: document), "content")
        }
        let links = chapterLinks(document, baseURL: pageURL, bookID: bookID)
        if name.isEmpty || links.isEmpty { return nil }

        var author = WebHTML.text(WebHTML.selectFirst("#info .author, .book-info .author, .bookinfo .author, .info .author, a[href*=/author/]", in: document))
        author = WebRegex.replace("^\u{4f5c}\u{8005}\\s*[:\u{ff1a}]?\\s*", in: author, with: "").trimmingCharacters(in: .whitespacesAndNewlines)
        if author.isEmpty {
            author = WebHTML.attr(WebHTML.selectFirst("meta[property=og:novel:author], meta[name=author]", in: document), "content")
        }
        if author.isEmpty {
            let info = WebHTML.text(WebHTML.selectFirst("#info, .book-info, .bookinfo", in: document))
            author = WebRegex.capture("\u{4f5c}\u{8005}\\s*[:\u{ff1a}]\\s*([^\\s|]+)", in: info) ?? ""
        }

        let introNode = WebHTML.selectFirst("#intro, .intro, .book-intro, .bookintro, .description", in: document)
        var tags: [String] = []
        var intro = WebHTML.text(introNode)
        if let introNode {
            let tagLinks = WebHTML.select("a[href*=/tag/]", in: introNode)
            tags = tagLinks.map { WebHTML.text($0) }
            if let last = tagLinks.last {
                var tail = ""
                var node: Node? = last.nextSibling()
                while let current = node {
                    tail += (try? current.outerHtml()) ?? ""
                    node = current.nextSibling()
                }
                if let tailDocument = parse(tail, baseURL: nil) {
                    intro = WebHTML.text(tailDocument.body())
                } else {
                    intro = ""
                }
                let strip = CharacterSet(charactersIn: "\u{3001},\u{ff0c}:\u{ff1a} ")
                while let first = intro.unicodeScalars.first, strip.contains(first) {
                    intro.unicodeScalars.removeFirst()
                }
            }
        }

        var coverURL: URL?
        if let cover = WebHTML.selectFirst("#fmimg img, .book-cover img, .bookimg img, .cover img", in: document) {
            coverURL = WebHTML.join(pageURL, WebHTML.attr(cover, "src"))
        }
        let category = WebHTML.attr(WebHTML.selectFirst("meta[property=og:novel:category]", in: document), "content")
        var allTags = tags.filter { !$0.isEmpty }
        if !category.isEmpty, !allTags.contains(category) { allTags.append(category) }

        var chapters: [ChapterRef] = []
        for (offset, link) in links.enumerated() {
            chapters.append(ChapterRef(id: link.url.absoluteString, index: offset + 1, title: link.name, url: link.url, isPaid: link.paid))
        }
        return BookInfo(
            source: .xiyuwx, id: bookID, url: bookURL, title: name,
            author: author.isEmpty ? "Unknown" : author,
            synopsis: intro.isEmpty ? nil : intro, coverURL: coverURL, tags: allTags,
            language: "zh", chapters: chapters)
    }

    // MARK: Chapter

    private static let noiseSelector = "script, style, noscript, .ads, .advertisement, .read-ads, .chapter-ad, .bottom-ad, .ad"

    /// Nil when the page has no readable chapter body (caller falls back to the browser).
    static func chapterPage(fromHTML html: String, pageURL: URL?) -> XiyuwxChapterPage? {
        guard let document = parse(html, baseURL: pageURL) else { return nil }
        guard let content = WebHTML.selectFirst(
            "#content, #chaptercontent, #chapterContent, .chapter-content, .chapterContent, .read-content, .readContent, .content",
            in: document) else { return nil }
        let shuffled = WebHTML.selectFirst("#chapter", in: content)
        let rendered = WebHTML.selectFirst("#ad", in: content)
        // Reader mode leaves the scrambled source beside the rendered copy; read only one.
        var renderedText = ""
        if let rendered { renderedText = WebHTML.text(rendered) }
        var shuffleScript = ""
        for script in WebHTML.select("script", in: document) {
            let data = script.data()
            if data.contains("_ii_rr(") { shuffleScript = data; break }
        }

        var lines: [String] = []
        if let rendered, !renderedText.isEmpty {
            WebHTML.removeAll(noiseSelector, in: rendered)
            lines = WebHTML.lines(rendered)
        } else if let shuffled, !shuffleScript.isEmpty {
            guard let encoded = WebRegex.capture("_ii_rr\\s*\\(\\s*['\"]([^'\"]+)", in: shuffleScript),
                  let positions = decodePositions(encoded) else { return nil }
            var raw = (try? shuffled.html()) ?? ""
            raw = replaceFirst("\\[.*?\u{4f5c}\u{8005}.*?\u{63d0}\u{793a}.*?\\]", in: raw, options: [.dotMatchesLineSeparators])
            guard let ordered = unshuffle(splitParagraphs(raw), positions: positions) else { return nil }
            for piece in ordered {
                guard let fragment = parse(piece, baseURL: nil) else { continue }
                let text = WebHTML.text(fragment.body()).replacingOccurrences(of: "\u{00A0}", with: " ")
                let collapsed = WebRegex.replace("\\s+", in: text, with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
                if !collapsed.isEmpty { lines.append(collapsed) }
            }
        } else if shuffled != nil {
            // A shuffled source without its ordering map is unusable; let the browser render it.
            return nil
        } else {
            WebHTML.removeAll(noiseSelector, in: content)
            lines = WebHTML.lines(content)
        }
        if lines.count > 1, WebRegex.matches("^(?:\u{4e66}\\s*)?\u{7b2c}\\s*[0-9]+\u{7ae0}", in: lines[0]) {
            lines.removeFirst()
        }
        if lines.isEmpty { return nil }
        return XiyuwxChapterPage(lines: lines, nextPage: nextPage(document, pageURL: pageURL))
    }

    private static func nextPage(_ document: Document, pageURL: URL?) -> URL? {
        guard let pageURL else { return nil }
        let currentPath = rawPath(pageURL)
        let stem = WebRegex.replace("(?:_[0-9]+)?\\.html$", in: currentPath, with: "", options: [.caseInsensitive])
        let pattern = "^" + NSRegularExpression.escapedPattern(for: stem) + "_[0-9]+\\.html$"
        let nextLabel = "\u{4e0b}\u{4e00}\u{9875}"
        for link in WebHTML.select("a[href]", in: document) {
            guard WebHTML.text(link) == nextLabel else { continue }
            guard let candidate = WebHTML.join(pageURL, WebHTML.attr(link, "href")),
                  candidate.host == pageURL.host else { continue }
            if WebRegex.matches(pattern, in: rawPath(candidate), options: [.caseInsensitive]) { return candidate }
        }
        return nil
    }

    static func chapterHTML(_ lines: [String]) -> String {
        lines.map { "<p>\(WebHTML.escape($0))</p>" }.joined(separator: "\n")
    }
}
