import Foundation
import NpiaCore

/// TODO(generic reader (D)): port from rules-lib.js, gm_stubs.js, bridge.js, _reader_chapter_result.
public struct ReaderAdapter: SourceAdapter {
    public static let id: SourceID = .reader
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
