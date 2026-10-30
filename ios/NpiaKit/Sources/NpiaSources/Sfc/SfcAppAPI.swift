import CryptoKit
import Foundation
import NpiaCore

/// The sfc Android app API: request signing, login, and chapter text decoding.
/// Port of `_sfc_sign`, `_sfc_headers`, `_sfc_api_response`, `_sfc_login` and
/// `_sfc_content_to_outputs` in the desktop `external_scraper.py`. The desktop could also
/// pull the session out of an Android emulator; here the user signs in with the app API.
enum SfcAppAPI {
    static let host = "api.\u{73}\u{66}\u{61}\u{63}\u{67}.com"
    static let base = URL(string: "https://\(host)")!
    static let deviceToken = "910D166A-736E-3231-8B21-8D12DFD75F16"
    static let salt = "lPQDb9AKO7$LjkPG"
    static let cookieKey = SecretKey.source(.sfc, "appCookie")
    static let sessionCookieName = "session_APP"
    static let communityCookieName = ".SFCommunity"

    /// The app's product token, appended to the sign-in web view's user agent so sfc's backend may hand
    /// that view an app session (`session_APP`), which `captureAppSession` then keeps. The base user agent
    /// stays Safari's, so sign-ins that refuse embedded views (Facebook) still work.
    static let webLoginUserAgentSuffix = "boluobao/5.2.16"

    /// The app's request signature (MD5 over a nonce-scrambled auth string).
    static func sign(nonce: String, timestamp: Int64, deviceToken: String = deviceToken, salt: String = salt) -> String {
        // Real nonces are 36-character UUIDs; the modulo only keeps odd input from trapping.
        let longNonce = Array((String(repeating: nonce.isEmpty ? "0" : nonce, count: 4)).utf8)
        func index(_ position: Int) -> Int { Int(longNonce[position % longNonce.count]) % 36 }
        func slice(_ start: Int, _ length: Int) -> ArraySlice<UInt8> {
            longNonce[min(start, longNonce.count)..<min(start + length, longNonce.count)]
        }
        let reorder = Array(slice(index(1), 13) + slice(index(2), 16) + slice(index(3), 36) + slice(index(4), 36))
        let auth = Array("\(timestamp)\(salt)\(deviceToken)\(nonce)".utf8)
        var mixed: [UInt8] = []
        for position in 0..<min(auth.count, reorder.count) {
            mixed.append(UInt8((Int(auth[position]) + Int(reorder[position])) >> 1))
        }
        func part(_ from: Int, _ to: Int?) -> [UInt8] {
            let lower = min(from, mixed.count)
            let upper = min(to ?? mixed.count, mixed.count)
            return Array(mixed[lower..<max(lower, upper)])
        }
        let reordered = part(65, nil) + part(0, 13) + part(29, 65) + part(13, 29)
        var final: [UInt8] = []
        for code in reordered {
            let value = Int(code)
            if value < 0x30 {
                final.append(0x39 < value + 19 && value + 19 < 0x41 ? 0x39 : UInt8(value + 19))
            } else if (0x39 < value && value < 0x41) || (0x5A < value && value < 0x61) {
                final.append(UInt8(value + 19))
            } else {
                final.append(code)
            }
        }
        return Insecure.MD5.hash(data: Data(final)).map { String(format: "%02X", $0) }.joined()
    }

    static func headers(nonce: String, timestamp: Int64, method: String = "GET") -> [String: String] {
        let signature = sign(nonce: nonce, timestamp: timestamp)
        var headers = [
            "accept": "application/vnd.\u{73}\u{66}\u{61}\u{63}\u{67}.api+json;version=1",
            "accept-charset": "UTF-8",
            "authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
            "sfsecurity": "nonce=\(nonce)&timestamp=\(timestamp)&devicetoken=\(deviceToken)&sign=\(signature)",
            "user-agent": "boluobao/5.2.16(android;35)/OPPO/\(deviceToken.lowercased())/OPPO",
        ]
        if method.uppercased() != "GET" {
            headers["content-type"] = "application/json; charset=UTF-8"
        }
        return headers
    }

    static func statusCode(_ payload: [String: Any]) -> Int? {
        (payload["status"] as? [String: Any]).map { HTTPSourceSupport.int($0["httpCode"]) }
    }

