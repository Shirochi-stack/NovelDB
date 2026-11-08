import Foundation

/// The engines a book link can go through. Each link tries them in the user's order until one
/// finds chapters (the desktop's order): the app's own scrapers, the novel-downloader rules, the
/// WebToEpub parsers, lightnovel-crawler's sources, the LNReader plugins, FanFicFare's adapters,
/// Article Image Queue readers, Tako readers, HaruNeko websites, gallery-dl's extractors, custom
/// engines, then the generic fallback. The Python engines run the desktop's modules in Pyodide.
/// Raw values are the names the desktop config uses.
public enum SiteEngine: String, Codable, CaseIterable, Sendable {
    case native
    case novelDownloader = "novel_downloader"
    case webtoepub
    case lncrawl
    case lnreader
    case fanficfare
    case mangapeak
    case tako
    case comix
    case haruneko
    case gallerydl
    case custom
    case generic

    public var title: String {
        switch self {
        case .native: return "Built-in scrapers"
        case .novelDownloader: return "novel-downloader rules"
        case .webtoepub: return "WebToEpub parsers"
        case .lnreader: return "LNReader plugins"
        case .haruneko: return "HaruNeko websites"
        case .lncrawl: return "lightnovel-crawler sources"
        case .fanficfare: return "FanFicFare adapters"
        case .gallerydl: return "gallery-dl extractors"
        case .mangapeak: return "Article Image Queue readers"
        case .tako: return "Tako readers"
        case .comix: return "Comix reader"
        case .custom: return "Custom engines"
        case .generic: return "Generic fallback"
        }
    }

    public var hint: String {
        switch self {
        case .native: return "The app's own scrapers for the sites it knows best."
        case .novelDownloader: return "Rules for mostly Chinese and Japanese sites."
        case .webtoepub: return "Parsers for 650+ sites, mostly English translations."
        case .lnreader: return "Plugins for 320+ novel sites in many languages."
        case .haruneko: return "800+ manga, manhwa and comic sites."
        case .lncrawl: return "400+ novel sites (Python, in the app)."
        case .fanficfare: return "100+ fan-fiction archives and forums (Python, in the app)."
        case .gallerydl: return "Manga sites such as fanfox and mangahere (Python, in the app)."
        case .mangapeak: return "Article Image Queue's MangaPeak reader (Python, in the app)."
        case .tako: return "Tako's MangaDex and MangaHere readers (Python, in the app)."
        case .comix: return "Reads the Comix chapter list and reader pages in the web view."
        case .custom: return "Python engines imported in Settings."
        case .generic: return "Any other site: reads the chapter list and text without site rules."
        }
    }

    /// Known engines in the given order (missing ones appended), without the switched-off ones.
    /// Unknown names and repeats are dropped.
    public static func ordered(_ names: [String]?, disabled: [SiteEngine] = []) -> [SiteEngine] {
        var seen: [SiteEngine] = []
        let everyName = allCases.map { $0.rawValue }
        for name in (names ?? []) + everyName {
            if let engine = SiteEngine(rawValue: name), !seen.contains(engine) {
                seen.append(engine)
            }
        }
        let additions: [SiteEngine] = [.mangapeak, .tako, .comix, .custom]
        for engine in additions where !(names ?? []).contains(engine.rawValue) {
            seen.removeAll { $0 == engine }
            let before: SiteEngine = engine == .custom ? .generic : .haruneko
            seen.insert(engine, at: seen.firstIndex(of: before) ?? seen.endIndex)
        }
        return seen.filter { !disabled.contains($0) }
    }
}

/// Which site engines run and in what order, and which rule engines update themselves.
/// Updates are on unless the user turns them off.
public struct EngineSettings: Codable, Sendable, Equatable {
    /// Every engine once, in the order they are tried (switched-off ones keep their place).
    public var order: [SiteEngine]
    public var disabled: [SiteEngine]
    public var novelDownloaderAutoUpdate: Bool
    public var webtoepubAutoUpdate: Bool
    public var lnreaderAutoUpdate: Bool
    public var harunekoAutoUpdate: Bool
    public var lncrawlAutoUpdate: Bool
    public var fanficfareAutoUpdate: Bool
    public var gallerydlAutoUpdate: Bool
    /// Dynamic priority (the desktop's): a link no built-in scraper owns goes only to the engines that
    /// list its site, then the generic fallback, instead of to every engine that is on.
    public var dynamicPriority: Bool

