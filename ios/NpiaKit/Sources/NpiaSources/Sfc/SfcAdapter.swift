import Foundation
import NpiaCore
import SwiftSoup

/// Chinese light-novel platform. The book page and free chapters are plain HTML; VIP
/// chapters come from the Android app API with the user's app session (`login`), or from the
/// website with its sign-in (`session`), which shows bought VIP chapters as an image.
/// Port of the sfc rule in `rules-lib.js` plus `_sfc_*` / `login_sfc_app` in the desktop
/// `external_scraper.py`, and of the Android `SfcAdapter`.
public struct SfcAdapter: SourceAdapter {
    public static let id: SourceID = .sfc
    public static let tier: SourceTier = .http
    public static let sites: Set<String> = [bookHost]

    static let domain = "\u{73}\u{66}\u{61}\u{63}\u{67}.com"
    static let bookHost = "book." + domain
    /// Byte sizes of the placeholder image served instead of a VIP chapter image.
    static let placeholderImageSizes: Set<Int> = [53658, 42356]
    /// The site sometimes sends the placeholder for a bought chapter; it is asked for again this many times.
    static let placeholderRetries = 5
    /// The website can take over a minute to answer (it renders VIP chapter images on request).
    static let siteTimeout: TimeInterval = 120

    /// The website sign-in's ticket, from the cookies the app's browser and downloads share.
    private let websiteTicket: @Sendable () -> String?
    /// A website ticket the app API turned down (401), so later chapters go straight to the website.
    private let refused = RefusedTicket()

    public init() {
        self.init(websiteTicket: { SfcAdapter.sharedTicket() })
    }

    init(websiteTicket: @escaping @Sendable () -> String?) {
        self.websiteTicket = websiteTicket
    }

    public static func matches(_ url: URL) -> Bool {
        (url.host ?? "").lowercased() == bookHost && bookID(url) != nil
    }

    static func bookID(_ url: URL) -> String? {
        HTTPSourceSupport.firstMatch("^/Novel/(\\d+)", in: url.path)
    }

    static func chapterID(_ url: URL) -> String? {
        HTTPSourceSupport.allMatches("(\\d+)", in: url.path).last?.first
    }

    static let headers = [
        "User-Agent": HTTPSourceSupport.desktopUserAgent,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    ]

    // MARK: - Login

    /// Signs in through the app API and saves the session cookie (`sfc.appCookie`) in
    /// `secrets`. Returns false when the reply carried no session.
    public static func login(username: String, password: String, http: any HTTPFetching, secrets: any SecretStore) async throws -> Bool {
        try await SfcAppAPI.login(username: username, password: password, http: http, secrets: secrets)
    }

    /// Saves a cookie header copied from the app; false when `session_APP` is missing.
    @discardableResult
    public static func saveAppCookie(_ cookie: String, secrets: any SecretStore) -> Bool {
        SfcAppAPI.saveCookie(cookie, secrets: secrets)
    }

    public static func hasAppSession(_ secrets: any SecretStore) -> Bool {
        SfcAppAPI.savedCookie(secrets) != nil
    }

    /// Forgets the saved app session.
    public static func signOut(_ secrets: any SecretStore) {
        secrets.set(nil, for: SfcAppAPI.cookieKey)
    }

    // MARK: - Website sign-in

    /// The login ticket every sfc host shares; the website's sign-in sets it, as does the app API's.
    static let ticketCookie = SfcAppAPI.communityCookieName

    /// The website's sign-in page in the app's own browser. Besides a password it offers Facebook, QQ,
    /// WeChat and Weibo, which the app API's sign-in does not; the sfc app's own login stays inside that app.
    static let session = SessionSite(
        source: .sfc,
        signInURL: URL(string: "https://m." + domain + "/login")!,
        cookieURLs: [URL(string: "https://" + bookHost + "/")!, URL(string: "https://m." + domain + "/")!,
                     URL(string: "https://passport." + domain + "/")!],
        domain: domain,
        cookieNames: [ticketCookie],
        requirement: ticketCookie
    ) { present in
        present.contains(SfcAdapter.ticketCookie)
    }

