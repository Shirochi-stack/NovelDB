import Foundation
import NpiaCore

/// Counts the requests a parallel author sweep has made, across all workers.
final class NpiaRequestBudget: @unchecked Sendable {
    private let lock = NSLock()
    private let limit: Int?
    private var made = 0

    init(limit: Int?) {
        self.limit = limit
    }

    func exhausted() -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard let limit else { return false }
        return made >= limit
    }

    /// Takes one request from the budget; false when none is left.
    func acquire() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if let limit, made >= limit { return false }
        made += 1
        return true
    }
}

/// Port of `npia_author_catalog.py`: the public per-author novel lists that the "scrape all
/// novels" search walks after the keyword sweep.
enum NpiaAuthorCatalog {
    static var url: URL { URL(string: "https://\(NpiaParser.host)/proc/user")! }

    /// One validated catalog page.
    struct Page: Sendable, Equatable {
        var rows: [NpiaNovelRow]
        var more: Bool
        /// The novel ids of the page in order, used to spot a server that repeats a page.
        var signature: [String]
    }

    /// What the desktop yields as `(author, page, rows, more, error, attempts)`.
    struct Event: Sendable, Equatable {
        var owner: String
        var page: Int
        /// nil when the page failed or the run was stopped.
        var rows: [NpiaNovelRow]?
        var more: Bool
        var error: String?
        var signature: [String]
    }

    /// `iter_author_pages` for one page: waits a random delay, posts the request, and retries
    /// up to `retries` times. Returns nil when the run stopped (or the request budget ran out)
    /// before a request was made and nothing had failed yet.
    static func fetchPage(
        author: String, page: Int, pageSize: Int = 100, delay: Double = 1, delayMax: Double? = nil,
        retries: Int = 4, signatures: Set<[String]> = [], context: NpiaSearchContext,
        stopped: @Sendable () -> Bool = { Task.isCancelled },
        acquire: @Sendable () -> Bool = { true }
    ) async throws -> Page? {
        guard NpiaNovelRow.isPositiveInteger(author) else { throw NpiaSearchError("invalid author ID") }
        guard (1...100).contains(pageSize) else { throw NpiaSearchError("author page size must be between 1 and 100") }
        let delayMax = delayMax ?? delay
        guard delay >= 0, delayMax >= delay, retries >= 1 else {
            throw NpiaSearchError("invalid author delay range or retry count")
        }

        var lastError: NpiaSearchError?
        for attempt in 1...retries {
            await context.pause(Double.random(in: delay...delayMax))
            if stopped() || !acquire() {
                if let lastError { throw lastError }
                return nil
            }
            var response: HTTPResponse?
            do {
                let body = NpiaAuth.formBody([
                    ("mode", "get_member_writer_novel"), ("mem_no", author),
                    ("paging[rowCount]", String(pageSize)), ("paging[curPage]", String(page)),
                    ("paging[order]", "date"), ("paging[sort][date]", "1"),
                ])
                let reply = try await context.http.send(
                    HTTPRequest(url: url, method: "POST", headers: NpiaAuth.headers(form: true), body: body)
                )
                response = reply
                return try validate(reply, author: author, page: page, pageSize: pageSize, signatures: signatures)
            } catch {
                if Task.isCancelled, response == nil {
                    if let lastError { throw lastError }
                    return nil
                }
                // Transport errors can contain credential-bearing URLs, so only their type is logged.
                let reason = response != nil ? NpiaSearchError.message(error) : String(describing: type(of: error))
                let details = response.map(NpiaSearchText.responseDetails) ?? "no response details"
                let failure = NpiaSearchError("\(reason); \(details)")
                lastError = failure
                context.log("Author \(author) page \(page) attempt \(attempt)/\(retries) failed: \(failure)")
                if attempt == retries { throw failure }
            }
        }
        throw lastError ?? NpiaSearchError("author catalog request failed")
    }