    public init(
        order: [SiteEngine] = SiteEngine.allCases,
        disabled: [SiteEngine] = [],
        novelDownloaderAutoUpdate: Bool = true,
        webtoepubAutoUpdate: Bool = true,
        lnreaderAutoUpdate: Bool = true,
        harunekoAutoUpdate: Bool = true,
        lncrawlAutoUpdate: Bool = true,
        fanficfareAutoUpdate: Bool = true,
        gallerydlAutoUpdate: Bool = true,
        dynamicPriority: Bool = true
    ) {
        let full = SiteEngine.ordered(order.map { $0.rawValue })
        let off = full.filter { disabled.contains($0) }
        self.order = full
        // One engine always stays on.
        self.disabled = off.count == full.count ? [] : off
        self.novelDownloaderAutoUpdate = novelDownloaderAutoUpdate
        self.webtoepubAutoUpdate = webtoepubAutoUpdate
        self.lnreaderAutoUpdate = lnreaderAutoUpdate
        self.harunekoAutoUpdate = harunekoAutoUpdate
        self.lncrawlAutoUpdate = lncrawlAutoUpdate
        self.fanficfareAutoUpdate = fanficfareAutoUpdate
        self.gallerydlAutoUpdate = gallerydlAutoUpdate
        self.dynamicPriority = dynamicPriority
    }

    private enum CodingKeys: String, CodingKey {
        case order, disabled, novelDownloaderAutoUpdate, webtoepubAutoUpdate, lnreaderAutoUpdate, harunekoAutoUpdate,
             lncrawlAutoUpdate, fanficfareAutoUpdate, gallerydlAutoUpdate, dynamicPriority
    }

    /// Every key is optional and unknown engine names are dropped, so hand-edited files still load.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let names = try container.decodeIfPresent([String].self, forKey: .order)
        let off = try container.decodeIfPresent([String].self, forKey: .disabled) ?? []
        let novelDownloader = try container.decodeIfPresent(Bool.self, forKey: .novelDownloaderAutoUpdate) ?? true
        let webtoepub = try container.decodeIfPresent(Bool.self, forKey: .webtoepubAutoUpdate) ?? true
        let lnreader = try container.decodeIfPresent(Bool.self, forKey: .lnreaderAutoUpdate) ?? true
        let haruneko = try container.decodeIfPresent(Bool.self, forKey: .harunekoAutoUpdate) ?? true
        let lncrawl = try container.decodeIfPresent(Bool.self, forKey: .lncrawlAutoUpdate) ?? true
        let fanficfare = try container.decodeIfPresent(Bool.self, forKey: .fanficfareAutoUpdate) ?? true
        let gallerydl = try container.decodeIfPresent(Bool.self, forKey: .gallerydlAutoUpdate) ?? true
        let dynamic = try container.decodeIfPresent(Bool.self, forKey: .dynamicPriority) ?? true
        self.init(
            order: SiteEngine.ordered(names),
            disabled: off.compactMap { SiteEngine(rawValue: $0) },
            novelDownloaderAutoUpdate: novelDownloader,
            webtoepubAutoUpdate: webtoepub,
            lnreaderAutoUpdate: lnreader,
            harunekoAutoUpdate: haruneko,
            lncrawlAutoUpdate: lncrawl,
            fanficfareAutoUpdate: fanficfare,
            gallerydlAutoUpdate: gallerydl,
            dynamicPriority: dynamic
        )
    }

    /// The engines to try, in order.
    public func enabled() -> [SiteEngine] {
        SiteEngine.ordered(order.map { $0.rawValue }, disabled: disabled)
    }

    /// Every engine in order, switched-off ones included.
    public func fullOrder() -> [SiteEngine] {
        SiteEngine.ordered(order.map { $0.rawValue })
    }

    public func isEnabled(_ engine: SiteEngine) -> Bool {
        !disabled.contains(engine)
    }

    /// Switches an engine on or off. Returns false, changing nothing, when that would leave
    /// every engine off.
    @discardableResult
    public mutating func setEnabled(_ engine: SiteEngine, _ on: Bool) -> Bool {
        if on {
            disabled.removeAll { $0 == engine }
            return true
        }
        guard enabled().contains(where: { $0 != engine }) else { return false }
        if !disabled.contains(engine) { disabled.append(engine) }
        return true
    }

    public func autoUpdate(_ engine: SiteEngine) -> Bool {
        switch engine {
        case .native, .generic, .mangapeak, .tako, .comix, .custom: return false
        case .novelDownloader: return novelDownloaderAutoUpdate
        case .webtoepub: return webtoepubAutoUpdate
        case .lnreader: return lnreaderAutoUpdate
        case .haruneko: return harunekoAutoUpdate
        case .lncrawl: return lncrawlAutoUpdate
        case .fanficfare: return fanficfareAutoUpdate
        case .gallerydl: return gallerydlAutoUpdate
        }
    }

    /// The default order with every engine on; the update choices stay as they are.
    public mutating func resetOrder() {
        order = SiteEngine.allCases
        disabled = []
    }
}
