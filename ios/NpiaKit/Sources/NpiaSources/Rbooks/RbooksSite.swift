import Foundation
import NpiaCore

/// Addresses, cookie names and URL shapes of rbooks. Names are escapes so tracked files carry codenames only.
enum RbooksSite {
    static let domain = "\u{72}\u{69}\u{64}\u{69}\u{62}\u{6f}\u{6f}\u{6b}\u{73}.com"
    static let origin = "https://" + domain
    static let apiOrigin = "https://book-api." + domain
    static let libraryOrigin = "https://library." + domain
    static let accountOrigin = "https://account." + domain

    /// The viewer and the book API both want a normal desktop browser.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    static let viewportWidth = 1280
    static let viewportHeight = 900

    static let signInURL = URL(string: origin + "/account/login")!

    /// Access token, refresh token, and the global-service token.
    static let accessCookie = "\u{72}\u{69}\u{64}\u{69}-at"
    static let refreshCookie = "\u{72}\u{69}\u{64}\u{69}-rt"
    static let globalCookie = "\u{72}\u{69}\u{64}\u{69}-gs-at"
    static let loginCookies = [accessCookie, refreshCookie, globalCookie]

    static let session = SessionSite(
        source: .rbooks,
        signInURL: signInURL,
        cookieURLs: [URL(string: origin + "/")!, URL(string: accountOrigin + "/")!, URL(string: libraryOrigin + "/")!],
        domain: domain,
        cookieNames: RbooksSite.loginCookies,
        requirement: RbooksSite.accessCookie + " or " + RbooksSite.refreshCookie
    ) { present in
        RbooksSite.loginCookies.contains { present.contains($0) }
    }

    static func bookURL(_ bookID: String) -> URL {
        URL(string: origin + "/books/" + bookID)!
    }

    private static func hostMatches(_ host: String) -> Bool {
        host == domain || host.hasSuffix("." + domain)
    }

    private static func lowerHost(_ url: URL) -> String {
        (url.host ?? "").lowercased()
    }

    private static func pathMatches(_ pattern: String, _ url: URL) -> Bool {
        url.path.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    /// Product, viewer and library book URLs.
    static func matches(_ url: URL) -> Bool {
        hostMatches(lowerHost(url)) && pathMatches("^/(?:books/\\d+(?:/view)?|library/books/\\d+)/?$", url)
    }

    /// `/books/<id>` anywhere in the path; empty when there is none.
    static func bookID(_ url: URL) -> String {
        HTTPSourceSupport.firstMatch("/books/(\\d+)", in: url.path, options: [.caseInsensitive]) ?? ""
    }

    static func isLibrary(_ url: URL) -> Bool {
        pathMatches("^/library/books/\\d+/?$", url)
    }

    /// The reader itself (`/books/<id>/view`, `/library/books/<id>`), as opposed to a product page.
    static func isViewerURL(_ url: URL?) -> Bool {
        guard let url else { return false }
        return pathMatches("(?:^/books/\\d+/view|^/library/books/\\d+)/?$", url)
    }

    /// The login or adult-verification pages the site sends a signed-out visitor to.
    static func isAccountGate(_ url: URL?) -> Bool {
        guard let url else { return false }
        return pathMatches("/(?:account/login|account/verify-adult)", url) || lowerHost(url).hasPrefix("account.")
    }
}
