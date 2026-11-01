import Foundation

/// Reuses up to `size` pages of one profile across concurrent chapter tasks, so a batch with
/// `threads = N` keeps at most N web views alive (the desktop opened one Playwright page per worker).
public actor WebPagePool {
    private let engine: any WebEngineProviding
    private let profile: String
    private let size: Int
    private let prepare: (@Sendable (any WebPageDriving) async -> Void)?
    private var idle: [any WebPageDriving] = []
    private var created = 0
    private var waiters: [CheckedContinuation<any WebPageDriving, Error>] = []
    private var closed = false

    /// - Parameter prepare: runs once on every new page (for example `startCapture`).
    public init(engine: any WebEngineProviding, profile: String, size: Int,
                prepare: (@Sendable (any WebPageDriving) async -> Void)? = nil) {
        self.engine = engine
        self.profile = profile
        self.size = max(1, size)
        self.prepare = prepare
    }

    /// Pages created so far (live or idle).
    public var pageCount: Int { created }

    public func acquire() async throws -> any WebPageDriving {
        if closed { throw CancellationError() }
        if let page = idle.popLast() { return page }
        if created < size {
            created += 1
            do {
                let page = try await engine.newPage(profile: profile)
                await prepare?(page)
                return page
            } catch {
                created -= 1
                throw error
            }
        }
        return try await withCheckedThrowingContinuation { waiters.append($0) }
    }

    public func release(_ page: any WebPageDriving) async {
        if closed {
            await page.close()
        } else if !waiters.isEmpty {
            waiters.removeFirst().resume(returning: page)
        } else {
            idle.append(page)
        }
    }

    /// Closes idle pages and fails waiting tasks; pages still leased close on release.
    public func closeAll() async {
        closed = true
        let pages = idle
        idle = []
        for waiter in waiters { waiter.resume(throwing: CancellationError()) }
        waiters = []
        for page in pages { await page.close() }
    }

    /// Leases a page for `body` and returns it to the pool afterwards.
    public nonisolated func withPage<T: Sendable>(_ body: (any WebPageDriving) async throws -> T) async throws -> T {
        let page = try await acquire()
        do {
            let value = try await body(page)
            await release(page)
            return value
        } catch {
            await release(page)
            throw error
        }
    }
}