    /// One signed request. HTTP code 417 inside the JSON means the signature was rejected
    /// (not a purchase check), so it is retried with a fresh nonce, up to 20 times. The server turns
    /// down valid signatures often, so, as on the desktop, only every fifth rejection is logged.
    static func send(_ path: String, method: String = "GET", body: Data? = nil, cookie: String?, http: any HTTPFetching, logger: (any JobLogging)? = nil) async throws -> (HTTPResponse, [String: Any]) {
        let url = URL(string: path, relativeTo: base)!.absoluteURL
        var last: (HTTPResponse, [String: Any])?
        for attempt in 1...20 {
            let nonce = UUID().uuidString.uppercased()
            var headers = headers(nonce: nonce, timestamp: Int64(Date().timeIntervalSince1970 * 1000), method: method)
            if let cookie, !cookie.isEmpty { headers["Cookie"] = cookie }
            let response = try await http.send(HTTPRequest(url: url, method: method, headers: headers, body: body, quietStatuses: [417]))
            let payload = (try? HTTPSourceSupport.jsonObject(response.body)) ?? [:]
            last = (response, payload)
            if statusCode(payload) != 417 { return (response, payload) }
            if attempt % 5 == 0 {
                logger?.log(.debug, "sfc app API rejected the signature \(attempt) times; " + (attempt < 20 ? "trying a new one" : "giving up"))
            }
            if attempt < 20 { try await HTTPSourceSupport.sleep(seconds: 0.25) }
        }
        return last!
    }

    // MARK: - Session cookie

    static func cookieNames(_ cookie: String) -> Set<String> {
        Set(cookie.split(separator: ";").compactMap { item -> String? in
            let trimmed = item.trimmingCharacters(in: .whitespaces)
            guard let equals = trimmed.firstIndex(of: "=") else { return nil }
            return String(trimmed[..<equals])
        })
    }

