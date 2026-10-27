import Foundation
import NpiaCore

/// Everything a writer needs for one finished book.
public struct BookBundle: Sendable {
    public var book: BookInfo
    /// Chapters in reading order, notices first when included.
    public var chapters: [ChapterContent]
    /// Image bytes keyed by `ImageRef.filename`, already processed.
    public var images: [String: Data]
    public var cover: ProcessedCover?
    public var settings: AppSettings

    public init(book: BookInfo, chapters: [ChapterContent], images: [String: Data] = [:], cover: ProcessedCover? = nil, settings: AppSettings) {
        self.book = book
        self.chapters = chapters
        self.images = images
        self.cover = cover
        self.settings = settings
    }
}

public struct ProcessedCover: Sendable {
    public var data: Data
    public var fileExtension: String

    public init(data: Data, fileExtension: String) {
        self.data = data
        self.fileExtension = fileExtension
    }
}

public protocol BookWriter: Sendable {
    var format: OutputFormat { get }
    /// Writes the file(s) into `directory` and returns the main output file.
    func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL
}

public enum OutputError: Error, Equatable {
    case notImplemented(OutputFormat)
    /// The format has nothing to hold for this book (e.g. CBZ without images); no file is written.
    case nothingToWrite(String)
}

public enum Writers {
    public static func writer(for format: OutputFormat) -> any BookWriter {
        switch format {
        case .epub: return EPUBWriter()
        case .txt: return TXTWriter()
        case .pdf: return PDFWriter()
        case .html: return HTMLWriter()
        case .cbz: return CBZWriter()
        }
    }

    /// Output file name without extension, following `namingMode` and `appendRange`.
    public static func baseName(for book: BookInfo, settings: SharedSettings) -> String {
        var name = settings.namingMode == .id ? book.id : book.title
        if settings.appendRange, settings.rangeFrom != nil || settings.rangeTo != nil {
            let from = settings.rangeFrom.map(String.init) ?? "1"
            let to = settings.rangeTo.map(String.init) ?? "end"
            name += " (\(from)-\(to))"
        }
        let illegal = CharacterSet(charactersIn: "/\\:*?\"<>|").union(.controlCharacters)
        let cleaned = name.unicodeScalars.map { illegal.contains($0) ? "_" : String($0) }.joined()
        let trimmed = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? book.id : String(trimmed.prefix(180))
    }
}
