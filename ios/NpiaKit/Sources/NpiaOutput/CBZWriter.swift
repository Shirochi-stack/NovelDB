import Foundation
import NpiaCore
import ZIPFoundation

/// Comic archive laid out like the desktop external downloader's CBZ output:
/// `0000_cover.<ext>`, `ComicInfo.xml`, then `NNNN_<chapter title>/NNNN.<ext>` per image.
/// Images are the bundle's already-processed bytes; chapters without images are skipped.
public struct CBZWriter: BookWriter {
    public let format = OutputFormat.cbz
    public init() {}

    public struct Entry: Equatable, Sendable {
        public var path: String
        public var data: Data
        public var compressed: Bool
    }

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        let entries = Self.entries(for: bundle)
        guard entries.contains(where: { $0.path.contains("/") }) else {
            throw OutputError.nothingToWrite("CBZ skipped: no images in this book")
        }
        let fm = FileManager.default
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(baseName + ".cbz")
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
        let archive = try Archive(url: url, accessMode: .create, pathEncoding: nil)
        for entry in entries {
            let data = entry.data
            try archive.addEntry(
                with: entry.path, type: .file, uncompressedSize: Int64(data.count),
                compressionMethod: entry.compressed ? .deflate : .none,
                provider: { position, size in data.subdata(in: Int(position)..<Int(position) + size) }
            )
        }
        return url
    }

    /// Every archive entry in order. Separate from `write` so the layout can be tested.
    public static func entries(for bundle: BookBundle) -> [Entry] {
        let compressImages = bundle.settings.images.zipCompressImages
        var entries: [Entry] = []
        if let cover = bundle.cover {
            entries.append(Entry(path: "0000_cover." + cover.fileExtension.lowercased(), data: cover.data, compressed: compressImages))
        }
        let book = bundle.book
        let info = """
        <?xml version="1.0" encoding="utf-8"?>
        <ComicInfo>
          <Title>\(OutputSupport.escape(book.title))</Title>
          <Writer>\(OutputSupport.escape(book.author ?? ""))</Writer>
          <Summary>\(OutputSupport.escape(book.synopsis ?? ""))</Summary>
        </ComicInfo>

        """
        entries.append(Entry(path: "ComicInfo.xml", data: Data(info.utf8), compressed: true))
        for chapter in bundle.chapters where !chapter.ref.isNotice {
            let images = chapter.images.compactMap { ref in bundle.images[ref.filename].map { (ref.filename, $0) } }
            guard !images.isEmpty else { continue }
            let number = max(1, chapter.ref.index)
            let title = chapter.ref.title.isEmpty ? "Chapter \(number)" : chapter.ref.title
            let folder = String(format: "%04d_", number) + sanitize(title)
            for (offset, image) in images.enumerated() {
                var ext = (image.0 as NSString).pathExtension.lowercased()
                if ext.isEmpty { ext = "jpg" }
                entries.append(Entry(path: folder + "/" + String(format: "%04d.", offset + 1) + ext, data: image.1, compressed: compressImages))
            }
        }
        return entries
    }

    /// Chapters that contribute nothing to the archive, for the job log.
    public static func chaptersWithoutImages(_ bundle: BookBundle) -> [ChapterRef] {
        bundle.chapters.filter { chapter in
            !chapter.ref.isNotice && !chapter.images.contains { bundle.images[$0.filename] != nil }
        }.map(\.ref)
    }

    /// Desktop `_sanitize_filename`.
    static func sanitize(_ name: String) -> String {
        let illegal = Set("<>:\"/\\|?*")
        let replaced = String(name.map { illegal.contains($0) ? "_" : $0 })
        let trimmed = replaced.trimmingCharacters(in: CharacterSet(charactersIn: ". "))
        return trimmed.isEmpty ? "novel" : trimmed
    }
}
