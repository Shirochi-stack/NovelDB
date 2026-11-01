import Foundation
import NpiaCore

/// The kinds of page the Catalog tab can show; the app decides it for the page on screen.
public enum CatalogKind: Sendable, Equatable {
    /// NovelDB or NovelDB Next: outside links open in the Browser tab, cards keep their cart buttons.
    case novelDB
    /// A site one of our scrapers or the script engines handle: outside links open in the Browser
    /// tab, and a downloadable page gets a button.
    case supported
    /// Any other site: outside links are renamed and sent to the cart when the setting is on.
    case other
}

/// What the Catalog tab does for one page.
public struct CatalogPageMode: Sendable, Equatable {
    public var kind: CatalogKind
    /// Outside links are renamed "Download" and go to the cart (only on `.other`, setting on).
    public var cartLinks: Bool
    /// The page can be downloaded, so the floating Download button adds it to the cart.
    public var download: Bool

    public init(kind: CatalogKind, cartLinks: Bool, download: Bool) {
        self.kind = kind
        self.cartLinks = cartLinks
        self.download = download
    }
}

/// The page a Download button is made for: the address and title its bridge reported.
public struct CatalogDownloadPage: Sendable, Equatable {
    public var url: String
    public var title: String

    public init(url: String, title: String) {
        self.url = url
        self.title = title
    }
}

/// Decides what the Catalog tab does for the page on screen. The Android app (`CatalogSite.kt`) and
/// the desktop app (`catalog/sites.py`) decide the same way; keep the three in step.
public enum CatalogPages {
    /// The NovelDB catalog website, hosted on GitHub Pages.
    public static let defaultAddress = "https://shirochi-stack.github.io/NovelDB/"
    /// NovelDB Next, offered as a suggestion; its cards get link-cart buttons too.
    public static let nextAddress = "https://noveldb.pages.dev/"

    private static let nextHost = URL(string: nextAddress)?.host ?? "noveldb.pages.dev"
    private static let pagesHost = URL(string: defaultAddress)?.host ?? "shirochi-stack.github.io"
    private static let pagesRoot: String = {
        let path = URL(string: defaultAddress)?.path ?? "/NovelDB"
        return path.hasSuffix("/") ? String(path.dropLast()) : path
    }()

    /// A page of NovelDB (the GitHub Pages site) or NovelDB Next (and its preview deployments):
    /// outside links open in the Browser tab and are never renamed or sent to the cart.
    public static func isNovelDB(_ url: URL) -> Bool {
        guard let host = url.host?.lowercased(), !host.isEmpty else { return false }
        if host == nextHost || host.hasSuffix("." + nextHost) { return true }
        return host == pagesHost && (url.path == pagesRoot || url.path.hasPrefix(pagesRoot + "/"))
    }

    public static func kind(of url: URL, engines: EngineSettings = EngineSettings(), store: EngineStore = .shared) -> CatalogKind {
        if isNovelDB(url) { return .novelDB }
        if SourceCatalog.supportedSite(url, engines: engines, store: store) { return .supported }
        return .other
    }

    public static func mode(for url: URL, engines: EngineSettings = EngineSettings(), cartLinksSetting: Bool, store: EngineStore = .shared) -> CatalogPageMode {
        let found = Self.kind(of: url, engines: engines, store: store)
        return CatalogPageMode(
            kind: found,
            cartLinks: found == .other && cartLinksSetting,
            download: found == .supported && SourceCatalog.downloadablePage(url, engines: engines, store: store)
        )
    }

