import Foundation

/// A styled piece of text inside one paragraph.
struct PDFRun: Equatable {
    var text: String
    var bold: Bool
    var italic: Bool
}

enum PDFBlock: Equatable {
    /// `heading` is the h1-h6 level, nil for body text.
    case paragraph([PDFRun], heading: Int?)
    case image(String)
}

/// Turns a chapter's XHTML fragment into paragraphs and images for the PDF layout.
/// Ruby annotations, scripts, and styles are dropped; whitespace collapses as in HTML.
enum PDFHTMLParser {
    private static let blockTags: Set<String> = [
        "p", "div", "li", "ul", "ol", "blockquote", "section", "article", "header", "footer",
        "table", "tr", "figure", "figcaption", "center", "hr", "pre", "dl", "dt", "dd", "aside", "nav", "body",
    ]
    private static let skippedTags: Set<String> = ["script", "style", "rt", "rp", "head", "title", "noscript"]

    static func blocks(_ html: String) -> [PDFBlock] {
        var blocks: [PDFBlock] = []
        var runs: [PDFRun] = []
        var bold = 0, italic = 0, skip = 0
        var heading: Int?

        func flush() {
            defer { runs = [] }
            while let first = runs.first, first.text.allSatisfy(isCollapsible) { runs.removeFirst() }
            while let last = runs.last, last.text.allSatisfy(isCollapsible) { runs.removeLast() }
            guard !runs.isEmpty else { return }
            runs[0].text = String(runs[0].text.drop(while: isCollapsible))
            runs[runs.count - 1].text = String(runs[runs.count - 1].text.reversed().drop(while: isCollapsible).reversed())
            blocks.append(.paragraph(runs, heading: heading))
        }

        func appendText(_ raw: Substring) {
            guard skip == 0, !raw.isEmpty else { return }
            var text = collapse(decodeEntities(String(raw)))
            let previousEndsBlank = runs.last.map { $0.text.last.map { $0 == " " || $0 == "\u{2028}" } ?? true } ?? true
            if previousEndsBlank, text.first == " " { text.removeFirst() }
            guard !text.isEmpty else { return }
            if let last = runs.last, last.bold == (bold > 0), last.italic == (italic > 0) {
                runs[runs.count - 1].text += text
            } else {
                runs.append(PDFRun(text: text, bold: bold > 0, italic: italic > 0))
            }
        }

        var index = html.startIndex
        while index < html.endIndex {
            guard let open = html[index...].firstIndex(of: "<") else {
                appendText(html[index...])
                break
            }
            appendText(html[index..<open])
            if html[open...].hasPrefix("<!--") {
                index = html.range(of: "-->", range: open..<html.endIndex)?.upperBound ?? html.endIndex
                continue
            }
            guard let close = html[open...].firstIndex(of: ">") else {
                appendText(html[open...])
                break
            }
            let tag = html[html.index(after: open)..<close]
            index = html.index(after: close)

            var body = tag[...]
            let closing = body.hasPrefix("/")
            if closing { body = body.dropFirst() }
            let name = body.prefix { $0.isLetter || $0.isNumber || $0 == ":" }.lowercased()
            let selfClosing = tag.hasSuffix("/")
            guard !name.isEmpty else { continue }

            if skippedTags.contains(name) {
                if !selfClosing { skip = max(0, skip + (closing ? -1 : 1)) }
                continue
            }
            guard skip == 0 else { continue }

            switch name {
            case "br":
                appendLineBreak(&runs, bold: bold > 0, italic: italic > 0)
            case "b", "strong":
                if !selfClosing { bold = max(0, bold + (closing ? -1 : 1)) }
            case "i", "em", "cite":
                if !selfClosing { italic = max(0, italic + (closing ? -1 : 1)) }
            case "h1", "h2", "h3", "h4", "h5", "h6":
                flush()
                heading = closing ? nil : Int(name.dropFirst())
            case "img", "image":
                guard !closing, let src = attribute(in: String(tag), names: ["src", "xlink:href", "href"]) else { continue }
                flush()
                blocks.append(.image(decodeEntities(src)))
            default:
                if blockTags.contains(name) { flush() }
            }
        }
        flush()
        return blocks
    }

    private static func isCollapsible(_ character: Character) -> Bool {
        character == " " || character == "\u{2028}"
    }

    private static func appendLineBreak(_ runs: inout [PDFRun], bold: Bool, italic: Bool) {
        if let last = runs.last {
            // Drop the space before the break so lines do not start or end with one.
            if last.text.hasSuffix(" ") { runs[runs.count - 1].text.removeLast() }
        }
        if let last = runs.last, last.bold == bold, last.italic == italic {
            runs[runs.count - 1].text += "\u{2028}"
        } else {
            runs.append(PDFRun(text: "\u{2028}", bold: bold, italic: italic))
        }
    }

    /// HTML whitespace collapsing; keeps no-break and ideographic spaces.
    static func collapse(_ text: String) -> String {
        var output = ""
        output.reserveCapacity(text.count)
        var lastWasSpace = false
        for character in text {
            if character == " " || character == "\n" || character == "\t" || character == "\r" || character == "\u{0C}" || character == "\r\n" {
                if !lastWasSpace { output.append(" ") }
                lastWasSpace = true
            } else {
                output.append(character)
                lastWasSpace = false
            }
        }
        return output
    }

    static func attribute(in tag: String, names: [String]) -> String? {
        for name in names {
            let pattern = "(?:^|\\s)" + NSRegularExpression.escapedPattern(for: name) + "\\s*=\\s*([\"'])(.*?)\\1"
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .dotMatchesLineSeparators]) else { continue }
            let ns = tag as NSString
            if let match = regex.firstMatch(in: tag, range: NSRange(location: 0, length: ns.length)) {
                let value = ns.substring(with: match.range(at: 2))
                if !value.isEmpty { return value }
            }
        }
        return nil
    }

    private static let namedEntities: [String: String] = [
        "amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'", "nbsp": "\u{00A0}",
        "hellip": "\u{2026}", "mdash": "\u{2014}", "ndash": "\u{2013}", "lsquo": "\u{2018}",
        "rsquo": "\u{2019}", "ldquo": "\u{201C}", "rdquo": "\u{201D}", "middot": "\u{00B7}",
        "laquo": "\u{00AB}", "raquo": "\u{00BB}", "copy": "\u{00A9}", "times": "\u{00D7}",
    ]

    static func decodeEntities(_ text: String) -> String {
        guard text.contains("&") else { return text }
        var output = ""
        output.reserveCapacity(text.count)
        var index = text.startIndex
        while index < text.endIndex {
            let character = text[index]
            guard character == "&",
                  let semicolon = text[index...].prefix(12).firstIndex(of: ";") else {
                output.append(character)
                index = text.index(after: index)
                continue
            }
            let entity = text[text.index(after: index)..<semicolon]
            var replacement: String?
            if entity.hasPrefix("#x") || entity.hasPrefix("#X") {
                replacement = UInt32(entity.dropFirst(2), radix: 16).flatMap(Unicode.Scalar.init).map { String(Character($0)) }
            } else if entity.hasPrefix("#") {
                replacement = UInt32(entity.dropFirst()).flatMap(Unicode.Scalar.init).map { String(Character($0)) }
            } else {
                replacement = namedEntities[String(entity)]
            }
            if let replacement {
                output += replacement
                index = text.index(after: semicolon)
            } else {
                output.append(character)
                index = text.index(after: index)
            }
        }
        return output
    }
}
