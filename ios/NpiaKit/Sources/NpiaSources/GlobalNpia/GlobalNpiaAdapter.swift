import Foundation
import NpiaCore

/// TODO(webengine (E)): port from external_scraper.py _global_npia_*.
public struct GlobalNpiaAdapter: SourceAdapter {
    public static let id: SourceID = .globalNpia
    public static let tier: SourceTier = .lightBrowser

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        false
    }

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        throw SourceError.notImplemented(Self.id)
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        throw SourceError.notImplemented(Self.id)
    }
}
