import Foundation
import NpiaCore

/// Downloads a chapter's images as soon as the chapter is read, while the later chapters are still
/// being read. The image step used to start only after the last chapter: a long book waited through
/// every chapter list before its first image, and addresses a site signs for a while (kpage: two
/// hours) could run out first. Android's `ImagePrefetch` and the desktop's `KpageImages` do the same.
///
/// The bytes wait in temporary files (mapped, not in memory) until the image step (`ImageStage`)
/// takes them: it waits for a download that is still on its way, and asks the site itself only for
/// what was never started or failed here (and reports it).
final class ImagePrefetch: @unchecked Sendable {
    private let http: any HTTPFetching
    private let logger: any JobLogging
    private let gate: AsyncGate
    /// Left free on the device: with less, images wait for the image step (which keeps them compressed).
    private let reserveBytes: Int64
    private let lock = NSLock()
    private var tasks: [String: Task<Data?, Never>] = [:]
    private var known = Set<String>()
    private var arrived = Set<String>()
    private var closed = false

    init(http: any HTTPFetching, workers: Int, logger: any JobLogging = NullLogger(), reserveBytes: Int64 = 512 * 1024 * 1024) {
        self.http = http
        self.logger = logger
        self.gate = AsyncGate(limit: max(1, workers))
        self.reserveBytes = reserveBytes
    }

    /// How many images were started while the chapters were read: here, or by the source itself (`arrived`).
    var started: Int { lock.withLock { known.count } }

    /// True when `filename`'s "✓" line is already in the log: it was downloaded here, or its source reported it.
    func brought(_ filename: String) -> Bool { lock.withLock { arrived.contains(filename) } }

    /// An image its source downloaded itself, as part of reading the chapter (`SourceContext.onImage`):
    /// its line is written now, like the ones downloaded here, and the image step does not repeat it.
    func arrived(_ ref: ChapterRef, position: Int, filename: String, bytes: Int, seconds: TimeInterval) {
        lock.withLock {
            known.insert(filename)
            arrived.insert(filename)
        }
        logger.log(.debug, Self.line(Self.label(ref, position: position), bytes: bytes, seconds: seconds))
    }

    /// The image step's label for an image's log line: "[12] Image #3 [Chapter title]".
    private static func label(_ ref: ChapterRef, position: Int) -> String {
        "[\((ref.isNotice ? "N" : "") + String(ref.index))] Image #\(position + 1) [\(ref.title)]"
    }

    private static func line(_ label: String, bytes: Int, seconds: TimeInterval) -> String {
        let speed = ImageStage.formatSize(Int(Double(bytes) / max(seconds, 0.001)))
        return "  ✓ \(label): \(ImageStage.formatSize(bytes)) [\(speed)/s]"
    }

    /// A chapter that was just read: its images that came without bytes start downloading.
    func offer(_ result: ChapterResult) {
        guard case .content(let content) = result else { return }
        guard Self.freeBytes() >= reserveBytes else { return }
        let page = content.ref.url
        lock.withLock {
            guard !closed else { return }
            for (position, image) in content.images.enumerated() {
                guard image.data == nil, !known.contains(image.filename),
                      let scheme = image.url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { continue }
                known.insert(image.filename)
                let label = Self.label(content.ref, position: position)
                tasks[image.filename] = Task { [weak self] in
                    guard let self else { return nil }
                    return await self.fetch(image, page: page, label: label)
                }
            }
        }
    }

    /// The bytes `filename`'s download brought, once it ends; nil when none was started here or it
    /// failed. They are let go here: the image step holds them from now on.
    func take(_ filename: String) async -> Data? {
        guard let task = lock.withLock({ tasks.removeValue(forKey: filename) }) else { return nil }
        return await task.value
    }

    /// Stops what is still on its way and lets every byte go (the job ended or was stopped).
    func close() {
        let open = lock.withLock { () -> [Task<Data?, Never>] in
            closed = true
            let all = Array(tasks.values)
            tasks = [:]
            return all
        }
        for task in open { task.cancel() }
    }

    private func fetch(_ image: ImageRef, page: URL?, label: String) async -> Data? {
        await gate.acquire()
        let data = Task.isCancelled ? nil : await download(image, page: page, label: label)
        await gate.release()
        return data
    }

    /// Like the image step: asked again from the chapter's page when the server wants a Referer (fanfox).
    private func download(_ image: ImageRef, page: URL?, label: String) async -> Data? {
        let began = Date()
        do {
            var response = try await http.get(image.url)
            if response.status == 401 || response.status == 403, let page {
                response = try await http.get(image.url, headers: ["Referer": page.absoluteString])
            }
            guard (200..<300).contains(response.status), !response.body.isEmpty else { return nil }
            logger.log(.debug, Self.line(label, bytes: response.body.count, seconds: Date().timeIntervalSince(began)))
            let name = image.filename
            lock.withLock { _ = arrived.insert(name) }
            return Self.onDisk(response.body)
        } catch {
            // The image step asks again and reports what it cannot get.
            return nil
        }
    }

    /// The bytes backed by a file instead of memory: a book's images are far more than memory holds.
    /// The file is unlinked at once; its space is freed when the bytes are let go.
    static func onDisk(_ data: Data) -> Data {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("nd-image-\(UUID().uuidString)")
        guard (try? data.write(to: file)) != nil else { return data }
        defer { try? FileManager.default.removeItem(at: file) }
        return (try? Data(contentsOf: file, options: .alwaysMapped)) ?? data
    }

    private static func freeBytes() -> Int64 {
        let values = try? FileManager.default.temporaryDirectory.resourceValues(forKeys: [.volumeAvailableCapacityKey])
        guard let free = values?.volumeAvailableCapacity else { return Int64.max }
        return Int64(free)
    }
}
