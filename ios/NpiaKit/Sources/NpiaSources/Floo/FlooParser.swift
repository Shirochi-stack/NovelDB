import Foundation
import NpiaCore
import SwiftSoup

/// What a floo chapter page turned out to be.
enum FlooChapterOutcome: Equatable, Sendable {
    /// The reader replaced the chapter with a prompt. `login` or `purchase`.
    case locked(String)
    /// Readable text, one paragraph per line.
    case text([String])
    /// VIP chapter drawn as images, in reading order.
    case images([URL])
    /// No chapter container and no recognisable prompt.
    case none
}

/// Pure parsing for floo (`_floo_*` in the desktop external scraper).
enum FlooParser {
    /// Source domain, kept as escapes so the name never appears in the repository text.
    static let name = "\u{66}\u{61}\u{6c}\u{6f}\u{6f}"
    static let domain = name + ".com"
    static let bookHost = "b." + domain
    static let hosts: Set<String> = [bookHost, "wap." + domain, "www." + domain]
    static let userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    static let contentSelector = ".noveContent, #novelContent, .novelContent, #chapterContent, .chapter-content"

    static let loginWall = "\u{60a8}\u{8fd8}\u{6ca1}\u{6709}\u{767b}\u{5f55}|\u{8bf7}\u{767b}\u{5f55}\u{540e}"
    static let purchaseWall = "\u{8ba2}\u{9605}\u{672c}\u{7ae0}|\u{8d2d}\u{4e70}\u{672c}\u{7ae0}|\u{4f59}\u{989d}\u{4e0d}\u{8db3}|\u{5f00}\u{901a}VIP|\u{5347}\u{7ea7}VIP\u{4f1a}\u{5458}|\u{8bbe}\u{7f6e}\u{81ea}\u{52a8}\u{8ba2}\u{9605}"
    private static let purchaseHint = "\u{8ba2}\u{9605}|\u{5145}\u{503c}|\u{8d2d}\u{4e70}\u{672c}\u{7ae0}|\u{5f00}\u{901a}VIP"
    private static let purchaseStart = "^(?:\u{8ba2}\u{9605}|\u{5145}\u{503c}|\u{8d2d}\u{4e70}\u{672c}\u{7ae0}|\u{5f00}\u{901a}VIP)"
    private static let paidMarker = "\\bvip\\b|\u{4ed8}\u{8d39}|\u{8ba2}\u{9605}|\u{6536}\u{8d39}|\u{5df2}\u{9501}|\u{1f512}"
    private static let authorPrefix = "^\u{4f5c}\u{8005}\\s*[:\u{ff1a}]?\\s*"

    // MARK: URLs

    static func canonicalBookURL(_ bookID: String) -> URL? {
        URL(string: "https://\(bookHost)/\(bookID).html")
    }

    /// Book id from a book, catalog, VIP reader or chapter link on any of the three hosts.
    static func bookID(_ url: URL) -> String {
        guard let host = url.host?.lowercased(), hosts.contains(host) else { return "" }
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let path = components?.path ?? url.path
        let patterns = [
            "^/([0-9]+)(?:[_./]|$)",
            // VIP reader: /vip/724903/1658.html
            "^/vip/([0-9]+)/[0-9]+\\.html$",
            // Chapter list: /html_724_724903/
            "^/html_[0-9]+_([0-9]+)(?:/|\\.html)?$",
            "^/(?:book|novel|catalog|directory)/?([0-9]+)(?:[/._]|$)",
        ]
        for pattern in patterns {
            if let groups = WebRegex.firstMatch(pattern, in: path, options: [.caseInsensitive]),
               groups.count > 1, let id = groups[1] {
                return id
            }
        }
        let items = components?.queryItems ?? []
        for key in ["id", "bookid", "book_id", "novelid"] {
            if let value = items.first(where: { $0.name == key })?.value,
               WebRegex.matches("^[1-9][0-9]*$", in: value) {
                return value
            }
        }
        return ""
    }

    static func matches(_ url: URL) -> Bool { !bookID(url).isEmpty }

    // MARK: Text helpers

    private static func hasCJK(_ value: String) -> Bool {
        value.unicodeScalars.contains { $0.value >= 0x3400 && $0.value <= 0x9fff }
    }

    /// Repairs GBK text that was already misread as Latin-1 or Windows-1252.
    static func fixText(_ value: String) -> String {
        if value.isEmpty || hasCJK(value) { return value }
        for encoding in [String.Encoding.isoLatin1, String.Encoding.windowsCP1252] {
            guard let bytes = value.data(using: encoding),
                  let repaired = String(data: bytes, encoding: WebHTML.gb18030) else { continue }
            if hasCJK(repaired) { return repaired }
        }
        return value
    }

