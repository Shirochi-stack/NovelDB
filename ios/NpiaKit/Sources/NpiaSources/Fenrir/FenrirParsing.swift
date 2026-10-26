import Foundation
import NpiaCore
import SwiftSoup

/// One ordered piece of reader content.
enum FenrirBlock: Equatable {
    case text(String)
    case image(String)
}

/// Pure parsing helpers for the fenrir source (port of `_fenrir_*` in external_scraper.py).
enum FenrirParsing {
    static let origin = "https://fenrirealm.com"
    static let hosts: Set<String> = ["fenrirealm.com", "www.fenrirealm.com"]
    static let userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

    /// `_FENRIR_HIDDEN_CSS`.
    static let hiddenCSS = #"clip\s*:\s*rect\(\s*0|clip-path\s*:\s*inset\(\s*50%|display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px)?\b"#

    /// `_FENRIR_INVISIBLE_CHARS`.
    static let invisiblePattern = "[\u{00AD}\u{200B}-\u{200F}\u{202A}-\u{202E}\u{2060}-\u{2064}\u{FEFF}]"

    // MARK: URLs

    /// `(series_slug, chapter_slug)` for a fenrir URL; empty strings when it is not one.
    static func urlParts(_ url: URL) -> (series: String, chapter: String) {
        guard let host = url.host?.lowercased(), hosts.contains(host) else { return ("", "") }
        let parts = url.path.split(separator: "/").map(String.init)
        guard parts.count >= 2, parts[0] == "series" else { return ("", "") }
        return (parts[1], parts.count > 2 ? parts[2] : "")
    }

    static func isFenrirURL(_ url: URL) -> Bool {
        !urlParts(url).series.isEmpty
    }

    /// Chapter refs carry the reader id after a pipe: `<slug>|<uuid or id>`.
    static func refID(slug: String, chapterID: String) -> String {
        "\(slug)|\(chapterID)"
    }

    static func splitRefID(_ id: String) -> (slug: String, chapterID: String) {
        guard let bar = id.lastIndex(of: "|") else { return (id, "") }
        return (String(id[id.startIndex..<bar]), String(id[id.index(after: bar)...]))
    }

    // MARK: JSON value helpers (Python truthiness and str())

    static func isTruthy(_ value: Any?) -> Bool {
        guard let value = value, !(value is NSNull) else { return false }
        if let text = value as? String { return !text.isEmpty }
        if let number = value as? NSNumber { return number.doubleValue != 0 }
        if let array = value as? [Any] { return !array.isEmpty }
        if let dict = value as? [String: Any] { return !dict.isEmpty }
        return true
    }

    static func string(_ value: Any?) -> String {
        guard let value = value, !(value is NSNull) else { return "" }
        if let text = value as? String { return text }
        if let number = value as? NSNumber {
            let double = number.doubleValue
            if double == double.rounded(), abs(double) < 1e15 { return String(Int64(double)) }
            return "\(double)"
        }
        return ""
    }

    static func double(_ value: Any?) -> Double {
        guard let value = value, !(value is NSNull) else { return 0 }
        if let number = value as? NSNumber { return number.doubleValue }
        if let text = value as? String { return Double(text.trimmingCharacters(in: .whitespaces)) ?? 0 }
        return 0
    }

