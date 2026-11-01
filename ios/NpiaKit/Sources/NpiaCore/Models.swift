import Foundation

/// Source codenames. Raw values match the desktop scraper's method prefixes.
public enum SourceID: String, Codable, CaseIterable, Sendable {
    case npia
    case globalNpia = "global_npia"
    case mpia
    case kpage
    case sfc
    case nweb
    case jara
    case rbooks
    case ntk
    case floo
    case qdn
    case fenrir
    case xiyuwx
    case yeduji
    case maru
    case kobo
    /// Generic sites driven by `rules-lib.js`.
    case reader
}

/// How much browser automation a source needs.
public enum SourceTier: String, Codable, Sendable {
    /// Plain HTTP; no web view.
    case http
    /// A web view for login or a few page evaluations.
    case lightBrowser
    /// Rendering-dependent extraction (decryption, viewers, glyph decoding).
    case heavyBrowser
}

public struct ChapterRef: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    /// 1-based position in the book's reading order; used for ranges and numbering.
    public var index: Int
    public var title: String
    public var url: URL?
    public var isPaid: Bool
    public var isNotice: Bool

    public init(id: String, index: Int, title: String, url: URL? = nil, isPaid: Bool = false, isNotice: Bool = false) {
        self.id = id
        self.index = index
        self.title = title
        self.url = url
        self.isPaid = isPaid
        self.isNotice = isNotice
    }
}

public struct BookInfo: Codable, Sendable, Hashable {
    public var source: SourceID
    public var id: String
    public var url: URL
    public var title: String
    public var author: String?
    public var synopsis: String?
    public var coverURL: URL?
    public var tags: [String]
    public var status: String?
    public var language: String
    public var chapters: [ChapterRef]
    public var notices: [ChapterRef]

    public init(
        source: SourceID, id: String, url: URL, title: String, author: String? = nil,
        synopsis: String? = nil, coverURL: URL? = nil, tags: [String] = [], status: String? = nil,
        language: String = "ko", chapters: [ChapterRef] = [], notices: [ChapterRef] = []
    ) {
        self.source = source
        self.id = id
        self.url = url
        self.title = title
        self.author = author
        self.synopsis = synopsis
        self.coverURL = coverURL
        self.tags = tags
        self.status = status
        self.language = language
        self.chapters = chapters
        self.notices = notices
    }
}

public struct ImageRef: Codable, Sendable, Hashable {
    public var url: URL
    /// File name inside the output (e.g. `images/0001_03.webp`).
    public var filename: String
    /// Bytes when the source already holds them (decrypted or rendered images).
    public var data: Data?

    public init(url: URL, filename: String, data: Data? = nil) {
        self.url = url
        self.filename = filename
        self.data = data
    }
}

public struct ChapterContent: Codable, Sendable, Hashable {
    public var ref: ChapterRef
    /// XHTML body fragment; images reference `ImageRef.filename`.
    public var html: String
    public var images: [ImageRef]

    public init(ref: ChapterRef, html: String, images: [ImageRef] = []) {
        self.ref = ref
        self.html = html
        self.images = images
    }
}

public enum ChapterResult: Sendable {
    case content(ChapterContent)
    /// The account cannot read this chapter (unpaid, age gate, region).
    case refused(ChapterRef, reason: String)
    case failed(ChapterRef, message: String)

    public var ref: ChapterRef {
        switch self {
        case .content(let content): return content.ref
        case .refused(let ref, _), .failed(let ref, _): return ref
        }
    }
}

public enum SourceError: Error, Equatable, LocalizedError {
    case notImplemented(SourceID)
    case unsupportedURL(URL)
    case loginRequired(SourceID)
    case blocked(String)
    case parse(String)

    public var errorDescription: String? {
        switch self {
        case .notImplemented(let id): return "\(id.rawValue) is not available in this build yet."
        case .unsupportedURL(let url): return "No source recognises \(url.absoluteString)."
        case .loginRequired(let id): return "Sign in to \(id.rawValue) first."
        case .blocked(let message): return "Blocked: \(message)"
        case .parse(let message): return "Could not read the page: \(message)"
        }
    }
}
