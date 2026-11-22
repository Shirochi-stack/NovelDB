import Foundation
import NpiaCore
import NpiaImages
import NpiaNet
import NpiaOutput
import NpiaSources
import NpiaUpload
import NpiaWebEngine

/// One active write and one queued chapter. Cleanup waits for both before removing the staging files.
private actor ChapterExportQueue {
    private let output: FolderWriter.Prepared
    private let options: ImageSettings
    private let logger: any JobLogging
    private var pending: [Task<Void, Error>] = []

    init(output: FolderWriter.Prepared, options: ImageSettings, logger: any JobLogging) {
        self.output = output
        self.options = options
        self.logger = logger
    }

    func submit(_ chapter: ChapterContent, images: [String: Data]) async throws {
        if pending.count == 2 {
            try await pending[0].value
            pending.removeFirst()
        }
        try Task.checkCancellation()
        let previous = pending.last
        let write = Task<Void, Error>.detached(priority: .utility) { [output, options, logger] in
            if let previous { try await previous.value }
            try Task.checkCancellation()
            if options.webtoonExport == true && options.stitchingEnabled &&
                chapter.images.count > 1 && chapter.images.allSatisfy({ images[$0.filename] != nil }) {
                logger.log(.info, "Stitching chapter \(chapter.ref.index): \(chapter.ref.title)")
            }
            try output.append(chapter, images: images)
        }
        pending.append(write)
    }

    func finish() async throws {
        for task in pending { try await task.value }
        pending.removeAll()
    }

    func requestCancellation() {
        for task in pending { task.cancel() }
    }

    func cancelAndWait() async {
        requestCancellation()
        for task in pending { _ = try? await task.value }
        pending.removeAll()
    }
}

/// Prepares Folder and ZIP chapters (images encoded, strips stitched) while the other chapters are
/// still being read. The image step and the folder writer used to start only after the last chapter,
/// so a long comic was stitched in one go at the very end. Android's `EarlyChapters` does the same.
///
/// Every chapter that was read is handed over (`offer`); one task takes them a chapter at a time,
/// runs the image step for that chapter alone (it waits for the chapter's images, which started
/// downloading when the chapter was read) and hands it to the export queue. The pipeline's own image
/// step then handles only the chapters `finish` does not return: it ends the early work after the
/// chapter in the works, and leaves the ones still waiting to that step, which has the whole book's
/// progress and runs chapters side by side.
private final class EarlyChapters: @unchecked Sendable {
    /// A chapter that is already with the export queue: as the image step returned it.
    struct Done: Sendable {
        var chapter: ChapterContent
        var images: [String: Data]
        var failed: Int
    }

    private let stage: ImageStage
    private let queue: ChapterExportQueue
    private let preserveSources: Bool
    private let logger: any JobLogging
    private let lock = NSLock()
    private var names = Set<String>()
    private var waiting: [ChapterContent] = []
    private var finished: [String: Done] = [:]
    private var ending = false
    private var worker: Task<Void, Never>?

    init(stage: ImageStage, queue: ChapterExportQueue, preserveSources: Bool, logger: any JobLogging) {
        self.stage = stage
        self.queue = queue
        self.preserveSources = preserveSources
        self.logger = logger
    }

    /// A chapter that was just read, from any task.
    func offer(_ result: ChapterResult) {
        guard case .content(let chapter) = result else { return }
        lock.withLock { () -> Void in
            guard !ending else { return }
            let files = chapter.images.map { $0.filename }
            // An image two chapters share belongs to the image step that has every chapter at once.
            let shared = files.contains { names.contains($0) }
            names.formUnion(files)
            guard !shared else { return }
            waiting.append(chapter)
            if worker == nil {
                worker = Task<Void, Never> { [weak self] in
                    guard let self else { return }
                    await self.drain()
                }
            }
        }
    }

    private func next() -> ChapterContent? {
        lock.withLock { () -> ChapterContent? in
            if ending || waiting.isEmpty {
                worker = nil
                return nil
            }
            return waiting.removeFirst()
        }
    }

    private func drain() async {
        while let chapter = next() {
            await prepare(chapter)
        }
    }

    private func prepare(_ chapter: ChapterContent) async {
        do {
            let queue = self.queue
            // The image step hands a chapter over once all its images are done, so a return means it was handed over.
            let output = try await stage.run([chapter], preserveChapterSources: preserveSources, onChapterReady: { ready, images in
                try await queue.submit(ready, images: images)
            })
            guard let prepared = output.chapters.first else { return }
            let done = Done(chapter: prepared, images: output.images, failed: output.failed)
            let id = chapter.ref.id
            lock.withLock { finished[id] = done }
        } catch is CancellationError {
            // The job ended: nothing to report.
        } catch {
            logger.log(.warning, "Chapter \(chapter.ref.index) could not be prepared early (\(error)); it is written with the rest.")
        }
    }

