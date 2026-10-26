import Foundation

/// Small regex and entity helpers so the npia parser can keep the desktop patterns verbatim.
enum NpiaRx {
    static func compile(_ pattern: String, _ options: NSRegularExpression.Options = []) -> NSRegularExpression {
        // Patterns are literals in this module; a bad one is a programming error.
        try! NSRegularExpression(pattern: pattern, options: options)
    }

    /// Capture groups of every match; missing groups become "".
    static func all(_ pattern: String, in text: String, _ options: NSRegularExpression.Options = []) -> [[String]] {
        let ns = text as NSString
        return compile(pattern, options).matches(in: text, range: NSRange(location: 0, length: ns.length)).map { match in
            (0..<match.numberOfRanges).map { index in
                let range = match.range(at: index)
                return range.location == NSNotFound ? "" : ns.substring(with: range)
            }
        }
    }

    static func first(_ pattern: String, in text: String, _ options: NSRegularExpression.Options = []) -> [String]? {
        let ns = text as NSString
        guard let match = compile(pattern, options).firstMatch(in: text, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        return (0..<match.numberOfRanges).map { index in
            let range = match.range(at: index)
            return range.location == NSNotFound ? "" : ns.substring(with: range)
        }
    }

    /// Matches with their NSRange so callers can look at surrounding text.
    static func matches(_ pattern: String, in text: String, _ options: NSRegularExpression.Options = []) -> [NSTextCheckingResult] {
        compile(pattern, options).matches(in: text, range: NSRange(location: 0, length: (text as NSString).length))
    }

    static func contains(_ pattern: String, in text: String, _ options: NSRegularExpression.Options = []) -> Bool {
        compile(pattern, options).firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)) != nil
    }

    static func replace(_ pattern: String, in text: String, with template: String, _ options: NSRegularExpression.Options = []) -> String {
        compile(pattern, options).stringByReplacingMatches(
            in: text, range: NSRange(location: 0, length: (text as NSString).length), withTemplate: template
        )
    }

    /// Replaces each match with the closure's result (groups as in `all`).
    static func replace(_ pattern: String, in text: String, _ options: NSRegularExpression.Options = [], using transform: ([String]) -> String) -> String {
        let ns = text as NSString
        var result = ""
        var cursor = 0
        for match in compile(pattern, options).matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            result += ns.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            let groups = (0..<match.numberOfRanges).map { index -> String in
                let range = match.range(at: index)
                return range.location == NSNotFound ? "" : ns.substring(with: range)
            }
            result += transform(groups)
            cursor = match.range.location + match.range.length
        }
        result += ns.substring(from: cursor)
        return result
    }
}

enum NpiaEntities {
    private static let named: [String: String] = [
        "amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'", "nbsp": "\u{a0}",
        "middot": "\u{b7}", "hellip": "\u{2026}", "lsquo": "\u{2018}", "rsquo": "\u{2019}",
        "ldquo": "\u{201c}", "rdquo": "\u{201d}", "ndash": "\u{2013}", "mdash": "\u{2014}",
        "copy": "\u{a9}", "reg": "\u{ae}", "trade": "\u{2122}",
    ]

    /// Python's `html.unescape` for the entities pages actually use.
    static func decode(_ text: String) -> String {
        guard text.contains("&") else { return text }
        return NpiaRx.replace(#"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);"#, in: text) { groups in
            let body = groups[1]
            if body.hasPrefix("#x") || body.hasPrefix("#X") {
                if let code = UInt32(body.dropFirst(2), radix: 16), let scalar = Unicode.Scalar(code) { return String(Character(scalar)) }
            } else if body.hasPrefix("#") {
                if let code = UInt32(body.dropFirst()), let scalar = Unicode.Scalar(code) { return String(Character(scalar)) }
            } else if let value = named[body] {
                return value
            }
            return groups[0]
        }
    }

    /// Python's `html.escape(text, quote=True)`.
    static func escape(_ text: String) -> String {
        var out = ""
        out.reserveCapacity(text.count)
        for ch in text {
            switch ch {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            case "'": out += "&#x27;"
            default: out.append(ch)
            }
        }
        return out
    }
}
