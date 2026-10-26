import Foundation
import NpiaCore

/// TODO(http sources (C)): port from external_scraper.py _nweb_*, _nweb_series_*.
public struct NwebAdapter: SourceAdapter {
    public static let id: SourceID = .nweb
    public static let tier: SourceTier = .http

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
