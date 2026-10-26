import Foundation
import NpiaCore

/// TODO(hard sources (Phase 5)): port from external_scraper.py _mpia_*.
public struct MpiaAdapter: SourceAdapter {
    public static let id: SourceID = .mpia
    public static let tier: SourceTier = .heavyBrowser

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
