import Foundation
import NpiaCore

/// TODO(npia core (A)): port from downloader_core.py, npia_auth.py.
public struct NpiaAdapter: SourceAdapter {
    public static let id: SourceID = .npia
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
