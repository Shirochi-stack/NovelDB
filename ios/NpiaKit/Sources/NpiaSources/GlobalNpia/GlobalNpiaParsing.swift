import Foundation
import NpiaCore
import SwiftSoup

/// Pure helpers for the global_npia JSON API and chapter HTML (no network).
enum GlobalNpiaParsing {
    static let host: String = "global.\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let apiHost: String = "api-global.\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let imageHost: String = "pv-gn.\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let cookieDomain: String = ".\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let base: String = "https://" + host
    static let api: String = "https://" + apiHost
    static let imageCookieNames: [String] = ["CloudFront-Policy", "CloudFront-Key-Pair-Id", "CloudFront-Signature"]

    // MARK: URLs

    static func matches(_ url: URL) -> Bool {
        guard (url.host ?? "").lowercased() == host else { return false }
        return WebRegex.matches("^/novel/\\d+(?:/|$)", in: url.path, options: [.caseInsensitive])
    }

    static func novelID(from url: URL) -> String {
        WebRegex.capture("/novel/(\\d+)", in: url.path, options: [.caseInsensitive]) ?? ""
    }

    static func episodeID(from url: URL) -> String {
        WebRegex.capture("/viewer/(\\d+)", in: url.path, options: [.caseInsensitive]) ?? ""
    }

    // MARK: JSON helpers

    static func dict(_ value: Any?) -> [String: Any]? { value as? [String: Any] }

    static func string(_ value: Any?) -> String {
        guard let value = value, !(value is NSNull) else { return "" }
        if let text = value as? String { return text }
        if let number = value as? NSNumber { return number.stringValue }
        return "\(value)"
    }

    static func safeInt(_ value: Any?, fallback: Int = 0) -> Int {
        guard let value = value, !(value is NSNull) else { return fallback }
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String, let parsed = Int(text.trimmingCharacters(in: .whitespaces)) { return parsed }
        return fallback
    }

    static func pickStrings(_ items: Any?, keys: [String]) -> [String] {
        guard let list = items as? [Any] else { return [] }
        var values: [String] = []
        for item in list {
            if let text = item as? String {
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty { values.append(trimmed) }
                continue
            }
            guard let object = item as? [String: Any] else { continue }
            for key in keys {
                if let text = object[key] as? String {
                    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !trimmed.isEmpty { values.append(trimmed); break }
                }
            }
        }
        return values
    }

