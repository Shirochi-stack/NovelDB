import Foundation
import NpiaCore

/// How tags combine in a tag search (`fetch_novels_by_tags` modes).
public enum NpiaTagQuery: Sendable, Equatable {
    /// Tags inside a group are OR'd, the groups are AND'd: `[["TS"], ["a", "b"]]` is TS AND (a OR b).
    case groups([[String]])
    /// Novels that carry every tag.
    case and([String])
    /// Novels that carry any tag.
    case or([String])
}

/// The catalog searches of the desktop "Novel ID Tag Retrieval" dialog, ported from
/// `DownloaderCore.fetch_novels_by_tags`, `fetch_all_novels`, and `fetch_top100_rankings`.
public enum NpiaCatalogSearch {
    /// `rows` of a tag search request.
    static let tagRows = 50_000
    /// `ROWS_PER_PAGE` of the all-novels sweep, and its 40-page ceiling (600,000 results).
    static let sweepRows = 15_000
    static let sweepPages = 40
    static let sweepAttempts = 4

    /// The link a novel id opens (the same URL the Add tab accepts).
    public static func novelURL(id: String) -> URL {
        NpiaParser.novelURL(id)
    }

    // MARK: - Requests

    static func searchRequest(term: String, searchType: String, page: Int, rows: Int, ageParameter: String, timeout: TimeInterval) -> HTTPRequest {
        let fields: [(String, String)] = [
            ("cmd", "novel_search"), ("search_type", searchType), ("search_val", term),
            ("page", String(page)), ("rows", String(rows)), ("novel_type", ""),
            ("start_count_book", ""), ("end_count_book", ""), ("novel_age", ageParameter), ("start_days", ""),
            ("sort_col", "last_viewdate"), ("novel_genre", ""), ("block_out", "0"), ("block_stop", "0"),
            ("is_contest", "0"), ("is_complete", ""), ("is_challenge", "0"), ("list_display", "grid"),
        ]
        let query = String(decoding: NpiaAuth.formBody(fields), as: UTF8.self)
        let url = URL(string: "https://\(NpiaParser.host)/proc/novel?\(query)")!
        let headers = NpiaAuth.headers(referer: "https://\(NpiaParser.host)/search")
        return HTTPRequest(url: url, headers: headers, timeout: timeout)
    }

    private static func logAgeFilter(_ ageFilter: String, _ context: NpiaSearchContext) {
        if ageFilter == "15" {
            context.log("  Age filter: Non-adult only")
        } else if ageFilter == "19" {
            context.log("  Age filter: Adult only")
        }
    }

    // MARK: - Tag search

