import CryptoKit
import Foundation
import NpiaCore
import SwiftSoup

/// Book metadata read from the store page.
struct KoboMeta: Codable, Sendable, Equatable {
    var title: String = ""
    var author: String = ""
    var cover: String = ""
    var description: String = ""
    var publisher: String = ""
    var readUrl: String = ""
    var language: String = ""

    init() {}

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        title = (try? container.decode(String.self, forKey: .title)) ?? ""
        author = (try? container.decode(String.self, forKey: .author)) ?? ""
        cover = (try? container.decode(String.self, forKey: .cover)) ?? ""
        description = (try? container.decode(String.self, forKey: .description)) ?? ""
        publisher = (try? container.decode(String.self, forKey: .publisher)) ?? ""
        readUrl = (try? container.decode(String.self, forKey: .readUrl)) ?? ""
        language = (try? container.decode(String.self, forKey: .language)) ?? ""
    }
}

/// One table-of-contents entry of the reader.
struct KoboTOCEntry: Codable, Sendable, Equatable {
    var title: String
    var href: String
}

struct KoboTOC: Codable, Sendable {
    var base: String
    var entries: [KoboTOCEntry]
}

struct KoboSnapshotImage: Codable, Sendable {
    var url: String
    var data: String?
}

struct KoboSnapshotSection: Codable, Sendable {
    var path: String
    var html: String
    var images: [KoboSnapshotImage]
}

struct KoboSnapshot: Codable, Sendable {
    var paths: [String]
    var sections: [KoboSnapshotSection]
}

/// Pure helpers for the kobo source (port of `is_kobo`, `_kobo_*` and `KoboWebReader`).
enum KoboParsing {
    static let readerHost = "readnow.kobo.com"
    static let defaultTOCBase = "OEBPS/Text/toc.xhtml"

    // MARK: URLs

    static func isRakutenURL(_ url: URL) -> Bool {
        guard url.host?.lowercased() == "books.rakuten.co.jp" else { return false }
        return url.scheme == "https" && WebRegex.matches("^/rk/[0-9a-f]{32}/?$", in: url.path, options: [.caseInsensitive])
    }

    static func matches(_ url: URL) -> Bool {
        let host = (url.host ?? "").lowercased()
        if host == "books.rakuten.co.jp" { return isRakutenURL(url) }
        guard host == "kobo.com" || host == "www.kobo.com" else { return false }
        guard url.scheme == "http" || url.scheme == "https" else { return false }
        return WebRegex.matches("^/(?:[a-z]{2}/[a-z]{2}/)?ebook/[^/]+/?$", in: url.path, options: [.caseInsensitive])
    }

    static func isReaderURL(_ url: URL) -> Bool {
        url.scheme == "https" && url.host == readerHost
    }

    /// Reads the owned-book action embedded by localized stores (`isInLibrary` and `readNowUrl`).
    static func ownedReadURL(_ productHTML: String) -> String {
        guard let doc = try? WebHTML.parse(productHTML) else { return "" }
        for element in WebHTML.select("[data-kobo-gizmo-config]", in: doc) {
            let raw = (try? element.attr("data-kobo-gizmo-config")) ?? ""
            guard let data = raw.data(using: .utf8),
                  let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
            guard let owned = config["isInLibrary"] as? NSNumber, owned.boolValue,
                  CFGetTypeID(owned) == CFBooleanGetTypeID() else { continue }
            let readURL = (config["readNowUrl"] as? String) ?? ""
            if let parsed = URL(string: readURL), isReaderURL(parsed) { return readURL }
        }
        return ""
    }

    // MARK: Book

    static func bookID(_ url: URL) -> String {
        let last = url.path.split(separator: "/").last.map(String.init) ?? ""
        return last.isEmpty ? "kobo" : last
    }

    static let rakutenChapterID = "kobo-desktop"

    /// Rakuten Books JP volumes are read from the local Kobo Desktop app on the PC; that path
    /// does not exist on iOS, so the book is listed but its single chapter is refused.
    static func rakutenBook(_ url: URL) -> BookInfo {
        let title = "Rakuten Kobo ebook"
        let chapter = ChapterRef(id: rakutenChapterID, index: 1, title: title, url: url, isPaid: false)
        return BookInfo(source: .kobo, id: bookID(url), url: url, title: title, language: "ja", chapters: [chapter])
    }

