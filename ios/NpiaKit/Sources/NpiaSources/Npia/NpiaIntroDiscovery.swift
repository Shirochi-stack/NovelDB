import Foundation
import NpiaCore
import SwiftSoup

/// Port of `npia_intro_discovery.py`: bounded discovery of adult novels from the public intro
/// pages, which list some novels (and their authors) that keyword searches never return.
enum NpiaIntroDiscovery {
    /// One discovery run with a fresh checkpoint, like `discover(session, {})`. Failures are
    /// logged and the novels found so far are kept.
    static func discover(maxRequests: Int = 6, delay: Double = 2.0, context: NpiaSearchContext) async -> [NpiaNovelRow] {
        let run = Run(maxRequests: maxRequests, delay: delay, context: context)
        var failures = 0

        // The default list has different seeds and no working pagination. Fetch it before
        // campaign pagination can consume the shared request budget.
        do {
            _ = try await run.readCards("/intro")
        } catch {
            if !Task.isCancelled {
                failures += 1
                context.log("Public intro default PARTIAL; discoveries retained: \(NpiaSearchError.summary(error))")
            }
        }

        do {
            let initialIDs = try await run.readCards("/intro/megabox")
            if !initialIDs.isEmpty {
                try await run.paginate(after: initialIDs)
            }
        } catch {
            if !Task.isCancelled {
                failures += 1
                context.log("Public intro PARTIAL; discoveries retained: \(NpiaSearchError.summary(error))")
            }
        }
        context.log("Public intro: \(run.found.count) R19 novels discovered in \(run.requestsMade) requests")
        return run.found
    }

    /// The mutable state of one run; only ever used by the task that created it.
    private final class Run {
        let maxRequests: Int
        let delay: Double
        let context: NpiaSearchContext
        var requestsMade = 0
        /// Adult novels discovered, in discovery order.
        private(set) var found: [NpiaNovelRow] = []
        private var position: [String: Int] = [:]
        private var resolved: [String: String] = [:]

        init(maxRequests: Int, delay: Double, context: NpiaSearchContext) {
            self.maxRequests = maxRequests
            self.delay = delay
            self.context = context
        }

        private func store(_ row: NpiaNovelRow) {
            if let index = position[row.novelNo] {
                found[index] = row
            } else {
                position[row.novelNo] = found.count
                found.append(row)
            }
        }

        /// Waits the delay, then makes one request; nil when stopped or out of budget.
        private func request(_ path: String, form: [(String, String)]? = nil) async throws -> HTTPResponse? {
            await context.pause(max(0, delay))
            if Task.isCancelled || requestsMade >= maxRequests { return nil }
            requestsMade += 1
            let url = URL(string: "https://\(NpiaParser.host)\(path)")!
            let request: HTTPRequest
            if let form {
                request = HTTPRequest(url: url, method: "POST", headers: NpiaAuth.headers(form: true), body: NpiaAuth.formBody(form))
            } else {
                request = HTTPRequest(url: url, headers: NpiaAuth.headers())
            }
            let response = try await context.http.send(request)
            // The desktop refuses redirects; URLSession follows them, so a changed path means one happened.
            guard response.status == 200, response.url.path == url.path else {
                throw NpiaSearchError("public intro HTTP \(response.status)")
            }
            return response
        }

        /// Reads the novel cards of an intro page, keeping the adult ones and looking up their
        /// authors. Returns every valid card id of the page.
        func readCards(_ path: String) async throws -> [String] {
            guard let response = try await request(path) else { return [] }
            let soup = try SwiftSoup.parse(response.text)
            let cards = try soup.select(".intro-item[data-novel_no][data-novel_age]").array()
            if cards.isEmpty { throw NpiaSearchError("public intro cards unavailable") }

            var initialIDs: [String] = []
            for card in cards {
                let nid = try card.attr("data-novel_no")
                guard NpiaNovelRow.isDigits(nid) else { continue }
                initialIDs.append(nid)
                guard try card.attr("data-novel_age") == "19" else { continue }

                var row = NpiaNovelRow(novelNo: nid, novelAge: 19, authorID: nil)
                store(row)
                var owner = resolved[nid] ?? ""
                if owner.isEmpty {
                    guard let detailResponse = try await request("/novel/\(nid)") else { continue }
                    let detail = try SwiftSoup.parse(detailResponse.text)
                    if let canonical = try detail.select(#"meta[property="og:url"]"#).first() {
                        let content = try canonical.attr("content")
                        if Self.path(of: content) != "/novel/\(nid)" {
                            throw NpiaSearchError("intro detail identity mismatch")
                        }
                    }
                    if let link = try detail.select(".epnew-novel-info .in-writer .writer-name[href]").first() {
                        let href = try link.attr("href")
                        if let match = NpiaRx.first(#"\A/user/(\d+)/?\z"#, in: href) {
                            owner = match[1]
                            resolved[nid] = owner
                        }
                    }
                }
                if NpiaNovelRow.isPositiveInteger(owner) { row.authorID = owner }
                store(row)
            }
            return initialIDs
        }

        /// Pages through the campaign list, excluding the novels already seen.
        func paginate(after initialIDs: [String]) async throws {
            var excluded = NpiaSearchTerms.unique(initialIDs)
            var excludedSet = Set(excluded)
            var page = 2
            while requestsMade < maxRequests, !Task.isCancelled {
                let form: [(String, String)] = [("cmd", "ajax_intro_list"), ("page", String(page)), ("cate", "megabox"), ("csrf", "")]
                    + excluded.map { ("novel_no_arr[]", $0) }
                guard let response = try await request("/proc/intro", form: form) else { break }
                guard let payload = (try? JSONSerialization.jsonObject(with: response.body)) as? [String: Any] else {
                    throw NpiaSearchError("public intro returned invalid JSON", kind: "JSONDecodeError")
                }
                guard NpiaScalar(any: payload["result"]) == .string("success") else {
                    throw NpiaSearchError("public intro API error")
                }
                let data = payload["data"] as? [String: Any] ?? [:]
                guard let rows = data["list"] as? [Any] else {
                    throw NpiaSearchError("invalid public intro response")
                }
                if rows.isEmpty { break }

                var newIDs: [String] = []
                for item in rows {
                    guard let record = item as? [String: Any], NpiaNovelRow.isDigits(NpiaScalar(any: record["novel_no"]).text) else {
                        throw NpiaSearchError("invalid public intro record")
                    }
                    let nid = NpiaScalar(any: record["novel_no"]).text
                    if !excludedSet.contains(nid) { newIDs.append(nid) }
                    if NpiaScalar(any: record["novel_age"]).text == "19" {
                        store(NpiaNovelRow(json: record))
                    }
                }
                if newIDs.isEmpty { throw NpiaSearchError("public intro repeated an excluded page") }
                for nid in newIDs where excludedSet.insert(nid).inserted { excluded.append(nid) }
                page += 1
            }
        }

        /// `urlsplit(url).path.rstrip("/")`.
        static func path(of address: String) -> String {
            var path = URLComponents(string: address)?.path ?? ""
            while path.hasSuffix("/") { path.removeLast() }
            return path
        }
    }
}
