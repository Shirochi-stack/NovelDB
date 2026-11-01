import Foundation
import NpiaCore

/// Korean web-novel platform read through its public mobile-web JSON API.
/// Port of `_jara_*` in the desktop `external_scraper.py`.
///
/// Chapter text is AES-CBC encrypted with a short-lived key from `chapter_valid`, and
/// chapter requests are paced (one every 5 s) to stay under the site's bot check.
/// Purchased and adult chapters need the site's login token: either saved in the
/// `SecretStore` under `jara.token`, or read from the `signedInfo` localStorage entry of
/// the `jara` web profile after the user signs in there.
public struct JaraAdapter: SourceAdapter {
    public static let id: SourceID = .jara
    public static let tier: SourceTier = .http
    public static let sites: Set<String> = [host]

    static let host = "\u{6a}\u{6f}\u{61}\u{72}\u{61}.com"
    static let origin = URL(string: "https://www.\(host)")!
    static let api = URL(string: "https://api.\(host)")!
    static let home = URL(string: "https://www.\(host)/")!
    static let tokenKey = SecretKey.source(.jara, "token")
    static let webProfile = SourceID.jara.rawValue

    /// Public production config from the site's web bundle; re-read live when possible.
    static let defaultParams: [String: String] = [
        "api_key": "mw_8ba234e7801ba288554ca07ae44c7",
        "ver": "3.2.0",
        "device": "mw",
        "devicetoken": "mw",
    ]
    static let minRequestInterval = 5.0
    static let keyMaxAge = 20.0
    static let keyTTL = 31.5
    static let decryptRetries = 2

    let session = JaraSession()

    public init() {}

    public static func matches(_ url: URL) -> Bool {
        bookCode(url) != nil
    }

    static func bookCode(_ url: URL) -> String? {
        let host = (url.host ?? "").lowercased()
        guard [Self.host, "www.\(Self.host)", "m.\(Self.host)"].contains(host) else { return nil }
        if let code = HTTPSourceSupport.firstMatch("^/book/(\\d+)/?$", in: url.path) {
            return code
        }
        for key in ["bookCode", "book_code"] {
            if let value = HTTPSourceSupport.query(url, key), HTTPSourceSupport.isPositiveInteger(value) {
                return value
            }
        }
        return nil
    }

    static var headers: [String: String] {
        [
            "User-Agent": HTTPSourceSupport.desktopUserAgent,
            "Accept": "application/json",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Origin": origin.absoluteString,
            "Referer": origin.absoluteString + "/",
        ]
    }

    // MARK: - Helpers

    static func isTrue(_ value: Any?) -> Bool {
        ["TRUE", "Y", "1"].contains(HTTPSourceSupport.string(value).trimmingCharacters(in: .whitespaces).uppercased())
    }

    static func isLoginError(_ payload: [String: Any]) -> Bool {
        HTTPSourceSupport.int(payload["error_code"]) == 9200
            || HTTPSourceSupport.string(payload["message"]).contains("로그인")
    }

    static func isOK(_ payload: [String: Any]) -> Bool {
        HTTPSourceSupport.int(payload["status"]) == 1
    }

    /// "N화", keeping the episode subtitle as a suffix unless it is already numbered.
    static func chapterName(sortno: Int, title: String) -> String {
        let label = sortno > 0 ? "\(sortno)화" : ""
        if title.isEmpty { return label.isEmpty ? "Chapter" : label }
        if label.isEmpty || title.range(of: "\\d+\\s*(?:화|회|장|편)", options: .regularExpression) != nil {
            return title
        }
        return "\(label) - \(title)"
    }

