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
        // A phone-site link and its desktop twin are one book; kept in the form the source reads.
        return SourceCatalog.canonicalURL(url)
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

extension AppSettings {
    /// Where an Add-tab edit of an overridable field goes, mirroring the desktop split: npia
    /// edits the main window keys (`overrides["npia"]`), a source that already overrides the
    /// field keeps its override, and everything else edits the shared external-downloader keys.
    func writesOverride<T>(_ path: KeyPath<SharedOverrides, T?>, for source: SourceID?) -> Bool {
        guard let source else { return false }
        return source == .npia || overrides[source.rawValue]?[keyPath: path] != nil
    }

    mutating func setShared<T>(_ sharedPath: WritableKeyPath<SharedSettings, T>, _ overridePath: WritableKeyPath<SharedOverrides, T?>, _ value: T, for source: SourceID?) {
        if let source, writesOverride(overridePath, for: source) {
            var override = overrides[source.rawValue] ?? SharedOverrides()
            override[keyPath: overridePath] = value
            overrides[source.rawValue] = override.isEmpty ? nil : override
        } else {
            shared[keyPath: sharedPath] = value
        }
    }

    /// Chapter range ends are optional on both sides, so they need their own setter.
    mutating func setRange(_ sharedPath: WritableKeyPath<SharedSettings, Int?>, _ overridePath: WritableKeyPath<SharedOverrides, Int?>, _ value: Int?, for source: SourceID?) {
        if let source, writesOverride(overridePath, for: source) {
            var override = overrides[source.rawValue] ?? SharedOverrides()
            override[keyPath: overridePath] = value
            overrides[source.rawValue] = override.isEmpty ? nil : override
            // An override cannot say "no range", so clearing it also clears the shared end.
            if value == nil { shared[keyPath: sharedPath] = nil }
        } else {
            shared[keyPath: sharedPath] = value
        }
    }
}