    /// The ticket in `HTTPCookieStorage.shared`, which the web engine mirrors its web views into.
    static func sharedTicket() -> String? {
        let cookies = HTTPCookieStorage.shared.cookies(for: URL(string: "https://" + bookHost + "/")!) ?? []
        return cookies.first { $0.name == ticketCookie && !$0.value.isEmpty }?.value
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let bookID = Self.bookID(url) else { throw SourceError.unsupportedURL(url) }
        let infoURL = URL(string: "https://\(Self.bookHost)/Novel/\(bookID)/")!
        let indexURL = URL(string: "https://\(Self.bookHost)/Novel/\(bookID)/MainIndex/")!
        let index = try await HTTPSourceSupport.get(indexURL, headers: Self.headers, http: context.http, timeout: Self.siteTimeout).text
        let info = try await HTTPSourceSupport.get(infoURL, headers: Self.headers, http: context.http, timeout: Self.siteTimeout).text
        return try Self.book(indexHTML: index, infoHTML: info, bookID: bookID, infoURL: infoURL, indexURL: indexURL)
    }

    static func book(indexHTML: String, infoHTML: String, bookID: String, infoURL: URL, indexURL: URL) throws -> BookInfo {
        let index = try SwiftSoup.parse(indexHTML, indexURL.absoluteString)
        let info = try SwiftSoup.parse(infoHTML, infoURL.absoluteString)
        let title = try index.select("h1.story-title").first()?.text() ?? info.select("h1.title .text").first()?.text() ?? ""
        guard !title.isEmpty else { throw SourceError.parse("book title not found") }
        let author = try info.select(".author-name").first()?.text() ?? ""
        var synopsis = ""
        if let introduce = try info.select(".introduce").first() {
            synopsis = NwebAdapter.textLines(introduce).joined(separator: "\n")
        }
        let cover = try info.select("#hasTicket div.pic img").first().map { try $0.absUrl("src") } ?? ""
        var tags: [String] = []
        for link in try info.select("ul.tag-list > li.tag > a").array() {
            try link.select("span.icn").remove()
            let tag = try link.text().replacingOccurrences(of: "\\(\\d+\\)$", with: "", options: .regularExpression)
                .trimmingCharacters(in: .whitespaces)
            if !tag.isEmpty, !tags.contains(tag) { tags.append(tag) }
        }

        var chapters: [ChapterRef] = []
        for link in try index.select(".catalog-list a").array() {
            let href = try link.absUrl("href")
            guard let chapterURL = URL(string: href), let chapterID = chapterID(chapterURL) else { continue }
            var name = try link.attr("title").trimmingCharacters(in: .whitespaces)
            if name.isEmpty { name = try link.text() }
            let isVIP = try link.children().first()?.className() == "icn_vip"
            chapters.append(ChapterRef(id: chapterID, index: chapters.count + 1, title: name, url: chapterURL, isPaid: isVIP))
        }
        guard !chapters.isEmpty else { throw SourceError.parse("no chapters found in the catalog") }
        return BookInfo(
            source: id, id: bookID, url: infoURL, title: title,
            author: author.isEmpty ? nil : author, synopsis: synopsis.isEmpty ? nil : synopsis,
            coverURL: URL(string: cover), tags: tags, language: "zh", chapters: chapters
        )
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing chapter URL") }
        let chapterID = Self.chapterID(url) ?? ref.id
        var apiStatus: Int?
        // With an app session the app API serves every chapter, VIP ones included. Without one, the app API
        // is offered the website sign-in's ticket: text if it takes it, the website's image if it does not.
        let appCookie = SfcAppAPI.savedCookie(context.secrets)
        let ticket = appCookie == nil ? websiteTicket() : nil
        let offered = ticket.flatMap { refused.contains($0) ? nil : $0 }
        if let cookie = appCookie ?? offered.map({ Self.ticketCookie + "=" + $0 }) {
            switch try await SfcAppAPI.chapter(ref, chapterID: chapterID, cookie: cookie, http: context.http) {
            case .content(let content): return .content(content)
            case .noAccess(let status):
                if appCookie != nil {
                    apiStatus = status
                } else if status == 401, let offered {
                    refused.set(offered)
                }
            case .unavailable: break
            }
        }
        let page = try await HTTPSourceSupport.get(url, headers: Self.headers, http: context.http, timeout: Self.siteTimeout).text
        let outcome = try Self.webChapter(page, url: url, ref: ref)
        switch outcome {
        case .content(let content):
            return .content(content)
        case .vipImage(let imageURL):
            return try await vipImageChapter(imageURL, page: url, ref: ref, http: context.http)
        case .locked:
            if apiStatus != nil {
                return .refused(ref, reason: "the app session cannot read this VIP chapter")
            }
            if ticket != nil {
                return .refused(ref, reason: "VIP chapter the signed-in account has not bought")
            }
            return .refused(ref, reason: "VIP chapter: sign in to sfc in Settings > Enter App with the account that bought it")
        case .empty:
            return .failed(ref, message: "no chapter text")
        }
    }

