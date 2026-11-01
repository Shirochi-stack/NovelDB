import Foundation
import NpiaCore
import SwiftSoup

// Pure parsing/building helpers for the kpage source (port of the desktop `_kpage_*` helpers).

// MARK: - Models

struct KpageMeta: Equatable {
    var title: String
    var author: String
    var cover: String
    var description: String
}

struct KpageEpisode: Equatable {
    var productId: Int
    var order: Int
    var shortName: String
    var fullName: String
    var isFree: Bool
    var isAccessible: Bool
    var slideType: String
}

struct KpageImageFile: Equatable {
    var no: Int
    var url: String
    var width: Int
    var height: Int
    var filename: String
}

enum KpageViewerOutcome: Equatable {
    case locked(reason: String)
    case unavailable(reason: String)
    case images([KpageImageFile])
    case text(baseURL: String, secureURLs: [String])
}

struct KpageParagraph {
    var plain: String
    var html: String
    var type: String
    var style: [String: Any]
    var attrs: [String: Any]
    var chunkIndex: Int
    var contentId: Int
    var paragraphId: Int
}

// MARK: - Loose JSON helpers (Python truthiness semantics)

enum KpageJSON {
    static func isBool(_ value: Any) -> Bool {
        if let number = value as? NSNumber { return CFGetTypeID(number) == CFBooleanGetTypeID() }
        return false
    }

    static func isNumber(_ value: Any) -> Bool {
        if value is String { return false }
        if let number = value as? NSNumber { return CFGetTypeID(number) != CFBooleanGetTypeID() }
        return false
    }

    static func truthy(_ value: Any?) -> Bool {
        guard let value = value, !(value is NSNull) else { return false }
        if let text = value as? String { return !text.isEmpty }
        if let number = value as? NSNumber { return number.doubleValue != 0 }
        if let list = value as? [Any] { return !list.isEmpty }
        if let map = value as? [String: Any] { return !map.isEmpty }
        return true
    }

    /// `str(value)` for scalars; empty for anything else.
    static func string(_ value: Any?) -> String {
        guard let value = value, !(value is NSNull) else { return "" }
        if let text = value as? String { return text }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue ? "True" : "False" }
            if CFNumberIsFloatType(number as CFNumber) { return "\(number.doubleValue)" }
            return "\(number.intValue)"
        }
        return ""
    }

    static func int(_ value: Any?) -> Int {
        guard let value = value, !(value is NSNull) else { return 0 }
        if let text = value as? String { return Int(text.trimmingCharacters(in: .whitespaces)) ?? 0 }
        if let number = value as? NSNumber { return number.intValue }
        return 0
    }

    static func dict(_ value: Any?) -> [String: Any]? {
        value as? [String: Any]
    }

    /// First truthy raw value among `keys` (Python `a or b or c`).
    static func firstTruthy(_ map: [String: Any], _ keys: [String]) -> Any? {
        for key in keys {
            if truthy(map[key]) { return map[key] }
        }
        return nil
    }

    static func unescape(_ text: String) -> String {
        (try? Parser.unescapeEntities(text, false)) ?? text
    }

    static func data(_ text: String) -> Data {
        Data(text.utf8)
    }
}

// MARK: - Parsing namespace

enum KpageParsing {
    // MARK: URL

