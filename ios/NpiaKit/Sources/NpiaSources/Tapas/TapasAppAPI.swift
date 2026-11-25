import Foundation
import NpiaCore

/// tapas.io's web sign-in: a form POST to `/account/authenticate`, as on the desktop (`_tapas_login` in
/// `external_scraper.py`). tapas is not a `SourceID` adapter on the phone — its books download through the
/// generic reader engine — so the whole signed-in cookie jar is written into the store every request shares
/// (see `GenericCookieSession`). The host is an escape so tracked files carry codenames only.
public enum TapasAppAPI {
    public static let domain = "\u{74}\u{61}\u{70}\u{61}\u{73}.io"
    public static let cookieKey = SecretKey(rawValue: "tapas.appCookie")
    public static let session = GenericCookieSession(key: cookieKey, cookieURLs: [URL(string: "https://\(domain)/")!], domain: domain)

    static let signInURL = URL(string: "https://\(domain)/account/signin")!
    static let authURL = URL(string: "https://\(domain)/account/authenticate")!

    /// A desktop browser's user agent; the sign-in form expects one.
    static let userAgent =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    /// True when an authenticate POST landed signed-in: it redirects away from the sign-in page, so the
    /// final URL is no longer `/account/signin` or `/account/authenticate`; a bad login re-renders the
    /// sign-in form (which posts to `/account/authenticate`) with a 200. Pure, so it is unit tested.
    public static func loggedIn(finalURL: URL, body: String) -> Bool {
        let path = finalURL.path.lowercased()
        if path.hasPrefix("/account/signin") || path.hasPrefix("/account/authenticate") { return false }
        return !body.prefix(40000).lowercased().contains("/account/authenticate")
    }

    /// Signs in through tapas.io's web endpoint and keeps the session cookies. `http` must be the download
    /// client (its cookie jar is the shared store), so the sign-in cookies land where downloads read them.
    @discardableResult
    public static func login(
        username: String,
        password: String,
        http: any HTTPFetching,
        secrets: any SecretStore,
        cookies: any SessionCookieAccess
    ) async -> Bool {
        let user = username.trimmingCharacters(in: .whitespaces)
        guard !user.isEmpty, !password.isEmpty else { return false }
        // Prime the session cookies the form expects (JSESSIONID, AWSALB); best effort.
        _ = try? await http.get(signInURL, headers: ["User-Agent": userAgent])
        let form = "from=&email=\(encode(user))&password=\(encode(password))&offsetTime=0"
        let headers = [
            "User-Agent": userAgent,
            "Referer": signInURL.absoluteString,
            "Content-Type": "application/x-www-form-urlencoded",
        ]
        guard let response = try? await http.send(HTTPRequest(url: authURL, method: "POST", headers: headers, body: Data(form.utf8))),
              loggedIn(finalURL: response.url, body: response.text) else { return false }
        // The cookie jar captured the sign-in cookies; read them back for the saved copy.
        let jar = await cookies.cookies(for: URL(string: "https://\(domain)/")!)
        guard !jar.isEmpty else { return false }
        let header = jar.map { "\($0.key)=\($0.value)" }.joined(separator: "; ")
        return await session.save(header, secrets: secrets, cookies: cookies)
    }

    private static func encode(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? value
    }
}
