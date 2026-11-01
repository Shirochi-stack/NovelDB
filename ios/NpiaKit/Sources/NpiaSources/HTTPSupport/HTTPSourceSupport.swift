import Foundation
import NpiaCore

/// Small helpers shared by the HTTP-only adapters (sfc, nweb, jara, yeduji, maru).
enum HTTPSourceSupport {
    /// Desktop Chrome user agent (`_KR_UA`). The sites serve their desktop layouts to it,
    /// which is what the selectors expect.
    static let desktopUserAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

    /// GET that throws on HTTP errors.
    static func get(_ url: URL, headers: [String: String], http: any HTTPFetching, timeout: TimeInterval = 0) async throws -> HTTPResponse {
        let response = try await http.send(HTTPRequest(url: url, headers: headers, timeout: timeout))
        guard (200..<400).contains(response.status) else { throw HTTPError.status(response.status, url) }
        return response
    }

    static func query(_ url: URL, _ name: String) -> String? {
        URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first { $0.name == name }?.value?
            .trimmingCharacters(in: .whitespaces)
    }

    static func isPositiveInteger(_ value: String?) -> Bool {
        guard let value, let first = value.first, first != "0" else { return false }
        return value.allSatisfy(\.isASCII) && value.allSatisfy(\.isNumber)
    }

    /// First capture group of `pattern` in `text`.
    static func firstMatch(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> String? {
        allMatches(pattern, in: text, options: options).first?.first
    }

    /// Capture groups (from group 1) of every match.
    static func allMatches(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> [[String]] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: range).map { match in
            (1..<max(1, match.numberOfRanges)).map { index in
                Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
            }
        }
    }

    static func sleep(seconds: Double) async throws {
        guard seconds > 0 else { return }
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    /// JSON value as a string, accepting numbers too.
    static func string(_ value: Any?) -> String {
        switch value {
        case let text as String: return text
        case let number as NSNumber: return number.stringValue
        default: return ""
        }
    }

    static func int(_ value: Any?) -> Int {
        switch value {
        case let number as NSNumber: return number.intValue
        case let text as String: return Int(text.trimmingCharacters(in: .whitespaces)) ?? Int(Double(text) ?? 0)
        default: return 0
        }
    }

    static func double(_ value: Any?) -> Double {
        switch value {
        case let number as NSNumber: return number.doubleValue
        case let text as String: return Double(text.trimmingCharacters(in: .whitespaces)) ?? 0
        default: return 0
        }
    }

    static func jsonObject(_ data: Data) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw SourceError.parse("expected a JSON object")
        }
        return object
    }

    /// Decodes the common HTML entities (for titles taken from JSON APIs).
    static func unescapeHTML(_ value: String) -> String {
        guard value.contains("&") else { return value }
        var out = value
        for (entity, replacement) in [("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'"), ("&#x27;", "'"), ("&apos;", "'"), ("&nbsp;", "\u{a0}")] {
            out = out.replacingOccurrences(of: entity, with: replacement)
        }
        for groups in allMatches("&#(x?)([0-9A-Fa-f]+);", in: out) {
            let radix = groups[0].isEmpty ? 10 : 16
            if let code = UInt32(groups[1], radix: radix), let scalar = Unicode.Scalar(code) {
                out = out.replacingOccurrences(of: "&#\(groups[0])\(groups[1]);", with: String(Character(scalar)))
            }
        }
        return out.replacingOccurrences(of: "&amp;", with: "&")
    }
}