    /// Token from the `signedInfo` localStorage value, unless it has expired.
    static func token(fromSignedInfo raw: String, now: Date = Date()) -> String? {
        guard let data = raw.data(using: .utf8),
              let wrapper = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        let expire = HTTPSourceSupport.double(wrapper["expire"])
        if expire > 0, expire < now.timeIntervalSince1970 * 1000 { return nil }
        var inner = wrapper["data"] ?? wrapper
        if let text = inner as? String {
            guard let innerData = text.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: innerData) else { return nil }
            inner = object
        }
        let token = HTTPSourceSupport.string((inner as? [String: Any])?["token"]).trimmingCharacters(in: .whitespaces)
        return token.isEmpty ? nil : token
    }

    /// Reads `apiKey`, `version`, `device` and `devicetoken` from the site's main bundle.
    static func params(fromBundle bundle: String) -> [String: String] {
        let anchor = "[\"']https://api\\.\(NSRegularExpression.escapedPattern(for: host))[\"']\\s*,\\s*apiKey\\s*:"
        guard let range = bundle.range(of: anchor, options: .regularExpression) else { return [:] }
        let config = String(bundle[range.lowerBound...].prefix(600))
        var out: [String: String] = [:]
        for (key, name) in [("api_key", "apiKey"), ("ver", "version"), ("device", "device"), ("devicetoken", "devicetoken")] {
            if let value = HTTPSourceSupport.firstMatch("\\b\(name)\\s*:\\s*[\"']([^\"']+)[\"']", in: config) {
                out[key] = value
            }
        }
        return out
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let code = Self.bookCode(url) else { throw SourceError.unsupportedURL(url) }
        let token = await loadToken(context: context)
        await session.setToken(token)
        if token != nil {
            context.logger.log(.info, "Using the saved jara login for purchased and adult chapters.")
        }
        let query = ["book_code": code, "promotion_code": ""]
        var payload = try await apiGet("/v1/book/detail.joa", query, context: context)
        if !Self.isOK(payload), token != nil, Self.isLoginError(payload) {
            context.logger.log(.warning, "The saved jara login was rejected; continuing with free chapters only.")
            await session.setToken(nil)
            payload = try await apiGet("/v1/book/detail.joa", query, context: context)
        }
        guard Self.isOK(payload) else {
            if Self.isLoginError(payload) { throw SourceError.loginRequired(Self.id) }
            let message = HTTPSourceSupport.string(payload["message"])
            throw SourceError.parse(message.isEmpty ? "unknown error" : message)
        }
        return try Self.book(from: payload, code: code)
    }

    static func book(from payload: [String: Any], code: String) throws -> BookInfo {
        let book = payload["book"] as? [String: Any] ?? [:]
        let rows = (book["chapter"] as? [[String: Any]] ?? []).filter { !HTTPSourceSupport.string($0["cid"]).isEmpty }
        guard HTTPSourceSupport.string(book["book_code"]) == code, !rows.isEmpty else {
            throw SourceError.parse("the book has no readable chapter list")
        }
        let paidStore = isTrue(book["is_premium"]) || isTrue(book["is_nobless"]) || isTrue(book["is_finish"])
        var chapters: [ChapterRef] = []
        for row in rows.sorted(by: { HTTPSourceSupport.int($0["sortno"]) < HTTPSourceSupport.int($1["sortno"]) }) {
            let sortno = HTTPSourceSupport.int(row["sortno"])
            let title = HTTPSourceSupport.unescapeHTML(HTTPSourceSupport.string(row["sub_subject"])).trimmingCharacters(in: .whitespacesAndNewlines)
            let cid = HTTPSourceSupport.string(row["cid"])
            let isPaid = paidStore && !isTrue(row["is_free"])
            var components = URLComponents(url: origin.appendingPathComponent("viewer"), resolvingAgainstBaseURL: false)!
            components.queryItems = [
                URLQueryItem(name: "cid", value: cid),
                URLQueryItem(name: "bookCode", value: code),
                URLQueryItem(name: "sortno", value: String(sortno)),
            ]
            // A paid chapter the account owns (`is_buy`) is still marked paid; the chapter
            // request decides whether it can be read.
            chapters.append(ChapterRef(id: cid, index: chapters.count + 1, title: chapterName(sortno: sortno, title: title), url: components.url, isPaid: isPaid))
        }
        let title = HTTPSourceSupport.unescapeHTML(HTTPSourceSupport.string(book["subject"])).trimmingCharacters(in: .whitespacesAndNewlines)
        let author = HTTPSourceSupport.unescapeHTML(HTTPSourceSupport.string(book["writer_name"])).trimmingCharacters(in: .whitespacesAndNewlines)
        let intro = HTTPSourceSupport.unescapeHTML(HTTPSourceSupport.string(book["intro"]))
            .replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        var keywords: [String]
        if let list = book["keyword"] as? [Any] {
            keywords = list.map { HTTPSourceSupport.string($0) }
        } else {
            keywords = HTTPSourceSupport.string(book["keyword"]).components(separatedBy: CharacterSet(charactersIn: ",#").union(.whitespaces))
        }
        var tags: [String] = []
        for keyword in keywords {
            let tag = keyword.trimmingCharacters(in: CharacterSet(charactersIn: "#").union(.whitespaces))
            if !tag.isEmpty, !tags.contains(tag) { tags.append(tag) }
        }
        var category = HTTPSourceSupport.string(book["category_ko_name"]).trimmingCharacters(in: .whitespaces)
        if category.isEmpty { category = HTTPSourceSupport.string(book["category_name"]).trimmingCharacters(in: .whitespaces) }
        if !category.isEmpty, !tags.contains(category) { tags.insert(category, at: 0) }
        return BookInfo(
            source: id, id: code, url: origin.appendingPathComponent("book/\(code)"),
            title: title.isEmpty ? "Jara Book \(code)" : title,
            author: author.isEmpty ? nil : author, synopsis: intro.isEmpty ? nil : intro,
            coverURL: URL(string: HTTPSourceSupport.string(book["book_img"])), tags: tags,
            status: isTrue(book["chk_finish"]) ? "완결" : "연재", language: "ko", chapters: chapters
        )
    }

    // MARK: - Chapters

    /// Chapters are fetched one at a time: the site counts quick chapter requests.
    public func parseChapters(_ refs: [ChapterRef], book: BookInfo, context: SourceContext) async throws -> [ChapterResult] {
        var results: [ChapterResult] = []
        let shared = context.shared(for: Self.id)
        for ref in refs {
            try Task.checkCancellation()
            if !results.isEmpty { try await shared.sleepChapterDelay() }
            do {
                results.append(try await parseChapter(ref, book: book, context: context))
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as JaraAbort {
                // Bot check still active: stop asking, report the rest as failed.
                let start = results.count
                results.append(.failed(ref, message: error.message))
                for rest in refs.dropFirst(results.count) {
                    results.append(.failed(rest, message: error.message))
                }
                results[start...].forEach(context.onChapter)
                return results
            } catch {
                results.append(.failed(ref, message: error.localizedDescription))
            }
            if let last = results.last { context.onChapter(last) }
        }
        return results
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let cid = ref.url.flatMap { HTTPSourceSupport.query($0, "cid") } ?? ref.id
        var decryptFailures = 0
        var captchaChecks = 0
        while true {
            try await session.waitForRequestSlot(interval: Self.minRequestInterval)
            let keyPair = try await chapterKey(force: decryptFailures > 0, context: context)
            await session.reserveAfterKeyWait(interval: Self.minRequestInterval)
            let payload = try await apiGet("/v1/book/chapter.joa", ["cid": cid], context: context)
            let chapter = payload["chapter"] as? [String: Any] ?? [:]
            let redis = chapter["redis_data"] as? [String: Any]
            if Self.isTrue(payload["is_captcha"]) || Self.isTrue(redis?["is_captcha"]) {
                captchaChecks += 1
                if captchaChecks > 3 {
                    throw JaraAbort(message: "The bot check is still active. Open the defender page in the jara web profile, complete it, then download the remaining chapters again.")
                }
                if captchaChecks == 1 {
                    // TODO(http sources (C)): the desktop clicks the captcha in a headless browser.
                    // Here the user completes it in a visible web view.
                    context.logger.log(.warning, "jara asked for a captcha; opening it for you to complete.")
                    do {
                        try await context.web.presentLogin(url: Self.origin.appendingPathComponent("defender"), profile: Self.webProfile)
                    } catch {
                        throw JaraAbort(message: "Download stopped: the bot check was not completed.")
                    }
                    if let token = await loadToken(context: context) { await session.setToken(token) }
                } else {
                    try await HTTPSourceSupport.sleep(seconds: 3)
                }
                continue
            }
            guard Self.isOK(payload), !chapter.isEmpty else {
                if Self.isLoginError(payload) {
                    return .refused(ref, reason: "needs a login that owns this chapter")
                }
                let message = HTTPSourceSupport.string(payload["message"])
                if ref.isPaid {
                    return .refused(ref, reason: message.isEmpty ? "paid chapter not owned" : message)
                }
                return .failed(ref, message: message.isEmpty ? "unknown error" : message)
            }
            guard let text = try? JaraCrypto.decrypt(HTTPSourceSupport.string(chapter["content"]), key: keyPair.key, iv: keyPair.iv) else {
                // Encrypted with a key that expired before the server answered: fetch a new key
                // and request the chapter again, as the site's own viewer does.
                decryptFailures += 1
                if decryptFailures > Self.decryptRetries {
                    return .failed(ref, message: "could not decrypt the chapter")
                }
                try await HTTPSourceSupport.sleep(seconds: 1.5)
                continue
            }
            return try Self.chapter(text: text, note: HTTPSourceSupport.string(chapter["episode"]), ref: ref)
        }
    }

    static func chapter(text: String, note: String, ref: ChapterRef) throws -> ChapterResult {
        var blocks: [ReaderBlock]
        if text.range(of: "<\\s*/?\\s*(?:p|br|img|div|span)\\b", options: [.regularExpression, .caseInsensitive]) != nil {
            blocks = try ReaderBlocks.fromHTML(text, baseURL: home)
        } else {
            blocks = ReaderBlocks.fromPlainText(text)
        }
        let note = note.replacingOccurrences(of: "\r\n", with: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        if !note.isEmpty {
            blocks.append(.text("* * *"))
            blocks += ReaderBlocks.fromPlainText(note)
        }
        guard !blocks.isEmpty else { return .failed(ref, message: "empty chapter") }
        return .content(ReaderBlocks.content(blocks, ref: ref, cssClass: "jara-content"))
    }

    // MARK: - API

    func apiGet(_ path: String, _ params: [String: String], useToken: Bool = true, context: SourceContext) async throws -> [String: Any] {
        var query = await publicParams(context: context)
        if useToken, let token = await session.token { query["token"] = token }
        query.merge(params) { $1 }
        var components = URLComponents(string: Self.api.absoluteString + path)!
        components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        let response = try await context.http.get(components.url!, headers: Self.headers)
        if response.status >= 500 { throw HTTPError.status(response.status, components.url!) }
        do {
            return try HTTPSourceSupport.jsonObject(response.body)
        } catch {
            throw SourceError.parse("jara returned non-JSON (HTTP \(response.status))")
        }
    }

    func publicParams(context: SourceContext) async -> [String: String] {
        if let params = await session.params { return params }
        var params = Self.defaultParams
        do {
            let home = try await context.http.get(Self.home, headers: ["User-Agent": HTTPSourceSupport.desktopUserAgent]).text
            if let path = HTTPSourceSupport.allMatches("src=\"(/static/js/main\\.[A-Za-z0-9]+\\.chunk\\.js)\"", in: home).last?.first,
               let bundleURL = URL(string: path, relativeTo: Self.origin) {
                let bundle = try await context.http.get(bundleURL.absoluteURL, headers: ["User-Agent": HTTPSourceSupport.desktopUserAgent]).text
                params.merge(Self.params(fromBundle: bundle)) { $1 }
            }
        } catch {
            context.logger.log(.debug, "jara: using the built-in API config (\(error.localizedDescription)).")
        }
        // The chapter key is bound to this device ID, so it stays the same for the session.
        params["deviceuid"] = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return await session.setParamsIfMissing(params)
    }

    func fetchKey(context: SourceContext) async throws -> JaraKey {
        let payload = try await apiGet("/v1/book/chapter_valid.joa", [:], context: context)
        guard Self.isOK(payload), let data = payload["data"] as? [Any], data.count >= 2 else {
            throw SourceError.parse("jara did not return a chapter key")
        }
        return JaraKey(key: HTTPSourceSupport.string(data[0]), iv: HTTPSourceSupport.string(data[1]))
    }

    /// A key that stays live for the next chapter request. `chapter_valid` returns the live
    /// key unchanged until it expires and does not report its age, so the age is tracked
    /// from the call that first returned it; a stale key is waited out, not reused.
    func chapterKey(force: Bool, context: SourceContext) async throws -> JaraKey {
        if !force, let current = await session.freshKey(maxAge: Self.keyMaxAge) {
            return current
        }
        let sent = Date()
        let key = try await fetchKey(context: context)
        if await session.adoptIfNew(key, born: sent) { return key }
        let born = await session.keyBorn ?? sent
        if force, Date().timeIntervalSince(born) <= Self.keyMaxAge {
            return key
        }
        try await HTTPSourceSupport.sleep(seconds: born.addingTimeInterval(Self.keyTTL).timeIntervalSinceNow)
        for _ in 0..<3 {
            let sent = Date()
            let next = try await fetchKey(context: context)
            if await session.adoptIfNew(next, born: sent) { return next }
            try await HTTPSourceSupport.sleep(seconds: 1)
        }
        return key
    }

    /// Saved token, else the `signedInfo` entry of the web profile.
    func loadToken(context: SourceContext) async -> String? {
        if let saved = context.secrets.string(for: Self.tokenKey)?.trimmingCharacters(in: .whitespacesAndNewlines), !saved.isEmpty {
            if let token = Self.token(fromSignedInfo: saved) { return token }
            if !saved.hasPrefix("{") { return saved }
        }
        do {
            let page = try await context.web.newPage(profile: Self.webProfile)
            defer { Task { await page.close() } }
            try await page.load(Self.home, until: .domContentLoaded, timeout: 20)
            let data = try await page.evaluateJSON("return localStorage.getItem('signedInfo')")
            if let raw = try JSONDecoder().decode(String?.self, from: data), let token = Self.token(fromSignedInfo: raw) {
                return token
            }
        } catch {
            context.logger.log(.debug, "jara: no web login available (\(error.localizedDescription)).")
        }
        return nil
    }
}

struct JaraKey: Equatable, Sendable {
    var key: String
    var iv: String
}

struct JaraAbort: Error {
    var message: String
}

/// Per-job state: API params, token, the live chapter key, and request pacing.
actor JaraSession {
    private(set) var params: [String: String]?
    private(set) var token: String?
    private var key: JaraKey?
    private(set) var keyBorn: Date?
    private var nextRequestAt = Date.distantPast

    func setToken(_ value: String?) { token = value }

    func setParamsIfMissing(_ value: [String: String]) -> [String: String] {
        if let params { return params }
        params = value
        return value
    }

    func freshKey(maxAge: Double) -> JaraKey? {
        guard let key, let keyBorn, Date().timeIntervalSince(keyBorn) <= maxAge else { return nil }
        return key
    }

    /// Records `candidate` as the live key if it differs from the current one.
    func adoptIfNew(_ candidate: JaraKey, born: Date) -> Bool {
        guard candidate != key else { return false }
        key = candidate
        keyBorn = born
        return true
    }

    func waitForRequestSlot(interval: Double) async throws {
        let now = Date()
        let start = max(now, nextRequestAt)
        nextRequestAt = start.addingTimeInterval(interval)
        try await HTTPSourceSupport.sleep(seconds: start.timeIntervalSince(now))
    }

    /// A key wait can outlast the reserved slot; spacing is measured between chapter requests.
    func reserveAfterKeyWait(interval: Double) {
        nextRequestAt = max(nextRequestAt, Date().addingTimeInterval(interval))
    }
}
