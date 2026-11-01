import Foundation

/// The page patterns of a userscript header: the `// @match`, `// @include` and `// @exclude` lines
/// of the novel-downloader rules bundle. A page counts when some `@match` or `@include` accepts it
/// and no `@exclude` does, which is how the userscript manager decides where the script runs.
///
/// Match patterns follow Greasemonkey: `*` as the scheme is http or https, `*` elsewhere is any run
/// of characters (in the host it stops at a slash), and a host that starts with `*.` is the site
/// and all its subdomains. The path is compared with its query; the fragment and port are ignored.
/// `@include` and `@exclude` may also be `/regular expressions/` tested against the same address.
/// Hosts in the bundle are partly written as backslash-u escapes; they are decoded first.
///
/// The Android app (`UserScriptPatterns.kt`) and the desktop app (`userscript_patterns.py`) read the
/// header the same way; keep the three in step.
final class UserScriptPatterns: @unchecked Sendable {
    private let allow: [NSRegularExpression]
    private let deny: [NSRegularExpression]

    private init(allow: [NSRegularExpression], deny: [NSRegularExpression]) {
        self.allow = allow
        self.deny = deny
    }

    /// Number of `@match` and `@include` patterns that compiled.
    var allowCount: Int { allow.count }

    /// Number of `@exclude` patterns that compiled.
    var denyCount: Int { deny.count }

    func accepts(_ url: URL) -> Bool {
        guard let target = Self.target(url) else { return false }
        return allow.contains { Self.finds($0, in: target) } && !deny.contains { Self.finds($0, in: target) }
    }

    /// For addresses given as text; anything that is not a URL is not a page.
    func accepts(_ address: String) -> Bool {
        guard let url = URL(string: address.trimmingCharacters(in: .whitespacesAndNewlines)) else { return false }
        return accepts(url)
    }

    // MARK: - Reading the header

    private static let lineRegex = try! NSRegularExpression(
        pattern: #"^//\s*@(match|include|exclude)\s+(\S.*?)\s*$"#,
        options: [.anchorsMatchLines]
    )
    private static let endMarker = "==/UserScript=="

    /// Reads the header of `script` (the text up to its `==/UserScript==` line).
    static func parse(_ script: String) -> UserScriptPatterns {
        let header: String
        if let end = script.range(of: endMarker) {
            header = String(script[..<end.lowerBound])
        } else {
            header = String(script.prefix(200_000))
        }
        var allow: [NSRegularExpression] = []
        var deny: [NSRegularExpression] = []
        let whole = NSRange(header.startIndex..., in: header)
        for match in lineRegex.matches(in: header, options: [], range: whole) {
            guard let kindRange = Range(match.range(at: 1), in: header),
                  let patternRange = Range(match.range(at: 2), in: header),
                  let regex = compile(String(header[patternRange])) else { continue }
            if header[kindRange] == "exclude" {
                deny.append(regex)
            } else {
                allow.append(regex)
            }
        }
        return UserScriptPatterns(allow: allow, deny: deny)
    }

    // MARK: - One pattern

    /// One pattern as a regular expression over `scheme://host/path?query`; nil when it cannot be one.
    static func compile(_ raw: String) -> NSRegularExpression? {
        let pattern = decode(raw.trimmingCharacters(in: .whitespacesAndNewlines))
        if pattern.hasPrefix("/"), let last = pattern.lastIndex(of: "/"), last > pattern.startIndex {
            let flags = pattern[pattern.index(after: last)...]
            if flags.allSatisfy({ "ims".contains($0) }) {
                var options: NSRegularExpression.Options = []
                for flag in flags {
                    switch flag {
                    case "i": options.insert(.caseInsensitive)
                    case "m": options.insert(.anchorsMatchLines)
                    default: options.insert(.dotMatchesLineSeparators)
                    }
                }
                let body = String(pattern[pattern.index(after: pattern.startIndex)..<last])
                return try? NSRegularExpression(pattern: body, options: options)
            }
        }
        guard let separator = pattern.range(of: "://") else { return nil }
        let scheme = String(pattern[..<separator.lowerBound])
        let rest = String(pattern[separator.upperBound...])
        let slash = rest.firstIndex(of: "/")
        let host = slash.map { String(rest[..<$0]) } ?? rest
        let path = slash.map { String(rest[$0...]) } ?? "/"
        let schemeRegex = scheme == "*" ? "https?" : glob(scheme, ".*")
        let hostRegex: String
        if host == "*" {
            hostRegex = "[^/]*"
        } else if host.hasPrefix("*.") {
            hostRegex = "(?:[^/]*\\.)?" + glob(String(host.dropFirst(2)), "[^/]*")
        } else {
            hostRegex = glob(host, "[^/]*")
        }
        return try? NSRegularExpression(
            pattern: "^" + schemeRegex + "://" + hostRegex + glob(path, ".*") + "$",
            options: [.caseInsensitive]
        )
    }

    /// `text` with each backslash-u escape of four hex digits replaced by its character.
    static func decode(_ text: String) -> String {
        guard text.contains("\\") else { return text }
        let scalars = Array(text.unicodeScalars)
        var out = String.UnicodeScalarView()
        var index = 0
        while index < scalars.count {
            let scalar = scalars[index]
            if scalar == "\\", index + 5 < scalars.count, scalars[index + 1] == "u",
               let value = hex(scalars[(index + 2)...(index + 5)]), let decoded = Unicode.Scalar(value) {
                out.append(decoded)
                index += 6
            } else {
                out.append(scalar)
                index += 1
            }
        }
        return String(out)
    }

    private static func hex(_ digits: ArraySlice<Unicode.Scalar>) -> UInt32? {
        var value: UInt32 = 0
        for digit in digits {
            guard let number = Character(digit).hexDigitValue else { return nil }
            value = value * 16 + UInt32(number)
        }
        return value
    }

    /// Literal text between `*` wildcards, each wildcard becoming `any`.
    private static func glob(_ text: String, _ any: String) -> String {
        text.components(separatedBy: "*")
            .map { $0.isEmpty ? "" : NSRegularExpression.escapedPattern(for: $0) }
            .joined(separator: any)
    }

    /// `scheme://host/path?query` in lower case, without port and fragment.
    static func target(_ url: URL) -> String? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let scheme = parts.scheme?.lowercased(),
              let host = parts.host?.lowercased(), !host.isEmpty else { return nil }
        let path = parts.percentEncodedPath.isEmpty ? "/" : parts.percentEncodedPath
        let query = parts.percentEncodedQuery.map { "?" + $0 } ?? ""
        return scheme + "://" + host + path + query
    }

    private static func finds(_ regex: NSRegularExpression, in text: String) -> Bool {
        regex.firstMatch(in: text, options: [], range: NSRange(text.startIndex..., in: text)) != nil
    }
}
