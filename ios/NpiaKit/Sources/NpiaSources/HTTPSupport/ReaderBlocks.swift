import Foundation
import NpiaCore
import SwiftSoup

/// One paragraph or image of a chapter, in reading order.
enum ReaderBlock: Equatable, Sendable {
    case text(String)
    case image(URL)
}

/// Port of the desktop `_reader_html_blocks`, `_reader_plain_blocks` and
/// `_reader_chapter_result` helpers shared by the HTTP sources.
enum ReaderBlocks {
    private static let blockTags: Set<String> = [
        "p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "tr", "section", "article", "center", "pre",
    ]

    /// Splits reader HTML into blocks. Raw newlines inside text nodes are line breaks,
    /// as on readers whose paragraphs use `white-space: pre-line`.
    static func fromHTML(_ fragment: String, baseURL: URL?) throws -> [ReaderBlock] {
        let document = try SwiftSoup.parseBodyFragment(fragment, baseURL?.absoluteString ?? "")
        try document.select("script, style, noscript, button").remove()
        var blocks: [ReaderBlock] = []
        var line = ""

        func flush() {
            let text = collapseSpaces(line.replacingOccurrences(of: "\u{a0}", with: " "))
            line = ""
            if !text.isEmpty { blocks.append(.text(text)) }
        }

        func walk(_ node: Node) throws {
            for child in node.getChildNodes() {
                if let text = child as? TextNode {
                    let parts = text.getWholeText().components(separatedBy: "\n")
                    for (index, part) in parts.enumerated() {
                        if index > 0 { flush() }
                        line += part
                    }
                    continue
                }
                guard let element = child as? Element else { continue }
                let name = element.tagName().lowercased()
                if name == "br" {
                    flush()
                } else if name == "img" {
                    let src = [try element.attr("data-src"), try element.attr("data-original"), try element.attr("src")]
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                        .first { !$0.isEmpty } ?? ""
                    if let url = resolve(src, base: baseURL) {
                        flush()
                        blocks.append(.image(url))
                    }
                } else if blockTags.contains(name) {
                    flush()
                    try walk(element)
                    flush()
                } else {
                    try walk(element)
                }
            }
        }

        if let body = document.body() {
            try walk(body)
        }
        flush()
        return blocks
    }

    static func fromPlainText(_ text: String) -> [ReaderBlock] {
        text.replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n")
            .map { $0.replacingOccurrences(of: "\u{a0}", with: " ").trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .map { .text($0) }
    }

    /// Builds the chapter body. A leading paragraph that repeats the chapter title is dropped.
    static func content(_ blocks: [ReaderBlock], ref: ChapterRef, cssClass: String) -> ChapterContent {
        var blocks = blocks
        if case .text(let first)? = blocks.first, normalizeTitle(first) == normalizeTitle(ref.title) {
            blocks.removeFirst()
        }
        var parts: [String] = []
        var images: [ImageRef] = []
        var names: [URL: String] = [:]
        for block in blocks {
            switch block {
            case .text(let value):
                parts.append("<p>\(escape(value))</p>")
            case .image(let url):
                let name: String
                if let known = names[url] {
                    name = known
                } else {
                    name = imageFilename(url, chapter: ref.index, number: images.count + 1)
                    names[url] = name
                    images.append(ImageRef(url: url, filename: name))
                }
                parts.append("<p class=\"img\"><img src=\"\(escape(name))\" alt=\"\"/></p>")
            }
        }
        let html = "<div class=\"\(cssClass)\">\n" + parts.joined(separator: "\n") + "\n</div>"
        return ChapterContent(ref: ref, html: html, images: images)
    }

    /// `images/0001_03.jpg`: chapter index, image number, and the URL's extension.
    static func imageFilename(_ url: URL, chapter: Int, number: Int) -> String {
        var ext = url.pathExtension.lowercased()
        if ext.isEmpty || ext.count > 5 || !ext.allSatisfy({ $0.isLetter || $0.isNumber }) {
            ext = "jpg"
        }
        return "images/\(pad(chapter, 4))_\(pad(number, 2)).\(ext)"
    }

    private static func pad(_ value: Int, _ width: Int) -> String {
        let digits = String(value)
        return String(repeating: "0", count: max(0, width - digits.count)) + digits
    }

    /// NFKC, no whitespace, lower case: the desktop `_normalize_title_for_match`.
    static func normalizeTitle(_ value: String) -> String {
        value.precomposedStringWithCompatibilityMapping
            .unicodeScalars.filter { !CharacterSet.whitespacesAndNewlines.contains($0) }
            .reduce(into: "") { $0.unicodeScalars.append($1) }
            .lowercased()
    }

    static func collapseSpaces(_ value: String) -> String {
        value.replacingOccurrences(of: "[ \\t\\r\\f\\u{0B}]+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func escape(_ value: String, quote: Bool = true) -> String {
        var out = value
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        if quote {
            out = out.replacingOccurrences(of: "\"", with: "&quot;").replacingOccurrences(of: "'", with: "&#x27;")
        }
        return out
    }

    /// Paragraphs for a synopsis, one `<p>` per non-blank line.
    static func paragraphs(_ text: String) -> String {
        text.components(separatedBy: "\n")
            .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            .map { "<p>\(escape($0))</p>" }
            .joined()
    }

    static func resolve(_ value: String, base: URL?) -> URL? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let url = URL(string: trimmed, relativeTo: base)?.absoluteURL,
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return nil }
        return url
    }
}
