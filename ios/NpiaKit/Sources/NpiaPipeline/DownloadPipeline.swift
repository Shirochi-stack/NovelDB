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
    /// Waiting for the user to sign in to this host in the browser.
    case signingIn(String)
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
        let book: BookInfo
        do {
            book = try await adapter.parseBook(url, context: context)
        } catch SourceError.loginRequired(_) {
            try await signIn(url: url, source: source, onPhase: onPhase)
            onPhase(.fetchingBook)
            book = try await adapter.parseBook(url, context: context)
        }
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
        let stage = ImageStage(http: client, settings: settings, source: source, logger: logger)
        let imageOutput = try await stage.run(chapters) { done, total in
            onPhase(.downloadingImages(done: done, total: total))
        }
        if imageOutput.failed > 0 {
            logger.log(.warning, "\(imageOutput.failed) images could not be downloaded")
        }
        chapters = imageOutput.chapters
        var cover: ProcessedCover?
        if let coverURL = book.coverURL, let data = try? await client.get(coverURL).body, !data.isEmpty {
            let processed = try processor.process(data, isCover: true)
            cover = ProcessedCover(data: processed.data, fileExtension: processed.fileExtension)
        }

        let bundle = BookBundle(book: book, chapters: chapters, images: imageOutput.images, cover: cover, settings: settings)
        let baseName = Writers.baseName(for: book, settings: shared)
        var files: [URL] = []
        for format in shared.formats {
            onPhase(.writing(format))
            let writer = Writers.writer(for: format)
            var file = try await writer.write(bundle, baseName: baseName, to: outputDirectory)
            if let targetMB = settings.images.recompressTargetMB, targetMB > 0, format == .epub || format == .pdf {
                file = try await shrink(file, bundle: bundle, writer: writer, baseName: baseName, targetBytes: Int(targetMB * 1_048_576))
            }
            files.append(file)
        }

        var uploads: [UploadResult] = []
        if settings.upload.autoUpload {
            for file in files {
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

    /// Opens the sign-in sheet on the book's site and waits for the user to finish. Without a
    /// visible screen (app in the background) the job fails with instructions instead.
    private func signIn(url: URL, source: SourceID, onPhase: @escaping @Sendable (JobPhase) -> Void) async throws {
        let host = url.bareHost.isEmpty ? source.rawValue : url.bareHost
        logger.log(.info, "Sign-in needed for \(host); opening the browser")
        onPhase(.signingIn(host))
        do {
            try await web.presentLogin(url: url, profile: BrowserProfileName.value)
        } catch is CancellationError {
            throw SourceError.blocked("sign-in to \(host) was cancelled")
        } catch {
            throw SourceError.blocked("sign in to \(host) in the Browser tab, then retry")
        }
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

    /// `recompressTargetMB`: re-encode images at falling quality and rewrite until the file
    /// fits, at most four passes (desktop `_recompress_to_target_size`).
    private func shrink(_ file: URL, bundle: BookBundle, writer: any BookWriter, baseName: String, targetBytes: Int) async throws -> URL {
        func size(_ url: URL) -> Int { (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0 }
        var current = size(file)
        guard current > targetBytes else { return file }
        logger.log(.info, "\(file.lastPathComponent) is \(current) bytes; recompressing images toward \(targetBytes)")
        let processor = ImageProcessor(settings: bundle.settings.images)
        var quality = max(10, bundle.settings.images.quality)
        var working = bundle
        var output = file
        for pass in 1...4 {
            try Task.checkCancellation()
            guard let next = ImageProcessor.nextRecompressQuality(current: quality, size: current, target: targetBytes) else { break }
            quality = next
            for (name, data) in working.images {
                working.images[name] = processor.reencode(data, fileExtension: (name as NSString).pathExtension, quality: quality)
            }
            if let cover = working.cover {
                working.cover?.data = processor.reencode(cover.data, fileExtension: cover.fileExtension, quality: quality)
            }
            output = try await writer.write(working, baseName: baseName, to: outputDirectory)
            current = size(output)
            logger.log(.info, "Pass \(pass) at quality \(quality): \(current) bytes")
            if current <= targetBytes { break }
        }
        return output
    }
}

/// Sign-in prompts use the Browser tab's store, so a login made in either place is shared.
enum BrowserProfileName {
    static let value = "browser"
}
