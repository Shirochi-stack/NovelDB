import Foundation
import NpiaCore

/// Spaces page loads at least `minGap` seconds apart, however many workers ask: qdn's firewall blocks
/// bursts. Each caller reserves the next free slot and sleeps until it, so workers that arrive together
/// leave one gap apart.
actor HardPacer {
    private let minGap: TimeInterval
    private let clock: @Sendable () -> TimeInterval
    private var next: TimeInterval = -.infinity

    init(minGap: TimeInterval, clock: @escaping @Sendable () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.minGap = max(0, minGap)
        self.clock = clock
    }

    private func reserve() -> TimeInterval {
        let now = clock()
        let start = max(now, next)
        next = start + minGap
        return start - now
    }

    func pace() async throws {
        let wait = reserve()
        if wait > 0 { try await HTTPSourceSupport.sleep(seconds: wait) }
    }
}

/// Sign-in and verification prompts already shown for a book. The pipeline reads the book again after the
/// user signs in, with a new adapter, so this is process wide: the second ask for the same key goes on
/// without asking (and clears the key, so a later download asks again).
final class HardPrompts: @unchecked Sendable {
    static let shared = HardPrompts()

    private let lock = NSLock()
    private var keys = Set<String>()

    /// True the first time for `key`, false the second.
    func once(_ key: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if keys.remove(key) != nil { return false }
        keys.insert(key)
        return true
    }

    func clear(_ key: String) {
        lock.lock(); defer { lock.unlock() }
        keys.remove(key)
    }

    /// Tests start from a fresh process.
    func reset() {
        lock.lock(); defer { lock.unlock() }
        keys = []
    }
}

/// One caller at a time across `await`s (a plain actor lets others in whenever a call suspends).
actor HardLock {
    private var busy = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    private func acquire() async {
        if busy {
            await withCheckedContinuation { waiters.append($0) }
        } else {
            busy = true
        }
    }

    private func release() {
        if waiters.isEmpty {
            busy = false
        } else {
            waiters.removeFirst().resume()
        }
    }

    func run<T>(_ body: () async throws -> T) async rethrows -> T {
        await acquire()
        do {
            let value = try await body()
            release()
            return value
        } catch {
            release()
            throw error
        }
    }
}

enum HardSupport {
    /// The error is a cancellation: callers rethrow it instead of treating it as a failed attempt.
    static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError
    }

    /// Runs `body`; a failure other than cancellation gives nil (a page that is still loading throws
    /// when asked questions).
    static func attempt<T>(_ body: () async throws -> T) async throws -> T? {
        do {
            return try await body()
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            return nil
        }
    }

    /// Loads `url`; a load that times out is only a warning (the page is often usable already). Other
    /// failures are thrown.
    static func loadQuietly(_ page: any WebPageDriving, _ url: URL, timeout: TimeInterval, log: (String) -> Void) async throws {
        do {
            try await page.load(url, until: .domContentLoaded, timeout: timeout)
        } catch WebEngineError.timeout(let slow) {
            log("Page load warning: \(slow.absoluteString) took longer than \(Int(timeout)) s")
        }
    }

    static func closeQuietly(_ page: any WebPageDriving) async {
        await page.close()
    }

    /// A page in a desktop browser's shape (user agent, viewport), with the shared script installed as a
    /// document-start script.
    static func openPage(_ web: any WebEngineProviding, userAgent: String, width: Int, height: Int) async throws -> any WebPageDriving {
        let page = try await web.newPage(profile: WebProfile.external)
        await prepare(page, userAgent: userAgent, width: width, height: height)
        return page
    }

    static func prepare(_ page: any WebPageDriving, userAgent: String, width: Int, height: Int) async {
        await page.emulateDesktop(userAgent: userAgent, width: width, height: height)
        await page.addMainFrameInitScript(HardScripts.source)
    }

    /// Runs `work` for every chapter with at most `limit` in flight, like the default `parseChapters`: a worker
    /// rests one chapter delay before its next chapter, a chapter that throws becomes a failed result, and
    /// `onChapter` hears about each result as soon as it is known. Results come back in `refs` order.
    static func parseConcurrently(
        _ refs: [ChapterRef], limit: Int, shared: SharedSettings, context: SourceContext,
        work: @escaping @Sendable (ChapterRef) async throws -> ChapterResult
    ) async throws -> [ChapterResult] {
        var results = [ChapterResult?](repeating: nil, count: refs.count)
        var pending: [(offset: Int, element: ChapterRef)] = refs.enumerated().reversed()
        let onChapter = context.onChapter

        @Sendable func task(_ position: Int, _ ref: ChapterRef, rest: Bool) async throws -> (Int, ChapterResult) {
            if rest { try await shared.sleepChapterDelay() }
            do {
                return (position, try await work(ref))
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                return (position, .failed(ref, message: error.localizedDescription))
            }
        }

        try await withThrowingTaskGroup(of: (Int, ChapterResult).self) { group in
            for _ in 0..<max(1, limit) {
                guard let item = pending.popLast() else { break }
                group.addTask { try await task(item.offset, item.element, rest: false) }
            }
            while let (position, result) = try await group.next() {
                results[position] = result
                onChapter(result)
                if let item = pending.popLast() {
                    group.addTask { try await task(item.offset, item.element, rest: true) }
                }
            }
        }
        return results.compactMap { $0 }
    }
}