    static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber else { return nil }
        let double = number.doubleValue
        guard double == double.rounded(), abs(double) < 1e15 else { return nil }
        return Int(double)
    }

    static func collapseSpaces(_ value: String) -> String {
        WebRegex.replace(#"\s+"#, in: value, with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: Chapter rows

    static func chapterSlug(_ row: [String: Any]) -> String {
        var slug = string(row["slug"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if slug.isEmpty, let number = row["number"], !(number is NSNull) {
            let numberText = string(number)
            slug = isTruthy(row["part"]) ? "\(numberText).\(string(row["part"]))" : numberText
        }
        return slug
    }

    static func chapterName(_ row: [String: Any]) -> String {
        var name = collapseSpaces(string(row["name"]))
        let title = collapseSpaces(string(row["title"]))
        if !title.isEmpty, !name.lowercased().contains(title.lowercased()) {
            name = name.isEmpty ? title : "\(name) - \(title)"
        }
        if name.isEmpty { return "Chapter \(string(row["number"]))".trimmingCharacters(in: .whitespaces) }
        return name
    }

    /// True when a chapter costs coins and this account has not got it.
    static func paidLocked(_ row: [String: Any]) -> Bool {
        let locked = (row["locked"] as? [String: Any]) ?? [:]
        let bought = (row["bought"] as? [String: Any]) ?? [:]
        let price = double(locked["price"])
        return price > 0
            && !isTruthy(locked["unlocked_at"])
            && !isTruthy(bought["bought_at"])
            && !isTruthy(row["bypass"])
    }

    // MARK: Book

    /// Builds the book from the series info object and the chapter rows. Nil when no chapter has a slug.
    static func makeBook(info: [String: Any], rows rawRows: [[String: Any]], slug: String) -> BookInfo? {
        let ordered: [[String: Any]] = rawRows.enumerated().sorted { (left, right) -> Bool in
            let leftIndex: Double = integer(left.element["index"]).map { Double($0) } ?? 1e9
            let rightIndex: Double = integer(right.element["index"]).map { Double($0) } ?? 1e9
            if leftIndex != rightIndex { return leftIndex < rightIndex }
            let leftNumber = double(left.element["number"])
            let rightNumber = double(right.element["number"])
            if leftNumber != rightNumber { return leftNumber < rightNumber }
            return left.offset < right.offset
        }.map { $0.element }

        let seriesURLText = "\(origin)/series/\(slug)"
        guard let seriesURL = URL(string: seriesURLText) else { return nil }
        var chapters: [ChapterRef] = []
        for row in ordered {
            let rowSlug = chapterSlug(row)
            if rowSlug.isEmpty { continue }
            let paid = paidLocked(row)
            let name = chapterName(row)
            var chapterID = string(row["uuid"])
            if chapterID.isEmpty { chapterID = string(row["id"]) }
            let encodedSlug = rowSlug.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? rowSlug
            let chapterURL = URL(string: "\(seriesURLText)/\(encodedSlug)")
            chapters.append(ChapterRef(
                id: refID(slug: rowSlug, chapterID: chapterID),
                index: chapters.count + 1,
                title: name,
                url: chapterURL,
                isPaid: paid
            ))
        }
        if chapters.isEmpty { return nil }

        let descriptionHTML = string(info["description"]).trimmingCharacters(in: .whitespacesAndNewlines)
        var descriptionLines: [String] = []
        for block in htmlBlocks(descriptionHTML, base: origin + "/") {
            if case .text(let value) = block { descriptionLines.append(value) }
        }
        let description = descriptionLines.joined(separator: "\n")
        var cover = string(info["cover"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if !cover.isEmpty, let joined = WebHTML.join(URL(string: origin + "/"), cover) { cover = joined.absoluteString }
        let user = (info["user"] as? [String: Any]) ?? [:]
        var author = string(user["name"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if author.isEmpty { author = string(user["username"]).trimmingCharacters(in: .whitespacesAndNewlines) }
        if author.isEmpty { author = "Unknown" }
        func names(_ key: String) -> [String] {
            var result: [String] = []
            for item in (info[key] as? [Any]) ?? [] {
                guard let dict = item as? [String: Any], isTruthy(dict["name"]) else { continue }
                result.append(string(dict["name"]).trimmingCharacters(in: .whitespacesAndNewlines))
            }
            return result
        }
        let genres = names("genres")
        let tags = genres + names("tags").filter { !genres.contains($0) }
        var title = string(info["title"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if title.isEmpty { title = slug }
        let status = string(info["status"])
        return BookInfo(
            source: .fenrir, id: slug, url: seriesURL, title: title, author: author,
            synopsis: description.isEmpty ? nil : description,
            coverURL: cover.isEmpty ? nil : URL(string: cover),
            tags: tags, status: status.isEmpty ? nil : status, language: "en",
            chapters: chapters
        )
    }

    // MARK: Content

    static func stripInvisible(_ value: String) -> String {
        WebRegex.replace(invisiblePattern, in: value, with: "")
    }

    /// Drops hidden anti-copy blocks (CSS-hidden classes, aria-hidden, inline hidden styles).
    static func cleanHTML(_ content: String) -> String {
        guard let doc = try? WebHTML.parse(content), let body = doc.body() else { return stripInvisible(content) }
        var hidden = Set<String>()
        for style in WebHTML.select("style", in: doc) {
            let css = (try? style.html()) ?? ""
            for rule in WebRegex.allMatches(#"([^{}]+)\{([^{}]*)\}"#, in: css) {
                guard rule.count >= 3, let selector = rule[1], let ruleBody = rule[2] else { continue }
                if !WebRegex.matches(hiddenCSS, in: ruleBody, options: [.caseInsensitive]) { continue }
                for match in WebRegex.allMatches(#"\.([\w-]+)"#, in: selector) {
                    if match.count > 1, let name = match[1] { hidden.insert(name) }
                }
            }
            _ = try? style.remove()
        }
        for node in WebHTML.select("*", in: body) {
            let classText = WebHTML.attr(node, "class")
            let classes = Set(classText.split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" }).map { String($0) })
            let inline = (try? node.attr("style")) ?? ""
            let isHidden = !classes.isDisjoint(with: hidden)
                || WebHTML.attr(node, "aria-hidden") == "true"
                || node.hasAttr("hidden")
                || WebRegex.matches(hiddenCSS, in: inline, options: [.caseInsensitive])
            if isHidden { _ = try? node.remove() }
        }
        return stripInvisible((try? body.html()) ?? "")
    }

    private static let blockTags: Set<String> = [
        "p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "tr", "section", "article", "center", "pre",
    ]

    /// `_reader_html_blocks`: ordered text and image blocks; raw newlines in text are line breaks.
    static func htmlBlocks(_ fragment: String, base: String = "") -> [FenrirBlock] {
        guard let doc = try? WebHTML.parse(fragment), let body = doc.body() else { return [] }
        for tag in ["script", "style", "noscript", "button"] { WebHTML.removeAll(tag, in: body) }
        var blocks: [FenrirBlock] = []
        var line = ""
        let baseURL = URL(string: base)

        func flush() {
            var text = line.replacingOccurrences(of: "\u{00A0}", with: " ")
            text = WebRegex.replace("[ \\t\\r\\f\\v]+", in: text, with: " ")
            text = text.trimmingCharacters(in: .whitespacesAndNewlines)
            line = ""
            if !text.isEmpty { blocks.append(.text(text)) }
        }

        func walk(_ node: Node) {
            for child in node.getChildNodes() {
                if let textNode = child as? TextNode {
                    let parts = textNode.getWholeText().components(separatedBy: "\n")
                    for (offset, part) in parts.enumerated() {
                        if offset > 0 { flush() }
                        line += part
                    }
                } else if let element = child as? Element {
                    let name = element.tagName().lowercased()
                    if name == "br" {
                        flush()
                    } else if name == "img" {
                        var source = WebHTML.attr(element, "data-src")
                        if source.isEmpty { source = WebHTML.attr(element, "data-original") }
                        if source.isEmpty { source = WebHTML.attr(element, "src") }
                        if !source.isEmpty, let url = WebHTML.join(baseURL, source),
                           url.scheme == "http" || url.scheme == "https" {
                            flush()
                            blocks.append(.image(url.absoluteString))
                        }
                    } else if blockTags.contains(name) {
                        flush()
                        walk(element)
                        flush()
                    } else {
                        walk(element)
                    }
                }
            }
        }
        walk(body)
        flush()
        return blocks
    }

    static func plainBlocks(_ text: String) -> [FenrirBlock] {
        var blocks: [FenrirBlock] = []
        for raw in text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            let value = raw.replacingOccurrences(of: "\u{00A0}", with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
            if !value.isEmpty { blocks.append(.text(value)) }
        }
        return blocks
    }

    /// Decodes a reader document (ProseMirror style) keeping paragraph boundaries.
    static func documentBlocks(_ root: [String: Any]) -> [FenrirBlock] {
        var blocks: [FenrirBlock] = []
        var line = ""
        func flush() {
            let text = stripInvisible(line).trimmingCharacters(in: .whitespacesAndNewlines)
            line = ""
            if !text.isEmpty { blocks.append(.text(text)) }
        }
        func walk(_ item: Any?) {
            guard let node = item as? [String: Any] else { return }
            let kind = string(node["type"])
            let boundary = ["paragraph", "heading", "blockquote", "listItem", "codeBlock"].contains(kind)
            if boundary { flush() }
            if kind == "text" {
                line += string(node["text"])
            } else if kind == "hardBreak" {
                flush()
            } else if kind == "image" {
                flush()
                let attrs = (node["attrs"] as? [String: Any]) ?? [:]
                let src = string(attrs["src"])
                if !src.isEmpty, let url = WebHTML.join(URL(string: origin + "/"), src),
                   url.scheme == "http" || url.scheme == "https" {
                    blocks.append(.image(url.absoluteString))
                }
            }
            for child in (node["content"] as? [Any]) ?? [] { walk(child) }
            if boundary { flush() }
        }
        walk(root)
        flush()
        return blocks
    }

    /// `casefold(NFKC(unescape(value)))` without whitespace, for comparing titles.
    static func normalizeTitle(_ value: String) -> String {
        let unescaped = (try? Parser.unescapeEntities(value, false)) ?? value
        let folded = unescaped.precomposedStringWithCompatibilityMapping
        return WebRegex.replace(#"\s+"#, in: folded, with: "").lowercased()
    }

    static func imageExtension(_ url: String) -> String {
        let path = URL(string: url)?.path ?? ""
        let ext = (path as NSString).pathExtension.lowercased()
        if ["jpg", "jpeg", "png", "gif", "webp", "avif", "svg"].contains(ext) { return ext }
        return "jpg"
    }

    /// `_reader_chapter_result` for the output: paragraphs plus image references.
    static func chapterContent(blocks input: [FenrirBlock], ref: ChapterRef) -> ChapterContent? {
        var blocks = input
        if let first = blocks.first, case .text(let firstText) = first,
           normalizeTitle(firstText) == normalizeTitle(ref.title) {
            blocks.removeFirst()
        }
        if blocks.isEmpty { return nil }
        var parts: [String] = []
        var images: [ImageRef] = []
        var seen: [String: String] = [:]
        for block in blocks {
            switch block {
            case .text(let value):
                parts.append("<p>\(WebHTML.escape(value))</p>")
            case .image(let url):
                var filename: String? = seen[url]
                if filename == nil, let imageURL = URL(string: url) {
                    let name = String(format: "images/%04d_%02d", ref.index, images.count + 1) + "." + imageExtension(url)
                    seen[url] = name
                    filename = name
                    images.append(ImageRef(url: imageURL, filename: name))
                }
                if let filename = filename {
                    parts.append("<p class=\"img\"><img src=\"\(WebHTML.escape(filename))\" alt=\"\"/></p>")
                }
            }
        }
        let html = "<div class=\"fenrir-content\">\n" + parts.joined(separator: "\n") + "\n</div>"
        return ChapterContent(ref: ref, html: html, images: images)
    }

    /// `_fenrir_build_chapter`: nil when the payload carries no readable content.
    static func buildChapter(payload: [String: Any], ref: ChapterRef) -> ChapterContent? {
        let content = payload["content"]
        var document: [String: Any]?
        if let dict = content as? [String: Any] {
            document = dict
        } else if let text = content as? String, let data = text.data(using: .utf8),
                  let parsed = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            document = parsed
        }
        if let document = document, string(document["type"]) == "doc" {
            let blocks = documentBlocks(document)
            if blocks.isEmpty { return nil }
            return chapterContent(blocks: blocks, ref: ref)
        }
        guard let text = content as? String else { return nil }
        let blocks: [FenrirBlock]
        if string(payload["content_format"]) == "text" {
            blocks = plainBlocks(stripInvisible(text))
        } else {
            blocks = htmlBlocks(cleanHTML(text), base: origin + "/")
        }
        if blocks.isEmpty { return nil }
        return chapterContent(blocks: blocks, ref: ref)
    }

    // MARK: Retry-After

    /// Honours Retry-After seconds or an HTTP date; 30 seconds when unusable.
    static func retryDelay(_ value: String?, now: Date = Date()) -> TimeInterval {
        guard let raw = value else { return 30 }
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if WebRegex.matches("^[0-9]+$", in: text) {
            if let seconds = Double(text), seconds.isFinite { return seconds }
            return 30
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        for format in ["EEE, dd MMM yyyy HH:mm:ss zzz", "EEE, dd MMM yyyy HH:mm:ss Z"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: text) { return max(0, date.timeIntervalSince(now)) }
        }
        return 30
    }
}
