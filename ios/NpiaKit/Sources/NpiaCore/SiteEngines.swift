import Foundation

/// The engines a book link can go through. Each link tries them in the user's order until one
/// finds chapters: the app's own scrapers, the novel-downloader rules, then the WebToEpub parsers.
/// Raw values are the names the desktop config uses.
public enum SiteEngine: String, Codable, CaseIterable, Sendable {
    case native
    case novelDownloader = "novel_downloader"
    case webtoepub

    public var title: String {
        switch self {
        case .native: return "Built-in scrapers"
        case .novelDownloader: return "novel-downloader rules"
        case .webtoepub: return "WebToEpub parsers"
        }
    }

    public var hint: String {
        switch self {
        case .native: return "The app's own scrapers for the sites it knows best."
        case .novelDownloader: return "Rules for mostly Chinese and Japanese sites."
        case .webtoepub: return "Parsers for 650+ sites, mostly English translations."
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

    public init(
        order: [SiteEngine] = SiteEngine.allCases,
        disabled: [SiteEngine] = [],
        novelDownloaderAutoUpdate: Bool = true,
        webtoepubAutoUpdate: Bool = true
    ) {
        let full = SiteEngine.ordered(order.map { $0.rawValue })
        let off = full.filter { disabled.contains($0) }
        self.order = full
        // One engine always stays on.
        self.disabled = off.count == full.count ? [] : off
        self.novelDownloaderAutoUpdate = novelDownloaderAutoUpdate
        self.webtoepubAutoUpdate = webtoepubAutoUpdate
    }

    private enum CodingKeys: String, CodingKey {
        case order, disabled, novelDownloaderAutoUpdate, webtoepubAutoUpdate
    }

    /// Every key is optional and unknown engine names are dropped, so hand-edited files still load.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let names = try container.decodeIfPresent([String].self, forKey: .order)
        let off = try container.decodeIfPresent([String].self, forKey: .disabled) ?? []
        let novelDownloader = try container.decodeIfPresent(Bool.self, forKey: .novelDownloaderAutoUpdate) ?? true
        let webtoepub = try container.decodeIfPresent(Bool.self, forKey: .webtoepubAutoUpdate) ?? true
        self.init(
            order: SiteEngine.ordered(names),
            disabled: off.compactMap { SiteEngine(rawValue: $0) },
            novelDownloaderAutoUpdate: novelDownloader,
            webtoepubAutoUpdate: webtoepub
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
        case .native: return false
        case .novelDownloader: return novelDownloaderAutoUpdate
        case .webtoepub: return webtoepubAutoUpdate
        }
    }

    /// The default order with every engine on; the update choices stay as they are.
    public mutating func resetOrder() {
        order = SiteEngine.allCases
        disabled = []
    }
}