    /// `fetch_novels_by_tags`. Throws on a transport or format failure, like the desktop
    /// worker's "Tag retrieval failed"; a stopped run returns what it has with `stopped` set.
    public static func fetchNovels(
        query: NpiaTagQuery, delay: Double, ageFilter: String, threads: Int, context: NpiaSearchContext
    ) async throws -> NpiaSearchResult {
        let threads = max(1, threads)
        let tagCount: Int
        switch query {
        case .groups(let groups):
            tagCount = groups.reduce(0) { $0 + $1.count }
            context.log("Searching with \(groups.count) group(s), \(threads) thread(s):")
            for (index, group) in groups.enumerated() {
                context.log("  Group \(index + 1): \(group.joined(separator: " OR "))")
                if index < groups.count - 1 {
                    context.log("    AND")
                }
            }
        case .and(let tags):
            tagCount = tags.count
            context.log("Searching for novels with tags: \(tags.joined(separator: ", ")) (mode: AND, \(threads) thread(s))")
        case .or(let tags):
            tagCount = tags.count
            context.log("Searching for novels with tags: \(tags.joined(separator: ", ")) (mode: OR, \(threads) thread(s))")
        }
        logAgeFilter(ageFilter, context)
        let progress = NpiaProgress(total: tagCount, report: context.progress)

        func fetchTags(_ tags: [String]) async throws -> [NpiaIDSet] {
            try await fetchTagSets(tags, delay: delay, ageFilter: ageFilter, threads: threads, progress: progress, context: context)
        }

        let resultIDs: [String]
        switch query {
        case .groups(let groups):
            // Each group ORs its tags (in parallel); the groups are then AND'd.
            var resultSet: NpiaIDSet?
            for (index, group) in groups.enumerated() {
                if Task.isCancelled { break }
                var groupIDs = NpiaIDSet()
                for ids in try await fetchTags(group) { groupIDs.formUnion(ids) }
                context.log("  Group \(index + 1) result: \(groupIDs.count) novel(s)")
                if var current = resultSet {
                    current.formIntersection(groupIDs)
                    resultSet = current
                    context.log("  After AND with group \(index + 1): \(current.count) novel(s)")
                } else {
                    resultSet = groupIDs
                }
            }
            resultIDs = resultSet?.ids ?? []

        case .and(let tags) where tags.count > 1:
            var resultSet: NpiaIDSet?
            for ids in try await fetchTags(tags) {
                if var current = resultSet {
                    current.formIntersection(ids)
                    resultSet = current
                } else {
                    resultSet = ids
                }
            }
            resultIDs = resultSet?.ids ?? []
            context.log("  AND intersection: \(resultIDs.count) novel(s) match all \(tags.count) tag(s)")

        case .and(let tags), .or(let tags):
            var all = NpiaIDSet()
            for ids in try await fetchTags(tags) { all.formUnion(ids) }
            resultIDs = all.ids
        }

        context.log("Tag search complete: \(resultIDs.count) novel(s) found.")
        return NpiaSearchResult(ids: resultIDs, stopped: Task.isCancelled)
    }

    /// `_fetch_tags_parallel`: one id set per tag, in tag order. With several threads the tag
    /// requests start `delay` seconds apart, at most `threads` at a time.
    private static func fetchTagSets(
        _ tags: [String], delay: Double, ageFilter: String, threads: Int, progress: NpiaProgress, context: NpiaSearchContext
    ) async throws -> [NpiaIDSet] {
        if threads <= 1 {
            var results: [NpiaIDSet] = []
            for (offset, tag) in tags.enumerated() {
                if Task.isCancelled { break }
                results.append(try await fetchTag(tag, ageFilter: ageFilter, progress: progress, context: context))
                if delay > 0, offset < tags.count - 1 { await context.pause(delay) }
            }
            return results
        }
        return try await withThrowingTaskGroup(of: (Int, NpiaIDSet).self) { group in
            var results: [Int: NpiaIDSet] = [:]
            var running = 0
            for (offset, tag) in tags.enumerated() {
                if Task.isCancelled { break }
                if running >= threads, let finished = try await group.next() {
                    results[finished.0] = finished.1
                    running -= 1
                }
                group.addTask {
                    let ids = try await fetchTag(tag, ageFilter: ageFilter, progress: progress, context: context)
                    return (offset, ids)
                }
                running += 1
                if delay > 0, offset < tags.count - 1 { await context.pause(delay) }
            }
            while let finished = try await group.next() {
                results[finished.0] = finished.1
            }
            return results.keys.sorted().compactMap { results[$0] }
        }
    }

    /// `_fetch_tag`: every novel id of one tag in a single request.
    private static func fetchTag(_ tag: String, ageFilter: String, progress: NpiaProgress, context: NpiaSearchContext) async throws -> NpiaIDSet {
        if Task.isCancelled { return NpiaIDSet() }
        let request = searchRequest(term: tag, searchType: "novel_genre", page: 1, rows: tagRows, ageParameter: ageFilter, timeout: 60)
        let response: HTTPResponse
        do {
            response = try await context.withElapsedTicks { try await context.http.send(request) }
        } catch {
            if Task.isCancelled { return NpiaIDSet() }
            throw error
        }
        let payload: NpiaSearchPayload
        do {
            payload = try JSONDecoder().decode(NpiaSearchPayload.self, from: response.body)
        } catch {
            throw NpiaSearchError("tag '\(tag)' returned HTTP \(response.status) with a body that is not valid JSON", kind: "JSONDecodeError")
        }
        guard payload.isOK else {
            context.log("  [\(tag)] API error: \(payload.errorText)")
            progress.advance("Tag \(tag)")
            return NpiaIDSet()
        }
        guard payload.listIsValid else {
            throw NpiaSearchError("tag '\(tag)' returned a list that is not an array", kind: "TypeError")
        }
        var ids = NpiaIDSet()
        for record in payload.rows ?? [] {
            if let row = record.row, !row.novelNo.isEmpty { ids.insert(row.novelNo) }
        }
        context.log("  [\(tag)] \(ids.count) novel(s)")
        progress.advance("Tag \(tag)")
        return ids
    }