    static func text(_ element: Element?) -> String {
        fixText(WebHTML.text(element))
    }

    /// Candidate decodings of a page: UTF-8 (BOM stripped) else GB18030, then the charset-aware decode.
    static func decodings(_ data: Data) -> [String] {
        var body = data
        if body.starts(with: [0xEF, 0xBB, 0xBF]) { body = body.dropFirst(3) }
        let first: String
        if let utf8 = String(data: body, encoding: .utf8) {
            first = utf8
        } else if let gbk = String(data: body, encoding: WebHTML.gb18030) {
            first = gbk
        } else {
            first = String(decoding: body, as: UTF8.self)
        }
        var result = [first]
        let second = WebHTML.decode(data)
        if second != first { result.append(second) }
        return result
    }

    static func parse(_ html: String, baseURL: URL?) -> Document? {
        guard let document = try? WebHTML.parse(html, baseURL: baseURL) else { return nil }
        _ = document.outputSettings().prettyPrint(pretty: false)
        return document
    }

    /// The `C3VK` anti-bot cookie the server asks the client to set before reloading.
    static func challengeToken(in body: Data) -> String? {
        let head = String(decoding: body.prefix(2000), as: UTF8.self)
        return WebRegex.capture("cookie\\s*=\\s*[\"']C3VK=([0-9a-fA-F]+)", in: head)
    }

    // MARK: Book

    static func chapterLinks(_ document: Document, baseURL: URL, bookID: String) -> [(url: URL, name: String, paid: Bool)] {
        let selectors = [
            "div.C-Fo-Zuo div.DivTable a[href]", ".DivTable a[href]", ".chapter-list a[href]",
            ".chapterList a[href]", "#chapter-list a[href]", "#chapterList a[href]",
            ".catalog a[href]", ".directory a[href]", ".book-list a[href]",
        ]
        var links: [Element] = []
        for selector in selectors {
            links = WebHTML.select(selector, in: document)
            if !links.isEmpty { break }
        }
        let workPage = "^/" + NSRegularExpression.escapedPattern(for: bookID) + "\\.html$"
        var chapters: [(url: URL, name: String, paid: Bool)] = []
        var seen = Set<String>()
        for link in links {
            guard let href = WebHTML.join(baseURL, WebHTML.attr(link, "href")),
                  let host = href.host?.lowercased(), hosts.contains(host) else { continue }
            let path = URLComponents(url: href, resolvingAgainstBaseURL: false)?.path ?? href.path
            if !WebRegex.matches("\\.(?:html|aspx)$", in: path, options: [.caseInsensitive]) { continue }
            // Exclude the work page and catalog navigation.
            if WebRegex.matches(workPage, in: path, options: [.caseInsensitive]) { continue }
            let name = text(link)
            if name.isEmpty || seen.contains(href.absoluteString) { continue }
            seen.insert(href.absoluteString)
            var row: Element? = link.parent()
            var cursor: Element? = link.parent()
            while let candidate = cursor {
                let tag = candidate.tagName().lowercased()
                if tag == "li" || tag == "dd" || tag == "tr" { row = candidate; break }
                cursor = candidate.parent()
            }
            let linkClasses: String = (try? link.className()) ?? ""
            var rowClasses = ""
            var rowText = ""
            if let row {
                rowClasses = (try? row.className()) ?? ""
                rowText = text(row)
            }
            let marker = [linkClasses, rowClasses, rowText].joined(separator: " ")
            let paid = WebRegex.matches(paidMarker, in: marker, options: [.caseInsensitive])
            chapters.append((url: href, name: name, paid: paid))
        }
        return chapters
    }

    /// Nil when the page lacks a title or a catalog (the caller tries another source of the page).
    static func book(fromHTML pages: [String], pageURL: URL, bookID: String, canonical: URL) -> BookInfo? {
        for html in pages {
            guard let document = parse(html, baseURL: pageURL) else { continue }
            if let book = book(fromDocument: document, pageURL: pageURL, bookID: bookID, canonical: canonical) { return book }
        }
        return nil
    }

