import Foundation
import NpiaCore
import NpiaImages
import NpiaNet
import NpiaOutput
import NpiaSources
import NpiaUpload
import NpiaWebEngine

/// Turns upload events into short job status text such as "book.epub - Gofile 42%", so the
/// queue shows which host is being tried and how far it got.
private final class UploadStatusRelay: @unchecked Sendable {
    private let name: String
    private let logger: any JobLogging
    private let onPhase: @Sendable (JobPhase) -> Void
    private let lock = NSLock()
    private var lastText = ""

    init(name: String, logger: any JobLogging, onPhase: @escaping @Sendable (JobPhase) -> Void) {
        self.name = name
        self.logger = logger
        self.onPhase = onPhase
    }

    func handle(_ event: UploadBatchEvent) {
        switch event {
        case .starting(let index, let total, let name):
            if total > 1 { logger.log(.info, "Upload \(index) of \(total): \(name)") }
            update("\(name)")
        case .preparing(let zip, let total, let name):
            logger.log(.info, "Preparing ZIP \(zip) of \(total): \(name)")
            update("Preparing ZIP \(zip) of \(total)")
        case .host(let hostEvent):
            handle(hostEvent)
        case .uploaded(let result):
            logger.log(.info, "Uploaded \(result.summary): \(result.link.absoluteString)")
        case .failed(let name, let message):
            logger.log(.warning, "Upload of \(name) failed: \(message)")
        }
    }

    private func update(_ text: String) {
        lock.lock()
        let changed = text != lastText
        lastText = text
        lock.unlock()
        if changed { onPhase(.uploading(text)) }
    }

