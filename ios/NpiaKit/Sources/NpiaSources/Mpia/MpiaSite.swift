import Foundation
import NpiaCore

/// Addresses and URL shapes of the mpia site. The host is an escape so tracked files carry codenames only.
enum MpiaSite {
    static let domain = "\u{6d}\u{75}\u{6e}\u{70}\u{69}\u{61}.com"
    static let wwwOrigin = "https://www." + domain

    /// The site serves its desktop reader to a desktop browser, which is what the page logic expects.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    static let viewportWidth = 1280
    static let viewportHeight = 900

    static func bookURL(_ novelID: String) -> URL { URL(string: wwwOrigin + "/novel/detail/" + novelID)! }

    static func viewerURL(_ novelID: String, _ chapterID: String) -> URL {
        URL(string: wwwOrigin + "/novel/viewer/" + novelID + "/" + chapterID)!
    }

    private static func lowerHost(_ url: URL) -> String { (url.host ?? "").lowercased() }

    private static func pathMatches(_ pattern: String, _ url: URL) -> Bool {
        url.path.range(of: pattern, options: [.regularExpression]) != nil
    }

    /// Book-detail and viewer URLs, plus the legacy per-id host that now redirects to the www site.
    static func matches(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return false }
        switch lowerHost(url) {
        case domain, "www." + domain:
            return pathMatches(#"^/novel/(?:detail/\d+|viewer/\d+/\d+)/?$"#, url)
        case "novel." + domain:
            return pathMatches(#"^/\d+(?:/page/\d+)?(?:/neSrl/\d+)?/?$"#, url)
        default:
            return false
        }
    }

    /// Novel id from any mpia URL, or "" when there is none.
    static func novelID(_ url: URL) -> String {
        HTTPSourceSupport.firstMatch(#"^/(?:novel/(?:detail|viewer)/)?(\d+)(?:/|$)"#, in: url.path) ?? ""
    }
}