    static func seriesID(from url: String) -> String? {
        WebRegex.capture(#"/content/(\d+)"#, in: url)
    }

    static func viewerIDs(from url: String) -> (series: String, product: String)? {
        guard let groups = WebRegex.firstMatch(#"/content/(\d+)/viewer/(\d+)"#, in: url),
              groups.count > 2, let series = groups[1], let product = groups[2] else { return nil }
        return (series, product)
    }

    // MARK: Book metadata

    static func cleanTitle(_ raw: String) -> String {
        let cleaned = WebRegex.replace(
            "\\s*[-–]\\s*(웹소설|웹툰).*$", in: raw, with: "",
            options: [.caseInsensitive])
        return cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func parseMetadata(html: String) -> KpageMeta {
        guard let doc = try? WebHTML.parse(html) else {
            return KpageMeta(title: "", author: "", cover: "", description: "")
        }
        func og(_ property: String) -> String {
            WebHTML.attr(WebHTML.selectFirst("meta[property=og:\(property)]", in: doc), "content")
        }
        var title = cleanTitle(og("title"))
        if title.isEmpty, let h2 = WebHTML.selectFirst("h2", in: doc) {
            title = WebHTML.text(h2)
        }
        let author = findAuthor(doc: doc, title: title)
        return KpageMeta(title: title, author: author, cover: og("image"), description: og("description"))
    }

    private static func findAuthor(doc: Document, title: String) -> String {
        for element in WebHTML.select("span, div, a", in: doc) {
            guard let previous = try? element.previousElementSibling() else { continue }
            let text = WebHTML.text(element)
            let previousIsTitle = previous.tagName().lowercased() == "h2"
                || WebHTML.text(previous) == title
            if previousIsTitle, !text.isEmpty, text.count < 50, !text.contains("|") {
                return text
            }
        }
        // Fallback: the first short line after the title in the page text.
        guard !title.isEmpty, let body = doc.body() else { return "" }
        let bodyText = WebHTML.lines(body).joined(separator: "\n")
        guard let range = bodyText.range(of: title) else { return "" }
        let after = String(bodyText[range.upperBound...].prefix(200))
        let lines = after.components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        if let first = lines.first, first.count < 50 { return first }
        return ""
    }

    // MARK: Episode list

    static func titleNumber(_ title: String) -> Int {
        guard let value = WebRegex.capture("(\\d+)\\s*화", in: title) else { return 0 }
        return Int(value) ?? 0
    }

    /// Port of `hasUserAccessFlag` from the in-page episode script.
    static func hasUserAccessFlag(_ object: Any) -> Bool {
        walkAccess(object, path: "")
    }

    private static let skipKeyPattern = "price|count|blocked|lock|free_change"
    private static let accessKeyPattern = "purchas|bought|owned|rented|rental|ticket|pass|usable|useable|access|viewable|readable"
    private static let accessTruePattern = "^(yes|y|true|1|buy|bought|purchase|purchased|yes[_-]?purchase|yes[_-]?purchased|rent|rented|rental|yes[_-]?rent|yes[_-]?rented|ticket|pass|owned|available|viewable|readable|accessible)$"
    private static let accessFalsePattern = "^(false|no|n|none|null|0|not[_-]?purchased|not[_-]?rented|no[_-]?purchase|no[_-]?rent|unowned|unavailable|expired)$"

    private static func walkAccess(_ value: Any, path: String) -> Bool {
        var entries: [(String, Any)] = []
        if let map = value as? [String: Any] {
            entries = map.map { ($0.key, $0.value) }
        } else if let list = value as? [Any] {
            entries = list.enumerated().map { (String($0.offset), $0.element) }
        } else {
            return false
        }
        for (rawKey, item) in entries {
            let key = rawKey.lowercased()
            let full = path.isEmpty ? key : "\(path).\(key)"
            if WebRegex.matches(skipKeyPattern, in: full) { continue }
            if WebRegex.matches(accessKeyPattern, in: full) {
                if KpageJSON.isBool(item) {
                    if (item as? NSNumber)?.boolValue == true { return true }
                } else if KpageJSON.isNumber(item) {
                    if ((item as? NSNumber)?.doubleValue ?? 0) > 0 { return true }
                } else if let text = item as? String {
                    let lowered = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                    if WebRegex.matches(accessTruePattern, in: lowered) { return true }
                    if WebRegex.matches(accessFalsePattern, in: lowered) { continue }
                }
            }
            if item is [String: Any] || item is [Any] {
                if walkAccess(item, path: full) { return true }
            }
        }
        return false
    }

    static func parseEpisodes(json: Data) -> [KpageEpisode] {
        guard let root = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] else { return [] }
        let result = KpageJSON.dict(root["result"]) ?? [:]
        let list = (result["list"] as? [Any]) ?? []
        var episodes: [KpageEpisode] = []
        for entry in list {
            guard let map = entry as? [String: Any] else { continue }
            let item = KpageJSON.dict(map["item"]) ?? [:]
            let productId = KpageJSON.int(item["product_id"])
            if productId == 0 { continue }
            let order = KpageJSON.int(item["order_value"])
            let fullTitle = KpageJSON.string(item["title"])
            var shortName = fullTitle
            if let match = WebRegex.capture("([0-9]+화.*)$", in: fullTitle) {
                shortName = match
            } else if fullTitle.isEmpty {
                shortName = "Episode \(order)"
            }
            let isFree = KpageJSON.truthy(item["is_free"])
            let accessible = isFree || hasUserAccessFlag(item)
            episodes.append(KpageEpisode(
                productId: productId, order: order, shortName: shortName, fullName: fullTitle,
                isFree: isFree, isAccessible: accessible, slideType: KpageJSON.string(item["slide_type"])))
        }
        return sortEpisodes(episodes)
    }

    static func sortKey(_ episode: KpageEpisode) -> (Int, Int) {
        var order = episode.order
        if order == 0 {
            order = titleNumber(episode.fullName.isEmpty ? episode.shortName : episode.fullName)
        }
        return (order == 0 ? 1_000_000_000_000 : order, episode.productId)
    }

    /// Oldest to newest; stable for equal keys.
    static func sortEpisodes(_ episodes: [KpageEpisode]) -> [KpageEpisode] {
        let indexed = episodes.enumerated().map { (offset: $0.offset, episode: $0.element) }
        let sorted = indexed.sorted { lhs, rhs in
            let a = sortKey(lhs.episode)
            let b = sortKey(rhs.episode)
            if a.0 != b.0 { return a.0 < b.0 }
            if a.1 != b.1 { return a.1 < b.1 }
            return lhs.offset < rhs.offset
        }
        return sorted.map { $0.episode }
    }

    // MARK: Viewer data

    static func parseViewer(_ data: Data, status: Int) -> KpageViewerOutcome {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return .unavailable(reason: "non-json response")
        }
        var roots: [[String: Any]] = [root]
        for key in ["result", "data", "body"] {
            if let sub = root[key] as? [String: Any] { roots.append(sub) }
        }
        func findProp(_ names: [String]) -> Any? {
            for candidate in roots {
                for name in names {
                    if let value = candidate[name], !(value is NSNull) { return value }
                }
            }
            return nil
        }
        let viewerData = KpageJSON.dict(findProp(["viewerData", "viewer_data"])) ?? [:]
        let rawBase = KpageJSON.string(viewerData["atsServerUrl"] ?? viewerData["ats_server_url"])
        var baseURL = rawBase
        if rawBase.lowercased().hasPrefix("http:") {
            baseURL = "https:" + String(rawBase.dropFirst(5))
        }
        let contents: [Any] = (viewerData["contentsList"] as? [Any])
            ?? (viewerData["contents_list"] as? [Any])
            ?? (viewerData["contents"] as? [Any]) ?? []
        let imageData = KpageJSON.dict(viewerData["imageDownloadData"])
            ?? KpageJSON.dict(viewerData["image_download_data"]) ?? [:]
        let imageFiles: [Any] = (imageData["files"] as? [Any])
            ?? (imageData["fileList"] as? [Any])
            ?? (imageData["file_list"] as? [Any])
            ?? (viewerData["imageFiles"] as? [Any])
            ?? (viewerData["image_files"] as? [Any]) ?? []

        let message = findProp(["message", "msg"]) ?? root["message"] ?? root["msg"]
        let messageKey = findProp(["message_key", "messageKey"]) ?? root["message_key"] ?? root["messageKey"]
        let resultCode = findProp(["result_code", "resultCode"]) ?? root["result_code"] ?? root["resultCode"]
        let messageText = KpageJSON.string(message)
        let keyText = KpageJSON.string(messageKey)
        let accessText = "\(messageText) \(keyText)".lowercased()
        let denyPattern = "(not[_-]?purchased|purchase|rental|login|adult|age|auth|permission|forbidden|locked|구매|대여|로그인|성인|권한|인증|열람)"
        if status == 401 || status == 403 || WebRegex.matches(denyPattern, in: accessText, options: [.caseInsensitive]) {
            let reason = !keyText.isEmpty ? keyText : (!messageText.isEmpty ? messageText : "access denied")
            return .locked(reason: reason)
        }

        if (baseURL.isEmpty || contents.isEmpty) && imageFiles.isEmpty {
            let code = KpageJSON.string(resultCode)
            return .unavailable(reason: code.isEmpty
                ? "missing viewer resources"
                : "api returned no viewer resources (code \(code))")
        }

        if !imageFiles.isEmpty {
            var files: [KpageImageFile] = []
            for (offset, entry) in imageFiles.enumerated() {
                guard let map = entry as? [String: Any] else { continue }
                let url = KpageJSON.string(map["secureUrl"] ?? map["secure_url"])
                if url.isEmpty { continue }
                let number = KpageJSON.int(map["no"])
                files.append(KpageImageFile(
                    no: number != 0 ? number : offset + 1,
                    url: url,
                    width: KpageJSON.int(map["width"]),
                    height: KpageJSON.int(map["height"]),
                    filename: WebRegex.capture("filename=([^&]+)", in: url) ?? ""))
            }
            return .images(files)
        }

        var urls: [String] = []
        for entry in contents {
            guard let map = entry as? [String: Any] else { continue }
            let secure = KpageJSON.string(map["secureUrl"] ?? map["secure_url"])
            if !secure.isEmpty { urls.append(secure) }
        }
        return .text(baseURL: baseURL, secureURLs: urls)
    }

    // MARK: Paragraph extraction

    private static func styleToCSS(_ style: [String: Any]) -> String {
        var parts: [String] = []
        var color = KpageJSON.string(KpageJSON.firstTruthy(style, ["color", "fontColor", "textColor"]))
        if !color.isEmpty {
            if !color.hasPrefix("#") && !color.hasPrefix("rgb") { color = "#" + color }
            parts.append("color:\(color)")
        }
        var background = KpageJSON.string(KpageJSON.firstTruthy(style, ["backgroundColor", "highlight"]))
        if !background.isEmpty {
            if !background.hasPrefix("#") && !background.hasPrefix("rgb") { background = "#" + background }
            parts.append("background-color:\(background)")
        }
        if let size = KpageJSON.firstTruthy(style, ["fontSize", "size"]) {
            parts.append(KpageJSON.isNumber(size)
                ? "font-size:\(KpageJSON.string(size))px"
                : "font-size:\(KpageJSON.string(size))")
        }
        let align = KpageJSON.string(KpageJSON.firstTruthy(style, ["textAlign", "align"]))
        if !align.isEmpty { parts.append("text-align:\(align.lowercased())") }
        let decoration = KpageJSON.string(KpageJSON.firstTruthy(
            style, ["textDecoration", "text-decoration", "textDecorationLine", "text-decoration-line"])).lowercased()
        let lineThrough = KpageJSON.truthy(style["lineThrough"]) || KpageJSON.truthy(style["line-through"])
            || KpageJSON.truthy(style["strikethrough"]) || KpageJSON.truthy(style["strike"])
            || decoration.contains("line-through")
        if lineThrough { parts.append("text-decoration:line-through") }
        return parts.joined(separator: "; ")
    }

    private static func applyTags(_ fragment: String, _ style: [String: Any]) -> String {
        var result = fragment
        if KpageJSON.truthy(style["bold"]) || (style["fontWeight"] as? String) == "bold" {
            result = "<b>\(result)</b>"
        }
        if KpageJSON.truthy(style["italic"]) || (style["fontStyle"] as? String) == "italic" {
            result = "<i>\(result)</i>"
        }
        if KpageJSON.truthy(style["underline"]) {
            result = "<u>\(result)</u>"
        }
        return result
    }

    private static func wrapWithStyle(_ fragment: String, _ style: [String: Any], _ attrs: [String: Any]?) -> String {
        var cssParts: [String] = []
        let attrStyle = KpageJSON.string(attrs?["style"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if !attrStyle.isEmpty { cssParts.append(rstripSemicolons(attrStyle)) }
        let css = styleToCSS(style)
        if !css.isEmpty { cssParts.append(css) }
        var result = applyTags(fragment, style)
        var attrParts = ""
        if let attrs = attrs {
            let cls = KpageJSON.string(KpageJSON.firstTruthy(attrs, ["class", "className"]))
            if !cls.isEmpty { attrParts += " class=\"\(WebHTML.escape(cls))\"" }
        }
        if !cssParts.isEmpty { attrParts += " style=\"\(WebHTML.escape(cssParts.joined(separator: "; ")))\"" }
        if !attrParts.isEmpty { result = "<span\(attrParts)>\(result)</span>" }
        return result
    }

    private static func rstripSemicolons(_ text: String) -> String {
        var value = text
        while value.hasSuffix(";") { value.removeLast() }
        return value
    }

    private static func safeEscape(_ text: String) -> String {
        WebHTML.escape(KpageJSON.unescape(text))
    }

    private static func children(of node: [String: Any]) -> [[String: Any]] {
        ((node["childParagraphList"] as? [Any]) ?? []).compactMap { $0 as? [String: Any] }
    }

    private static func collectHTML(_ node: [String: Any], isRoot: Bool) -> String {
        let nodeType = KpageJSON.string(node["type"]).uppercased()
        if nodeType == "BR" { return "<br />" }
        if nodeType == "IMG" { return "" }
        let text = (node["text"] as? String) ?? ""
        let kids = children(of: node)
        let style = KpageJSON.dict(node["style"]) ?? [:]
        let attrs = KpageJSON.dict(node["attributes"]) ?? [:]
        let strike = nodeType == "S" || nodeType == "STRIKE" || nodeType == "DEL"

        if !text.isEmpty && kids.isEmpty {
            var fragment = wrapWithStyle(safeEscape(text), style, isRoot ? nil : attrs)
            if strike { fragment = "<s>\(fragment)</s>" }
            return fragment
        }
        var parts: [String] = []
        if !text.isEmpty { parts.append(safeEscape(text)) }
        for child in kids {
            let piece = collectHTML(child, isRoot: false)
            if !piece.isEmpty { parts.append(piece) }
        }
        var result = wrapWithStyle(parts.joined(), style, isRoot ? nil : attrs)
        if strike { result = "<s>\(result)</s>" }
        return result
    }

    private static func collectText(_ node: [String: Any]) -> [String] {
        var parts: [String] = []
        let text = ((node["text"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty { parts.append(KpageJSON.unescape(text)) }
        for child in children(of: node) { parts.append(contentsOf: collectText(child)) }
        return parts
    }

    private static func nodeHasImage(_ node: [String: Any]) -> Bool {
        if KpageJSON.string(node["type"]).uppercased() == "IMG" { return true }
        return children(of: node).contains { nodeHasImage($0) }
    }

    /// Port of `_kpage_extract_from_json` (without the CSS download; see the adapter notes).
    static func extractParagraphs(_ chunks: [Data], skipLastPage: Bool, keepFiller: Bool) -> [KpageParagraph] {
        var parsed: [(index: Int, paragraphs: [KpageParagraph])] = []
        for (chunkIndex, raw) in chunks.enumerated() {
            guard let root = (try? JSONSerialization.jsonObject(with: raw)) as? [String: Any] else { continue }
            let info = KpageJSON.dict(root["contentInfo"]) ?? [:]
            let contentId = KpageJSON.int(info["contentId"])
            let list = (info["paragraphList"] as? [Any]) ?? []
            var chunkParagraphs: [KpageParagraph] = []
            for entry in list {
                guard let paragraph = entry as? [String: Any] else { continue }
                let plain = collectText(paragraph).joined()
                let fragment = collectHTML(paragraph, isRoot: true)
                let hasText = !plain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                if nodeHasImage(paragraph) && !hasText { continue }
                if hasText || fragment.lowercased().contains("<br") {
                    chunkParagraphs.append(KpageParagraph(
                        plain: plain, html: fragment,
                        type: KpageJSON.string(paragraph["type"]).uppercased(),
                        style: KpageJSON.dict(paragraph["style"]) ?? [:],
                        attrs: KpageJSON.dict(paragraph["attributes"]) ?? [:],
                        chunkIndex: chunkIndex, contentId: contentId,
                        paragraphId: KpageJSON.int(paragraph["id"])))
                }
            }
            if !chunkParagraphs.isEmpty { parsed.append((chunkIndex, chunkParagraphs)) }
        }

        var skipLastIndex: Int?
        if skipLastPage && !keepFiller && parsed.count > 1 {
            skipLastIndex = parsed[parsed.count - 1].index
        }
        var all: [KpageParagraph] = []
        for entry in parsed {
            if !keepFiller {
                if let skip = skipLastIndex, skip == entry.index { continue }
                let combined = entry.paragraphs.map { $0.plain }.joined(separator: " ")
                if isColophonChunk(combined) { continue }
            }
            all.append(contentsOf: entry.paragraphs)
        }
        let indexed = all.enumerated().map { (offset: $0.offset, paragraph: $0.element) }
        let sorted = indexed.sorted { lhs, rhs in
            let a = lhs.paragraph
            let b = rhs.paragraph
            if a.chunkIndex != b.chunkIndex { return a.chunkIndex < b.chunkIndex }
            if a.contentId != b.contentId { return a.contentId < b.contentId }
            if a.paragraphId != b.paragraphId { return a.paragraphId < b.paragraphId }
            return lhs.offset < rhs.offset
        }
        return sorted.map { $0.paragraph }
    }

    // MARK: Colophon

    static func isColophonChunk(_ text: String) -> Bool {
        let compact = text.components(separatedBy: .whitespacesAndNewlines).joined()
        let lower = text.lowercased()
        let lowerCompact = compact.lowercased()
        let strong = ["ISBN", "UCI", "ⓒ", "©", "저작권법", "재가공할 수 없습니다", "서면 허락", "이 책의 내용을 이용하지 못합니다"]
        let publisher = ["발행인", "발행처", "펴낸곳", "펴낸 곳", "기획/편집", "기획 / 편집", "책임편집", "표지", "주소"]
        let contact = ["블로그", "트위터", "투고", "blog.\u{6e}\u{61}\u{76}\u{65}\u{72}.com", "dreambook", "samyangcnc"]

        func has(_ marker: String) -> Bool {
            let markerCompact = marker.components(separatedBy: .whitespacesAndNewlines).joined()
            return text.contains(marker) || compact.contains(markerCompact)
                || lower.contains(marker.lowercased()) || lowerCompact.contains(markerCompact.lowercased())
        }
        let strongHits = strong.filter { has($0) }.count
        let publisherHits = publisher.filter { has($0) }.count
        let contactHits = contact.filter { has($0) }.count
        if strongHits >= 1 && (publisherHits + contactHits) >= 2 { return true }
        if publisherHits >= 3 && contactHits >= 1 { return true }
        if publisherHits >= 2 && contactHits >= 2 { return true }
        return false
    }

    // MARK: Output

    /// `_kpage_strip_headings`: drops leading blank and "제N화" heading paragraphs.
    static func stripHeadings(_ paragraphs: [KpageParagraph]) -> [KpageParagraph] {
        var cleaned = paragraphs
        while let first = cleaned.first {
            let text = first.plain.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty || text == "&nbsp;" {
                cleaned.removeFirst()
            } else if WebRegex.matches("^제\\d+화\\.?$", in: text) {
                cleaned.removeFirst()
            } else {
                break
            }
        }
        return cleaned
    }

    static func headingTitle(_ paragraphs: [KpageParagraph], fallback: String) -> String {
        let headingTypes: Set<String> = ["h1", "h2", "h3", "h4", "h5", "h6", "head", "heading", "title"]
        for paragraph in paragraphs {
            let plain = paragraph.plain.trimmingCharacters(in: .whitespacesAndNewlines)
            if !plain.isEmpty && headingTypes.contains(paragraph.type.lowercased()) { return plain }
        }
        return fallback
    }

    private static func tagFor(_ type: String) -> String {
        let tag = type.lowercased()
        let direct: Set<String> = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "blockquote", "li"]
        if direct.contains(tag) { return tag }
        if tag == "head" || tag == "heading" || tag == "title" { return "h3" }
        return "p"
    }

    private static func attrsToHTML(_ attrs: [String: Any], _ style: [String: Any], extraClass: String) -> String {
        var result = ""
        var cls = KpageJSON.string(KpageJSON.firstTruthy(attrs, ["class", "className"]))
        if !extraClass.isEmpty {
            cls = "\(cls) \(extraClass)".trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if !cls.isEmpty { result += " class=\"\(WebHTML.escape(cls))\"" }

        var parts: [String] = []
        let rawStyle = KpageJSON.string(attrs["style"]).trimmingCharacters(in: .whitespacesAndNewlines)
        if !rawStyle.isEmpty { parts.append(rstripSemicolons(rawStyle)) }
        let attrAlign = KpageJSON.string(attrs["align"])
        if !attrAlign.isEmpty { parts.append("text-align:\(attrAlign.lowercased())") }

        var color = KpageJSON.string(KpageJSON.firstTruthy(style, ["color", "fontColor", "textColor"]))
        if !color.isEmpty {
            if !color.hasPrefix("#") && !color.hasPrefix("rgb") { color = "#" + color }
            parts.append("color:\(color)")
        }
        var background = KpageJSON.string(KpageJSON.firstTruthy(style, ["backgroundColor", "highlight"]))
        if !background.isEmpty {
            if !background.hasPrefix("#") && !background.hasPrefix("rgb") { background = "#" + background }
            parts.append("background-color:\(background)")
        }
        if let size = KpageJSON.firstTruthy(style, ["fontSize", "size"]) {
            parts.append(KpageJSON.isNumber(size)
                ? "font-size:\(KpageJSON.string(size))px"
                : "font-size:\(KpageJSON.string(size))")
        }
        let textAlign = KpageJSON.string(KpageJSON.firstTruthy(style, ["textAlign", "align"]))
        if !textAlign.isEmpty { parts.append("text-align:\(textAlign.lowercased())") }
        if KpageJSON.truthy(style["lineThrough"]) || KpageJSON.truthy(style["strikethrough"])
            || KpageJSON.truthy(style["strike"]) {
            parts.append("text-decoration:line-through")
        }
        let nonEmpty = parts.filter { !$0.isEmpty }
        if !nonEmpty.isEmpty { result += " style=\"\(WebHTML.escape(nonEmpty.joined(separator: "; ")))\"" }
        return result
    }

    static let pageBreak = "<div class=\"kpage-page-break\">&#160;</div>"

    /// `_kpage_build_output`: plain text and XHTML fragment.
    static func buildOutput(_ paragraphs: [KpageParagraph]) -> (text: String, html: String) {
        var textParts: [String] = []
        var htmlParts: [String] = []
        var previousChunk: Int?
        var firstHeadingSeen = false
        var pendingHeadingBreak = false
        var insertedHeadingBreak = false

        func appendBreak() {
            if !htmlParts.isEmpty && htmlParts[htmlParts.count - 1] != pageBreak {
                htmlParts.append(pageBreak)
            }
        }

        for paragraph in paragraphs {
            let hasText = !paragraph.plain.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            let hasBreak = paragraph.html.lowercased().contains("<br")
            if !hasText && !hasBreak { continue }
            let tag = tagFor(paragraph.type)
            if let previous = previousChunk, previous != paragraph.chunkIndex {
                appendBreak()
            }
            let isBlank = !hasText && hasBreak
            let isHeading = ["h1", "h2", "h3", "h4", "h5", "h6"].contains(tag)
            if pendingHeadingBreak && !insertedHeadingBreak && !isBlank && !isHeading {
                appendBreak()
                insertedHeadingBreak = true
                pendingHeadingBreak = false
            }
            var extraClass = ""
            if isHeading && !firstHeadingSeen {
                firstHeadingSeen = true
                pendingHeadingBreak = true
                extraClass = "kpage-source-heading"
            }
            textParts.append(paragraph.plain)
            let attributes = attrsToHTML(paragraph.attrs, paragraph.style, extraClass: extraClass)
            htmlParts.append("<\(tag)\(attributes)>\(paragraph.html)</\(tag)>")
            previousChunk = paragraph.chunkIndex
        }
        return (textParts.joined(separator: "\n"), htmlParts.joined(separator: "\n"))
    }

    // MARK: Image chapters

    private static func pad(_ number: Int, _ width: Int) -> String {
        let text = String(number)
        return text.count >= width ? text : String(repeating: "0", count: width - text.count) + text
    }

    /// Output file extension for an image file entry (defaults to jpg).
    static func imageExtension(_ file: KpageImageFile) -> String {
        var name = file.filename.removingPercentEncoding ?? file.filename
        name = name.components(separatedBy: "?")[0].components(separatedBy: "&")[0]
        name = WebRegex.replace("[^A-Za-z0-9._-]+", in: name, with: "_")
        name = name.trimmingCharacters(in: CharacterSet(charactersIn: "._"))
        guard let dot = name.lastIndex(of: ".") else { return "jpg" }
        let ext = String(name[name.index(after: dot)...]).lowercased()
        if !ext.isEmpty, ext.count <= 5, WebRegex.matches("^[a-z0-9]+$", in: ext) { return ext }
        return "jpg"
    }

    /// `_kpage_build_image_chapter`: html and images with local file names.
    static func buildImageChapter(_ files: [KpageImageFile], chapterName: String, chapterIndex: Int, baseURL: URL? = nil) -> (html: String, images: [ImageRef])? {
        let ordered = files.sorted { lhs, rhs in
            lhs.no != rhs.no ? lhs.no < rhs.no : lhs.url < rhs.url
        }
        var images: [ImageRef] = []
        var htmlParts: [String] = ["<div class=\"kpage-image-chapter\" style=\"text-align:center\">"]
        for (offset, file) in ordered.enumerated() {
            let position = offset + 1
            let imageURLText = KpageJSON.unescape(file.url.trimmingCharacters(in: .whitespacesAndNewlines))
            if imageURLText.isEmpty { continue }
            var resolved = URL(string: imageURLText)
            if resolved == nil, let encoded = imageURLText.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.union(CharacterSet(charactersIn: "%#"))) {
                resolved = URL(string: encoded)
            }
            if let candidate = resolved, candidate.scheme == nil, let base = baseURL {
                resolved = WebHTML.join(base, imageURLText)
            }
            guard let imageURL = resolved else { continue }
            let local = "images/\(pad(chapterIndex, 4))_\(pad(position, 2)).\(imageExtension(file))"
            images.append(ImageRef(url: imageURL, filename: local))
            let alt = "\(chapterName) image \(position)"
            var sizeAttrs = ""
            if file.width > 0 { sizeAttrs += " width=\"\(file.width)\"" }
            if file.height > 0 { sizeAttrs += " height=\"\(file.height)\"" }
            htmlParts.append(
                "<div class=\"kpage-image-page\" style=\"margin:0 auto 0.5rem\">"
                + "<img src=\"\(WebHTML.escape(local))\" alt=\"\(WebHTML.escape(alt))\"\(sizeAttrs)"
                + " style=\"display:block;max-width:100%;height:auto;margin:0 auto\"/></div>")
        }
        htmlParts.append("</div>")
        if images.isEmpty { return nil }
        return (htmlParts.joined(separator: "\n"), images)
    }

    // MARK: Duplicate boilerplate images

    private static func removeImagePage(_ html: String, filename: String) -> String {
        let escaped = NSRegularExpression.escapedPattern(for: WebHTML.escape(filename))
        let wrapper = "\\s*<div class=\"kpage-image-page\"[^>]*>\\s*<img\\b[^>]*\\bsrc=[\"']\(escaped)[\"'][^>]*/?>\\s*</div>"
        var result = WebRegex.replace(wrapper, in: html, with: "", options: [.caseInsensitive])
        let bare = "\\s*<img\\b[^>]*\\bsrc=[\"']\(escaped)[\"'][^>]*/?>"
        result = WebRegex.replace(bare, in: result, with: "", options: [.caseInsensitive])
        return result
    }

    /// Port of the desktop duplicate cover/boilerplate image removal: images among the first
    /// `maxPositions` of a chapter whose bytes repeat in another chapter are removed from every
    /// later chapter (the first copy in reading order is kept). Needs `ImageRef.data`.
    static func removeDuplicateLeadingImages(_ contents: [ChapterContent], maxPositions: Int) -> [ChapterContent] {
        let limit = max(1, min(10, maxPositions))
        var occurrences: [Data: [(chapter: Int, filename: String)]] = [:]
        var orderedKeys: [Data] = []
        for (chapterOffset, content) in contents.enumerated() {
            for image in content.images.prefix(limit) {
                guard let bytes = image.data, !bytes.isEmpty else { continue }
                if occurrences[bytes] == nil { orderedKeys.append(bytes) }
                occurrences[bytes, default: []].append((chapterOffset, image.filename))
            }
        }
        var duplicateNames = Set<String>()
        for key in orderedKeys {
            guard let items = occurrences[key] else { continue }
            let chapterCount = Set(items.map { $0.chapter }).count
            if chapterCount < 2 { continue }
            for item in items.dropFirst() { duplicateNames.insert(item.filename) }
        }
        if duplicateNames.isEmpty { return contents }
        return contents.map { content in
            var updated = content
            for name in duplicateNames where content.images.contains(where: { $0.filename == name }) {
                updated.html = removeImagePage(updated.html, filename: name)
            }
            updated.images = content.images.filter { !duplicateNames.contains($0.filename) }
            return updated
        }
    }
}
