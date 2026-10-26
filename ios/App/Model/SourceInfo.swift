import Foundation
import NpiaCore
import NpiaSources

/// Source-specific option groups the Add screen and settings show.
enum SourceOptionGroup: Hashable {
    case npia
    case kpage
    case ntkCover
    case nwebCover
    case koboLayout
    case longImages
}

enum SourceInfo {
    static func detect(_ text: String) -> SourceID? {
        guard let url = parseURL(text) else { return nil }
        return SourceCatalog.adapterType(for: url).map { $0.id }
    }

    static func parseURL(_ text: String) -> URL? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let candidate = trimmed.contains("://") ? trimmed : "https://" + trimmed
        guard let url = URL(string: candidate), url.host != nil else { return nil }
        return url
    }

    /// Every non-empty line that parses as a URL.
    static func urls(in text: String) -> [URL] {
        text.split(whereSeparator: \.isNewline).compactMap { parseURL(String($0)) }
    }

    static func groups(for source: SourceID) -> [SourceOptionGroup] {
        switch source {
        case .npia: return [.npia]
        case .kpage: return [.kpage, .longImages]
        case .ntk: return [.ntkCover, .longImages]
        case .nweb: return [.nwebCover]
        case .kobo: return [.koboLayout]
        case .floo, .rbooks, .qdn: return [.longImages]
        default: return []
        }
    }

    static func tierLabel(_ source: SourceID) -> String {
        guard let type = SourceCatalog.adapterType(for: source) else { return "" }
        switch type.tier {
        case .http: return "Direct download"
        case .lightBrowser: return "Uses a web view for sign-in"
        case .heavyBrowser: return "Renders pages in a web view (slower)"
        }
    }
}

extension SharedOverrides {
    /// The overridable fields where `value` differs from `base`.
    static func difference(_ value: SharedSettings, from base: SharedSettings) -> SharedOverrides {
        var result = SharedOverrides()
        if value.threads != base.threads { result.threads = value.threads }
        if value.intervalMin != base.intervalMin { result.intervalMin = value.intervalMin }
        if value.intervalMax != base.intervalMax { result.intervalMax = value.intervalMax }
        if value.formats != base.formats { result.formats = value.formats }
        if value.rangeFrom != base.rangeFrom { result.rangeFrom = value.rangeFrom }
        if value.rangeTo != base.rangeTo { result.rangeTo = value.rangeTo }
        if value.useCache != base.useCache { result.useCache = value.useCache }
        if value.cacheImages != base.cacheImages { result.cacheImages = value.cacheImages }
        if value.numberChapters != base.numberChapters { result.numberChapters = value.numberChapters }
        if value.separateTXT != base.separateTXT { result.separateTXT = value.separateTXT }
        return result
    }
}