    static func makeBook(url: URL, meta: KoboMeta) -> BookInfo {
        let title = meta.title.isEmpty ? "Kobo ebook" : meta.title
        let readURL = meta.readUrl
        let chapterURL = URL(string: readURL) ?? url
        let chapter = ChapterRef(id: "kobo-1", index: 1, title: title, url: chapterURL, isPaid: readURL.isEmpty)
        let language = meta.language.isEmpty ? "en" : (meta.language.components(separatedBy: "-").first ?? "en")
        return BookInfo(
            source: .kobo, id: bookID(url), url: url, title: title,
            author: meta.author.isEmpty ? nil : meta.author,
            synopsis: meta.description.isEmpty ? nil : meta.description,
            coverURL: meta.cover.isEmpty ? nil : URL(string: meta.cover),
            language: language.isEmpty ? "en" : language,
            chapters: [chapter]
        )
    }

    // MARK: Reader paths

    /// Resolves a table-of-contents href against the section it came from (`KoboWebReader._path`).
    static func resolvePath(_ href: String, base: String = defaultTOCBase) -> String {
        var rest = href
        if let hash = rest.firstIndex(of: "#") { rest = String(rest[rest.startIndex..<hash]) }
        if let query = rest.firstIndex(of: "?") { rest = String(rest[rest.startIndex..<query]) }
        if rest.hasPrefix("//") || WebRegex.matches("^[A-Za-z][A-Za-z0-9+.-]*:", in: rest) { return "" }
        var path = rest.removingPercentEncoding ?? rest
        while path.hasPrefix("/") { path.removeFirst() }
        if path.isEmpty { return "" }
        if !(path.hasPrefix("OEBPS/") || path.hasPrefix("OPS/")) {
            var directory = base.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
            if !directory.isEmpty { directory.removeLast() }
            path = (directory + [path]).joined(separator: "/")
        }
        return normalize(path)
    }

    /// `posixpath.normpath` for relative paths.
    static func normalize(_ path: String) -> String {
        var stack: [String] = []
        for part in path.split(separator: "/", omittingEmptySubsequences: true).map(String.init) {
            if part == "." { continue }
            if part == "..", let last = stack.last, last != ".." {
                stack.removeLast()
            } else {
                stack.append(part)
            }
        }
        return stack.isEmpty ? "." : stack.joined(separator: "/")
    }

    static func anchor(_ path: String) -> String {
        let digest = Insecure.SHA1.hash(data: Data(path.utf8))
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return "kobo-" + String(hex.prefix(12))
    }

    /// Merges overlapping reader windows without losing spine order (`KoboWebReader._ordered_paths`).
    static func orderedPaths(_ snapshots: [[String]]) throws -> [String] {
        var edges: [String: Set<String>] = [:]
        var indegree: [String: Int] = [:]
        var firstSeen: [String: Int] = [:]
        for snapshot in snapshots {
            var paths: [String] = []
            for path in snapshot where !paths.contains(path) { paths.append(path) }
            for path in paths {
                if firstSeen[path] == nil { firstSeen[path] = firstSeen.count }
                if edges[path] == nil { edges[path] = [] }
                if indegree[path] == nil { indegree[path] = 0 }
            }
            if paths.count > 1 {
                for position in 0..<(paths.count - 1) {
                    let left = paths[position]
                    let right = paths[position + 1]
                    if edges[left]?.contains(right) != true {
                        edges[left]?.insert(right)
                        indegree[right, default: 0] += 1
                    }
                }
            }
        }
        var ready: [String] = indegree.filter { $0.value == 0 }.map { $0.key }
        var result: [String] = []
        while !ready.isEmpty {
            var best = 0
            for position in 1..<max(ready.count, 1) where (firstSeen[ready[position]] ?? 0) < (firstSeen[ready[best]] ?? 0) {
                best = position
            }
            let path = ready.remove(at: best)
            result.append(path)
            for next in edges[path] ?? [] {
                indegree[next, default: 0] -= 1
                if indegree[next] == 0 { ready.append(next) }
            }
        }
        if result.count != indegree.count { throw SourceError.parse("Kobo reader returned inconsistent section order.") }
        return result
    }

    // MARK: Section HTML

    /// Strips scripts, frames and event handlers; points links to other sections at their anchors.
    static func cleanSection(_ source: String, path: String, anchors: [String: String], imageNames: [String: String]) -> String {
        guard let doc = try? WebHTML.parse(source), let body = doc.body() else { return WebHTML.escape(source) }
        for tag in ["script", "iframe", "object", "embed"] { WebHTML.removeAll(tag, in: body) }
        for node in WebHTML.select("*", in: body) {
            let names: [String] = node.getAttributes()?.asList().map { $0.getKey() } ?? []
            for name in names where name.lowercased().hasPrefix("on") {
                _ = try? node.removeAttr(name)
            }
        }
        for link in WebHTML.select("a[href]", in: body) {
            let target = resolvePath((try? link.attr("href")) ?? "", base: path)
            if let id = anchors[target] { _ = try? link.attr("href", "#" + id) }
        }
        for image in WebHTML.select("img[src]", in: body) {
            let source = (try? image.attr("src")) ?? ""
            if let name = imageNames[source] { _ = try? image.attr("src", name) }
        }
        return xhtmlBody(doc)
    }

