import Foundation
import NpiaCore

/// Built-in comic scrapers backed by the shared page and Python runtimes.
protocol BuiltInComicAdapter: SourceAdapter {}

extension BuiltInComicAdapter {
    static var tier: SourceTier { .lightBrowser }

    func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        try await ReaderSessions.shared.openBuiltIn(url, context: context, source: Self.id).book
    }

    func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let session = try await ReaderSessions.shared.session(for: book, context: context)
        return try await session.chapter(ref)
    }

    func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        try await parseChaptersSpaced(refs, book: book, context: context)
    }
}

struct ComixAdapter: BuiltInComicAdapter {
    static let id: SourceID = .comix
    static let sites: Set<String> = ["comix.to", "comix.ws"]

    static func matches(_ url: URL) -> Bool { !PageScript.comixBookPages(url).isEmpty }
}

struct MangaKAdapter: BuiltInComicAdapter {
    static let id: SourceID = .mangak
    static let sites: Set<String> = ["mangak.io"]

    static func matches(_ url: URL) -> Bool { PythonEngines.mangaKMatches(url) }
}