    /// Ends the early work, waits for the chapter in the works, and returns the chapters done, by chapter id.
    func finish() async -> [String: Done] {
        let task = lock.withLock { () -> Task<Void, Never>? in
            ending = true
            return worker
        }
        // A Stop ends the chapter in the works too, instead of this waiting for it (it may still be
        // waiting for its images).
        _ = await withTaskCancellationHandler {
            await task?.value
        } onCancel: {
            task?.cancel()
        }
        return lock.withLock { finished }
    }

    /// Drops everything (the job failed or was cancelled).
    func cancel() async {
        let task = lock.withLock { () -> Task<Void, Never>? in
            ending = true
            return worker
        }
        task?.cancel()
        await task?.value
    }
}

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
    public var onBook: (@Sendable ([ChapterRef]) -> Void)?
    public var onChapter: (@Sendable (ChapterResult) -> Void)?

    /// At most this many retry passes over failed chapters (fewer when Retries is set lower).
    public static let maxRetryPasses = 3

    public init(web: any WebEngineProviding = WKWebEngine(), secrets: any SecretStore = KeychainSecretStore(), outputDirectory: URL, logger: any JobLogging = NullLogger()) {
        self.web = web
        self.secrets = secrets
        self.outputDirectory = outputDirectory
        self.logger = logger
    }

    public func run(url: URL, settings: AppSettings, http: (any HTTPFetching)? = nil, onPhase: @escaping @Sendable (JobPhase) -> Void = { _ in }) async throws -> JobOutcome {
        if SiteOptOuts.matches(url) {
            logger.log(.error, SiteOptOuts.message)
            throw SourceError.siteDisabled
        }
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
        // Keep the Add screen's range when another engine reads this source's book.
        let requested = settings.effective(for: SourceCatalog.adapterType(for: url)?.id ?? source)
        var shared = settings.effective(for: source)
        shared.rangeFrom = requested.rangeFrom
        shared.rangeTo = requested.rangeTo
        logger.log(.info, "\(book.title): \(book.chapters.count) chapters")
        logger.log(.info, "Source \(source.rawValue) read through \(opened.engine.title); formats \(shared.formats.map { $0.rawValue }.joined(separator: ", "))")
        logger.log(.debug, "Settings: \(shared.threads) threads, \(shared.intervalMin)-\(shared.intervalMax) s between requests, range \(shared.rangeFrom.map(String.init) ?? "start")-\(shared.rangeTo.map(String.init) ?? "end"), skip paid \(shared.skipPaid)")

        let unlocked = try await autoPurchase(adapter, book: book, shared: shared, context: context)
        let chaptersNow = book.chapters.map { chapter -> ChapterRef in
            guard unlocked.contains(chapter.id) else { return chapter }
            var open = chapter
            open.isPaid = false
            return open
        }
        var selected = Self.select(chaptersNow, shared: shared)
        if source == .npia, settings.npia.includeNotices {
            selected = book.notices + selected
        }
        onBook?(selected)
        onPhase(.fetchingChapters(done: 0, total: selected.count))
        // A chapter's images start downloading as soon as it is read, not after the last chapter (ImagePrefetch).
        let prefetch = settings.images.saveImageURLsOnly ? nil
            : ImagePrefetch(http: client, workers: shared.imageWorkers, logger: logger)
        defer { prefetch?.close() }
        let processor = ImageProcessor(settings: settings.images)
        let prepared = !settings.images.saveImageURLsOnly && shared.formats.contains(where: { $0 == .folder || $0 == .zip })
            ? try FolderWriter.Prepared(directory: outputDirectory, options: settings.images) : nil
        defer { prepared?.close() }
        let compressAfter = prepared != nil && settings.images.compressAfterStitch
        let otherImageOutputs = shared.formats.contains { $0 == .epub || $0 == .cbz || $0 == .pdf }
        var stageSettings = settings
        if compressAfter && !otherImageOutputs { stageSettings.images.compress = false }
        var staged = ImageStage(http: client, settings: stageSettings, source: source, logger: logger)
        staged.prefetch = prefetch
        let stage = staged
        // A Folder or ZIP output: each chapter is prepared (and stitched) as soon as its images are in,
        // not after the last chapter is read (EarlyChapters).
        let exportQueue = prepared.map { ChapterExportQueue(output: $0, options: settings.images, logger: logger) }
        let early = exportQueue.map {
            EarlyChapters(stage: stage, queue: $0, preserveSources: compressAfter && otherImageOutputs, logger: logger)
        }
        let chapterRun = chapterContext(context, total: selected.count, onPhase: onPhase, prefetch: prefetch, early: early)
        var results: [ChapterResult]
        do {
            results = try await adapter.parseChapters(selected, book: book, context: chapterRun)
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
        } catch {
            await early?.cancel()
            await exportQueue?.cancelAndWait()
            throw error
        }
        // The chapter being prepared early is finished first; the ones still waiting are done below.
        let earlier = await early?.finish() ?? [:]
        if Task.isCancelled {
            // Stop: the chapters already handed to the export queue are not stitched any further.
            await exportQueue?.cancelAndWait()
            throw CancellationError()
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

        let allChapters = chapters
        if !earlier.isEmpty {
            chapters.removeAll { earlier[$0.ref.id] != nil }
            logger.log(.info, "\(earlier.count) chapter\(earlier.count == 1 ? " was" : "s were") prepared for the Folder/ZIP output while the chapters were read")
        }
        // The image step finishes after the chapters and can be far longer than them: say what it counts.
        let imageFiles = Set(chapters.flatMap { $0.images.map { $0.filename } }).count
        if imageFiles > 0 && !settings.images.saveImageURLsOnly {
            let withImages = chapters.filter { !$0.images.isEmpty }.count
            let early = prefetch?.started ?? 0
            logger.log(.info, "Downloading \(imageFiles) image\(imageFiles == 1 ? "" : "s") from \(withImages) of \(chapters.count) chapters"
                + (early > 0 ? "; \(early) started while the chapters were read" : ""))
        }
        let progress: @Sendable (Int, Int) -> Void = { done, total in
            onPhase(.downloadingImages(done: done, total: total))
        }
        let imageOutput: ImageStage.Output
        if let queue = exportQueue {
            do {
                let selectedChapters = chapters
                imageOutput = try await withTaskCancellationHandler {
                    var output = try await stage.run(selectedChapters, preserveChapterSources: compressAfter && otherImageOutputs,
                                                     onChapterReady: { chapter, images in
                        try await queue.submit(chapter, images: images)
                    }, onProgress: progress)
                    try await queue.finish()
                    if !earlier.isEmpty {
                        // The chapters prepared early and the rest, as one result in the book's order.
                        var later: [String: ChapterContent] = [:]
                        for chapter in output.chapters { later[chapter.ref.id] = chapter }
                        output.chapters = allChapters.compactMap { earlier[$0.ref.id]?.chapter ?? later[$0.ref.id] }
                        for done in earlier.values {
                            output.images.merge(done.images) { first, _ in first }
                            output.failed += done.failed
                        }
                    }
                    return output
                } onCancel: {
                    Task { await queue.requestCancellation() }
                }
            } catch {
                await queue.cancelAndWait()
                throw error
            }
        } else {
            imageOutput = try await stage.run(chapters, onProgress: progress)
        }
        logger.log(.info, "Images: \(imageOutput.images.count) ready" + (imageOutput.failed > 0 ? ", \(imageOutput.failed) failed" : ""))
        if imageOutput.failed > 0 {
            logger.log(.warning, "\(imageOutput.failed) images could not be downloaded")
        }
        chapters = imageOutput.chapters
        var cover: ProcessedCover?
        if let coverURL = book.coverURL, let data = await coverData(coverURL, page: book.url, client: client), !data.isEmpty {
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
                if let prepared, format == .folder || format == .zip {
                    if format == .folder, let folderIndex = shared.formats.firstIndex(of: .folder),
                       let zipIndex = shared.formats.firstIndex(of: .zip), folderIndex < zipIndex {
                        try prepared.pack()
                    }
                    file = try prepared.publish(format, baseName: baseName)
                } else {
                    file = try await writer.write(bundle, baseName: baseName, to: outputDirectory)
                }
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

    /// Logs each chapter result and advances progress immediately, including results from retries.
    private func chapterContext(_ base: SourceContext, total: Int,
                                onPhase: @escaping @Sendable (JobPhase) -> Void,
                                prefetch: ImagePrefetch? = nil, early: EarlyChapters? = nil) -> SourceContext {
        var context = base
        let counter = ChapterCounter()
        let timings = ChapterTimings()
        context.onChapterTiming = { ref, seconds in timings.record(ref.id, seconds) }
        // An image a source downloads itself while reading a chapter gets its log line as it arrives.
        context.onImage = { ref, position, filename, bytes, seconds in
            prefetch?.arrived(ref, position: position, filename: filename, bytes: bytes, seconds: seconds)
        }
        context.onChapter = { result in
            prefetch?.offer(result)
            early?.offer(result)
            onChapter?(result)
            let elapsed = timings.take(result.ref.id)
            let suffix = base.settings.shared.chapterTiming ? elapsed.map { String(format: " (%.2f s)", $0) } ?? "" : ""
            let label = "Chapter " + String(result.ref.index) + ": " + result.ref.title + suffix
            switch result {
            case .content: logger.log(.info, label)
            case .refused(_, let reason): logger.log(.warning, label + " skipped: " + reason)
            case .failed(_, let message): logger.log(.warning, label + " failed: " + message)
            }
            let done = min(counter.next(), total)
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
    /// `page`: the book's address, sent as the Referer when the server refuses a request without one (fanfox).
    private func coverData(_ url: URL, page: URL, client: any HTTPFetching) async -> Data? {
        if url.scheme?.lowercased() == "data" { return try? Data(contentsOf: url) }
        guard var answer = try? await client.get(url) else { return nil }
        if answer.status == 401 || answer.status == 403 {
            guard let again = try? await client.get(url, headers: ["Referer": page.absoluteString]) else { return nil }
            answer = again
        }
        return (200..<300).contains(answer.status) ? answer.body : nil
    }

    /// Auto purchase: unlocks the paid chapters that are in both the chapter range and the purchase
    /// range, before they are downloaded. Returns the ids of the chapters that opened.
    private func autoPurchase(_ adapter: any SourceAdapter, book: BookInfo, shared: SharedSettings, context: SourceContext) async throws -> Set<String> {
        guard shared.autoPurchase else { return [] }
        guard shared.purchaseRange != nil else {
            logger.log(.info, "Auto purchase is on but has no chapter range; nothing is purchased.")
            return []
        }
        let label = ChapterRangeText.named(from: shared.purchaseFrom, to: shared.purchaseTo, list: shared.purchaseList)
        let wanted = book.chapters.filter { $0.isPaid && !$0.isNotice && shared.purchasesChapter($0.index) && shared.keepsChapter($0.index) }
        if wanted.isEmpty {
            logger.log(.info, "Auto purchase (\(label)): no locked chapter in that range to unlock.")
            return []
        }
        logger.log(.info, "Auto purchase (\(label)): \(wanted.count) locked chapter\(wanted.count == 1 ? "" : "s") to unlock with free tickets.")
        var opened: Set<String> = []
        for ref in wanted {
            try Task.checkCancellation()
            let result: UnlockResult
            do {
                result = try await adapter.unlock(ref, book: book, context: context)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                result = .noTicket(reason: error.localizedDescription)
            }
            switch result {
            case .unlocked(let with):
                opened.insert(ref.id)
                logger.log(.info, "Auto purchase: chapter \(ref.index) unlocked with \(with).")
            case .noTicket(let reason):
                logger.log(.warning, "Auto purchase: no ticket for chapter \(ref.index) (\(reason)). Nothing was bought.")
            case .signedOut:
                logger.log(.warning, "Auto purchase needs a signed-in account. Sign in from the Browser tab, then download again.")
                return opened
            case .unsupported:
                logger.log(.warning, "Auto purchase (\(label)) is not available for \(type(of: adapter).id.rawValue) yet; the locked chapters stay locked.")
                return opened
            }
        }
        return opened
    }

    public static func select(_ chapters: [ChapterRef], shared: SharedSettings) -> [ChapterRef] {
        return chapters.filter { chapter in
            if !shared.keepsChapter(chapter.index) { return false }
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
final class ChapterCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    func next() -> Int {
        lock.lock(); defer { lock.unlock() }
        value += 1
        return value
    }
}

/// Each callback belongs to its own chapter, even when concurrent tasks finish out of order.
private final class ChapterTimings: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: TimeInterval] = [:]
    func record(_ id: String, _ seconds: TimeInterval) {
        lock.lock()
        defer { lock.unlock() }
        values[id, default: 0] += seconds
    }
    func take(_ id: String) -> TimeInterval? {
        lock.lock()
        defer { lock.unlock() }
        return values.removeValue(forKey: id)
    }
}