    // MARK: - Scrape all novels

    /// `fetch_all_novels`: a sweep over the search terms (`maxQueries` of `NpiaSearchTerms.allTerms`),
    /// the public intro pages, and the public catalog of every author seen along the way.
    public static func fetchAllNovels(
        delay: Double, ageFilter: String, maxQueries: Int? = nil, threads: Int, context: NpiaSearchContext
    ) async -> NpiaSearchResult {
        let queryCount = NpiaSearchTerms.clampQueryCount(maxQueries ?? NpiaSearchTerms.defaultQueryCount)
        let terms = Array(NpiaSearchTerms.allTerms.prefix(queryCount))
        let threads = max(1, threads)
        context.log("Scraping all novel IDs from Npia... (\(terms.count) queries, \(threads) thread(s))")
        logAgeFilter(ageFilter, context)

        let state = NpiaSweepState()
        let queryProgress = NpiaProgress(total: terms.count, from: 0, to: 0.5, report: context.progress)

        if threads > 1 {
            await withTaskGroup(of: Void.self) { group in
                var running = 0
                for (index, term) in terms.enumerated() {
                    if Task.isCancelled { break }
                    if running >= threads {
                        _ = await group.next()
                        running -= 1
                    }
                    group.addTask {
                        await sweepQuery(index: index, term: term, total: terms.count, ageFilter: ageFilter, state: state,
                                         progress: queryProgress, context: context)
                    }
                    running += 1
                    if delay > 0, index < terms.count - 1 { await context.pause(delay) }
                }
                await group.waitForAll()
            }
        } else {
            for (index, term) in terms.enumerated() {
                if Task.isCancelled {
                    context.log("  Stopped by user.")
                    break
                }
                await sweepQuery(index: index, term: term, total: terms.count, ageFilter: ageFilter, state: state,
                                 progress: queryProgress, context: context)
                if delay > 0, index < terms.count - 1 { await context.pause(delay) }
            }
        }

        context.progress(0.5, "Public intro pages")
        let introRows = await NpiaIntroDiscovery.discover(maxRequests: 6, delay: max(2.0, delay), context: context)
        for row in introRows {
            if let owner = row.authorID { state.addAuthor(owner) }
            if row.matches(ageFilter: ageFilter) { state.addID(row.novelNo) }
        }

        let authors = state.sortedAuthors()
        context.log("Checking \(authors.count) public author catalogs with 4 workers (1-2s random delay)...")
        context.progress(0.55, "Author catalogs")
        let authorProgress = NpiaProgress(total: authors.count, from: 0.55, to: 1, report: context.progress)
        var completedAuthors = 0
        do {
            try await NpiaAuthorCatalog.parallelPages(
                authors: authors.map { (owner: $0, page: 1) }, workers: 4, delay: 1, delayMax: 2, retries: 4, pageSize: 100,
                context: context
            ) { event in
                if let error = event.error {
                    context.log("  Author \(event.owner) PARTIAL: completed IDs retained; \(error)")
                    authorProgress.advance("Author \(event.owner)")
                    return
                }
                guard let rows = event.rows else { return }
                for row in rows where row.matches(ageFilter: ageFilter) {
                    state.addID(row.novelNo)
                }
                if !event.more {
                    completedAuthors += 1
                    authorProgress.advance("Author \(event.owner)")
                }
                context.log("  Author \(event.owner), page \(event.page): \(completedAuthors)/\(authors.count) complete, \(state.idCount) IDs total")
            }
        } catch {
            context.log("Author catalog sweep failed: \(NpiaSearchError.message(error))")
        }

        let ids = state.allIDs()
        context.log("  \(ids.count) novel(s) retrieved total")
        return NpiaSearchResult(ids: ids, stopped: Task.isCancelled)
    }

