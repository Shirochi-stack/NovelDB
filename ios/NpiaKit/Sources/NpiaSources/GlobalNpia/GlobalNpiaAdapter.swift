import Foundation
import NpiaCore

/// Login token state shared by the calls of one adapter instance.
final class GlobalNpiaAuth: @unchecked Sendable {
    private let lock = NSLock()
    private var token: String = ""
    private var refreshAttempted = false

    var loginAt: String {
        lock.lock(); defer { lock.unlock() }
        return token
    }

    var attempted: Bool {
        lock.lock(); defer { lock.unlock() }
        return refreshAttempted
    }

    func store(_ value: String) {
        lock.lock(); defer { lock.unlock() }
        token = value
    }

    func markAttempted() {
        lock.lock(); defer { lock.unlock() }
        refreshAttempted = true
    }
}

/// global_npia: HTTP JSON API (book, episode list, ticket, content) with the web-view login cookies.
///
/// Ad-gated episodes (the desktop earns a free ticket with a browser "ad worker") are not supported on iOS yet
/// and come back as `.refused`.
public struct GlobalNpiaAdapter: SourceAdapter {
    public static let id: SourceID = .globalNpia
    public static let tier: SourceTier = .lightBrowser
    public static let sites: Set<String> = [GlobalNpiaParsing.host]

    static let adRefusal: String = "needs an ad ticket; not supported yet"
    static let loginRefusal: String = "login required or not available to this account"