    /// The saved app cookie, if it carries `session_APP`.
    static func savedCookie(_ secrets: any SecretStore) -> String? {
        guard let cookie = secrets.string(for: cookieKey)?.trimmingCharacters(in: .whitespacesAndNewlines),
              cookieNames(cookie).contains(sessionCookieName) else { return nil }
        return cookie.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }.filter { $0.contains("=") }.joined(separator: "; ")
    }

    /// Stores a user-supplied cookie header; false when `session_APP` is missing.
    @discardableResult
    static func saveCookie(_ cookie: String, secrets: any SecretStore) -> Bool {
        let trimmed = cookie.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cookieNames(trimmed).contains(sessionCookieName) else { return false }
        secrets.set(trimmed, for: cookieKey)
        return true
    }

    /// Promotes an app session a sign-in web view picked up: when `present` (the sfc site cookies the
    /// view holds) carries `session_APP`, saves it with `.SFCommunity` as the app cookie, so the app API
    /// serves VIP text. Returns true when an app session was saved. Pure over the map, so it is unit tested.
    @discardableResult
    static func captureAppSession(present: [String: String], secrets: any SecretStore) -> Bool {
        guard let session = present[sessionCookieName], !session.isEmpty else { return false }
        var parts: [String] = []
        if let community = present[communityCookieName], !community.isEmpty {
            parts.append("\(communityCookieName)=\(community)")
        }
        parts.append("\(sessionCookieName)=\(session)")
        return saveCookie(parts.joined(separator: "; "), secrets: secrets)
    }

    /// `.SFCommunity` and `session_APP` from (possibly comma-joined) `Set-Cookie` headers.
    static func sessionCookie(fromSetCookie header: String) -> String? {
        func value(_ name: String) -> String? {
            HTTPSourceSupport.firstMatch("(?:^|[,;\\s])" + NSRegularExpression.escapedPattern(for: name) + "=([^;,\\s]+)", in: header)
        }
        guard let community = value(communityCookieName), let session = value(sessionCookieName) else { return nil }
        return "\(communityCookieName)=\(community); \(sessionCookieName)=\(session)"
    }

    /// Signs in through the app API and stores the session cookie in `secrets`.
    static func login(username: String, password: String, http: any HTTPFetching, secrets: any SecretStore) async throws -> Bool {
        let username = username.trimmingCharacters(in: .whitespaces)
        guard !username.isEmpty, !password.isEmpty else { return false }
        let body = try JSONSerialization.data(withJSONObject: ["password": password, "shuMeiId": "", "username": username], options: [.sortedKeys])
        let (response, payload) = try await send("/sessions", method: "POST", body: body, cookie: nil, http: http)
        guard statusCode(payload) == 200 else {
            let message = HTTPSourceSupport.string((payload["status"] as? [String: Any])?["msg"])
            throw SourceError.blocked(message.isEmpty ? "sfc login failed (HTTP \(response.status))" : message)
        }
        var cookie = response.headers["set-cookie"].flatMap(sessionCookie(fromSetCookie:))
        if cookie == nil {
            // URLSession may have moved the cookies into the shared jar instead.
            let stored = HTTPCookieStorage.shared.cookies(for: base) ?? []
            if let community = stored.first(where: { $0.name == communityCookieName }), let session = stored.first(where: { $0.name == sessionCookieName }) {
                cookie = "\(communityCookieName)=\(community.value); \(sessionCookieName)=\(session.value)"
            }
        }
        guard let cookie else { return false }
        secrets.set(cookie, for: cookieKey)
        return true
    }

    // MARK: - Chapter text

    static let charMap: [Unicode.Scalar: Unicode.Scalar] = {
        var map: [Unicode.Scalar: Unicode.Scalar] = [:]
        for (from, to) in zip(SfcCharTable.obfuscated.unicodeScalars, SfcCharTable.replacement.unicodeScalars) {
            map[from] = to
        }
        return map
    }()

    /// Undoes the app API's character substitution.
    static func decode(_ text: String) -> String {
        var out = String.UnicodeScalarView()
        for scalar in text.unicodeScalars {
            out.append(charMap[scalar] ?? scalar)
        }
        return String(out)
    }

    /// Chapter text to XHTML; `[img]url[/img]` markers become images.
    static func content(_ raw: String, ref: ChapterRef) -> ChapterContent {
        let text = decode(raw)
        guard let pattern = try? NSRegularExpression(pattern: "\\[img(?:=[^\\]]*)?\\](https?://.*?)\\[/img\\]", options: [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return ChapterContent(ref: ref, html: "")
        }
        var images: [ImageRef] = []
        var names: [URL: String] = [:]
        var parts: [String] = []
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: CharacterSet(charactersIn: "\n\r"))
        for line in lines {
            var cursor = line.startIndex
            var pieces: [String] = []
            for match in pattern.matches(in: line, range: NSRange(line.startIndex..., in: line)) {
                guard let whole = Range(match.range, in: line), let group = Range(match.range(at: 1), in: line) else { continue }
                let before = String(line[cursor..<whole.lowerBound])
                if !before.isEmpty { pieces.append(ReaderBlocks.escape(before)) }
                let address = HTTPSourceSupport.unescapeHTML(String(line[group]).trimmingCharacters(in: .whitespaces))
                if let url = URL(string: address) {
                    let name: String
                    if let known = names[url] {
                        name = known
                    } else {
                        name = ReaderBlocks.imageFilename(url, chapter: ref.index, number: images.count + 1)
                        names[url] = name
                        images.append(ImageRef(url: url, filename: name))
                    }
                    pieces.append("<img src=\"\(ReaderBlocks.escape(name))\" alt=\"\" />")
                }
                cursor = whole.upperBound
            }
            let tail = String(line[cursor...])
            if !tail.isEmpty { pieces.append(ReaderBlocks.escape(tail)) }
            parts.append(pieces.isEmpty ? "<p><br/></p>" : "<p>\(pieces.joined())</p>")
        }
        return ChapterContent(ref: ref, html: "<div>\(parts.joined())</div>", images: images)
    }

    enum ChapterOutcome {
        case content(ChapterContent)
        /// 401/403: the session cannot read this chapter.
        case noAccess(Int)
        case unavailable
    }

    static func chapter(_ ref: ChapterRef, chapterID: String, cookie: String, http: any HTTPFetching, logger: (any JobLogging)? = nil) async throws -> ChapterOutcome {
        let (_, payload) = try await send("/Chaps/\(chapterID)?expand=content%2Cexpand.content", cookie: cookie, http: http, logger: logger)
        return chapter(from: payload, ref: ref)
    }

    static func chapter(from payload: [String: Any], ref: ChapterRef) -> ChapterOutcome {
        let status = statusCode(payload)
        if status == 401 || status == 403 { return .noAccess(status ?? 0) }
        guard status == 200, let data = payload["data"] as? [String: Any] else { return .unavailable }
        let text = [data["content"] as? String, (data["expand"] as? [String: Any])?["content"] as? String]
            .compactMap { $0 }
            .joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return .unavailable }
        var named = ref
        if let title = data["title"] as? String, !title.isEmpty { named.title = title }
        return .content(content(text, ref: named))
    }
}
