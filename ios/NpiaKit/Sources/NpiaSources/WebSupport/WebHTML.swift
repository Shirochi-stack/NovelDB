import Foundation
import NpiaCore
import SwiftSoup

/// Helpers shared by the web-engine sources (BeautifulSoup and `html` module stand-ins).
enum WebHTML {
    /// `html.escape(value, quote=True)`.
    static func escape(_ value: String) -> String {
        var result = ""
        result.reserveCapacity(value.count)
        for character in value {
            switch character {
            case "&": result += "&amp;"
            case "<": result += "&lt;"
            case ">": result += "&gt;"
            case "\"": result += "&quot;"
            case "'": result += "&#x27;"
            default: result.append(character)
            }
        }
        return result
    }

    /// One `<p>` per non-empty line, escaped.
    static func paragraphs(_ lines: [String]) -> String {
        lines.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .map { "<p>\(escape($0))</p>" }
            .joined(separator: "\n")
    }

    /// Decodes page bytes using the declared charset, then UTF-8, then GB18030.
    static func decode(_ data: Data) -> String {
        let head = String(decoding: data.prefix(4096), as: UTF8.self)
        if let match = head.range(of: #"charset\s*=\s*["']?([a-zA-Z0-9_-]+)"#, options: [.regularExpression, .caseInsensitive]) {
            let declaration = String(head[match])
            let name = declaration.replacingOccurrences(of: #"(?i)charset\s*=\s*["']?"#, with: "", options: .regularExpression)
            if let encoding = encoding(named: name), let text = String(data: data, encoding: encoding) {
                return text
            }
        }
        if let text = String(data: data, encoding: .utf8) { return text }
        if let text = String(data: data, encoding: gb18030) { return text }
        return String(decoding: data, as: UTF8.self)
    }

    static let gb18030 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(
        CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))

    static func encoding(named name: String) -> String.Encoding? {
        switch name.lowercased() {
        case "utf-8", "utf8": return .utf8
        case "gbk", "gb2312", "gb18030": return gb18030
        case "big5":
            return String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(
                CFStringEncoding(CFStringEncodings.big5.rawValue)))
        case "euc-kr":
            return String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(
                CFStringEncoding(CFStringEncodings.EUC_KR.rawValue)))
        case "shift_jis", "sjis": return .shiftJIS
        case "iso-8859-1", "latin1": return .isoLatin1
        default:
            let cf = CFStringConvertIANACharSetNameToEncoding(name as CFString)
            guard cf != kCFStringEncodingInvalidId else { return nil }
            return String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(cf))
        }
    }

    static func parse(_ html: String, baseURL: URL? = nil) throws -> Document {
        try SwiftSoup.parse(html, baseURL?.absoluteString ?? "")
    }

    /// `node.get_text(' ', strip=True)`: whitespace collapsed, trimmed.
    static func text(_ element: Element?) -> String {
        guard let element else { return "" }
        return ((try? element.text()) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `node.get_text('\n', strip=True)`: one line per text run and `<br>`/block boundary,
    /// each trimmed, empty lines dropped.
    static func lines(_ element: Element?) -> [String] {
        guard let element else { return [] }
        var buffer = ""
        appendLines(element, into: &buffer)
        return buffer.components(separatedBy: "\n")
            .map { $0.replacingOccurrences(of: "\u{00A0}", with: " ").trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private static let blockTags: Set<String> = [
        "p", "div", "br", "li", "ul", "ol", "dd", "dt", "dl", "tr", "table", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "header", "footer", "hr",
    ]

    private static func appendLines(_ node: Node, into buffer: inout String) {
        for child in node.getChildNodes() {
            if let text = child as? TextNode {
                buffer += "\n" + text.getWholeText() + "\n"
            } else if let element = child as? Element {
                let tag = element.tagName().lowercased()
                if ["script", "style", "noscript", "template"].contains(tag) { continue }
                let block = blockTags.contains(tag)
                if block { buffer += "\n" }
                appendLines(element, into: &buffer)
                if block { buffer += "\n" }
            }
        }
    }

    /// Removes every element matching `selector` inside `element`.
    static func removeAll(_ selector: String, in element: Element) {
        guard let nodes = try? element.select(selector) else { return }
        for node in nodes.array() { try? node.remove() }
    }

    static func attr(_ element: Element?, _ name: String) -> String {
        guard let element else { return "" }
        return ((try? element.attr(name)) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func selectFirst(_ selector: String, in element: Element) -> Element? {
        try? element.select(selector).first()
    }

    static func select(_ selector: String, in element: Element) -> [Element] {
        (try? element.select(selector).array()) ?? []
    }

    /// `urllib.parse.urljoin`.
    static func join(_ base: URL?, _ href: String) -> URL? {
        let trimmed = href.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        if let base { return URL(string: trimmed, relativeTo: base)?.absoluteURL }
        return URL(string: trimmed)
    }
}

/// Small regex helpers over `NSRegularExpression`.
enum WebRegex {
    static func firstMatch(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> [String?]? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return nil }
        let range = NSRange(text.startIndex..., in: text)
        guard let match = regex.firstMatch(in: text, range: range) else { return nil }
        return (0..<match.numberOfRanges).map { index in
            Range(match.range(at: index), in: text).map { String(text[$0]) }
        }
    }

    /// First capture group (or the whole match when the pattern has no groups).
    static func capture(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> String? {
        guard let groups = firstMatch(pattern, in: text, options: options) else { return nil }
        return groups.count > 1 ? groups[1] : groups[0]
    }

    static func matches(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> Bool {
        firstMatch(pattern, in: text, options: options) != nil
    }

    static func allMatches(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> [[String?]] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: range).map { match in
            (0..<match.numberOfRanges).map { index in
                Range(match.range(at: index), in: text).map { String(text[$0]) }
            }
        }
    }

    static func replace(_ pattern: String, in text: String, with template: String, options: NSRegularExpression.Options = []) -> String {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return text }
        return regex.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: template)
    }
}

extension HTTPFetching {
    /// GET that throws `HTTPError.status` for non-2xx responses.
    func fetchOK(_ url: URL, headers: [String: String] = [:]) async throws -> HTTPResponse {
        let response = try await get(url, headers: headers)
        guard (200..<300).contains(response.status) else { throw HTTPError.status(response.status, url) }
        return response
    }
}