    /// `_query_one`: one search term across up to 40 pages of 15,000 results.
    private static func sweepQuery(
        index: Int, term: String, total: Int, ageFilter: String, state: NpiaSweepState, progress: NpiaProgress,
        context: NpiaSearchContext
    ) async {
        if Task.isCancelled { return }
        context.log("  Query \(index + 1)/\(total): searching '\(term)'...")
        var results = 0
        var matched = NpiaIDSet()
        var owners = Set<String>()
        do {
            try await context.withElapsedTicks {
                for page in 1...sweepPages {
                    guard let batch = try await sweepPage(term: term, page: page, ageFilter: ageFilter, context: context) else { break }
                    results += batch.count
                    for row in batch.rows {
                        if let owner = row.authorID { owners.insert(owner) }
                        if !row.novelNo.isEmpty, row.matches(ageFilter: ageFilter) { matched.insert(row.novelNo) }
                    }
                    // Fewer than a full page means there is no next one.
                    if batch.count < sweepRows { break }
                }
            }
        } catch {
            if !Task.isCancelled {
                context.log("    Error on '\(term)': \(NpiaSearchError.message(error))")
            }
            return
        }
        let merged = state.merge(ids: matched, authors: owners)
        context.log("    \(results) results, \(merged.newCount) new (total: \(merged.total))")
        progress.advance("Query \(index + 1)/\(total)")
    }

    /// One page of a sweep query with the desktop's retry loop. Returns nil after logging why
    /// the query should stop paging.
    private static func sweepPage(
        term: String, page: Int, ageFilter: String, context: NpiaSearchContext
    ) async throws -> (rows: [NpiaNovelRow], count: Int)? {
        let request = searchRequest(
            term: term, searchType: "all", page: page, rows: sweepRows,
            ageParameter: ageFilter == "19" ? "" : ageFilter, timeout: 120
        )
        var response: HTTPResponse?
        var lastError = ""
        for attempt in 1...sweepAttempts {
            if Task.isCancelled { break }
            do {
                let reply = try await context.http.send(request)
                response = reply
                if !NpiaSearchTerms.retryableStatusCodes.contains(reply.status) { break }
                lastError = "HTTP \(reply.status)"
            } catch {
                response = nil
                if Task.isCancelled { break }
                lastError = error.localizedDescription
            }
            if attempt < sweepAttempts, !Task.isCancelled {
                let wait = min(1 << (attempt - 1), 8)
                context.log("    Retry \(attempt)/\(sweepAttempts) for '\(term)' p\(page) after \(lastError); sleeping \(wait)s")
                await context.pause(Double(wait))
            }
        }
        if Task.isCancelled { return nil }
        guard let response else {
            context.log("    Failed page \(page) for '\(term)': \(lastError)")
            return nil
        }
        if response.status != 200 {
            context.log("    HTTP \(response.status) on page \(page), skipping")
            return nil
        }
        if response.body.first(where: { !isBlank($0) }) == nil {
            context.log("    Empty response on page \(page), skipping")
            return nil
        }
        let payload: NpiaSearchPayload
        do {
            payload = try JSONDecoder().decode(NpiaSearchPayload.self, from: response.body)
        } catch {
            let text = response.text.trimmingCharacters(in: .whitespacesAndNewlines)
            context.log("    Invalid JSON for '\(term)' p\(page) (len=\(text.count)): \(text.prefix(300))")
            return nil
        }
        guard payload.isOK else {
            context.log("    API error p\(page): \(payload.errorText)")
            return nil
        }
        guard payload.listIsValid else {
            throw NpiaSearchError("search list is not an array", kind: "TypeError")
        }
        let records = payload.rows ?? []
        return (records.compactMap(\.row), records.count)
    }

