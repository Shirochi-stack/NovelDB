import Foundation
import NpiaCore

/// TODO(webengine (E)): port from external_scraper.py _floo_*, floo_image_reader.py.
public struct FlooAdapter: SourceAdapter {
    public static let id: SourceID = .floo
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
