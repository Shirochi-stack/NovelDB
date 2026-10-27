import XCTest
import NpiaCore
@testable import NpiaSources

private let host = NpiaParser.host

/// Answers each request from a closure and records it. No test touches a live host.
private final class ScriptedHTTP: HTTPFetching, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [HTTPRequest] = []
    private let route: @Sendable (HTTPRequest) -> (Int, String)

    init(_ route: @escaping @Sendable (HTTPRequest) -> (Int, String)) {
        self.route = route
    }

    var requests: [HTTPRequest] {
        lock.lock(); defer { lock.unlock() }
        return log
    }

    func send(_ request: HTTPRequest) async throws -> HTTPResponse {
        lock.lock()
        log.append(request)
        lock.unlock()
        let (status, body) = route(request)
        return HTTPResponse(url: request.url, status: status, headers: [:], body: Data(body.utf8))
    }
}

private func query(_ request: HTTPRequest) -> [String: String] {
    var values: [String: String] = [:]
    for item in URLComponents(url: request.url, resolvingAgainstBaseURL: false)?.queryItems ?? [] {
        values[item.name] = item.value ?? ""
    }
    return values
}

private func postForm(_ request: HTTPRequest) -> [String: String] {
    let text = String(decoding: request.body ?? Data(), as: UTF8.self)
    var fields: [String: String] = [:]
    for pair in text.split(separator: "&") {
        let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        let key = parts[0].removingPercentEncoding ?? parts[0]
        let value = parts.count > 1 ? (parts[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? parts[1]) : ""
        fields[key] = value
    }
    return fields
}

private func json(_ object: [String: Any]) -> String {
    String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
}

private func searchJSON(_ rows: [(no: Int, age: Int, owner: String?)], status: Int = 200) -> String {
    let items = rows.map { row -> [String: Any] in
        var item: [String: Any] = ["novel_no": row.no, "novel_age": row.age, "novel_name": "n\(row.no)"]
        if let owner = row.owner { item["writer_mem_no"] = owner }
        return item
    }
    return json(["status": status, "list": items])
}

private func authorJSON(owner: String, ids: [Int], total: Int, age: Int = 15) -> String {
    let rows = ids.map { ["novel_no": $0, "novel_age": age, "writer_mem_no": owner] as [String: Any] }
    return json(["status": 200, "result": ["novel": rows, "allCount": total] as [String: Any]])
}

private func rankingPage(_ ids: [Int]) -> String {
    // Every novel shows up twice, like a cover link and a title link.
    "<ul>" + ids.map { "<li><a href=\"/novel/\($0)\"><img></a><a href=\"/novel/\($0)\">t</a></li>" }.joined() + "</ul>"
}

private func introCard(_ id: Int, age: Int) -> String {
    "<div class=\"intro-item\" data-novel_no=\"\(id)\" data-novel_age=\"\(age)\"><div class=\"intro-novel-info\"><p>w\(id)</p></div><span class=\"novel-title\">t\(id)</span></div>"
}

private func introDetail(_ id: Int, owner: Int, canonical: String? = nil) -> String {
    """
    <html><head><meta property="og:url" content="\(canonical ?? "https://\(host)/novel/\(id)")"></head><body>
    <div class="epnew-novel-info"><div class="in-writer"><a class="writer-name" href="/user/\(owner)">w</a></div></div>
    </body></html>
    """
}

final class NpiaSearchTermsTests: XCTestCase {
    func testTermCountsMatchTheDesktopLists() {
        XCTAssertEqual(NpiaSearchTerms.allTerms.count, 165)
        XCTAssertEqual(NpiaSearchTerms.defaultQueryCount, 165)
        XCTAssertEqual(NpiaSearchTerms.searchTags.count, 101)
        XCTAssertEqual(NpiaSearchTerms.sweepChars.count, 64)
        XCTAssertEqual(Set(NpiaSearchTerms.allTerms).count, NpiaSearchTerms.allTerms.count)
        XCTAssertEqual(NpiaSearchTerms.allTerms.first, "판타지")
        XCTAssertEqual(NpiaSearchTerms.allTerms.last, "9")
        XCTAssertEqual(NpiaSettings.maxSearchQueries, NpiaSearchTerms.defaultQueryCount)
        XCTAssertEqual(NpiaSearchTerms.retryableStatusCodes, [408, 429, 500, 502, 503, 504])
        XCTAssertEqual(NpiaSearchTerms.clampQueryCount(0), 1)
        XCTAssertEqual(NpiaSearchTerms.clampQueryCount(1000), 165)
    }

    func testCommonTagsKeepSearchValueAndShowCodenameLabel() {
        XCTAssertEqual(NpiaSearchTerms.commonTags.first?.title, "TS (Genderbend)")
        let provider = NpiaSearchTerms.commonTags.first { $0.english == "Npia" }
        XCTAssertEqual(provider?.title, "npia (Npia)")
        XCTAssertNotEqual(provider?.value, "npia", "the search value is the site's own tag text")
        XCTAssertEqual(Set(NpiaSearchTerms.commonTags.map(\.value)).count, NpiaSearchTerms.commonTags.count)
    }
}

final class NpiaSearchOutputTests: XCTestCase {
    func testTagLabelsUseEnglishNamesAndGroupSeparators() {
        XCTAssertEqual(NpiaSearchOutput.tagLabel(groups: nil), "all_novels")
        XCTAssertEqual(NpiaSearchOutput.tagLabel(groups: [["TS"], ["약피폐", "악피폐"]]), "TS+Moderate_Suffering_OR_Dark_Suffering")
        XCTAssertEqual(NpiaSearchOutput.tagLabel(groups: [["custom tag"], ["없는태그"]]), "custom tag+없는태그")
        XCTAssertEqual(NpiaSearchOutput.tagFileName(groups: nil), "tag_all_novels.txt")
        XCTAssertEqual(NpiaSearchOutput.tagFileName(groups: [["TS"], ["현대"]]), "tag_TS+Hyundai-Modern.txt")
    }

    func testIDListAndTop100Formats() {
        XCTAssertEqual(NpiaSearchOutput.idListText(["3", "1", "2"]), "3\n1\n2\n")
        XCTAssertEqual(NpiaSearchOutput.idListText([]), "")
        let rankings = [
            NpiaRanking(label: "daily all", ids: ["1", "2"]),
            NpiaRanking(label: "weekly adult", ids: ["2", "3"]),
            NpiaRanking(label: "monthly teen", ids: []),
        ]
        XCTAssertEqual(NpiaSearchOutput.top100Text(rankings), """
        # Daily All Top 100
        1
        2

        # Weekly Adult Top 100
        2
        3

        # Monthly Teen Top 100


        """)
        XCTAssertEqual(NpiaSearchOutput.uniqueIDs(rankings), ["1", "2", "3"])
        XCTAssertEqual(NpiaSearchOutput.top100FileName, "top100_rankings.txt")
    }

    func testSummariesMatchTheDesktopLabels() {
        XCTAssertEqual(NpiaSearchOutput.idSummary(count: 12, stopped: false), "Found 12 novel(s).")
        XCTAssertEqual(NpiaSearchOutput.idSummary(count: 0, stopped: true), "Found 0 novel(s). (stopped)")
        let rankings = [
            NpiaRanking(label: "daily all", ids: Array(repeating: "1", count: 100)),
            NpiaRanking(label: "weekly all", ids: ["1", "2"]),
            NpiaRanking(label: "monthly all", ids: ["3"]),
            NpiaRanking(label: "daily adult", ids: ["9"]),
        ]
        XCTAssertEqual(NpiaSearchOutput.top100Summary(rankings, stopped: false), """
        Top 100: 4 unique novels
          all: daily: 100, weekly: 2, monthly: 1
          adult: daily: 1
        """)
        XCTAssertTrue(NpiaSearchOutput.top100Summary(rankings, stopped: true).hasSuffix("\n(stopped)"))
    }

    func testPlanTurnsSelectionsIntoGroups() {
        XCTAssertEqual(NpiaSearchPlan.customTags(" a, ,b ,,c"), ["a", "b", "c"])
        // Grid order first, then the custom tags.
        XCTAssertEqual(NpiaSearchPlan.selection(selected: ["약피폐", "TS", "unknown"], custom: "x, y"), ["TS", "약피폐", "x", "y"])
        // No groups: every tag is its own group, so they are all AND'd.
        XCTAssertEqual(
            NpiaSearchPlan.groupsToSearch(selected: ["약피폐", "TS"], custom: "x", groups: []),
            [["TS"], ["약피폐"], ["x"]]
        )
        XCTAssertEqual(
            NpiaSearchPlan.groupsToSearch(selected: ["TS"], custom: "x", groups: [["a", "b"]]),
            [["a", "b"]]
        )
        XCTAssertTrue(NpiaSearchPlan.groupsToSearch(selected: [], custom: " , ", groups: []).isEmpty)
    }

    func testNovelURLUsesTheNpiaHost() {
        XCTAssertEqual(NpiaCatalogSearch.novelURL(id: "419368").absoluteString, "https://\(host)/novel/419368")
    }
}

final class NpiaTagSearchTests: XCTestCase {
    private func searchContext(_ http: ScriptedHTTP, _ logger: CollectingLogger) -> NpiaSearchContext {
        NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
    }

    /// TS has 1-4, "약피폐" 3-5, "악피폐" 4 and 9.
    private func tagHTTP() -> ScriptedHTTP {
        ScriptedHTTP { request in
            guard request.url.path == "/proc/novel" else { return (404, "") }
            switch query(request)["search_val"] {
            case "TS": return (200, searchJSON([(1, 15, nil), (2, 15, nil), (3, 19, nil), (4, 19, nil)]))
            case "약피폐": return (200, searchJSON([(3, 19, nil), (4, 19, nil), (5, 15, nil)]))
            case "악피폐": return (200, searchJSON([(4, 19, nil), (9, 15, nil)]))
            default: return (200, searchJSON([]))
            }
        }
    }

    func testGroupsAreOrWithinAndAndAcrossGroups() async throws {
        for threads in [1, 3] {
            let http = tagHTTP()
            let logger = CollectingLogger()
            let result = try await NpiaCatalogSearch.fetchNovels(
                query: .groups([["TS"], ["약피폐", "악피폐"]]), delay: 0, ageFilter: "15", threads: threads,
                context: searchContext(http, logger)
            )
            XCTAssertEqual(result.ids, ["3", "4"], "threads: \(threads)")
            XCTAssertFalse(result.stopped)
            XCTAssertEqual(http.requests.count, 3)
            XCTAssertTrue(logger.lines.contains("  Group 1 result: 4 novel(s)"))
            XCTAssertTrue(logger.lines.contains("  Group 2 result: 4 novel(s)"))
            XCTAssertTrue(logger.lines.contains("  After AND with group 2: 2 novel(s)"))
            XCTAssertEqual(logger.lines.last, "Tag search complete: 2 novel(s) found.")
        }
    }

    func testRequestShapeMatchesTheDesktop() async throws {
        let http = tagHTTP()
        let logger = CollectingLogger()
        _ = try await NpiaCatalogSearch.fetchNovels(query: .and(["TS"]), delay: 0, ageFilter: "19", threads: 1, context: searchContext(http, logger))
        let request = try XCTUnwrap(http.requests.first)
        let values = query(request)
        XCTAssertEqual(request.method, "GET")
        XCTAssertEqual(request.url.host, host)
        XCTAssertEqual(values["cmd"], "novel_search")
        XCTAssertEqual(values["search_type"], "novel_genre")
        XCTAssertEqual(values["search_val"], "TS")
        XCTAssertEqual(values["rows"], "50000")
        XCTAssertEqual(values["page"], "1")
        XCTAssertEqual(values["novel_age"], "19", "a tag search sends the age filter as given")
        XCTAssertEqual(values["sort_col"], "last_viewdate")
        XCTAssertEqual(values["list_display"], "grid")
        XCTAssertEqual(request.headers["X-Requested-With"], "XMLHttpRequest")
        XCTAssertEqual(request.headers["Referer"], "https://\(host)/search")
        XCTAssertEqual(logger.lines.first, "Searching for novels with tags: TS (mode: AND, 1 thread(s))")
        XCTAssertTrue(logger.lines.contains("  Age filter: Adult only"))
    }

    func testGroupLogShowsTheQuery() async throws {
        let logger = CollectingLogger()
        _ = try await NpiaCatalogSearch.fetchNovels(
            query: .groups([["TS"], ["약피폐", "악피폐"]]), delay: 0, ageFilter: "", threads: 2, context: searchContext(tagHTTP(), logger)
        )
        XCTAssertEqual(Array(logger.lines.prefix(4)), [
            "Searching with 2 group(s), 2 thread(s):", "  Group 1: TS", "    AND", "  Group 2: 약피폐 OR 악피폐",
        ])
    }

    func testAndAndOrModes() async throws {
        let logger = CollectingLogger()
        let both = try await NpiaCatalogSearch.fetchNovels(
            query: .and(["TS", "약피폐"]), delay: 0, ageFilter: "", threads: 1, context: searchContext(tagHTTP(), logger)
        )
        XCTAssertEqual(both.ids, ["3", "4"])
        XCTAssertTrue(logger.lines.contains("  AND intersection: 2 novel(s) match all 2 tag(s)"))

        let either = try await NpiaCatalogSearch.fetchNovels(
            query: .or(["TS", "악피폐"]), delay: 0, ageFilter: "", threads: 1, context: searchContext(tagHTTP(), CollectingLogger())
        )
        XCTAssertEqual(either.ids, ["1", "2", "3", "4", "9"])
    }

    func testApiErrorGivesAnEmptyTagAndBadJSONThrows() async throws {
        let failing = ScriptedHTTP { _ in (200, json(["status": 500, "errmsg": "boom"])) }
        let logger = CollectingLogger()
        let empty = try await NpiaCatalogSearch.fetchNovels(query: .or(["TS"]), delay: 0, ageFilter: "", threads: 1, context: searchContext(failing, logger))
        XCTAssertTrue(empty.ids.isEmpty)
        XCTAssertTrue(logger.lines.contains("  [TS] API error: boom"))

        let html = ScriptedHTTP { _ in (503, "<html>busy</html>") }
        do {
            _ = try await NpiaCatalogSearch.fetchNovels(query: .or(["TS"]), delay: 0, ageFilter: "", threads: 1, context: searchContext(html, CollectingLogger()))
            XCTFail("expected an error")
        } catch {
            XCTAssertTrue(NpiaSearchError.message(error).contains("HTTP 503"))
        }
    }

    func testStoppedRunReturnsWhatItHasAndSaysSo() async throws {
        let http = tagHTTP()
        let context = searchContext(http, CollectingLogger())
        let task = Task {
            // Wait for the cancel below, so the run starts already stopped.
            while !Task.isCancelled { await Task.yield() }
            return try await NpiaCatalogSearch.fetchNovels(query: .groups([["TS"]]), delay: 0, ageFilter: "", threads: 1, context: context)
        }
        task.cancel()
        let result = try await task.value
        XCTAssertTrue(result.stopped)
        XCTAssertTrue(http.requests.isEmpty, "a stopped run makes no further requests")
    }
}

final class NpiaTop100Tests: XCTestCase {
    func testFirstHundredUniqueIDsKeepTheirOrder() async {
        let http = ScriptedHTTP { request in
            request.url.path.contains("/month/") ? (200, rankingPage(Array(1000...1010))) : (200, rankingPage(Array(1...150)))
        }
        let logger = CollectingLogger()
        let result = await NpiaCatalogSearch.fetchTop100(
            ageFilter: "", context: NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        )
        XCTAssertEqual(result.rankings.map(\.label), [
            "daily all", "weekly all", "monthly all", "daily adult", "weekly adult", "monthly adult",
            "daily teen", "weekly teen", "monthly teen",
        ])
        XCTAssertEqual(result.rankings[0].ids.count, 100)
        XCTAssertEqual(result.rankings[0].ids.prefix(3).map { $0 }, ["1", "2", "3"])
        XCTAssertEqual(result.rankings[0].ids.last, "100")
        XCTAssertEqual(result.rankings[2].ids.count, 11)
        XCTAssertFalse(result.stopped)
        XCTAssertEqual(http.requests.first?.url.path, "/top100/all/today/view/all/plus")
        XCTAssertEqual(http.requests[2].url.path, "/top100/all/month/view/all/plus")
        XCTAssertTrue(logger.lines.contains("  Fetching daily all..."))
        XCTAssertTrue(logger.lines.contains("  daily all: 100 novels"))
    }

    func testAgeFilterPicksTheAudience() async {
        let http = ScriptedHTTP { _ in (200, "") }
        let adult = await NpiaCatalogSearch.fetchTop100(ageFilter: "19", context: NpiaSearchContext(http: http, sleep: { _ in }))
        XCTAssertEqual(adult.rankings.map(\.label), ["daily adult", "weekly adult", "monthly adult"])
        XCTAssertTrue(http.requests.allSatisfy { $0.url.path.hasSuffix("/view/adult/plus") })
        let teen = await NpiaCatalogSearch.fetchTop100(ageFilter: "15", context: NpiaSearchContext(http: http, sleep: { _ in }))
        XCTAssertEqual(teen.rankings.map(\.label), ["daily teen", "weekly teen", "monthly teen"])
    }

    func testHTTPErrorGivesAnEmptyRankingAndALogLine() async {
        let http = ScriptedHTTP { request in request.url.path.contains("/weekly/") ? (503, "") : (200, rankingPage([7])) }
        let logger = CollectingLogger()
        let result = await NpiaCatalogSearch.fetchTop100(
            ageFilter: "19", context: NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        )
        XCTAssertEqual(result.rankings.map(\.ids), [["7"], [], ["7"]])
        XCTAssertTrue(logger.lines.contains("  weekly adult: HTTP 503"))
    }
}

final class NpiaAuthorCatalogTests: XCTestCase {
    private func context(_ http: ScriptedHTTP, _ logger: CollectingLogger = CollectingLogger()) -> NpiaSearchContext {
        NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
    }

    private func fetch(_ body: String, status: Int = 200, author: String = "7", page: Int = 1, signatures: Set<[String]> = [],
                       retries: Int = 1, logger: CollectingLogger = CollectingLogger()) async throws -> NpiaAuthorCatalog.Page? {
        let http = ScriptedHTTP { _ in (status, body) }
        return try await NpiaAuthorCatalog.fetchPage(
            author: author, page: page, retries: retries, signatures: signatures, context: context(http, logger)
        )
    }

    private func failure(_ body: String, status: Int = 200, signatures: Set<[String]> = [], page: Int = 1) async -> String {
        do {
            _ = try await fetch(body, status: status, page: page, signatures: signatures)
            return "no error"
        } catch {
            return NpiaSearchError.message(error)
        }
    }

    func testValidPagesReportWhetherMoreFollow() async throws {
        let first = try await fetch(authorJSON(owner: "7", ids: Array(1...100), total: 150))
        XCTAssertEqual(first?.rows.count, 100)
        XCTAssertEqual(first?.more, true)
        let second = try await fetch(authorJSON(owner: "7", ids: Array(101...150), total: 150), page: 2)
        XCTAssertEqual(second?.rows.count, 50)
        XCTAssertEqual(second?.more, false)
        XCTAssertEqual(second?.rows.first?.authorID, "7")
    }

    func testIdentityMismatchIsRejected() async {
        let message = await failure(authorJSON(owner: "99", ids: [1, 2], total: 2))
        XCTAssertTrue(message.hasPrefix("author catalog identity mismatch; HTTP 200;"), message)
    }

    func testRepeatedPageIsRejected() async {
        let message = await failure(authorJSON(owner: "7", ids: [1, 2], total: 4), signatures: [["1", "2"]], page: 2)
        XCTAssertTrue(message.hasPrefix("author catalog repeated a page;"), message)
    }

    func testIncompletePagesAreRejected() async {
        let short = await failure(authorJSON(owner: "7", ids: Array(1...50), total: 150))
        XCTAssertTrue(short.hasPrefix("author catalog returned an incomplete page;"), short)
        let empty = await failure(authorJSON(owner: "7", ids: [], total: 5))
        XCTAssertTrue(empty.hasPrefix("author catalog returned an incomplete page;"), empty)
    }

    func testMalformedResponsesAreRejected() async {
        let apiError = await failure(json(["status": 500]))
        XCTAssertTrue(apiError.hasPrefix("author catalog API error;"), apiError)
        let missing = await failure(json(["status": 200, "result": ["novel": "x"]]))
        XCTAssertTrue(missing.hasPrefix("invalid author catalog response;"), missing)
        let record = await failure(json(["status": 200, "result": ["novel": [["novel_no": "abc"]], "allCount": 1] as [String: Any]]))
        XCTAssertTrue(record.hasPrefix("invalid author catalog record;"), record)
        let notJSON = await failure("<html>nope</html>")
        XCTAssertTrue(notJSON.hasPrefix("author catalog returned invalid JSON;"), notJSON)
    }

    func testHTTPFailuresRetryAndThenGiveUp() async {
        let logger = CollectingLogger()
        do {
            _ = try await fetch("oops", status: 500, retries: 2, logger: logger)
            XCTFail("expected an error")
        } catch {
            XCTAssertTrue(NpiaSearchError.message(error).hasPrefix("author catalog HTTP 500; HTTP 500;"))
        }
        XCTAssertEqual(logger.lines.filter { $0.hasPrefix("Author 7 page 1 attempt") }.count, 2)
        XCTAssertTrue(logger.lines[0].hasPrefix("Author 7 page 1 attempt 1/2 failed: author catalog HTTP 500;"))
    }

    func testInvalidArgumentsThrow() async {
        do {
            _ = try await fetch(authorJSON(owner: "7", ids: [1], total: 1), author: "abc")
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(NpiaSearchError.message(error), "invalid author ID")
        }
    }

    func testResponseDetailsHideCredentials() {
        let response = HTTPResponse(url: URL(string: "https://x.test")!, status: 403, headers: ["content-type": "text/html"],
                                    body: Data("{\"LOGINKEY\": \"abc123\", \"ok\": 1}  password=hunter2".utf8))
        let details = NpiaSearchText.responseDetails(response)
        XCTAssertTrue(details.hasPrefix("HTTP 403; content-type='text/html'; body-chars="))
        XCTAssertFalse(details.contains("abc123"))
        XCTAssertFalse(details.contains("hunter2"))
        XCTAssertTrue(details.contains("[redacted]"))
    }

    func testParallelWorkersWalkEveryAuthorPageInOrder() async throws {
        let http = ScriptedHTTP { request in
            let form = postForm(request)
            let owner = form["mem_no"] ?? ""
            switch (owner, form["paging[curPage]"] ?? "") {
            case ("7", "1"): return (200, authorJSON(owner: "7", ids: Array(1...100), total: 130))
            case ("7", "2"): return (200, authorJSON(owner: "7", ids: Array(101...130), total: 130))
            case ("8", _): return (200, authorJSON(owner: "8", ids: [500], total: 1))
            case ("9", _): return (200, authorJSON(owner: "1234", ids: [600], total: 1))
            default: return (404, "")
            }
        }
        let logger = CollectingLogger()
        var events: [NpiaAuthorCatalog.Event] = []
        try await NpiaAuthorCatalog.parallelPages(
            authors: [(owner: "7", page: 1), (owner: "8", page: 1), (owner: "9", page: 1)], workers: 2, delay: 0, delayMax: 0,
            retries: 1, pageSize: 100, context: context(http, logger)
        ) { events.append($0) }

        XCTAssertEqual(events.count, 4)
        let sevens = events.filter { $0.owner == "7" }
        XCTAssertEqual(sevens.map(\.page), [1, 2])
        XCTAssertEqual(sevens.map(\.more), [true, false])
        XCTAssertEqual(events.first { $0.owner == "8" }?.rows?.map(\.novelNo), ["500"])
        let mismatched = try XCTUnwrap(events.first { $0.owner == "9" })
        XCTAssertNil(mismatched.rows)
        XCTAssertTrue(mismatched.error?.hasPrefix("author catalog identity mismatch") == true)
        XCTAssertEqual(http.requests.count, 4)
        XCTAssertTrue(http.requests.allSatisfy { postForm($0)["mode"] == "get_member_writer_novel" && postForm($0)["paging[rowCount]"] == "100" })
    }

    func testRequestBudgetStopsTheSweep() async throws {
        let http = ScriptedHTTP { request in (200, authorJSON(owner: postForm(request)["mem_no"] ?? "", ids: [1], total: 1)) }
        var events: [NpiaAuthorCatalog.Event] = []
        try await NpiaAuthorCatalog.parallelPages(
            authors: [(owner: "1", page: 1), (owner: "2", page: 1), (owner: "3", page: 1)], workers: 1, delay: 0, delayMax: 0,
            retries: 1, pageSize: 100, maxRequests: 2, context: context(http)
        ) { events.append($0) }
        XCTAssertEqual(http.requests.count, 2)
        XCTAssertEqual(events.filter { $0.rows != nil }.count, 2)
    }
}

final class NpiaIntroDiscoveryTests: XCTestCase {
    func testDiscoversAdultNovelsFromCardsAndCampaignPages() async {
        let http = ScriptedHTTP { request in
            switch request.url.path {
            case "/intro", "/intro/megabox": return (200, "<main>\(introCard(101, age: 19))\(introCard(102, age: 19))\(introCard(103, age: 15))</main>")
            case "/novel/101": return (200, introDetail(101, owner: 42))
            case "/novel/102": return (200, introDetail(102, owner: 43))
            case "/proc/intro":
                let form = postForm(request)
                if form["page"] == "2" {
                    return (200, json(["result": "success", "data": ["list": [
                        ["novel_no": 301, "novel_age": 19, "mem_no": "77"], ["novel_no": 302, "novel_age": 15, "mem_no": "78"],
                    ]]]))
                }
                return (200, json(["result": "success", "data": ["list": []]]))
            default: return (404, "")
            }
        }
        let logger = CollectingLogger()
        let rows = await NpiaIntroDiscovery.discover(
            maxRequests: 6, delay: 0, context: NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        )
        XCTAssertEqual(rows.map(\.novelNo), ["101", "102", "301"])
        XCTAssertEqual(rows.map(\.authorID), ["42", "43", "77"])
        XCTAssertEqual(logger.lines.last, "Public intro: 3 R19 novels discovered in 6 requests")
        XCTAssertEqual(http.requests.map(\.url.path), ["/intro", "/novel/101", "/novel/102", "/intro/megabox", "/proc/intro", "/proc/intro"])
        let campaign = postForm(http.requests[4])
        XCTAssertEqual(campaign["cmd"], "ajax_intro_list")
        XCTAssertEqual(campaign["cate"], "megabox")
        XCTAssertEqual(campaign["novel_no_arr[]"] != nil, true)
        let body = String(decoding: http.requests[4].body ?? Data(), as: UTF8.self)
        XCTAssertEqual(body.components(separatedBy: "novel_no_arr%5B%5D=").count - 1, 3, "every seen id is excluded")
    }

    func testIdentityMismatchKeepsEarlierDiscoveries() async {
        let http = ScriptedHTTP { request in
            switch request.url.path {
            case "/intro": return (200, "<main>\(introCard(101, age: 19))</main>")
            case "/novel/101": return (200, introDetail(101, owner: 42, canonical: "https://\(host)/novel/999"))
            default: return (404, "")
            }
        }
        let logger = CollectingLogger()
        let rows = await NpiaIntroDiscovery.discover(
            maxRequests: 6, delay: 0, context: NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        )
        XCTAssertEqual(rows.map(\.novelNo), ["101"])
        XCTAssertNil(rows.first?.authorID)
        XCTAssertTrue(logger.lines.contains("Public intro default PARTIAL; discoveries retained: ValueError: intro detail identity mismatch"))
        XCTAssertTrue(logger.lines.contains("Public intro PARTIAL; discoveries retained: ValueError: public intro HTTP 404"))
        XCTAssertEqual(logger.lines.last, "Public intro: 1 R19 novels discovered in 3 requests")
    }

    func testRepeatedCampaignPageEndsTheRunWithAPartialLog() async {
        let http = ScriptedHTTP { request in
            switch request.url.path {
            case "/intro": return (200, "<main>\(introCard(103, age: 15))</main>")
            case "/intro/megabox": return (200, "<main>\(introCard(103, age: 15))</main>")
            case "/proc/intro": return (200, json(["result": "success", "data": ["list": [["novel_no": 103, "novel_age": 15]]]]))
            default: return (404, "")
            }
        }
        let logger = CollectingLogger()
        let rows = await NpiaIntroDiscovery.discover(
            maxRequests: 6, delay: 0, context: NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        )
        XCTAssertTrue(rows.isEmpty)
        XCTAssertTrue(logger.lines.contains("Public intro PARTIAL; discoveries retained: ValueError: public intro repeated an excluded page"))
    }
}

final class NpiaScrapeAllTests: XCTestCase {
    /// Two search terms, each returning a few novels; author 7 owns a novel (50) no search found.
    private func sweepHTTP(extraAuthorRows: Bool = true) -> ScriptedHTTP {
        ScriptedHTTP { request in
            switch request.url.path {
            case "/proc/novel":
                switch query(request)["search_val"] {
                case "판타지": return (200, searchJSON([(1, 15, "7"), (2, 19, "8"), (3, 15, nil)]))
                case "현대": return (200, searchJSON([(2, 19, "8"), (4, 19, nil)]))
                default: return (200, searchJSON([]))
                }
            case "/proc/user":
                let owner = postForm(request)["mem_no"] ?? ""
                return owner == "7"
                    ? (200, authorJSON(owner: "7", ids: extraAuthorRows ? [1, 50] : [1], total: extraAuthorRows ? 2 : 1))
                    : (200, authorJSON(owner: owner, ids: [2], total: 1, age: 19))
            default: return (404, "")
            }
        }
    }

    private func run(_ http: ScriptedHTTP, age: String, threads: Int = 1) async -> (NpiaSearchResult, [String]) {
        let logger = CollectingLogger()
        let context = NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        let result = await NpiaCatalogSearch.fetchAllNovels(delay: 0, ageFilter: age, maxQueries: 2, threads: threads, context: context)
        return (result, logger.lines)
    }

    func testSweepIntroAndAuthorCatalogsAreUnioned() async {
        for threads in [1, 2] {
            let (result, lines) = await run(sweepHTTP(), age: "", threads: threads)
            XCTAssertEqual(Set(result.ids), ["1", "2", "3", "4", "50"], "threads: \(threads)")
            XCTAssertEqual(result.ids.count, 5)
            XCTAssertFalse(result.stopped)
            XCTAssertEqual(lines.first, "Scraping all novel IDs from Npia... (2 queries, \(threads) thread(s))")
            XCTAssertTrue(lines.contains("  Query 1/2: searching '판타지'..."))
            XCTAssertTrue(lines.contains("    3 results, 3 new (total: 3)") || lines.contains("    2 results, 2 new (total: 2)"))
            XCTAssertTrue(lines.contains("Public intro default PARTIAL; discoveries retained: ValueError: public intro HTTP 404"))
            XCTAssertTrue(lines.contains("Checking 2 public author catalogs with 4 workers (1-2s random delay)..."))
            XCTAssertTrue(lines.contains("  Author 7, page 1: 1/2 complete, 5 IDs total") || lines.contains("  Author 7, page 1: 2/2 complete, 5 IDs total"))
            XCTAssertEqual(lines.last, "  5 novel(s) retrieved total")
        }
    }

    func testAgeFilterSelectsAdultOrNonAdultAndShapesTheRequest() async {
        let adultHTTP = sweepHTTP()
        let (adult, adultLines) = await run(adultHTTP, age: "19")
        XCTAssertEqual(Set(adult.ids), ["2", "4"])
        XCTAssertTrue(adultLines.contains("  Age filter: Adult only"))
        let sweep = adultHTTP.requests.filter { $0.url.path == "/proc/novel" }
        XCTAssertEqual(sweep.count, 2)
        XCTAssertEqual(query(sweep[0])["novel_age"], "", "the adult sweep asks for everything and filters locally")
        XCTAssertEqual(query(sweep[0])["search_type"], "all")
        XCTAssertEqual(query(sweep[0])["rows"], "15000")

        let teenHTTP = sweepHTTP(extraAuthorRows: false)
        let (teen, teenLines) = await run(teenHTTP, age: "15")
        XCTAssertEqual(Set(teen.ids), ["1", "3"])
        XCTAssertTrue(teenLines.contains("  Age filter: Non-adult only"))
        XCTAssertEqual(query(teenHTTP.requests.first { $0.url.path == "/proc/novel" }!)["novel_age"], "15")
    }

    func testRetryableStatusIsRetriedWithBackoffLines() async {
        let attempts = Counter()
        let http = ScriptedHTTP { request in
            guard request.url.path == "/proc/novel" else { return (404, "") }
            if attempts.next() < 3 { return (503, "") }
            return (200, searchJSON([(11, 15, nil)]))
        }
        let logger = CollectingLogger()
        let context = NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        let result = await NpiaCatalogSearch.fetchAllNovels(delay: 0, ageFilter: "", maxQueries: 1, threads: 1, context: context)
        XCTAssertEqual(result.ids, ["11"])
        XCTAssertTrue(logger.lines.contains("    Retry 1/4 for '판타지' p1 after HTTP 503; sleeping 1s"))
        XCTAssertTrue(logger.lines.contains("    Retry 2/4 for '판타지' p1 after HTTP 503; sleeping 2s"))
    }

    func testBadSearchResponsesAreLoggedAndSkipped() async {
        let http = ScriptedHTTP { request in
            guard request.url.path == "/proc/novel" else { return (404, "") }
            switch query(request)["search_val"] {
            case "판타지": return (200, "not json")
            default: return (200, json(["status": 500, "errmsg": "limit"]))
            }
        }
        let logger = CollectingLogger()
        let context = NpiaSearchContext(http: http, log: { logger.log(.info, $0) }, sleep: { _ in })
        let result = await NpiaCatalogSearch.fetchAllNovels(delay: 0, ageFilter: "", maxQueries: 2, threads: 1, context: context)
        XCTAssertTrue(result.ids.isEmpty)
        XCTAssertTrue(logger.lines.contains("    Invalid JSON for '판타지' p1 (len=8): not json"))
        XCTAssertTrue(logger.lines.contains("    API error p1: limit"))
    }
}

private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func next() -> Int {
        lock.lock(); defer { lock.unlock() }
        value += 1
        return value
    }
}