    private static func isBlank(_ byte: UInt8) -> Bool {
        byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D || byte == 0x0B || byte == 0x0C
    }

    // MARK: - Top 100

    /// `fetch_top100_rankings`: the first 100 unique novel ids of each ranking page. An age
    /// filter of "19" reads the adult lists only, "15" the teen lists only.
    public static func fetchTop100(ageFilter: String, context: NpiaSearchContext) async -> NpiaTop100Result {
        let audiences: [(path: String, label: String)]
        if ageFilter == "19" {
            audiences = [("adult/plus", "adult")]
        } else if ageFilter == "15" {
            audiences = [("teen/plus", "teen")]
        } else {
            audiences = [("all/plus", "all"), ("adult/plus", "adult"), ("teen/plus", "teen")]
        }
        let periods: [(path: String, label: String)] = [("today", "daily"), ("weekly", "weekly"), ("month", "monthly")]
        let progress = NpiaProgress(total: audiences.count * periods.count, report: context.progress)

        var rankings: [NpiaRanking] = []
        for audience in audiences {
            for period in periods {
                if Task.isCancelled {
                    context.log("  Stopped by user.")
                    return NpiaTop100Result(rankings: rankings, stopped: true)
                }
                let label = "\(period.label) \(audience.label)"
                let url = URL(string: "https://\(NpiaParser.host)/top100/all/\(period.path)/view/\(audience.path)")!
                context.log("  Fetching \(label)...")
                do {
                    let response = try await context.http.send(HTTPRequest(url: url, headers: NpiaAuth.headers(), timeout: 30))
                    if response.status != 200 {
                        context.log("  \(label): HTTP \(response.status)")
                        rankings.append(NpiaRanking(label: label, ids: []))
                        progress.advance(label)
                        continue
                    }
                    let found = NpiaRx.all(#"/novel/(\d+)"#, in: response.text).map { $0[1] }
                    let ids = Array(NpiaSearchTerms.unique(found).prefix(100))
                    rankings.append(NpiaRanking(label: label, ids: ids))
                    context.log("  \(label): \(ids.count) novels")
                } catch {
                    if Task.isCancelled {
                        context.log("  Stopped by user.")
                        return NpiaTop100Result(rankings: rankings, stopped: true)
                    }
                    context.log("  \(label): Error - \(error.localizedDescription)")
                    rankings.append(NpiaRanking(label: label, ids: []))
                }
                progress.advance(label)
                await context.pause(0.3)
            }
        }
        return NpiaTop100Result(rankings: rankings, stopped: Task.isCancelled)
    }
}

/// Ids and authors the sweep has collected, shared by the concurrent queries.
final class NpiaSweepState: @unchecked Sendable {
    private let lock = NSLock()
    private var ids = NpiaIDSet()
    private var authors = Set<String>()

    /// Adds a query's results; reports how many ids were new and the running total.
    func merge(ids found: NpiaIDSet, authors owners: Set<String>) -> (newCount: Int, total: Int) {
        lock.lock(); defer { lock.unlock() }
        let before = ids.count
        ids.formUnion(found)
        authors.formUnion(owners)
        return (ids.count - before, ids.count)
    }

    func addID(_ id: String) {
        lock.lock(); defer { lock.unlock() }
        ids.insert(id)
    }

    func addAuthor(_ owner: String) {
        lock.lock(); defer { lock.unlock() }
        authors.insert(owner)
    }

    var idCount: Int {
        lock.lock(); defer { lock.unlock() }
        return ids.count
    }

    func allIDs() -> [String] {
        lock.lock(); defer { lock.unlock() }
        return ids.ids
    }

    /// `sorted(authors, key=int)`.
    func sortedAuthors() -> [String] {
        lock.lock(); defer { lock.unlock() }
        return authors.sorted { $0.count != $1.count ? $0.count < $1.count : $0 < $1 }
    }
}
