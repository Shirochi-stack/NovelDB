import Foundation
import NpiaCore

/// URL shapes of the ntk site. Its numbered mirror host keeps moving, so it is recognised by a name pattern.
enum NtkSite {
    /// The site serves its desktop pages (and the Cloudflare check) to a desktop browser.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    static let viewportWidth = 1280
    static let viewportHeight = 900

    /// A mirror host that answers these (often 522 or 523) just loses its turn to the next one.
    static let mirrorErrors = Set(400...599)

    static let hostPattern = #"^(?:www\.)?(?:ntk|newtoki|sbxh)\d+\.(?:com|org)$"#
    /// Webtoon episodes from mirror sources have ids such as `goodtoon-745176` or `kp-5656-6755`.
    static let pathPattern = #"^/(?:novel|webtoon)/\d+(?:/[A-Za-z0-9][\w-]*)?/?$"#

    static func ownsSite(_ host: String) -> Bool {
        host.range(of: hostPattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    static func matches(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return false }
        guard let host = url.host, ownsSite(host) else { return false }
        return url.path.range(of: pathPattern, options: [.regularExpression]) != nil
    }

    /// "novel" or "webtoon" — the two page kinds, which take different content paths.
    static func kind(_ url: URL) -> String {
        HTTPSourceSupport.firstMatch(#"^/(novel|webtoon)/"#, in: url.path) ?? "novel"
    }

    static func novelID(_ url: URL) -> String {
        HTTPSourceSupport.firstMatch(#"/(?:novel|webtoon)/(\d+)"#, in: url.path) ?? ""
    }

    static func origin(_ url: URL) -> String {
        "\(url.scheme ?? "https")://\(url.host ?? "")"
    }

    /// A chapter link reads under the book-index URL, so identity and the download are the whole book.
    static func indexURL(_ url: URL) -> URL {
        let id = novelID(url)
        if id.isEmpty { return url }
        return URL(string: "\(origin(url))/\(kind(url))/\(id)") ?? url
    }

    /// The chapter HTML without the given pages (no mirror served them).
    static func withoutPages(_ html: String, names: [String]) -> String {
        names.reduce(html) { text, name in
            let pattern = #"<div class="ntk-webtoon-page"><img src=""# + NSRegularExpression.escapedPattern(for: name) + #""[^>]*/></div>\n?"#
            return text.replacingOccurrences(of: pattern, with: "", options: .regularExpression)
        }
    }

    /// Whether the bytes start like an image: a mirror can answer 200 with an error page.
    static func isImage(_ data: Data) -> Bool {
        let bytes = [UInt8](data.prefix(12))
        func starts(_ prefix: [UInt8], at offset: Int = 0) -> Bool {
            bytes.count >= offset + prefix.count && Array(bytes[offset..<(offset + prefix.count)]) == prefix
        }
        return starts([0xFF, 0xD8, 0xFF]) || starts([0x89, 0x50, 0x4E, 0x47]) || starts([0x47, 0x49, 0x46, 0x38])
            || (starts([0x52, 0x49, 0x46, 0x46]) && starts([0x57, 0x45, 0x42, 0x50], at: 8))
            || starts([0x66, 0x74, 0x79, 0x70], at: 4)
    }
}