    /// The checks the desktop runs on every page before keeping it.
    static func validate(_ response: HTTPResponse, author: String, page: Int, pageSize: Int, signatures: Set<[String]>) throws -> Page {
        guard response.status == 200 else { throw NpiaSearchError("author catalog HTTP \(response.status)") }
        // The desktop refuses redirects; URLSession follows them, so a changed path means one happened.
        guard response.url.path == url.path else { throw NpiaSearchError("author catalog was redirected") }
        guard let object = try? JSONSerialization.jsonObject(with: response.body) else {
            throw NpiaSearchError("author catalog returned invalid JSON", kind: "JSONDecodeError")
        }
        guard let data = object as? [String: Any], NpiaScalar(any: data["status"]).text == "200" else {
            throw NpiaSearchError("author catalog API error")
        }
        let result = data["result"] as? [String: Any] ?? [:]
        guard let rawRows = result["novel"] as? [Any],
              case .int(let total) = NpiaScalar(any: result["allCount"]), total >= 0 else {
            throw NpiaSearchError("invalid author catalog response")
        }

        var rows: [NpiaNovelRow] = []
        for item in rawRows {
            guard let record = item as? [String: Any], NpiaNovelRow.isDigits(NpiaScalar(any: record["novel_no"]).text) else {
                throw NpiaSearchError("invalid author catalog record")
            }
            let row = NpiaNovelRow(json: record)
            if let owner = row.authorID, owner != author {
                throw NpiaSearchError("author catalog identity mismatch")
            }
            rows.append(row)
        }
        let signature = rows.map(\.novelNo)
        if !rows.isEmpty, signatures.contains(signature) {
            throw NpiaSearchError("author catalog repeated a page")
        }
        if rows.isEmpty, (page - 1) * pageSize < total {
            throw NpiaSearchError("author catalog returned an incomplete page")
        }
        let more = page * pageSize < total
        let expected = min(pageSize, max(0, total - (page - 1) * pageSize))
        if rows.count < expected {
            throw NpiaSearchError("author catalog returned an incomplete page")
        }
        return Page(rows: rows, more: more, signature: signature)
    }

    /// `parallel_author_pages`: walks every author's pages with at most `workers` requests in
    /// flight and at most one page per author at a time. `onEvent` runs on the calling task, one
    /// event at a time, so the caller can update its collections without locking.
    static func parallelPages(
        authors: [(owner: String, page: Int)], workers: Int = 4, delay: Double = 1, delayMax: Double = 2,
        retries: Int = 4, pageSize: Int = 100, maxRequests: Int? = nil, context: NpiaSearchContext,
        onEvent: (Event) async -> Void
    ) async throws {
        guard workers >= 1 else { throw NpiaSearchError("author workers must be positive") }
        var queue = authors.map { (owner: $0.owner, page: $0.page) }
        var signatures: [String: Set<[String]>] = [:]
        let budget = NpiaRequestBudget(limit: maxRequests)
        let exhausted: @Sendable () -> Bool = { Task.isCancelled || budget.exhausted() }

        await withTaskGroup(of: Event.self) { group in
            var running = 0
            while running > 0 || (!queue.isEmpty && !exhausted()) {
                while !queue.isEmpty, running < workers, !exhausted() {
                    let next = queue.removeFirst()
                    let known = signatures[next.owner] ?? []
                    signatures[next.owner] = known
                    group.addTask {
                        await event(owner: next.owner, page: next.page, known: known, delay: delay, delayMax: delayMax,
                                    retries: retries, pageSize: pageSize, context: context, exhausted: exhausted, budget: budget)
                    }
                    running += 1
                }
                if running == 0 { break }
                guard let finished = await group.next() else { break }
                running -= 1
                if finished.rows != nil, finished.more {
                    signatures[finished.owner, default: []].insert(finished.signature)
                    queue.insert((owner: finished.owner, page: finished.page + 1), at: 0)
                } else {
                    signatures[finished.owner] = nil
                }
                await onEvent(finished)
            }
        }
    }

    private static func event(
        owner: String, page: Int, known: Set<[String]>, delay: Double, delayMax: Double, retries: Int,
        pageSize: Int, context: NpiaSearchContext, exhausted: @escaping @Sendable () -> Bool, budget: NpiaRequestBudget
    ) async -> Event {
        do {
            let result = try await fetchPage(
                author: owner, page: page, pageSize: pageSize, delay: delay, delayMax: delayMax, retries: retries,
                signatures: known, context: context, stopped: exhausted, acquire: { budget.acquire() }
            )
            guard let result else {
                return Event(owner: owner, page: page, rows: nil, more: false, error: nil, signature: [])
            }
            return Event(owner: owner, page: page, rows: result.rows, more: result.more, error: nil, signature: result.signature)
        } catch {
            return Event(owner: owner, page: page, rows: nil, more: false, error: NpiaSearchError.message(error), signature: [])
        }
    }
}
