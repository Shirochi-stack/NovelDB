import Foundation
import NpiaCore

/// TODO(http sources (C)): port from external_scraper.py _jara_*.
public struct JaraAdapter: SourceAdapter {
    public static let id: SourceID = .jara
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