    /// The script that tells the page's bridge what it is: whether to rename outside links, and the
    /// page to offer for download (if any). `page` is used only when the mode allows a download.
    public static func setModeScript(_ mode: CatalogPageMode, download page: CatalogDownloadPage?) -> String {
        var payload = "null"
        if mode.download, let page {
            let object: [String: String] = ["url": page.url, "title": page.title]
            if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
                payload = String(decoding: data, as: UTF8.self)
            }
        }
        return "window.NovelDBApp && window.NovelDBApp.setMode({cartLinks: \(mode.cartLinks), download: \(payload)})"
    }

    // MARK: - Addresses and titles

    /// Same page, ignoring a trailing slash before the query or fragment and an empty fragment (as the bridge does).
    public static func sameAddress(_ a: String, _ b: String) -> Bool {
        plain(a) == plain(b)
    }

    private static func plain(_ address: String) -> String {
        var text = address
        if text.hasSuffix("#") { text.removeLast() }
        return text.replacingOccurrences(of: #"/+(?=$|[?#])"#, with: "", options: .regularExpression)
    }

    /// The site's host without a leading `www.`, lower case.
    static func bareHost(_ url: URL) -> String {
        url.bareHost
    }

    private static let separators = try! NSRegularExpression(pattern: #"\s+[-–—|·•]\s+|\s*\|\s*"#)

    /// A page title for the link cart: the title without the site's name, so "Solo Leveling - Site"
    /// is saved as "Solo Leveling". Segments split by " - ", " | " and the like that name the site
    /// (or are only "Home") are dropped; a title made of nothing else is kept as it is.
    public static func cleanTitle(_ title: String, for url: URL) -> String {
        let text = title.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return "" }
        let label = siteLabel(url)
        let parts = split(text, by: separators)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        let kept = parts.filter { part in
            let namesSite = label.count >= 3 && part.range(of: label, options: .caseInsensitive) != nil
            let generic = part.lowercased() == "home" || part.lowercased() == "official site"
            return !(namesSite || generic)
        }
        let result = kept.isEmpty ? text : kept.joined(separator: " - ")
        return String(result.prefix(200))
    }

    /// `global.example.co.uk` gives `example`: the name people use for the site, for spotting it in a title.
    public static func siteLabel(_ url: URL) -> String {
        guard var host = url.host?.lowercased() else { return "" }
        if host.hasPrefix("www.") { host.removeFirst(4) }
        let labels = host.split(separator: ".").map(String.init)
        guard labels.count >= 2, let last = labels.last else { return "" }
        let secondLevel: Set<String> = ["co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu"]
        let drop = last.count == 2 && labels.count >= 3 && secondLevel.contains(labels[labels.count - 2]) ? 2 : 1
        return labels[labels.count - 1 - drop]
    }

    private static func split(_ text: String, by regex: NSRegularExpression) -> [String] {
        let whole = text as NSString
        var parts: [String] = []
        var start = 0
        for match in regex.matches(in: text, options: [], range: NSRange(location: 0, length: whole.length)) {
            parts.append(whole.substring(with: NSRange(location: start, length: match.range.location - start)))
            start = match.range.location + match.range.length
        }
        parts.append(whole.substring(from: start))
        return parts
    }
}

/// Follows the page on screen in the Catalog tab and the address and title its bridge last reported.
///
/// The view's own address (`navigated`) and the page's report (`located`) both move the page along:
/// single-page sites change their address without loading, and the page tells the app. A Download
/// button is only offered for the address the page reported, so a report made for another address
/// stops counting as soon as the view moves.
public struct CatalogPageTracker: Sendable, Equatable {
    public struct Report: Sendable, Equatable {
        public var url: String
        public var title: String
    }

    /// The page on screen, as far as the app knows.
    public private(set) var pageURL: URL
    /// The page's address as text, compared with a report (`CatalogPages.sameAddress`).
    public private(set) var pageAddress: String
    public private(set) var reported: Report?

    public init(pageURL: URL) {
        self.pageURL = pageURL
        self.pageAddress = pageURL.absoluteString
    }

    /// Starts over on another site (the setting changed).
    public mutating func reset(to url: URL) {
        pageURL = url
        pageAddress = url.absoluteString
        reported = nil
    }

    /// The view moved to `url` (a load, or a history change inside the page). A report made for
    /// another address no longer counts.
    public mutating func navigated(to url: URL) {
        pageURL = url
        pageAddress = url.absoluteString
        if let reported, !CatalogPages.sameAddress(reported.url, pageAddress) { self.reported = nil }
    }

    /// The page's bridge reported its address and title. A page can only speak for the site the view
    /// shows (`shown`); returns whether the report was taken.
    @discardableResult
    public mutating func located(url: String, title: String, shown: URL?) -> Bool {
        guard url.lowercased().hasPrefix("http"), let page = URL(string: url) else { return false }
        if let shown, shown.absoluteString != "about:blank", CatalogPages.bareHost(shown) != CatalogPages.bareHost(page) {
            return false
        }
        pageURL = page
        pageAddress = url
        reported = Report(url: url, title: title)
        return true
    }

    /// The page to offer for download: the reported one, when `mode` allows a download and the
    /// report is for the page on screen. The title is cleaned for the link cart.
    public func download(for mode: CatalogPageMode) -> CatalogDownloadPage? {
        guard mode.download, let reported, CatalogPages.sameAddress(reported.url, pageAddress) else { return nil }
        let title = CatalogPages.cleanTitle(reported.title, for: URL(string: reported.url) ?? pageURL)
        return CatalogDownloadPage(url: reported.url, title: title)
    }
}