    private let auth = GlobalNpiaAuth()

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        GlobalNpiaParsing.matches(url)
    }

    // MARK: Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        let novelID = GlobalNpiaParsing.novelID(from: url)
        if novelID.isEmpty { throw SourceError.unsupportedURL(url) }
        Self.ensureBaseCookies()
        if Self.savedLogin() {
            if await refreshLogin(context: context, expected: nil) {
                context.logger.log(.info, "[global_npia] using the saved authenticated session")
            } else {
                context.logger.log(.info, "[global_npia] saved login could not be used; continuing anonymously")
            }
        } else {
            context.logger.log(.info, "[global_npia] no saved login; using anonymous access")
        }

        let novelResponse = try await requestJSON(
            GlobalNpiaParsing.api + "/v1/novel", query: [("novel_no", novelID)],
            loginAt: auth.loginAt, maxRetries: 3, context: context
        )
        guard novelResponse.status == 200, let novelPayload = GlobalNpiaParsing.dict(novelResponse.payload),
              let result = GlobalNpiaParsing.dict(novelPayload["result"]),
              GlobalNpiaParsing.dict(result["novel"]) != nil else {
            let message = GlobalNpiaParsing.payloadMessage(novelResponse.payload)
            throw SourceError.parse("metadata request failed (HTTP \(novelResponse.status))" + (message.isEmpty ? "" : ": " + message))
        }
        let novel = GlobalNpiaParsing.dict(result["novel"]) ?? [:]
        let info = GlobalNpiaParsing.dict(result["info"]) ?? [:]
        var countValue = GlobalNpiaParsing.safeInt(info["epi_cnt"], fallback: 0)
        if countValue == 0 { countValue = GlobalNpiaParsing.safeInt(novel["count_epi"], fallback: 0) }
        let rows = max(1, countValue == 0 ? 1000 : countValue)

        let listResponse = try await requestJSON(
            GlobalNpiaParsing.api + "/v1/novel/episode/list",
            query: [("novel_no", novelID), ("rows", String(rows)), ("sort", "ASC")],
            loginAt: auth.loginAt, maxRetries: 3, context: context
        )
        guard listResponse.status == 200,
              let listPayload = GlobalNpiaParsing.dict(listResponse.payload),
              let listResult = GlobalNpiaParsing.dict(listPayload["result"]),
              let episodes = listResult["list"] as? [Any] else {
            let message = GlobalNpiaParsing.payloadMessage(listResponse.payload)
            throw SourceError.parse("episode list failed (HTTP \(listResponse.status))" + (message.isEmpty ? "" : ": " + message))
        }
        let book = try GlobalNpiaParsing.makeBook(novelID: novelID, novelPayload: novelPayload, episodeList: episodes)
        context.logger.log(.info, "[global_npia] \(book.title): \(book.chapters.count) chapters")
        return book
    }

    // MARK: Chapter

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        var episodeNo = ""
        if let url = ref.url { episodeNo = GlobalNpiaParsing.episodeID(from: url) }
        if episodeNo.isEmpty { episodeNo = ref.id }
        guard !episodeNo.isEmpty, Int(episodeNo) != nil else {
            return .failed(ref, message: "invalid chapter URL")
        }
        let chapterURL: URL = ref.url ?? URL(string: "\(GlobalNpiaParsing.base)/viewer/\(episodeNo)")!
        let loggedIn = Self.savedLogin()
        let deniedReason = loggedIn ? Self.loginRefusal : "login required"

        if auth.loginAt.isEmpty, let cached = context.secrets.string(for: SecretKey.source(.globalNpia, "token")), !cached.isEmpty {
            auth.store(cached)
        }
        if auth.loginAt.isEmpty && loggedIn && !auth.attempted {
            _ = await refreshLogin(context: context, expected: nil)
        }

        var authRefreshed = false
        for _ in 0..<3 {
            let loginAt = auth.loginAt
            let ticketResponse = try await requestJSON(
                GlobalNpiaParsing.api + "/v1/novel/episode", query: [("episode_no", episodeNo)],
                loginAt: loginAt, maxRetries: 4, context: context
            )
            let status = ticketResponse.status
            let ticket = ticketResponse.payload
            if GlobalNpiaParsing.authExpired(status: status, payload: ticket) && !authRefreshed {
                authRefreshed = true
                if await refreshLogin(context: context, expected: loginAt) { continue }
            }
            if GlobalNpiaParsing.adRequired(status: status, payload: ticket) {
                return .refused(ref, reason: Self.adRefusal)
            }
            if status >= 400 || status == 0 {
                if GlobalNpiaParsing.accessDenied(status: status, payload: ticket) {
                    return .refused(ref, reason: deniedReason)
                }
                let message = GlobalNpiaParsing.payloadMessage(ticket)
                return .failed(ref, message: "ticket failed (HTTP \(status))" + (message.isEmpty ? "" : ": " + message))
            }
            let extracted = GlobalNpiaParsing.extractTicketToken(ticket)
            if extracted.token.isEmpty && extracted.directURL.isEmpty {
                if GlobalNpiaParsing.accessDenied(status: status, payload: ticket) {
                    return .refused(ref, reason: deniedReason)
                }
                return .failed(ref, message: "ticket contained no content token")
            }
            let contentURL: String = extracted.directURL.isEmpty
                ? GlobalNpiaParsing.api + "/v1/novel/episode/content" : extracted.directURL
            let query: [(String, String)] = extracted.directURL.isEmpty ? [("_t", extracted.token)] : []
            let content = try await requestJSON(contentURL, query: query, loginAt: "", maxRetries: 3, context: context)
            if content.status >= 400 || content.status == 0 {
                if GlobalNpiaParsing.accessDenied(status: content.status, payload: content.payload) {
                    return .refused(ref, reason: deniedReason)
                }
                let message = GlobalNpiaParsing.payloadMessage(content.payload)
                return .failed(ref, message: "content failed (HTTP \(content.status))" + (message.isEmpty ? "" : ": " + message))
            }
            let raw = GlobalNpiaParsing.rawContent(content.payload)
            if raw.isEmpty { return .failed(ref, message: "empty content") }
            let cookies = GlobalNpiaParsing.signedImageCookies(ticket)
            return try await buildResult(raw: raw, ref: ref, chapterURL: chapterURL, cookies: cookies, context: context)
        }
        return .failed(ref, message: "could not obtain an episode ticket")
    }

    private func buildResult(
        raw: String, ref: ChapterRef, chapterURL: URL, cookies: [String: String], context: SourceContext
    ) async throws -> ChapterResult {
        let processed: GlobalNpiaParsing.ProcessedChapter?
        do {
            processed = try GlobalNpiaParsing.processChapterHTML(raw, chapterURL: chapterURL, chapterIndex: ref.index)
        } catch {
            return .failed(ref, message: "could not parse chapter HTML: \(error.localizedDescription)")
        }
        guard let chapter = processed else { return .failed(ref, message: "empty content") }
        var images: [ImageRef] = []
        for asset in chapter.assets {
            let url: URL = URL(string: asset.url) ?? chapterURL
            var data: Data?
            if asset.url.hasPrefix("data:") {
                data = GlobalNpiaParsing.dataURIBytes(asset.url)
            } else if !cookies.isEmpty, (url.host ?? "").lowercased() == GlobalNpiaParsing.imageHost {
                let cookieHeader: String = GlobalNpiaParsing.imageCookieNames
                    .compactMap { name -> String? in cookies[name].map { "\(name)=\($0)" } }
                    .joined(separator: "; ")
                let headers: [String: String] = ["Cookie": cookieHeader, "Referer": GlobalNpiaParsing.base + "/"]
                if let response = try? await context.http.get(url, headers: headers),
                   (200..<300).contains(response.status), !response.body.isEmpty {
                    data = response.body
                }
            }
            images.append(ImageRef(url: url, filename: asset.filename, data: data))
        }
        return .content(ChapterContent(ref: ref, html: chapter.html, images: images))
    }

    // MARK: Auth

    private static func cookieValues() -> [String: String] {
        var values: [String: String] = [:]
        let storage = HTTPCookieStorage.shared
        for origin in [GlobalNpiaParsing.base, GlobalNpiaParsing.api] {
            guard let url = URL(string: origin) else { continue }
            for cookie in storage.cookies(for: url) ?? [] { values[cookie.name] = cookie.value }
        }
        return values
    }

    static func savedLogin() -> Bool {
        GlobalNpiaParsing.hasSavedLogin(cookies: cookieValues())
    }

    /// The API wants a USERKEY and last_login cookie even for anonymous access.
    static func ensureBaseCookies() {
        let existing = cookieValues()
        func add(_ name: String, _ value: String) {
            let properties: [HTTPCookiePropertyKey: Any] = [
                .name: name, .value: value, .domain: GlobalNpiaParsing.cookieDomain, .path: "/",
            ]
            if let cookie = HTTPCookie(properties: properties) { HTTPCookieStorage.shared.setCookie(cookie) }
        }
        if existing["USERKEY"] == nil { add("USERKEY", UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()) }
        if existing["last_login"] == nil { add("last_login", "basic") }
    }

    /// Exchanges the web-view login cookies for a fresh LOGINAT token (desktop `_global_npia_refresh_login`).
    private func refreshLogin(context: SourceContext, expected: String?) async -> Bool {
        if let expected = expected, !auth.loginAt.isEmpty, auth.loginAt != expected { return true }
        if expected == nil, auth.attempted, auth.loginAt.isEmpty { return false }
        auth.markAttempted()
        guard let response = try? await requestJSON(
            GlobalNpiaParsing.api + "/v1/login/refresh", query: [], loginAt: "", maxRetries: 1, context: context
        ) else { return false }
        guard let token = GlobalNpiaParsing.loginToken(status: response.status, payload: response.payload) else { return false }
        auth.store(token)
        context.secrets.set(token, for: SecretKey.source(.globalNpia, "token"))
        return true
    }

    // MARK: HTTP

    private static let userAgent: String =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

    private func requestJSON(
        _ urlString: String, query: [(String, String)], loginAt: String, maxRetries: Int, context: SourceContext
    ) async throws -> (status: Int, payload: Any?) {
        guard var components = URLComponents(string: urlString) else { throw SourceError.parse("bad URL \(urlString)") }
        if !query.isEmpty {
            var items: [URLQueryItem] = components.queryItems ?? []
            for pair in query { items.append(URLQueryItem(name: pair.0, value: pair.1)) }
            components.queryItems = items
        }
        guard let url = components.url else { throw SourceError.parse("bad URL \(urlString)") }
        var headers: [String: String] = [
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": GlobalNpiaParsing.base,
            "Referer": GlobalNpiaParsing.base + "/",
            "User-Agent": Self.userAgent,
            "X-Requested-With": "XMLHttpRequest",
        ]
        if !loginAt.isEmpty { headers["login-at"] = loginAt.removingPercentEncoding ?? loginAt }

        var lastStatus = 0
        var lastPayload: Any?
        let attempts = max(1, maxRetries)
        for attempt in 1...attempts {
            try Task.checkCancellation()
            do {
                let response = try await context.http.send(HTTPRequest(url: url, headers: headers, timeout: 90))
                lastStatus = response.status
                if let object = try? JSONSerialization.jsonObject(with: response.body, options: [.fragmentsAllowed]) {
                    lastPayload = object
                } else {
                    lastPayload = ["error": String(response.text.prefix(300))]
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                lastStatus = 0
                lastPayload = ["error": error.localizedDescription]
            }
            let retryable = (lastStatus == 429 || lastStatus >= 500 || lastStatus == 0)
                && !GlobalNpiaParsing.adRequired(status: lastStatus, payload: lastPayload)
            if !retryable || attempt >= attempts { break }
            let base: Double = lastStatus == 429 ? max(5.0, pow(1.25, Double(attempt + 2))) : min(3.0, pow(1.25, Double(attempt)))
            let delay = base + Double.random(in: 0.2...0.8)
            try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }
        return (lastStatus, lastPayload)
    }
}