    static func book(fromDocument document: Document, pageURL: URL, bookID: String, canonical: URL) -> BookInfo? {
        var title = text(WebHTML.selectFirst("h1#novelName", in: document))
        if title.isEmpty { title = text(WebHTML.selectFirst("h1", in: document)) }
        let links = chapterLinks(document, baseURL: pageURL, bookID: bookID)
        if title.isEmpty || links.isEmpty { return nil }

        var author = text(WebHTML.selectFirst("a.rentouOne, .author a, .author", in: document))
        if author.isEmpty {
            // Desktop book pages: the author link beside the avatar searches by author
            // (l_0_1.html?t=2&k=...); the avatar link's title reads "<author>_<site> author".
            var box: Element = document
            if let novel = WebHTML.selectFirst("#novelName", in: document), let parent = novel.parent() { box = parent }
            let searchLink = WebHTML.select("a[href]", in: box).first { WebHTML.attr($0, "href").contains("l_0_1.html?t=2") }
            author = text(searchLink)
            if author.isEmpty {
                let guru = WebHTML.select("a[title]", in: box).first { WebHTML.attr($0, "href").contains("/guru/") }
                if let guru {
                    let label = WebHTML.attr(guru, "title")
                    let head = label.components(separatedBy: "_").first ?? label
                    author = fixText(head.trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
        }
        if author.isEmpty, let image = WebHTML.selectFirst("img.rentouOne", in: document) {
            var alt = WebHTML.attr(image, "alt")
            if alt.isEmpty { alt = WebHTML.attr(image, "title") }
            author = fixText(alt)
            if author.isEmpty {
                author = text(image.parent())
                author = WebRegex.replace(authorPrefix, in: author, with: "")
            }
        }
        if author.isEmpty {
            author = fixText(WebHTML.attr(WebHTML.selectFirst("meta[name=author]", in: document), "content"))
        }
        let introduction = text(WebHTML.selectFirst("div.T-L-T-C-Box1, .book-intro, .bookIntro, .intro", in: document))
        var coverURL: URL?
        if let cover = WebHTML.selectFirst("img.imgcss, .book-cover img, .cover img", in: document) {
            coverURL = WebHTML.join(pageURL, WebHTML.attr(cover, "src"))
        }
        let tags = WebHTML.select("div.T-R-T-B2-Box1 a", in: document).map { text($0) }.filter { !$0.isEmpty }

        var chapters: [ChapterRef] = []
        for (offset, link) in links.enumerated() {
            chapters.append(ChapterRef(id: link.url.absoluteString, index: offset + 1, title: link.name, url: link.url, isPaid: link.paid))
        }
        return BookInfo(
            source: .floo, id: bookID, url: canonical, title: title,
            author: author.isEmpty ? "Unknown" : author,
            synopsis: introduction.isEmpty ? nil : introduction, coverURL: coverURL, tags: tags,
            language: "zh", chapters: chapters)
    }

    // MARK: Chapter

    /// URL of one server-drawn VIP image from an `image_do3(...)` call's arguments.
    static func imageURL(arguments: String) -> URL? {
        let args = arguments.components(separatedBy: ",").map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "'\""))
        }
        guard args.count >= 11 else { return nil }
        let num = args[0], origin = args[1], book = args[2], chapter = args[3], en = args[4]
        let part = args[5], key = args[6], user = args[7], stamp = args[8], size = args[9], color = args[10]
        let chapterType = args.count > 11 ? args[11] : "1"
        let family = args.count > 12 ? args[12] : "0"
        let background = args.count > 13 ? args[13] : "1"
        let host = chapterType == "0" ? "read6" : "read"
        let text = "https://\(host).\(domain)/Page4VipImage.aspx?num=\(num)&o=\(origin)&id=\(book)&n=\(chapter)"
            + "&ct=\(chapterType)&en=\(en)&t=\(part)&font_size=\(size)&font_color=\(color)"
            + "&FontFamilyType=\(family)&backgroundtype=\(background)&u=\(user)&time=\(stamp)&k=\(key)"
        return URL(string: text)
    }

