import Foundation
import NpiaCore

/// Addresses, cookie names and URL shapes of qdn. The site's name is written as escapes so tracked files
/// carry codenames only.
enum QdnSite {
    static let domain = "\u{71}\u{69}\u{64}\u{69}\u{61}\u{6e}.com"
    static let host = "www." + domain
    static let origin = "https://" + host

    /// A phone is sent to this host; its book and chapter paths have the same shape as the desktop ones.
    static let mobileHost = "m." + domain

    /// The reader needs a desktop browser: a phone user agent is sent to a different site.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    static let viewportWidth = 1280
    static let viewportHeight = 800

    /// The phone site's account page. Signed out, it sends the browser to the site's own sign-in with the
    /// return address set, so the login cookies end up on the site itself (a bare sign-in page would not set them).
    static let signInURL = URL(string: "https://" + mobileHost + "/user/")!

    /// `ywkey` and `ywguid` are session cookies; `ywopenid` goes with them.
    static let loginCookies = ["ywkey", "ywguid", "ywopenid"]

    static let session = SessionSite(
        source: .qdn,
        signInURL: signInURL,
        // The phone site shares the parent domain's cookies; reading both hosts covers a login made on either.
        cookieURLs: [URL(string: origin + "/")!, URL(string: "https://" + mobileHost + "/")!],
        domain: domain,
        cookieNames: loginCookies,
        requirement: "ywkey and ywguid"
    ) { present in present.contains("ywkey") && present.contains("ywguid") }

    static func bookURL(_ bookID: String) -> URL {
        URL(string: origin + "/book/" + bookID + "/")!
    }

    private static func hostMatches(_ host: String) -> Bool {
        host == domain || host.hasSuffix("." + domain)
    }

    private static func lowerHost(_ url: URL) -> String {
        (url.host ?? "").lowercased()
    }

    /// Book or chapter URLs: `/book/<id>` and `/chapter/<book id>/<chapter id>`, on the desktop host, the
    /// phone host and the bare domain.
    static func matches(_ url: URL) -> Bool {
        hostMatches(lowerHost(url)) && HTTPSourceSupport.firstMatch("^/(?:book|chapter)/(\\d+)", in: url.path) != nil
    }

    /// The book id of a book or chapter link of the site, or nil.
    static func bookID(_ url: URL) -> String? {
        guard hostMatches(lowerHost(url)) else { return nil }
        return HTTPSourceSupport.firstMatch("^/(?:book|chapter)/(\\d+)", in: url.path)
    }

    /// The desktop book address (`https://www.<domain>/book/<id>/`) for any book or chapter link on the
    /// desktop or phone host; other links come back unchanged.
    static func canonicalURL(_ url: URL) -> URL {
        bookID(url).map(bookURL) ?? url
    }

    /// The last number in a chapter URL's path, or nil.
    static func chapterID(_ url: URL) -> String? {
        HTTPSourceSupport.firstMatch("^/chapter/\\d+/(\\d+)", in: url.path)
    }
}
