import Foundation
import NpiaCore

/// URL shapes of the ntk site. Its numbered mirror host keeps moving, so it is recognised by a name pattern.
enum NtkSite {
    /// The site serves its desktop pages (and the Cloudflare check) to a desktop browser.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    static let viewportWidth = 1280
    static let viewportHeight = 900

    static let hostPattern = #"^(?:www\.)?(?:ntk|newtoki|sbxh)\d+\.(?:com|org)$"#
    static let pathPattern = #"^/(?:novel|webtoon)/\d+(?:/\d+)?/?$"#

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
}
