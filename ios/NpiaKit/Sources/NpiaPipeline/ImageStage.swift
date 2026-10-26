import Foundation
import NpiaCore
import NpiaImages

/// Downloads and recompresses chapter images with bounded concurrency, then points the
/// chapter HTML at the final file names.
public struct ImageStage: Sendable {
    public var http: any HTTPFetching
    public var settings: AppSettings
    public var source: SourceID
    public var logger: any JobLogging

    public init(http: any HTTPFetching, settings: AppSettings, source: SourceID, logger: any JobLogging = NullLogger()) {
        self.http = http
        self.settings = settings
        self.source = source
        self.logger = logger
    }

    public struct Output: Sendable {
        public var chapters: [ChapterContent]
        /// Processed bytes keyed by the (possibly renamed) `ImageRef.filename`.
        public var images: [String: Data]
        public var failed: Int
    }

    public func run(_ chapters: [ChapterContent], onProgress: @escaping @Sendable (_ done: Int, _ total: Int) -> Void = { _, _ in }) async throws -> Output {
        if settings.images.saveImageURLsOnly {
            return Output(chapters: chapters.map(Self.remoteOnly), images: [:], failed: 0)
        }

        // One download per file name, in reading order.
        var unique: [ImageRef] = []
        var seen = Set<String>()
        for ref in chapters.flatMap(\.images) where seen.insert(ref.filename).inserted {
            unique.append(ref)
        }

        let processor = ImageProcessor(settings: settings.images)
        let workers = max(1, settings.effective(for: source).imageWorkers)
        let compressionGate = AsyncGate(limit: max(1, settings.images.compressionWorkers))
        var processed: [String: ProcessedImage] = [:]
        var failed = 0
        var done = 0
        onProgress(0, unique.count)

        try await withThrowingTaskGroup(of: (String, ProcessedImage?).self) { group in
            var pending = Array(unique.reversed())
            for _ in 0..<workers {
                guard let ref = pending.popLast() else { break }
                group.addTask { (ref.filename, try await self.fetch(ref, processor: processor, gate: compressionGate)) }
            }
            while let (name, image) = try await group.next() {
                if let image { processed[name] = image } else { failed += 1 }
                done += 1
                onProgress(done, unique.count)
                if let ref = pending.popLast() {
                    group.addTask { (ref.filename, try await self.fetch(ref, processor: processor, gate: compressionGate)) }
                }
            }
        }

        let renames = Self.renames(for: unique.map(\.filename), processed: processed)
        var images: [String: Data] = [:]
        images.reserveCapacity(processed.count)
        for (name, image) in processed {
            images[renames[name] ?? name] = image.data
        }
        let rewritten = chapters.map { Self.apply(renames, to: $0, keeping: processed) }
        return Output(chapters: rewritten, images: images, failed: failed)
    }

    private func fetch(_ ref: ImageRef, processor: ImageProcessor, gate: AsyncGate) async throws -> ProcessedImage? {
        try Task.checkCancellation()
        let data: Data
        if let bytes = ref.data {
            data = bytes
        } else {
            do {
                let response = try await http.get(ref.url)
                guard (200..<300).contains(response.status), !response.body.isEmpty else {
                    logger.log(.warning, "Image failed (\(response.status)): \(ref.url.absoluteString)")
                    return nil
                }
                data = response.body
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                logger.log(.warning, "Image failed: \(ref.url.absoluteString): \(error.localizedDescription)")
                return nil
            }
        }
        await gate.acquire()
        let result = Task.isCancelled ? nil : ((try? processor.process(data)) ?? ProcessedImage(data: data, fileExtension: ImageProcessor.sniffExtension(data)))
        await gate.release()
        try Task.checkCancellation()
        return result
    }

    /// Old name -> new name for every image whose extension changed. Collisions get a
    /// numeric suffix so two sources never overwrite each other.
    static func renames(for names: [String], processed: [String: ProcessedImage]) -> [String: String] {
        var taken = Set(names)
        var renames: [String: String] = [:]
        for name in names {
            guard let image = processed[name], image.fileExtension != "bin" else { continue }
            let currentExtension = (name as NSString).pathExtension.lowercased()
            let same = currentExtension == image.fileExtension || (currentExtension == "jpeg" && image.fileExtension == "jpg")
            guard !same else { continue }
            var candidate = ImageReferences.renamed(name, toExtension: image.fileExtension)
            var counter = 2
            while taken.contains(candidate) {
                let stem = (ImageReferences.renamed(name, toExtension: "x") as NSString).deletingPathExtension
                candidate = "\(stem)_\(counter).\(image.fileExtension)"
                counter += 1
            }
            taken.insert(candidate)
            renames[name] = candidate
        }
        return renames
    }

    /// Renames refs and HTML references; drops refs whose download failed.
    static func apply(_ renames: [String: String], to chapter: ChapterContent, keeping processed: [String: ProcessedImage]) -> ChapterContent {
        var chapter = chapter
        var local: [String: String] = [:]
        for ref in chapter.images {
            if let new = renames[ref.filename] { local[ref.filename] = new }
        }
        if !local.isEmpty {
            chapter.html = ImageReferences.rewrite(chapter.html, renames: local)
        }
        chapter.images = chapter.images.compactMap { ref in
            guard processed[ref.filename] != nil else { return nil }
            var ref = ref
            ref.filename = renames[ref.filename] ?? ref.filename
            ref.data = nil
            return ref
        }
        return chapter
    }

    /// `saveImageURLsOnly`: point every reference at the remote URL and download nothing.
    static func remoteOnly(_ chapter: ChapterContent) -> ChapterContent {
        var chapter = chapter
        var remote: [String: String] = [:]
        for ref in chapter.images where ref.url.scheme == "http" || ref.url.scheme == "https" {
            remote[ref.filename] = ref.url.absoluteString
        }
        if !remote.isEmpty {
            // The whole value becomes the remote URL, including any `../` prefix.
            chapter.html = ImageReferences.rewriteValues(chapter.html) { value in
                if let exact = remote[value] { return exact }
                return remote.first { value.hasSuffix("/" + $0.key) }?.value
            }
        }
        chapter.images = []
        return chapter
    }
}

/// Counting semaphore for async tasks.
actor AsyncGate {
    private let limit: Int
    private var active = 0
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(limit: Int) {
        self.limit = limit
    }

    func acquire() async {
        if active < limit {
            active += 1
            return
        }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        if waiters.isEmpty {
            active = max(0, active - 1)
        } else {
            waiters.removeFirst().resume()
        }
    }
}
