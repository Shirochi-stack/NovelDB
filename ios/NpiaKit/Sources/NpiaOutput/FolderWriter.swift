import CoreGraphics
import Foundation
import ImageIO
import NpiaCore
import NpiaImages
import UniformTypeIdentifiers
import ZIPFoundation

public struct FolderWriter: BookWriter {
    public let format: OutputFormat

    public init(format: OutputFormat) { self.format = format }

    public func write(_ bundle: BookBundle, baseName: String, to directory: URL) async throws -> URL {
        let prepared = try Prepared(directory: directory, options: bundle.settings.images)
        defer { prepared.close() }
        for chapter in OutputSupport.readingOrder(bundle.chapters) {
            try prepared.append(chapter, images: bundle.images)
        }
        return try prepared.publish(format, baseName: baseName)
    }

    /// The job's chapter files are prepared once, then published as Folder, ZIP, or both.
    public struct Prepared: Sendable {
        private let directory: URL
        private let options: ImageSettings
        private let temporary: URL
        private let stage: URL
        private let previous: URL
        private let packed: URL
        private let processor: ImageProcessor?

        public init(directory: URL, options: ImageSettings) throws {
            self.directory = directory
            self.options = options
            processor = options.compressAfterStitch ? ImageProcessor(settings: options) : nil
            let temporary = directory.appendingPathComponent(".noveldb-folder-" + UUID().uuidString, isDirectory: true)
            self.temporary = temporary
            stage = temporary.appendingPathComponent("book", isDirectory: true)
            previous = temporary.appendingPathComponent("previous")
            packed = temporary.appendingPathComponent("book.zip")
            try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
        }

        public func append(_ chapter: ChapterContent, images available: [String: Data]) throws {
            try Task.checkCancellation()
            let fm = FileManager.default
            let stitching = options.webtoonExport == true && options.stitchingEnabled && !options.saveImageURLsOnly
            let subfolders = stitching && options.chapterFoldersEnabled
            let images = chapter.images.enumerated().compactMap { index, image -> Page? in
                guard let bytes = available[image.filename] else { return nil }
                let ext = (image.filename as NSString).pathExtension.lowercased()
                let valid = ext.range(of: #"^[a-z0-9]{1,8}$"#, options: .regularExpression) != nil
                return Page(number: index + 1, ext: valid ? ext : "jpg", data: bytes)
            }
            let text = HTMLText.plainText(chapter.html)
            let links = options.saveImageURLsOnly ? chapter.images.map { $0.url.absoluteString } : []
            if images.isEmpty && text.isEmpty && links.isEmpty { return }
            let clean = CBZWriter.sanitize(String(chapter.ref.title.filter { !$0.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) }))
            let shortTitle = clean.unicodeScalars.prefix(60).map { String($0) }.joined()
            let name = String(format: "%04d ", max(1, chapter.ref.index)) + CBZWriter.sanitize(shortTitle)
            var folder = stage
            func useChapterFolder() throws {
                guard folder == stage else { return }
                folder = FolderWriter.uniqueURL(in: stage, stem: name, suffix: "")
                try fm.createDirectory(at: folder, withIntermediateDirectories: false)
            }
            if subfolders { try useChapterFolder() }
            func target(_ stem: String, _ ext: String) -> URL {
                let prefix = folder == stage ? name + " - " : ""
                return FolderWriter.uniqueURL(in: folder, stem: prefix + stem, suffix: "." + ext)
            }
            func save(_ page: Page, position: Int) throws {
                try Task.checkCancellation()
                let encoded = try processor?.process(page.data)
                let ext = encoded.flatMap { $0.fileExtension == "bin" ? nil : $0.fileExtension } ?? page.ext
                try (encoded?.data ?? page.data).write(to: target(String(format: "%03d", position), ext))
            }
            if stitching && images.count > 1 && images.count == chapter.images.count {
                if options.exportStitchHeight == 0 {
                    if try !PNGStrip.write(images.map(\.data), to: target("001", "png")) {
                        for page in images {
                            try save(page, position: page.number)
                        }
                    }
                } else {
                    var first: Page?
                    var index = 0
                    func saveStrip(_ page: Page) throws {
                        index += 1
                        try save(page, position: index)
                    }
                    try FolderWriter.stitch(images, maxHeight: options.exportStitchHeight) { page in
                        if let pending = first {
                            try useChapterFolder()
                            try saveStrip(pending)
                            first = nil
                        } else if index == 0 {
                            first = page
                            return
                        }
                        try saveStrip(page)
                    }
                    if let first { try saveStrip(first) }
                }
            } else {
                for page in images {
                    try save(page, position: page.number)
                }
            }
            if !text.isEmpty { try Data((text + "\n").utf8).write(to: target("chapter", "txt")) }
            if !links.isEmpty { try Data((links.joined(separator: "\n") + "\n").utf8).write(to: target("images", "txt")) }
        }

