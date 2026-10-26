import Foundation
import NpiaCore
import NpiaImages
import NpiaNet
import NpiaOutput
import NpiaSources
import NpiaUpload
import NpiaWebEngine

public enum JobPhase: Sendable, Equatable {
    case queued
    case fetchingBook
    case fetchingChapters(done: Int, total: Int)
    case downloadingImages(done: Int, total: Int)
    case writing(OutputFormat)
    case uploading(String)
    case finished
    case failed(String)
}

public struct JobOutcome: Sendable {
    public var book: BookInfo
    public var files: [URL]
    public var uploads: [UploadResult]
    /// Chapters that were refused or failed, with the reason.
    public var skipped: [(ChapterRef, String)]
}

/// Runs one download end to end: detect source, parse book, fetch chapters and images,
/// write each output format, and optionally upload.
public struct DownloadPipeline: Sendable {
    public var web: any WebEngineProviding
    public var secrets: any SecretStore
    public var outputDirectory: URL
    public var logger: any JobLogging

    public init(web: any WebEngineProviding = WKWebEngine(), secrets: any SecretStore = KeychainSecretStore(), outputDirectory: URL, logger: any JobLogging = NullLogger()) {
        self.web = web
        self.secrets = secrets
        self.outputDirectory = outputDirectory
        self.logger = logger
    }

    public func run(url: URL, settings: AppSettings, http: (any HTTPFetching)? = nil, onPhase: @escaping @Sendable (JobPhase) -> Void = { _ in }) async throws -> JobOutcome {
        let adapter = try SourceCatalog.adapter(for: url)
        let source = type(of: adapter).id
        let shared = settings.effective(for: source)
        let client = http ?? URLSessionHTTPClient(settings: shared)
        let context = SourceContext(http: client, web: web, secrets: secrets, settings: settings, logger: logger)

        onPhase(.fetchingBook)
        let book = try await adapter.parseBook(url, context: context)
        logger.log(.info, "\(book.title): \(book.chapters.count) chapters")

        var selected = Self.select(book.chapters, shared: shared)
        if source == .npia, settings.npia.includeNotices {
            selected = book.notices + selected
        }
        onPhase(.fetchingChapters(done: 0, total: selected.count))
        let results = try await adapter.parseChapters(selected, book: book, context: context)

        var chapters: [ChapterContent] = []
        var skipped: [(ChapterRef, String)] = []
        for result in results {
            switch result {
            case .content(let content): chapters.append(content)
            case .refused(let ref, let reason), .failed(let ref, let reason): skipped.append((ref, reason))
            }
        }
        onPhase(.fetchingChapters(done: chapters.count, total: selected.count))

        let processor = ImageProcessor(settings: settings.images)
        let images = try await fetchImages(chapters.flatMap(\.images), http: client, processor: processor, onPhase: onPhase)
        var cover: ProcessedCover?
        if let coverURL = book.coverURL, let data = try? await client.get(coverURL).body {
            let processed = try processor.process(data, isCover: true)
            cover = ProcessedCover(data: processed.data, fileExtension: processed.fileExtension)
        }

        let bundle = BookBundle(book: book, chapters: chapters, images: images, cover: cover, settings: settings)
        let baseName = Writers.baseName(for: book, settings: shared)
        var files: [URL] = []
        for format in shared.formats {
            onPhase(.writing(format))
            files.append(try await Writers.writer(for: format).write(bundle, baseName: baseName, to: outputDirectory))
        }

        var uploads: [UploadResult] = []
        if settings.upload.autoUpload {
            for file in files where !file.hasDirectoryPath {
                onPhase(.uploading(file.lastPathComponent))
                do {
                    uploads.append(try await UploaderCatalog.upload(file, settings: settings.upload))
                } catch {
                    logger.log(.warning, "Upload failed for \(file.lastPathComponent): \(error.localizedDescription)")
                }
            }
        }

        onPhase(.finished)
        return JobOutcome(book: book, files: files, uploads: uploads, skipped: skipped)
    }

    /// Applies the chapter range and paid filter.
    public static func select(_ chapters: [ChapterRef], shared: SharedSettings) -> [ChapterRef] {
        chapters.filter { chapter in
            if let from = shared.rangeFrom, chapter.index < from { return false }
            if let to = shared.rangeTo, chapter.index > to { return false }
            if shared.skipPaid, chapter.isPaid { return false }
            return true
        }
    }

    private func fetchImages(_ refs: [ImageRef], http: any HTTPFetching, processor: ImageProcessor, onPhase: @escaping @Sendable (JobPhase) -> Void) async throws -> [String: Data] {
        var images: [String: Data] = [:]
        var done = 0
        // TODO(images workstream): bounded concurrency (`imageWorkers`) and rewriting filenames
        // when recompression changes the extension.
        for ref in refs {
            try Task.checkCancellation()
            let data: Data?
            if let bytes = ref.data {
                data = bytes
            } else {
                data = try? await http.get(ref.url).body
            }
            if let data {
                images[ref.filename] = (try? processor.process(data))?.data ?? data
            } else {
                logger.log(.warning, "Image failed: \(ref.url.absoluteString)")
            }
            done += 1
            onPhase(.downloadingImages(done: done, total: refs.count))
        }
        return images
    }
}