    static func payloadMessage(_ payload: Any?) -> String {
        guard let object = payload as? [String: Any] else { return payload == nil ? "" : string(payload) }
        let result = dict(object["result"]) ?? [:]
        var values: [Any?] = [object["errmsg"], object["message"], object["error"], object["code"]]
        values.append(contentsOf: [result["errmsg"], result["message"], result["name"], result["code"]])
        let parts: [String] = values.compactMap { value -> String? in
            let text = string(value)
            return text.isEmpty ? nil : text
        }
        return parts.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The server's basic-advertisement episode gate.
    static func adRequired(status: Int, payload: Any?) -> Bool {
        guard let object = payload as? [String: Any] else { return false }
        let result = dict(object["result"]) ?? [:]
        var code = string(object["code"])
        if code.isEmpty { code = string(result["code"]) }
        code = code.trimmingCharacters(in: .whitespaces)
        var name = string(result["name"])
        if name.isEmpty { name = string(object["name"]) }
        name = name.uppercased()
        let message = payloadMessage(payload).lowercased()
        let codeMatch = code == "0010" || code == "10"
        let gated = codeMatch && (name == "NOVEL_ERROR" || message.contains("advertisement"))
        return status >= 400 && (gated || message.contains("basic advertisement"))
    }

    static func accessDenied(status: Int, payload: Any?) -> Bool {
        if status == 401 || status == 403 { return true }
        let message = payloadMessage(payload)
        let pattern = "login|logged in|purchase|paid|permission|forbidden|access denied|not available|\u{AD6C}\u{B9E4}|\u{B85C}\u{ADF8}\u{C778}|\u{AD8C}\u{D55C}"
        return WebRegex.matches(pattern, in: message, options: [.caseInsensitive])
    }

    static func authExpired(status: Int, payload: Any?) -> Bool {
        let message = payloadMessage(payload).lowercased()
        if status == 401 || status == 403 { return true }
        if message.contains("logged in") || message.contains("login") { return true }
        return message.contains("token") && message.contains("expire")
    }

    static func hasSavedLogin(cookies: [String: String]) -> Bool {
        let userKey = (cookies["USERKEY"] ?? "").trimmingCharacters(in: .whitespaces)
        let tKey = (cookies["TKEY"] ?? "").trimmingCharacters(in: .whitespaces)
        let loginKey = (cookies["LOGINKEY"] ?? "").trimmingCharacters(in: .whitespaces)
        let isLogin = (cookies["ISLOGIN"] ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        let marker = !["", "0", "false", "n", "no"].contains(isLogin)
        return !userKey.isEmpty && (!tKey.isEmpty || (!loginKey.isEmpty && marker))
    }

    /// `result.LOGINAT` from `/v1/login/refresh`, URL-decoded, or nil.
    static func loginToken(status: Int, payload: Any?) -> String? {
        guard let object = payload as? [String: Any] else { return nil }
        let result = dict(object["result"])
        let statusCode = string(object["statusCode"])
        guard status == 200, statusCode.isEmpty || statusCode == "200",
              let token = result?["LOGINAT"] as? String,
              !token.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return token.removingPercentEncoding ?? token
    }

    // MARK: Book

    struct Episode: Equatable {
        var episodeNo: String
        var number: Int
        var title: String
    }

    /// Sorted, de-duplicated episodes (by `epi_num`, then source order).
    static func episodes(from list: [Any]) -> [Episode] {
        var keyed: [(Int, Int, [String: Any])] = []
        for (offset, item) in list.enumerated() {
            guard let object = item as? [String: Any] else { continue }
            keyed.append((safeInt(object["epi_num"], fallback: offset + 1), offset, object))
        }
        let sorted = keyed.sorted { (left: (Int, Int, [String: Any]), right: (Int, Int, [String: Any])) -> Bool in
            left.0 != right.0 ? left.0 < right.0 : left.1 < right.1
        }
        var seen = Set<String>()
        var result: [Episode] = []
        for entry in sorted {
            let object = entry.2
            guard let episodeNo = intString(object["episode_no"]) else { continue }
            if seen.contains(episodeNo) { continue }
            seen.insert(episodeNo)
            let number = safeInt(object["epi_num"], fallback: result.count + 1)
            var title = string(object["epi_title"]).trimmingCharacters(in: .whitespacesAndNewlines)
            if title.isEmpty { title = "Episode \(number)" }
            result.append(Episode(episodeNo: episodeNo, number: number, title: title))
        }
        return result
    }

    private static func intString(_ value: Any?) -> String? {
        guard let value = value, !(value is NSNull) else { return nil }
        if let number = value as? NSNumber { return String(number.intValue) }
        if let text = value as? String, let parsed = Int(text.trimmingCharacters(in: .whitespaces)) { return String(parsed) }
        return nil
    }

    static func makeBook(novelID: String, novelPayload: [String: Any], episodeList: [Any]) throws -> BookInfo {
        guard let result = dict(novelPayload["result"]), let novel = dict(result["novel"]) else {
            throw SourceError.parse("metadata payload has no novel")
        }
        guard let bookURL = URL(string: "\(base)/novel/\(novelID)") else { throw SourceError.parse("bad book url") }
        let found = episodes(from: episodeList)
        if found.isEmpty { throw SourceError.parse("no episodes were found") }
        var chapters: [ChapterRef] = []
        for (position, episode) in found.enumerated() {
            chapters.append(ChapterRef(
                id: episode.episodeNo, index: position + 1, title: episode.title,
                url: URL(string: "\(base)/viewer/\(episode.episodeNo)"), isPaid: false
            ))
        }
        var author = ""
        if let writers = result["writer_list"] as? [Any], let first = writers.first as? [String: Any] {
            author = string(first["writer_name"]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let tags: [String] = pickStrings(result["tag_list"], keys: ["tag_name", "name", "title"])
            + pickStrings(novel["tag_list"], keys: ["tag_name", "name", "title"])
        var categories: [String] = pickStrings(result["cate_list"], keys: ["cate_name", "name", "title"])
        categories += pickStrings(novel["cate_list"], keys: ["cate_name", "name", "title"])
        categories += pickStrings(result["genre_list"], keys: ["genre_name", "name", "title"])
        categories += pickStrings(novel["genre_list"], keys: ["genre_name", "name", "title"])
        var subjects: [String] = []
        for value in categories + tags where !subjects.contains(value) { subjects.append(value) }

        var cover = string(novel["novel_full_img"])
        if cover.isEmpty { cover = string(novel["novel_img"]) }
        var coverURL: URL?
        if cover.hasPrefix("//") {
            coverURL = URL(string: "https:" + cover)
        } else if !cover.isEmpty {
            coverURL = WebHTML.join(bookURL, cover)
        }
        let synopsis = string(novel["novel_story"]).trimmingCharacters(in: .whitespacesAndNewlines)
        var title = string(novel["novel_name"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if title.isEmpty { title = "Novel \(novelID)" }
        let complete = string(novel["flag_complete"]) == "1"
        return BookInfo(
            source: .globalNpia, id: novelID, url: bookURL, title: title,
            author: author.isEmpty ? nil : author,
            synopsis: synopsis.isEmpty ? nil : synopsis,
            coverURL: coverURL, tags: subjects, status: complete ? "Completed" : "Ongoing",
            language: "en", chapters: chapters
        )
    }

    // MARK: Ticket / content payloads

    static func looksLikeJWT(_ value: String) -> Bool {
        let parts = value.components(separatedBy: ".")
        guard parts.count == 3, parts.allSatisfy({ !$0.isEmpty }) else { return false }
        for part in parts {
            var text = part.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            if text.count % 4 == 1 { return false }
            while text.count % 4 != 0 { text += "=" }
            if Data(base64Encoded: text) == nil { return false }
        }
        return true
    }

    /// (token, directURL) using the desktop's strict order; both empty when absent.
    static func extractTicketToken(_ payload: Any?) -> (token: String, directURL: String) {
        let result = dict(dict(payload)?["result"]) ?? [:]
        var fallback = ""
        let keys = ["_t", "t", "token"]
        for key in keys {
            if let value = result[key] as? String, !value.isEmpty {
                if looksLikeJWT(value) { return (value, "") }
                if fallback.isEmpty { fallback = value }
            }
        }
        for name in result.keys.sorted() {
            guard let nested = result[name] as? [String: Any] else { continue }
            for key in keys {
                if let value = nested[key] as? String, !value.isEmpty {
                    if looksLikeJWT(value) { return (value, "") }
                    if fallback.isEmpty { fallback = value }
                }
            }
        }
        var strings: [String] = []
        collectStrings(payload, into: &strings)
        for value in strings where value.hasPrefix("http://") || value.hasPrefix("https://") {
            guard let components = URLComponents(string: value),
                  (components.host ?? "") == apiHost,
                  components.path.hasSuffix("/v1/novel/episode/content") else { continue }
            let token = components.queryItems?.first(where: { $0.name == "_t" })?.value ?? ""
            if !token.isEmpty {
                if looksLikeJWT(token) { return (token, value) }
                if fallback.isEmpty { fallback = token }
            }
        }
        return (fallback, "")
    }

    private static func collectStrings(_ value: Any?, into output: inout [String]) {
        if let text = value as? String {
            output.append(text)
        } else if let object = value as? [String: Any] {
            for key in object.keys.sorted() { collectStrings(object[key], into: &output) }
        } else if let list = value as? [Any] {
            for item in list { collectStrings(item, into: &output) }
        }
    }

    static func signedImageCookies(_ ticket: Any?) -> [String: String] {
        guard let signed = dict(dict(dict(ticket)?["result"])?["signed_key"]) else { return [:] }
        var cookies: [String: String] = [:]
        for name in imageCookieNames {
            let value = string(signed[name])
            if !value.isEmpty { cookies[name] = value }
        }
        return cookies
    }

    static func rawContent(_ payload: Any?) -> String {
        let object = dict(payload) ?? [:]
        let result = dict(object["result"]) ?? [:]
        if let data = dict(result["data"]) {
            func sortKey(_ key: String) -> (Int, Int) {
                let digits = WebRegex.capture("(\\d+)$", in: key).flatMap { Int($0) } ?? 0
                return (key == "epi_content" ? 0 : 1, digits)
            }
            let keys: [String] = data.keys.filter { $0.hasPrefix("epi_content") }.sorted { (left: String, right: String) -> Bool in
                let a = sortKey(left)
                let b = sortKey(right)
                if a.0 != b.0 { return a.0 < b.0 }
                if a.1 != b.1 { return a.1 < b.1 }
                return left < right
            }
            var joined = ""
            for key in keys {
                if let text = data[key] as? String, !text.isEmpty { joined += text }
            }
            let trimmed = joined.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        let candidates: [Any?] = [result["content"], result["html"], result["text"], object["content"]]
        for candidate in candidates {
            if let text = candidate as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return text.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        return ""
    }

    // MARK: Chapter HTML

    static func bestSrcset(_ value: String) -> String {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("data:") { return text }
        var best = ""
        var bestScore = -Double.infinity
        for raw in text.components(separatedBy: ",") {
            let bits: [String] = raw.split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" }).map { String($0) }
            guard let first = bits.first else { continue }
            var score = 1.0
            if bits.count > 1, let last = bits.last?.lowercased() {
                if last.hasSuffix("w"), let number = Double(String(last.dropLast())) {
                    score = number
                } else if last.hasSuffix("x"), let number = Double(String(last.dropLast())) {
                    score = number * 10000.0
                }
            }
            if score > bestScore {
                best = first
                bestScore = score
            }
        }
        return best
    }

    static func normalizeURL(_ value: String, base: URL) -> String {
        var text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        text = text.replacingOccurrences(of: "&amp;", with: "&")
        if text.isEmpty || text.hasPrefix("blob:") { return "" }
        if text.hasPrefix("data:") { return text }
        guard let joined = WebHTML.join(base, text) else { return "" }
        var absolute = joined.absoluteString
        if let hash = absolute.firstIndex(of: "#") { absolute = String(absolute[absolute.startIndex..<hash]) }
        return absolute
    }

    static func imageExtension(for urlString: String) -> String {
        if urlString.hasPrefix("data:") {
            let mime = urlString.components(separatedBy: ";").first?.lowercased() ?? ""
            switch mime {
            case "data:image/png": return "png"
            case "data:image/gif": return "gif"
            case "data:image/webp": return "webp"
            case "data:image/avif": return "avif"
            case "data:image/svg+xml": return "svg"
            default: return "jpg"
            }
        }
        let path = URL(string: urlString)?.path ?? ""
        let last = (path as NSString).lastPathComponent
        let ext = (last as NSString).pathExtension.lowercased()
        if (2...5).contains(ext.count), ext.allSatisfy({ $0.isLetter || $0.isNumber }) { return ext }
        return "jpg"
    }

    static func dataURIBytes(_ uri: String) -> Data? {
        guard uri.hasPrefix("data:"), let comma = uri.firstIndex(of: ",") else { return nil }
        let header = String(uri[uri.startIndex..<comma])
        let payload = String(uri[uri.index(after: comma)...])
        if header.contains(";base64") { return Data(base64Encoded: payload) }
        return payload.removingPercentEncoding.map { Data($0.utf8) }
    }

    /// Rewrites `url(...)` in a style string through `transform` (empty result becomes `none`).
    static func rewriteBackgrounds(_ style: String, transform: (String) -> String) -> String {
        let pattern = "url\\(\\s*([\"']?)(.*?)\\1\\s*\\)"
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return style }
        let source = style as NSString
        var output = ""
        var cursor = 0
        for match in regex.matches(in: style, range: NSRange(location: 0, length: source.length)) {
            output += source.substring(with: NSRange(location: cursor, length: match.range.location - cursor))
            let inner = source.substring(with: match.range(at: 2))
            let replaced = transform(inner)
            output += replaced.isEmpty ? "none" : "url(\"\(replaced)\")"
            cursor = match.range.location + match.range.length
        }
        output += source.substring(from: cursor)
        return output
    }

    struct ProcessedChapter {
        var html: String
        /// Distinct remote assets in order, with the local filename assigned to each.
        var assets: [(url: String, filename: String)]
    }

    /// Cleans the episode HTML: resolves lazy/picture images, assigns local filenames, strips scripts.
    static func processChapterHTML(_ raw: String, chapterURL: URL, chapterIndex: Int) throws -> ProcessedChapter? {
        let document = try WebHTML.parse(raw, baseURL: chapterURL)
        guard let body = document.body() else { return nil }
        let lazyAttributes: [String] = ["data-original", "data-lazy-src", "data-src", "data-url", "data-image", "data-cfsrc"]
        let sourceAttributes: [String] = ["data-srcset", "srcset"]

        func chooseSource(_ image: Element) -> String {
            if let parent = image.parent(), parent.tagName().lowercased() == "picture" {
                for source in WebHTML.select("source", in: parent) {
                    for attribute in sourceAttributes {
                        let candidate = bestSrcset(WebHTML.attr(source, attribute))
                        if !candidate.isEmpty { return normalizeURL(candidate, base: chapterURL) }
                    }
                }
            }
            var candidate = bestSrcset(WebHTML.attr(image, "data-srcset"))
            if !candidate.isEmpty { return normalizeURL(candidate, base: chapterURL) }
            for attribute in lazyAttributes {
                candidate = WebHTML.attr(image, attribute)
                if !candidate.isEmpty { return normalizeURL(candidate, base: chapterURL) }
            }
            candidate = bestSrcset(WebHTML.attr(image, "srcset"))
            if !candidate.isEmpty { return normalizeURL(candidate, base: chapterURL) }
            return normalizeURL(WebHTML.attr(image, "src"), base: chapterURL)
        }

        var filenames: [String: String] = [:]
        var ordered: [(url: String, filename: String)] = []
        func register(_ url: String) -> String {
            if let existing = filenames[url] { return existing }
            let number = ordered.count + 1
            let name = String(format: "images/%04d_%02d.", chapterIndex, number) + imageExtension(for: url)
            filenames[url] = name
            ordered.append((url: url, filename: name))
            return name
        }

        for image in WebHTML.select("img", in: body) {
            let source = chooseSource(image)
            if !source.isEmpty {
                _ = try image.attr("src", register(source))
            }
            for attribute in lazyAttributes + sourceAttributes { _ = try image.removeAttr(attribute) }
        }
        WebHTML.removeAll("source", in: body)
        for picture in WebHTML.select("picture", in: body) {
            let inner = try picture.html()
            _ = try picture.before(inner)
            try picture.remove()
        }
        for element in WebHTML.select("[style]", in: body) {
            let style = WebHTML.attr(element, "style")
            let rewritten = rewriteBackgrounds(style) { (raw: String) -> String in
                let normalized = normalizeURL(raw, base: chapterURL)
                return normalized.isEmpty ? "" : register(normalized)
            }
            if rewritten != style { _ = try element.attr("style", rewritten) }
        }
        WebHTML.removeAll("script, noscript, iframe", in: body)

        var html = try body.html().trimmingCharacters(in: .whitespacesAndNewlines)
        if html.isEmpty { return nil }
        html = WebRegex.replace("<(br|hr|img)((?:\\s[^>]*?)?)\\s*/?>", in: html, with: "<$1$2/>", options: [.caseInsensitive])
        html = WebRegex.replace("\\s+/>", in: html, with: "/>")
        html = html.replacingOccurrences(of: "&nbsp;", with: "&#160;")
        return ProcessedChapter(html: "<div class=\"global-npia-content\">" + html + "</div>", assets: ordered)
    }
}