        public func pack() throws {
            try requireContent(.zip)
            let fm = FileManager.default
            let zip = try Archive(url: packed, accessMode: .create, pathEncoding: nil)
            for name in try fm.subpathsOfDirectory(atPath: stage.path).sorted() {
                try Task.checkCancellation()
                let file = stage.appendingPathComponent(name)
                guard try file.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true else { continue }
                try zip.addEntry(with: name, fileURL: file,
                                 compressionMethod: file.pathExtension == "txt" ? .deflate : .none)
            }
        }

        public func publish(_ format: OutputFormat, baseName: String) throws -> URL {
            let fm = FileManager.default
            let archive = format == .zip
            let target = directory.appendingPathComponent(baseName + "." + format.rawValue, isDirectory: !archive)
            if let kind = try? target.resourceValues(forKeys: [.isSymbolicLinkKey, .isDirectoryKey]),
               kind.isSymbolicLink == true || kind.isDirectory == archive {
                throw CocoaError(.fileWriteFileExists)
            }
            if archive && !fm.fileExists(atPath: packed.path) { try pack() }
            if !archive { try requireContent(format) }
            try Task.checkCancellation()
            if fm.fileExists(atPath: target.path) { try fm.moveItem(at: target, to: previous) }
            do {
                try fm.moveItem(at: archive ? packed : stage, to: target)
            } catch {
                if fm.fileExists(atPath: previous.path) { try fm.moveItem(at: previous, to: target) }
                throw error
            }
            try? fm.removeItem(at: previous)
            return target
        }

        private func requireContent(_ format: OutputFormat) throws {
            if try FileManager.default.contentsOfDirectory(atPath: stage.path).isEmpty {
                throw OutputError.nothingToWrite("No chapter content for " + format.label)
            }
        }

        public func close() {
            let fm = FileManager.default
            if !fm.fileExists(atPath: previous.path) { try? fm.removeItem(at: temporary) }
        }
    }

    private struct Page {
        let number: Int
        let ext: String
        let data: Data
    }

    private static func uniqueURL(in directory: URL, stem: String, suffix: String) -> URL {
        var url = directory.appendingPathComponent(stem + suffix, isDirectory: suffix.isEmpty)
        var duplicate = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = directory.appendingPathComponent("\(stem) (\(duplicate))" + suffix, isDirectory: suffix.isEmpty)
            duplicate += 1
        }
        return url
    }

    private static func stitch(_ images: [Page], maxHeight: Int, emit: (Page) throws -> Void) throws {
        var group: [(Page, Int)] = []
        var width = 0
        var height = 0
        func flush() throws {
            if group.count > 1, let strip = autoreleasepool(invoking: { render(group, width: width, height: height) }) {
                try emit(strip)
            } else {
                for (page, _) in group { try emit(page) }
            }
            group.removeAll(keepingCapacity: true)
            width = 0
            height = 0
        }
        for page in images {
            try Task.checkCancellation()
            let source = CGImageSourceCreateWithData(page.data as CFData, nil)
            let meta = source.flatMap { CGImageSourceCopyPropertiesAtIndex($0, 0, nil) as? [CFString: Any] }
            let w = meta?[kCGImagePropertyPixelWidth] as? Int ?? 0
            let h = meta?[kCGImagePropertyPixelHeight] as? Int ?? 0
            var limit = min(maxHeight, 24_000_000 / max(1, width, w))
            if !group.isEmpty && (w <= 0 || h <= 0 || height + h > limit) {
                try flush()
                limit = min(maxHeight, 24_000_000 / max(1, w))
            }
            if w <= 0 || h <= 0 || h > limit {
                try emit(page)
            } else {
                width = max(width, w)
                height += h
                group.append((page, h))
            }
        }
        try flush()
    }

    private static func render(_ group: [(Page, Int)], width: Int, height: Int) -> Page? {
        guard let canvas = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                     space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        canvas.setFillColor(CGColor(gray: 1, alpha: 1))
        canvas.fill(CGRect(x: 0, y: 0, width: width, height: height))
        var bottom = height
        for (page, size) in group {
            bottom -= size
            let drawn = autoreleasepool {
                guard let source = CGImageSourceCreateWithData(page.data as CFData, nil),
                      let panel = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return false }
                let left = (width - panel.width) / 2
                canvas.draw(panel, in: CGRect(x: left, y: bottom, width: panel.width, height: size))
                return true
            }
            if !drawn { return nil }
        }
        let bytes = NSMutableData()
        guard let image = canvas.makeImage(),
              let dest = CGImageDestinationCreateWithData(bytes, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return Page(number: group[0].0.number, ext: "png", data: bytes as Data)
    }
}
