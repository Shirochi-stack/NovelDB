import Foundation
import NpiaCore

/// Pure parsing for npia pages and payloads, ported from `downloader_core.py`,
/// `gui.py` (`extract_chapter_content_and_images`), and `external_scraper.py`
/// (`_npia_parse_episode_html`). No networking, so every function is fixture-testable.
enum NpiaParser {
    static let host = "\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let imageHost = "images.\u{6e}\u{6f}\u{76}\u{65}\u{6c}\u{70}\u{69}\u{61}.com"
    static let hostPattern = #"\#u{6e}\#u{6f}\#u{76}\#u{65}\#u{6c}\#u{70}\#u{69}\#u{61}\.com"#
    static let base = URL(string: "https://\(host)/")!

    static func novelURL(_ id: String) -> URL { URL(string: "https://\(host)/novel/\(id)")! }
    static func viewerURL(_ id: String) -> URL { URL(string: "https://\(host)/viewer/\(id)")! }

    // MARK: - URLs

    /// Novel id from a `/novel/<id>` URL on the main site (not the global one).
    static func novelID(from url: URL) -> String? {
        let host = url.bareHost
        guard host == Self.host || (host.hasSuffix("." + Self.host) && host != "global." + Self.host) else { return nil }
        return NpiaRx.first(#"^/novel/(\d+)(?:/|$)"#, in: url.path)?[1]
    }

    // MARK: - Book page

    struct Metadata: Equatable {
        var title: String
        var author: String
        var coverURL: URL?
        var tags: [String]
        var synopsis: String
        var status: String?
    }

    static func parseMetadata(_ text: String, novelID: String) -> Metadata {
        let title: String
        if let match = NpiaRx.first(#"productName = '(.+?)';"#, in: text) {
            title = NpiaEntities.decode(match[1])
        } else if let match = NpiaRx.first(#"<meta\s+property=["']og:title["']\s+content=["'](.+?)["']"#, in: text, .caseInsensitive) {
            title = lastDashPart(NpiaEntities.decode(match[1]))
        } else if let match = NpiaRx.first(#"<title>(.+?)</title>"#, in: text, .caseInsensitive) {
            title = lastDashPart(NpiaEntities.decode(match[1]))
        } else {
            title = "Novel_\(novelID)"
        }

        let author = NpiaRx.first(#"<a class="writer-name"[^>]*>\s*(.+?)\s*</a>"#, in: text)
            .map { NpiaEntities.decode($0[1]).trimmingCharacters(in: .whitespacesAndNewlines) } ?? "Unknown Author"

        var tags: [String] = []
        var seen = Set<String>()
        for match in NpiaRx.all(#"<span class="tag".*?>(#.+?)</span>"#, in: text) {
            let raw = match[1]
            guard !raw.trimmingCharacters(in: .whitespaces).isEmpty else { continue }
            let tag = NpiaEntities.decode(String(raw.drop(while: { $0 == "#" })).trimmingCharacters(in: .whitespaces))
            if seen.insert(tag.lowercased()).inserted { tags.append(tag) }
        }

        return Metadata(
            title: title.trimmingCharacters(in: .whitespacesAndNewlines),
            author: author,
            coverURL: extractCoverURL(text).flatMap(URL.init(string:)),
            tags: tags,
            synopsis: parseSynopsis(text),
            status: parseStatus(text)
        )
    }

    private static func lastDashPart(_ full: String) -> String {
        let parts = full.components(separatedBy: " - ")
        return parts.count > 1 ? parts[parts.count - 1].trimmingCharacters(in: .whitespaces) : full
    }

    static func parseSynopsis(_ text: String) -> String {
        if let match = NpiaRx.first(#"<div[^>]*class=["'][^"']*\bsynopsis\b[^"']*["'][^>]*>(.*?)</div>"#, in: text, [.caseInsensitive, .dotMatchesLineSeparators]) {
            var syn = NpiaRx.replace(#"<\s*br\s*/?\s*>"#, in: match[1], with: "\n", .caseInsensitive)
            syn = NpiaRx.replace(#"</?[^>]+>"#, in: syn, with: " ")
            syn = NpiaEntities.decode(syn)
            syn = NpiaRx.replace(#"[ \t\f\u000b]+"#, in: syn, with: " ")
            syn = NpiaRx.replace(#"\n{3,}"#, in: syn, with: "\n\n")
            syn = syn.trimmingCharacters(in: .whitespacesAndNewlines)
            if !syn.isEmpty { return syn }
        }
        let meta = NpiaRx.first(#"<meta[^>]*name=["']description["'][^>]*content=(["'])(.*?)\1"#, in: text, [.caseInsensitive, .dotMatchesLineSeparators])
            .map { NpiaEntities.decode($0[2].trimmingCharacters(in: .whitespacesAndNewlines)) } ?? ""
        let og = NpiaRx.first(#"<meta[^>]*property=["']og:description["'][^>]*content=(["'])(.*?)\1"#, in: text, [.caseInsensitive, .dotMatchesLineSeparators])
            .map { NpiaEntities.decode($0[2].trimmingCharacters(in: .whitespacesAndNewlines)) } ?? ""
        return og.count > meta.count ? og : meta
    }

    /// `parse_npia_status`: the completion badge inside `.epnew-novel-info .in-badge`, or nil
    /// when the page has no badge block (login/error page or unknown layout).
    static func parseStatus(_ text: String) -> String? {
        let ns = text as NSString
        guard let info = NpiaRx.matches(#"class=["'][^"']*\bepnew-novel-info\b"#, in: text, .caseInsensitive).first else { return nil }
        let after = ns.substring(from: info.range.location)
        guard let badge = NpiaRx.first(#"<(\w+)[^>]*class=["'][^"']*\bin-badge\b[^"']*["'][^>]*>(.*?)</\1>"#, in: after, [.caseInsensitive, .dotMatchesLineSeparators]) else {
            return nil
        }
        return NpiaRx.contains(#"class=["'][^"']*\bb_comp\b"#, in: badge[2], .caseInsensitive) ? "Completed" : "Ongoing"
    }

    // MARK: - Cover

    static func normalizeImageboxURL(_ value: String) -> String? {
        var value = NpiaEntities.decode(value).trimmingCharacters(in: .whitespacesAndNewlines)
        value = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
        value = value.replacingOccurrences(of: "\\/", with: "/")
        value = NpiaRx.replace(#"[\s,;)]+$"#, in: value, with: "")
        if value.hasPrefix("//") {
            value = "https:" + value
        } else if value.hasPrefix("/imagebox/") {
            value = "https://\(imageHost)" + value
        } else if value.hasPrefix("imagebox/") {
            value = "https://\(imageHost)/" + value
        }
        value = NpiaRx.replace(#"^https?://(?:image\.\#(hostPattern)|\#(hostPattern))/imagebox/"#, in: value, with: "https://\(imageHost)/imagebox/", .caseInsensitive)
        guard NpiaRx.contains(#"^https://images?\.\#(hostPattern)/imagebox/"#, in: value, .caseInsensitive) else { return nil }
        return value
    }

    /// `_extract_cover_url`: scores every candidate and prefers original covers over previews.
    static func extractCoverURL(_ text: String) -> String? {
        var candidates: [(score: Int, order: Int, url: String)] = []
        var seen: [String: Int] = [:]

        func add(_ raw: String, _ bonus: Int) {
            guard let url = normalizeImageboxURL(raw) else { return }
            let lower = url.lowercased()
            if lower.contains("readycover") { return }
            var score = bonus
            if lower.contains("/imagebox/original/") {
                score += 1000
            } else if lower.contains("/imagebox/cover/") {
                score += 500
            } else {
                score += 250
            }
            score += lower.contains("_q_") || lower.contains("_q_ori") ? -80 : 40
            if lower.hasSuffix(".wimg") { score -= 120 }
            if lower.contains("_ori.file") { score += 30 }
            if let index = seen[url] {
                if score > candidates[index].score { candidates[index].score = score }
                return
            }
            seen[url] = candidates.count
            candidates.append((score, candidates.count, url))
        }

        let dotAll: NSRegularExpression.Options = [.caseInsensitive, .dotMatchesLineSeparators]
        for tag in NpiaRx.all(#"<meta\b[^>]*>"#, in: text, dotAll).map({ $0[0] }) {
            guard NpiaRx.contains(#"\b(?:property|name)=['"](?:og:image|twitter:image|image)['"]"#, in: tag, .caseInsensitive) else { continue }
            if let match = NpiaRx.first(#"\bcontent=(['"])(.*?)\1"#, in: tag, dotAll) { add(match[2], 220) }
        }
        for tag in NpiaRx.all(#"<img\b[^>]*>"#, in: text, dotAll).map({ $0[0] }) {
            guard NpiaRx.contains(#"\bclass=(['"])[^'"]*\bcover_img\b"#, in: tag, .caseInsensitive) else { continue }
            if let match = NpiaRx.first(#"\bsrc=(['"])(.*?)\1"#, in: tag, dotAll) { add(match[2], 200) }
        }
        let ns = text as NSString
        for match in NpiaRx.matches(#"\bepnew-cover-box\b"#, in: text, .caseInsensitive) {
            let chunk = ns.substring(with: NSRange(location: match.range.location, length: min(1600, ns.length - match.range.location)))
            for attr in NpiaRx.all(#"\b(?:href|src)=(['"])(.*?)\1"#, in: chunk, dotAll) { add(attr[2], 120) }
        }
        for match in NpiaRx.all(#"\bimageUrl\s*=\s*(['"])(.*?)\1"#, in: text, dotAll) { add(match[2], 160) }

        let scan = text.replacingOccurrences(of: "\\/", with: "/")
        let imagebox = #"(?:https?:)?//(?:images?\.\#(hostPattern)|\#(hostPattern))/imagebox/(?:original|cover|[0-9a-f]{1,3})/[^\s"'<>\\)]+|/imagebox/(?:original|cover|[0-9a-f]{1,3})/[^\s"'<>\\)]+"#
        for match in NpiaRx.all(imagebox, in: scan, .caseInsensitive) { add(match[0], 0) }

        return candidates.max { a, b in a.score != b.score ? a.score < b.score : a.order > b.order }?.url
    }

    // MARK: - Chapter list and notices

    /// `_npia_parse_episode_html`: chapters from one `episode_list` page, with PLUS rows marked paid.
    static func parseEpisodePage(_ source: String) -> [(id: String, title: String, isPaid: Bool)] {
        let ns = source as NSString
        var result: [(id: String, title: String, isPaid: Bool)] = []
        var seen = Set<String>()
        for match in NpiaRx.matches(#"id=["']bookmark_(\d+)["'][^>]*>\s*</i>(.*?)</b>"#, in: source, [.caseInsensitive, .dotMatchesLineSeparators]) {
            let id = ns.substring(with: match.range(at: 1))
            guard seen.insert(id).inserted else { continue }
            let rawTitle = NpiaRx.replace(#"<[^>]+>"#, in: ns.substring(with: match.range(at: 2)), with: "")
            let title = NpiaEntities.decode(rawTitle).trimmingCharacters(in: .whitespacesAndNewlines)

            let before = ns.substring(to: match.range.location)
            let rowStart = (before as NSString).range(of: "<tr", options: .backwards).location
            let end = match.range.location + match.range.length
            let rowEndRange = ns.range(of: "</tr>", range: NSRange(location: end, length: ns.length - end))
            let start = rowStart == NSNotFound ? match.range.location : rowStart
            let stop = rowEndRange.location == NSNotFound ? end : rowEndRange.location + 5
            let row = ns.substring(with: NSRange(location: start, length: stop - start))
            let isPlus = NpiaRx.contains(#"(?:\bPLUS\b|플러스|b_plus|icon[-_]?plus)"#, in: row, .caseInsensitive)
            result.append((id, title.isEmpty ? "Chapter \(result.count + 1)" : title, isPlus))
        }
        return result
    }

    /// `parse_npia_notice_html`: author notices from the novel page, oldest first.
    static func parseNotices(_ source: String) -> [(id: String, title: String)] {
        guard let table = NpiaRx.first(#"<table\b[^>]*\bclass=["'][^"']*\bnotice_table\b[^"']*["'][^>]*>.*?</table>"#, in: source, [.caseInsensitive, .dotMatchesLineSeparators])?[0] else {
            return []
        }
        var matches = NpiaRx.all(#"location=['"]/viewer/(\d+)['"][^>]*><b>(.*?)</b>"#, in: table, .dotMatchesLineSeparators)
        if matches.isEmpty {
            matches = NpiaRx.all(#"href=['"]/viewer/(\d+)['"][^>]*><b>(.*?)</b>"#, in: table, .dotMatchesLineSeparators)
        }
        let notices = matches.map { match -> (id: String, title: String) in
            let clean = NpiaRx.replace(#"<.*?>"#, in: match[2], with: "").trimmingCharacters(in: .whitespacesAndNewlines)
            return (match[1], NpiaEntities.decode(clean.isEmpty ? "Notice" : "Notice: " + clean))
        }
        return notices.reversed()
    }

    // MARK: - Viewer payload

    enum PayloadProblem: Error, Equatable {
        /// The server rejected access (`잘못된 접근`, login or age verification).
        case blocked(String)
        /// An error page or malformed JSON; worth retrying.
        case invalid(String)
    }

    /// `validate_chapter_payload`: returns the `s` segment list or explains why it is unusable.
    static func validatePayload(_ text: String) -> Result<[[String: Any]], PayloadProblem> {
        let stripped = text.drop(while: { $0.isWhitespace })
        let probe = stripped.prefix(4096).lowercased()
        if probe.hasPrefix("<!doctype html") || probe.hasPrefix("<html") {
            return .failure(.invalid("HTTP 200 contained an HTML error page"))
        }
        guard let object = try? JSONSerialization.jsonObject(with: Data(stripped.utf8)) else {
            return .failure(.invalid("invalid viewer JSON"))
        }
        guard let data = object as? [String: Any] else {
            return .failure(.invalid("viewer JSON root is not an object"))
        }
        func message(_ fallback: String) -> String {
            for key in ["errmsg", "message", "error"] {
                if let value = data[key], !"\(value)".isEmpty, !(value is NSNull) { return "\(value)" }
            }
            return fallback
        }
        if isCode500(data["status"]) || isCode500(data["code"]) {
            let text = message("server error")
            if text.contains("잘못된 접근") { return .failure(.blocked("viewer access restricted: \(text)")) }
            return .failure(.invalid("viewer returned status/code 500: \(text)"))
        }
        guard let segments = data["s"] as? [Any] else {
            // Desktop checks the whole body for these words; only the non-content fields are
            // checked here so a chapter that merely mentions logging in is not refused.
            let text = message("missing chapter segment list ('s')")
            if isAccessWall(text) { return .failure(.blocked("login/age verification required: \(text)")) }
            return .failure(.invalid("viewer returned no chapter content: \(text)"))
        }
        return .success(segments.compactMap { $0 as? [String: Any] })
    }

    static func isAccessWall(_ text: String) -> Bool {
        text.contains("\u{bcf8}\u{c778}\u{c778}\u{c99d}") || text.contains("\u{b85c}\u{adf8}\u{c778}")
            || text.lowercased().contains("authentication required")
    }

    private static func isCode500(_ value: Any?) -> Bool {
        switch value {
        case let number as NSNumber: return number.intValue == 500
        case let string as String: return string == "500"
        default: return false
        }
    }

    struct RenderOptions {
        var stripLeadingSpaces = false
        var removeNewlines = false
        var saveImageURLsOnly = false
        /// Prefix for image file names, e.g. `0012` or `n0001`.
        var imagePrefix = "0001"
    }

    /// `extract_chapter_content_and_images`: segments to an XHTML body fragment plus the images
    /// it references. Images are not downloaded here; the pipeline fetches `ImageRef.url`.
    static func renderSegments(_ segments: [[String: Any]], options: RenderOptions) -> (html: String, images: [ImageRef]) {
        var parts: [String] = []
        var images: [ImageRef] = []
        let imgPattern = #"<img\b[^>]*\bsrc=(['"])(.*?)\1[^>]*>"#
        let dotAll: NSRegularExpression.Options = [.caseInsensitive, .dotMatchesLineSeparators]
        let zeroHeight = #"<p\s+style=['"]height:\s*0px;[^>]*>.*?</p>"#

        for segment in segments {
            guard let raw = segment["text"] as? String, !raw.isEmpty, !raw.contains("cover-wrapper") else { continue }
            var text = raw

            if NpiaRx.contains(imgPattern, in: text, dotAll) {
                text = NpiaRx.replace(imgPattern, in: text, dotAll) { groups in
                    let src = groups[2]
                    if unfetchableImageReason(src) != nil { return "" }
                    guard let urlString = normalizeChapterImageURL(src), let url = URL(string: urlString) else { return "" }
                    if options.saveImageURLsOnly {
                        return #"<img class="remote-image" alt="Image \#(images.count + 1)" src="\#(NpiaEntities.escape(urlString))"/>"#
                    }
                    let number = images.count + 1
                    let filename = "images/\(options.imagePrefix)_\(String(format: "%02d", number)).\(imageExtension(url))"
                    images.append(ImageRef(url: url, filename: filename))
                    return #"<img alt="\#(number)" src="\#(filename)" width="100%"/>"#
                }
                text = NpiaRx.replace(zeroHeight, in: text, with: "", dotAll)
                text = breaks(text, remove: options.removeNewlines)
                parts.append("<p>\(text)</p>")
                continue
            }

            text = NpiaRx.replace(zeroHeight, in: text, with: "", dotAll)
            // Keep inline formatting through tag stripping with placeholders.
            var preserved: [String] = []
            var tags = "b|strong|i|em|u|s|strike|del|sub|sup|ruby|rb|rt|rp|rtc"
            if !options.removeNewlines { tags += "|br" }
            text = NpiaRx.replace(#"</?(?:\#(tags))\b[^>]*>"#, in: text, .caseInsensitive) { groups in
                preserved.append(groups[0])
                return "\u{0}FMT\(preserved.count - 1)\u{0}"
            }
            // Only tags that start with an ASCII letter, so Korean text in angle brackets survives.
            text = NpiaRx.replace(#"</?[a-zA-Z][^>]*>"#, in: text, with: "")
            if options.removeNewlines {
                text = text.replacingOccurrences(of: "\r", with: "").replacingOccurrences(of: "\n", with: "")
                if text.replacingOccurrences(of: "\u{0}", with: "").trimmingCharacters(in: .whitespaces).isEmpty { continue }
            } else {
                text = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
                if text.isEmpty && preserved.isEmpty { continue }
            }
            text = NpiaEntities.decode(text)
            if options.stripLeadingSpaces {
                text = NpiaRx.replace(#"^(?:[ \t\f\u000b ]|&nbsp;)+"#, in: text, with: "")
            }
            guard !text.isEmpty else { continue }
            var safe = NpiaEntities.escape(text)
            if !options.removeNewlines { safe = safe.replacingOccurrences(of: "\n", with: "<br/>") }
            for (index, tag) in preserved.enumerated() {
                let restored = NpiaRx.contains(#"^</?br\b"#, in: tag, .caseInsensitive) ? "<br/>" : tag
                safe = safe.replacingOccurrences(of: "\u{0}FMT\(index)\u{0}", with: restored)
            }
            parts.append("<p>\(safe)</p>")
        }

        if parts.isEmpty { return ("<p>[No text segments found in chapter]</p>", images) }
        return (parts.joined(), images)
    }

    private static func breaks(_ text: String, remove: Bool) -> String {
        if remove {
            return NpiaRx.replace(#"<br\s*/?>"#, in: text, with: "", .caseInsensitive)
                .replacingOccurrences(of: "\r", with: "").replacingOccurrences(of: "\n", with: "")
        }
        return NpiaRx.replace(#"<br\s*/?>"#, in: text, with: "<br/>", .caseInsensitive)
            .replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
            .replacingOccurrences(of: "\n", with: "<br/>")
    }

    static let unfetchableSchemes: Set<String> = ["file", "data", "about", "blob", "javascript", "cid", "res", "chrome"]

    static func unfetchableImageReason(_ value: String) -> String? {
        let raw = NpiaEntities.decode(value).trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            .replacingOccurrences(of: "\\/", with: "/")
        if raw.isEmpty { return "empty image URL" }
        if NpiaRx.contains(#"^[A-Za-z]:[\\/]"#, in: raw) { return "local file path" }
        if let scheme = NpiaRx.first(#"^([A-Za-z][A-Za-z0-9+.-]*):"#, in: raw)?[1].lowercased(), unfetchableSchemes.contains(scheme) {
            return "non-web image source (\(scheme):)"
        }
        return nil
    }

    static func normalizeChapterImageURL(_ value: String) -> String? {
        var value = NpiaEntities.decode(value).trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            .replacingOccurrences(of: "\\/", with: "/")
        guard !value.isEmpty else { return nil }
        if value.hasPrefix("//") {
            value = "https:" + value
        } else if value.hasPrefix("/imagebox/") {
            value = "https://\(imageHost)" + value
        } else if value.hasPrefix("imagebox/") {
            value = "https://\(imageHost)/" + value
        } else if !NpiaRx.contains(#"^[A-Za-z][A-Za-z0-9+.-]*:"#, in: value) {
            guard let joined = URL(string: value, relativeTo: base)?.absoluteString else { return nil }
            value = joined
        }
        guard let url = URL(string: value), let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme), url.host != nil else {
            return nil
        }
        return value
    }

    /// Extension guessed from the URL; the pipeline rewrites it if recompression changes the format.
    static func imageExtension(_ url: URL) -> String {
        let ext = url.pathExtension.lowercased()
        switch ext {
        case "jpg", "jpeg", "png", "gif", "webp", "avif": return ext
        default: return "jpg"
        }
    }
}