    func handle(_ event: UploadEvent) {
        let text: String
        switch event {
        case .trying(let host, _, _):
            text = "\(name) \u{b7} \(host)"
        case .progress(let host, let progress):
            text = "\(name) \u{b7} \(host) \(Int(progress.fraction * 100))%"
        case .failed(let host, let message):
            logger.log(.warning, "Upload to \(host) failed: \(message)")
            return
        }
        lock.lock()
        let changed = text != lastText
        lastText = text
        lock.unlock()
        if changed { onPhase(.uploading(text)) }
    }
}

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
    /// The pause before the first retry pass over failed chapters; later passes wait longer.
    public var retryPause: TimeInterval = 5

    /// At most this many retry passes over failed chapters (fewer when Retries is set lower).
    public static let maxRetryPasses = 3

    public init(web: any WebEngineProviding = WKWebEngine(), secrets: any SecretStore = KeychainSecretStore(), outputDirectory: URL, logger: any JobLogging = NullLogger()) {
        self.web = web
        self.secrets = secrets
        self.outputDirectory = outputDirectory
        self.logger = logger
    }

    public func run(url: URL, settings: AppSettings, http: (any HTTPFetching)? = nil, onPhase: @escaping @Sendable (JobPhase) -> Void = { _ in }) async throws -> JobOutcome {
        guard SiteEngineRouter.canTry(url, engines: settings.engines) else { throw SourceError.unsupportedURL(url) }
        // The retry count is the same for every source, so the client does not depend on which engine reads the book.
        let client = http ?? URLSessionHTTPClient(settings: settings.shared, logger: logger)
        let started = Date()
        let context = SourceContext(http: client, web: web, secrets: secrets, settings: settings, logger: logger)

        // The site engines (built-in scraper, novel-downloader rules, WebToEpub) are tried in the
        // user's order; the adapter that read the book also reads its chapters.
        onPhase(.fetchingBook)
        let opened: SiteEngineRouter.Opened
        do {
            opened = try await SiteEngineRouter.open(url, context: context)
        } catch SourceError.loginRequired(let required) {
            try await signIn(url: url, source: required, onPhase: onPhase)
            onPhase(.fetchingBook)
            opened = try await SiteEngineRouter.open(url, context: context)
        }
        let adapter = opened.adapter
        let book = opened.book
        let source = type(of: adapter).id
        let shared = settings.effective(for: source)
        logger.log(.info, "\(book.title): \(book.chapters.count) chapters")
        logger.log(.info, "Source \(source.rawValue) read through \(opened.engine.title); formats \(shared.formats.map { $0.rawValue }.joined(separator: ", "))")
        logger.log(.debug, "Settings: \(shared.threads) threads, \(shared.intervalMin)-\(shared.intervalMax) s between requests, range \(shared.rangeFrom.map(String.init) ?? "start")-\(shared.rangeTo.map(String.init) ?? "end"), skip paid \(shared.skipPaid)")

        var selected = Self.select(book.chapters, shared: shared)
        if source == .npia, settings.npia.includeNotices {
            selected = book.notices + selected
        }
        onPhase(.fetchingChapters(done: 0, total: selected.count))
        let chapterLog = OrderedChapterLog(selected, logger: logger)
        let chapterRun = chapterContext(context, total: selected.count, log: chapterLog, onPhase: onPhase)
        var results = try await adapter.parseChapters(selected, book: book, context: chapterRun)
        chapterLog.flush()
        // Failed chapters (not refused ones) get more passes, each after a longer pause.
        let passes = min(max(shared.maxRetries, 0), Self.maxRetryPasses)
        if passes > 0 {
            for pass in 1...passes {
                let failed = results.indices.filter { if case .failed = results[$0] { return true } else { return false } }
                if failed.isEmpty { break }
                logger.log(.info, "Retrying \(failed.count) failed chapter\(failed.count == 1 ? "" : "s") (pass \(pass) of \(passes))")
                try await Task.sleep(nanoseconds: UInt64(retryPause * Double(pass) * 1_000_000_000))
                let again = try await adapter.parseChapters(failed.map { results[$0].ref }, book: book, context: chapterRun)
                for (offset, result) in again.enumerated() where offset < failed.count { results[failed[offset]] = result }
            }
        }

        var chapters: [ChapterContent] = []
        var skipped: [(ChapterRef, String)] = []
        for result in results {
            switch result {
            case .content(let content): chapters.append(content)
            case .refused(let ref, let reason), .failed(let ref, let reason): skipped.append((ref, reason))
            }
        }
        onPhase(.fetchingChapters(done: chapters.count, total: selected.count))
        logger.log(skipped.isEmpty ? .info : .warning, "Read \(chapters.count) of \(selected.count) chapters" + (skipped.isEmpty ? "" : ", \(skipped.count) skipped"))

        let processor = ImageProcessor(settings: settings.images)
        let stage = ImageStage(http: client, settings: settings, source: source, logger: logger)
        let imageOutput = try await stage.run(chapters) { done, total in
            onPhase(.downloadingImages(done: done, total: total))
        }
        logger.log(.info, "Images: \(imageOutput.images.count) ready" + (imageOutput.failed > 0 ? ", \(imageOutput.failed) failed" : ""))
        if imageOutput.failed > 0 {
            logger.log(.warning, "\(imageOutput.failed) images could not be downloaded")
        }
        chapters = imageOutput.chapters
        var cover: ProcessedCover?
        if let coverURL = book.coverURL, let data = await coverData(coverURL, client: client), !data.isEmpty {
            let processed = try processor.process(data, isCover: true)
            cover = ProcessedCover(data: processed.data, fileExtension: processed.fileExtension)
        }

        let bundle = BookBundle(book: book, chapters: chapters, images: imageOutput.images, cover: cover, settings: settings)
        let baseName = Writers.baseName(for: book, settings: shared)
        var files: [URL] = []
        for format in shared.formats {
            onPhase(.writing(format))
            // Desktop "Save as HTML (instead of TXT)".
            let writer: any BookWriter = format == .txt && settings.text.saveHTML ? HTMLWriter() : Writers.writer(for: format)
            if format == .cbz {
                for ref in CBZWriter.chaptersWithoutImages(bundle) {
                    logger.log(.info, "Skipping CBZ chapter " + String(ref.index) + ": " + ref.title + " (no images)")
                }
            }
            var file: URL
            do {
                file = try await writer.write(bundle, baseName: baseName, to: outputDirectory)
            } catch OutputError.nothingToWrite(let reason) {
                logger.log(.warning, reason)
                continue
            }
            if let targetMB = settings.images.recompressTargetMB, targetMB > 0, format == .epub || format == .pdf {
                file = try await shrink(file, bundle: bundle, writer: writer, baseName: baseName, targetBytes: Int(targetMB * 1_048_576))
            }
            files.append(file)
            let bytes = (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            logger.log(.info, "Wrote \(file.lastPathComponent) (\(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)))")
        }

        var uploads: [UploadResult] = []
        if settings.upload.autoUpload, !files.isEmpty {
            // The nameID is the stand-in name (`<source>_<book id>`) for a title with nothing
            // usable in ASCII; the files of one job are bundled like any other upload.
            let nameID = UploadNaming.nameID(source: source.rawValue, bookID: book.id, url: url)
            let items = files.map { UploadItem(file: $0, title: book.title, nameID: nameID) }
            let label = files.count == 1 ? files[0].lastPathComponent : "\(files.count) files"
            onPhase(.uploading(label))
            logger.log(.info, "Uploading \(label)")
            let status = UploadStatusRelay(name: label, logger: logger, onPhase: onPhase)
            do {
                let outcome = try await UploaderCatalog.uploadBatch(items, settings: settings.upload, events: { status.handle($0) })
                uploads = outcome.uploads
                if outcome.uploads.isEmpty {
                    logger.log(.warning, "Upload failed for \(label)")
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                logger.log(.warning, "Upload failed for \(label): \(error.localizedDescription)")
            }
        }

        logger.log(.info, "Finished in " + String(format: "%.1f", Date().timeIntervalSince(started)) + " s")
        onPhase(.finished)
        return JobOutcome(book: book, files: files, uploads: uploads, skipped: skipped)
    }

    /// A context whose `onChapter` advances the progress bar as each chapter lands; `log` writes them in reading order.
    private func chapterContext(_ base: SourceContext, total: Int, log: OrderedChapterLog,
                                onPhase: @escaping @Sendable (JobPhase) -> Void) -> SourceContext {
        var context = base
        let counter = ChapterCounter()
        context.onChapter = { result in
            let done = min(counter.next(), total)
            log.add(result)
            onPhase(.fetchingChapters(done: done, total: total))
        }
        return context
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
    /// A cover's bytes: decoded in place for a data: URL (a cover a source fetched itself), else downloaded.
    private func coverData(_ url: URL, client: any HTTPFetching) async -> Data? {
        if url.scheme?.lowercased() == "data" { return try? Data(contentsOf: url) }
        return try? await client.get(url).body
    }

    public static func select(_ chapters: [ChapterRef], shared: SharedSettings) -> [ChapterRef] {
        let range = shared.chapterRange
        return chapters.filter { chapter in
            if !range.contains(chapter.index) { return false }
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

/// Thread-safe chapter counter for progress callbacks that arrive from concurrent tasks.
/// Logs chapters in reading order: one that finishes early waits for those before it. `flush` logs any
/// still waiting (a chapter an adapter never reported); later results (retries) are logged as they come.
final class OrderedChapterLog: @unchecked Sendable {
    private let lock = NSLock()
    private let logger: any JobLogging
    private var place: [ChapterRef: Int] = [:]
    private var waiting: [Int: ChapterResult] = [:]
    private var next = 0
    private var flushed = false

    init(_ selected: [ChapterRef], logger: any JobLogging) {
        self.logger = logger
        for (offset, ref) in selected.enumerated() where place[ref] == nil { place[ref] = offset }
    }

    func add(_ result: ChapterResult) {
        lock.lock(); defer { lock.unlock() }
        guard !flushed, let at = place[result.ref], at >= next else { write(result); return }
        waiting[at] = result
        while let ready = waiting.removeValue(forKey: next) {
            write(ready)
            next += 1
        }
    }

    func flush() {
        lock.lock(); defer { lock.unlock() }
        for key in waiting.keys.sorted() { if let result = waiting[key] { write(result) } }
        waiting.removeAll()
        flushed = true
    }

    private func write(_ result: ChapterResult) {
        let label = "Chapter " + String(result.ref.index) + ": " + result.ref.title
        switch result {
        case .content: logger.log(.info, label)
        case .refused(_, let reason): logger.log(.warning, label + " skipped: " + reason)
        case .failed(_, let message): logger.log(.warning, label + " failed: " + message)
        }
    }
}

final class ChapterCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    func next() -> Int {
        lock.lock(); defer { lock.unlock() }
        value += 1
        return value
    }
}