    enum WebChapter {
        case content(ChapterContent)
        case vipImage(URL)
        case locked
        case empty
    }

    static func webChapter(_ html: String, url: URL, ref: ChapterRef) throws -> WebChapter {
        let document = try SwiftSoup.parse(html, url.absoluteString)
        let guest = try document.select(".user-bar > .top-link > .normal-link").first()?.html().contains("SF游客") ?? false
        // A guest only ever sees a preview of a VIP chapter.
        if try document.select(".pay-section").first() != nil || (ref.isPaid && guest) {
            return .locked
        }
        if let image = try document.select(".article-content > #vipImage").first(),
           let imageURL = URL(string: try image.absUrl("src")) {
            return .vipImage(imageURL)
        }
        guard let content = try document.select(".article-content").first() else {
            return ref.isPaid ? .locked : .empty
        }
        var named = ref
        if let title = try document.select("h1.article-title").first()?.text(), !title.isEmpty {
            named.title = title
        }
        let blocks = try ReaderBlocks.fromHTML(try content.outerHtml(), baseURL: url)
        guard !blocks.isEmpty else { return .empty }
        return .content(ReaderBlocks.content(blocks, ref: named, cssClass: "sfc-content"))
    }

    /// Bought VIP chapters on the website are a single rendered image.
    func vipImageChapter(_ imageURL: URL, page: URL, ref: ChapterRef, http: any HTTPFetching) async throws -> ChapterResult {
        let headers = [
            "User-Agent": HTTPSourceSupport.desktopUserAgent,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": page.absoluteString,
        ]
        var response = try await HTTPSourceSupport.get(imageURL, headers: headers, http: http, timeout: Self.siteTimeout)
        for _ in 0..<Self.placeholderRetries where Self.placeholderImageSizes.contains(response.body.count) {
            try await HTTPSourceSupport.sleep(seconds: 1)
            response = try await HTTPSourceSupport.get(imageURL, headers: headers, http: http, timeout: Self.siteTimeout)
        }
        if Self.placeholderImageSizes.contains(response.body.count) {
            return .refused(ref, reason: "VIP chapter image was not served (not bought)")
        }
        let filename = ReaderBlocks.imageFilename(URL(string: "vip.gif")!, chapter: ref.index, number: 1)
        let html = "<div class=\"sfc-content\"><p class=\"img\"><img src=\"\(filename)\" alt=\"\"/></p></div>"
        return .content(ChapterContent(ref: ref, html: html, images: [ImageRef(url: imageURL, filename: filename, data: response.body)]))
    }
}

/// The website ticket the app API turned down; shared by copies of the adapter.
final class RefusedTicket: @unchecked Sendable {
    private let lock = NSLock()
    private var value: String?

    func contains(_ ticket: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return value == ticket
    }

    func set(_ ticket: String) {
        lock.lock()
        value = ticket
        lock.unlock()
    }
}