    private static func xhtmlBody(_ doc: Document) -> String {
        doc.outputSettings().syntax(syntax: .xml)
        return (try? doc.body()?.html()) ?? ""
    }

    static func sectionText(_ html: String) -> String {
        guard let doc = try? WebHTML.parse(html), let body = doc.body() else { return "" }
        return WebHTML.lines(body).joined(separator: "\n")
    }

    /// True when most of the text sits in the private block the reader uses for font-encoded text.
    static func isFontEncoded(_ text: String, entryTitles: [String]) -> Bool {
        guard !text.isEmpty else { return false }
        var encoded = 0
        var total = 0
        for scalar in text.unicodeScalars {
            total += 1
            if scalar.value >= 0x1f60 && scalar.value <= 0x1fff { encoded += 1 }
        }
        return encoded * 100 > total && entryTitles.contains { $0.hasPrefix("Chapter") }
    }

    static func imageExtension(dataURL: String) -> String {
        guard dataURL.hasPrefix("data:") else { return "jpg" }
        let head = dataURL.components(separatedBy: ";").first ?? ""
        let mime = (head.components(separatedBy: "/").last ?? "").lowercased()
        let map: [String: String] = ["jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp", "svg+xml": "svg"]
        return map[mime] ?? "jpg"
    }

    static func imageBytes(dataURL: String) -> Data? {
        guard dataURL.hasPrefix("data:"), let comma = dataURL.firstIndex(of: ",") else { return nil }
        let header = dataURL[dataURL.startIndex..<comma]
        let payload = String(dataURL[dataURL.index(after: comma)...])
        if header.contains(";base64") { return Data(base64Encoded: payload, options: .ignoreUnknownCharacters) }
        return payload.removingPercentEncoding.map { Data($0.utf8) }
    }

    /// Builds the chapter from the collected sections, in spine order.
    static func buildContent(
        ref: ChapterRef, entries: [(title: String, path: String)], collected: [String: KoboSnapshotSection],
        snapshots: [[String]], horizontalLayout: Bool
    ) throws -> ChapterContent {
        let missing = entries.filter { collected[$0.path] == nil }.map { $0.title }
        if !missing.isEmpty { throw SourceError.parse("Kobo reader did not load: " + missing.prefix(3).joined(separator: ", ")) }
        let ordered = try orderedPaths(snapshots)
        let uncaptured = ordered.filter { collected[$0] == nil }
        if !uncaptured.isEmpty {
            throw SourceError.parse("Kobo reader did not render every source section: " + uncaptured.prefix(3).joined(separator: ", "))
        }
        var anchors: [String: String] = [:]
        for path in ordered { anchors[path] = anchor(path) }

        var images: [ImageRef] = []
        var imageNames: [String: String] = [:]
        for path in ordered {
            guard let section = collected[path] else { continue }
            for image in section.images {
                let data = image.data ?? ""
                if image.url.hasPrefix("blob:") && data.isEmpty {
                    throw SourceError.parse("A Kobo reader image did not load in " + path)
                }
                if image.url.isEmpty || imageNames[image.url] != nil { continue }
                let name = String(format: "images/%04d_%03d", ref.index, images.count + 1) + "." + imageExtension(dataURL: data)
                imageNames[image.url] = name
                let remote = URL(string: image.url) ?? ref.url ?? URL(string: "https://www.kobo.com/")!
                images.append(ImageRef(url: remote, filename: name, data: imageBytes(dataURL: data)))
            }
        }

        var bodies: [String] = []
        var textParts: [String] = []
        for path in ordered {
            guard let section = collected[path], let id = anchors[path] else { continue }
            let clean = cleanSection(section.html, path: path, anchors: anchors, imageNames: imageNames)
            bodies.append("<section class=\"kobo-source-section\" id=\"\(id)\">\(clean)</section>")
            textParts.append(sectionText(clean))
        }
        if isFontEncoded(textParts.joined(separator: "\n\n"), entryTitles: entries.map { $0.title }) {
            throw SourceError.parse("Kobo reader text is still font-encoded; a readable EPUB cannot be produced.")
        }
        let style = horizontalLayout ? " style=\"writing-mode:horizontal-tb\"" : ""
        let html = "<div class=\"kobo-content\"\(style)>" + bodies.joined() + "</div>"
        return ChapterContent(ref: ref, html: html, images: images)
    }

    /// A JSON string literal usable inside JavaScript source.
    static func jsString(_ value: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [value]),
              var text = String(data: data, encoding: .utf8) else { return "\"\"" }
        text.removeFirst()
        text.removeLast()
        return text
    }
}