    /// Classifies one chapter page. `pages` are alternative decodings of the same document.
    static func chapter(fromHTML pages: [String], pageURL: URL?) -> FlooChapterOutcome {
        var chosen: Document?
        var content: Element?
        for html in pages {
            guard let document = parse(html, baseURL: pageURL) else { continue }
            chosen = document
            if let found = WebHTML.selectFirst(contentSelector, in: document) {
                content = found
                break
            }
        }
        guard let content else {
            let pageText = WebHTML.text(chosen)
            if WebRegex.matches(loginWall, in: pageText) { return .locked("login") }
            if WebRegex.matches(purchaseHint, in: pageText) { return .locked("purchase") }
            return .none
        }
        let markup = (try? content.outerHtml()) ?? ""
        let imageCalls = WebRegex.allMatches("image_do3?\\(([^)]*)\\)", in: markup).compactMap { $0.count > 1 ? $0[1] : nil }
        WebHTML.removeAll("script, style, noscript, .ads, .advertisement", in: content)
        // Free chapters end with a recharge promotion linking to the pay site.
        let payHost = "pay." + domain
        for link in WebHTML.select("a[href]", in: content) where WebHTML.attr(link, "href").contains(payHost) {
            var ancestor: Element? = link.parent()
            while let candidate = ancestor, candidate !== content {
                if candidate.tagName().lowercased() == "p" { try? candidate.remove(); break }
                ancestor = candidate.parent()
            }
        }
        let wallText = fixText(WebHTML.text(content))
        // A signed-in reader who has not bought the chapter gets the same box with a
        // "not subscribed" line, so only its text says why.
        let hasLoginLink = WebHTML.select("a[href]", in: content).contains { WebHTML.attr($0, "href").contains("regist/login") }
        if hasLoginLink || WebRegex.matches(loginWall, in: wallText) { return .locked("login") }

        // VIP text is drawn as images. The server HTML only has loading placeholders plus
        // image_do3(...) calls; the site script builds each URL from those arguments.
        var imageURLs: [URL] = imageCalls.compactMap { imageURL(arguments: $0) }
        // A rendered browser page has already moved the URL into CSS.
        if imageURLs.isEmpty {
            for part in WebHTML.select(".con_img [style]", in: content) {
                let style = WebHTML.attr(part, "style")
                guard style.contains("Page4VipImage"),
                      let raw = WebRegex.capture("url\\(\\s*[\"']?([^\"')]+Page4VipImage[^\"')]*)", in: style),
                      let resolved = WebHTML.join(pageURL ?? URL(string: "https://\(bookHost)/"), raw) else { continue }
                imageURLs.append(resolved)
            }
        }
        if !imageURLs.isEmpty { return .images(imageURLs) }

        // An image slot without an image, or a subscription offer, is what a signed-in
        // reader sees before buying the chapter.
        if !WebHTML.select(".con_img, .c_c3, .c_c4", in: content).isEmpty
            || WebRegex.matches(purchaseWall, in: wallText) {
            return .locked("purchase")
        }
        let lines = WebHTML.lines(content).map { fixText($0) }
        let joined = lines.joined(separator: "\n")
        if joined.isEmpty || WebRegex.matches(purchaseStart, in: joined) { return .locked("purchase") }
        return .text(lines)
    }

    static func chapterHTML(_ lines: [String]) -> String {
        lines.map { "<p>\(WebHTML.escape($0))</p>" }.joined(separator: "\n")
    }

    // MARK: Images

    /// File extension for image bytes, or nil when the bytes are not an image (e.g. a login page).
    static func imageExtension(_ data: Data) -> String? {
        let bytes = [UInt8](data.prefix(12))
        if bytes.count >= 4, bytes[0] == 0x47, bytes[1] == 0x49, bytes[2] == 0x46 { return "gif" }
        if bytes.count >= 4, bytes[0] == 0x89, bytes[1] == 0x50, bytes[2] == 0x4E, bytes[3] == 0x47 { return "png" }
        if bytes.count >= 3, bytes[0] == 0xFF, bytes[1] == 0xD8, bytes[2] == 0xFF { return "jpg" }
        if bytes.count >= 12, bytes[0] == 0x52, bytes[1] == 0x49, bytes[2] == 0x46, bytes[3] == 0x46,
           bytes[8] == 0x57, bytes[9] == 0x45, bytes[10] == 0x42, bytes[11] == 0x50 { return "webp" }
        return nil
    }

    /// `images/<chapter 4 digits>_<n 2 digits>.<ext>`.
    static func imageFilename(chapterIndex: Int, number: Int, ext: String) -> String {
        "images/\(padded(chapterIndex, 4))_\(padded(number, 2)).\(ext)"
    }

    private static func padded(_ value: Int, _ width: Int) -> String {
        let digits = String(value)
        return digits.count >= width ? digits : String(repeating: "0", count: width - digits.count) + digits
    }

    static func imageHTML(_ filenames: [String]) -> String {
        filenames.map { "<p><img src=\"\(WebHTML.escape($0))\" alt=\"\"/></p>" }.joined(separator: "\n")
    }
}
