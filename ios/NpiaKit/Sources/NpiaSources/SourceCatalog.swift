import Foundation
import NpiaCore

public enum SourceCatalog {
    /// Checked in order; put more specific matchers before generic ones (`reader` last).
    public static let adapters: [any SourceAdapter.Type] = [
        NpiaAdapter.self,
        GlobalNpiaAdapter.self,
        MpiaAdapter.self,
        KpageAdapter.self,
        SfcAdapter.self,
        NwebAdapter.self,
        JaraAdapter.self,
        RbooksAdapter.self,
        NtkAdapter.self,
        FlooAdapter.self,
        QdnAdapter.self,
        FenrirAdapter.self,
        XiyuwxAdapter.self,
        YedujiAdapter.self,
        MaruAdapter.self,
        KoboAdapter.self,
        ReaderAdapter.self,
    ]

    public static func adapterType(for url: URL) -> (any SourceAdapter.Type)? {
        adapters.first { $0.matches(url) }
    }

    /// The built-in scraper that owns the URL, if any. `reader` is the rule engines' adapter, not
    /// a built-in scraper, so it never counts here.
    public static func nativeAdapterType(for url: URL) -> (any SourceAdapter.Type)? {
        adapters.first { $0.id != .reader && $0.matches(url) }
    }

    /// The host (lower case) of an http or https address, nil for anything else.
    static func webHost(_ url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host?.lowercased(), !host.isEmpty else { return nil }
        return host
    }

    /// True when a built-in scraper or one of the switched-on script engines handles the URL's site:
    /// the Catalog tab then leaves its links alone and offers a Download button on pages it can take.
    /// A built-in scraper's site counts even while the built-in scrapers are switched off.
    public static func supportedSite(_ url: URL, engines: EngineSettings = EngineSettings(), store: EngineStore = .shared) -> Bool {
        guard let host = webHost(url) else { return false }
        if adapters.contains(where: { $0.id != .reader && $0.ownsSite(host) }) { return true }
        return engines.enabled().contains { engine in
            guard let rules = engine.ruleEngine else { return false }
            return EngineSites.lists(rules, url, store: store)
        }
    }

    /// True when this page, not just its site, can be downloaded, so the Catalog and Browser tabs show
    /// their buttons only where a download makes sense:
    /// - a built-in scraper takes the page (`matches` looks at the page) while the built-in scrapers are on;
    /// - or a switched-on script engine lists the page: the novel-downloader rules by the page patterns
    ///   of their header, WebToEpub by host with any path but the home page.
    /// The Add screen still accepts any web link (`SiteEngineRouter.canTry`); this decides where to
    /// offer the shortcut.
    public static func downloadablePage(_ url: URL, engines: EngineSettings = EngineSettings(), store: EngineStore = .shared) -> Bool {
        guard webHost(url) != nil else { return false }
        let order = engines.enabled()
        if order.contains(.native), nativeAdapterType(for: url) != nil { return true }
        return order.contains { engine in
            guard let rules = engine.ruleEngine else { return false }
            return EngineSites.listsPage(rules, url, store: store)
        }
    }

    public static func adapter(for url: URL) throws -> any SourceAdapter {
        guard let type = adapterType(for: url) else { throw SourceError.unsupportedURL(url) }
        return type.init()
    }

    public static func adapterType(for id: SourceID) -> (any SourceAdapter.Type)? {
        adapters.first { $0.id == id }
    }

    /// `url` in the form its source reads it under (see `SourceAdapter.canonicalURL`); unchanged when
    /// no source owns it. The Add screen, the link cart and the queue keep links in this form.
    public static func canonicalURL(_ url: URL) -> URL {
        nativeAdapterType(for: url).map { $0.canonicalURL(url) } ?? url
    }
}
