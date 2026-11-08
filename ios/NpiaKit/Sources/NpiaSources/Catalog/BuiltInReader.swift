import Foundation
import NpiaCore

/// The Catalog reader's way to the built-in scrapers (`window.NovelDBTransport` with `{op: "book"}` or
/// `{op: "chapter"}`): a book whose site a built-in scraper owns (npia, sfc, ntk, ...) is read with it,
/// as a download would, instead of the page's generic engine, which cannot read those sites' chapters.
///
/// - `{op: "book", url}` -> `{bookname, author, engine, chapters: [{name}]}`
/// - `{op: "chapter", url, index}` -> `{chapterName, contentHtml}` (pictures inline as data: addresses
///   when the scraper holds their bytes, else their own addresses)
/// - `{error, skip: true}` when no built-in scraper owns the address: the page tries its own engines.
/// - With `engines: true`, the app's site engines that list the site instead (gallery-dl, HaruNeko,
///   FanFicFare, ...: those a download would try, the generic fallback left to the page), or `{skip}`.
actor BuiltInReader {
    static let shared = BuiltInReader()

    private var books: [String: (adapter: any SourceAdapter, book: BookInfo)] = [:]
    private var order: [String] = []

    func handle(op: String, address: String, index: Int, engines: Bool = false, reader: String? = nil,
                page: Int? = nil, context: SourceContext) async -> String {
        guard let url = URL(string: address) else { return Self.json(["error": "not an address", "skip": true]) }
        let type = SourceCatalog.nativeAdapterType(for: url)
        let builtInComic = type?.id == .comix || type?.id == .mangak
        let genericFirst = SiteEngineRouter.genericFirst(url)
        let steps = engines ? SiteEngineRouter.plan(for: url, engines: context.settings.engines).filter {
            if genericFirst { return $0 == .tako && PythonEngines.listsBundled($0, url: url) }
            return $0 != .native && $0 != .generic
        } : []
        if engines ? steps.isEmpty : type == nil || (builtInComic && !context.settings.engines.isEnabled(.native)) {
            return Self.json(["error": engines ? "no site engine lists this site" : "no built-in scraper for this site", "skip": true])
        }
        do {
            let paged = !engines && reader != nil && (type?.id == .npia || type?.id == .mpia)
            let key = paged ? (reader ?? "") + "|" + address : engines ? "engines|" + address : address
            if paged, op == "book", let page {
                if type?.id == .mpia {
                    return try await mpiaPage(key, url: url, page: page, context: context)
                }
                return try await npiaPage(key, url: url, page: page, context: context)
            }
            if paged && books[key] == nil { return Self.json(["error": "the reader was closed"]) }
            let (adapter, book) = try await open(key, url: url, type: type, steps: steps, context: context)
            if op == "book" {
                if book.chapters.isEmpty { return Self.json(["error": "no chapters found"]) }
                return Self.json([
                    "bookname": book.title, "author": book.author ?? "", "engine": steps.first?.title ?? type?.id.rawValue ?? "",
                    "chapters": book.chapters.map { chapter in
                        var item = ["name": chapter.title]
                        if !steps.isEmpty || builtInComic, let address = chapter.url { item["url"] = address.absoluteString }
                        return item
                    },
                ])
            }
            guard book.chapters.indices.contains(index) else { return Self.json(["error": "no such chapter"]) }
            let ref = book.chapters[index]
            switch try await adapter.parseChapter(ref, book: book, context: context) {
            case .content(let content):
                var html = content.html
                // The scraper names its pictures by their file names in a download: the reader shows them inline.
                for image in content.images {
                    let shown = image.data.map { "data:\(Self.mime(image.filename));base64,\($0.base64EncodedString())" } ?? image.url.absoluteString
                    html = html.replacingOccurrences(of: image.filename, with: shown)
                }
                let comic = (!steps.isEmpty || builtInComic) && !content.images.isEmpty &&
                    html.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
                        .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                var reply: [String: Any] = ["chapterName": ref.title, "contentHtml": html, "comic": comic]
                if builtInComic, let url = ref.url { reply["imageReferer"] = url.absoluteString }
                return Self.json(reply)
            case .refused(_, let reason):
                return Self.json(["error": "This chapter is locked: \(reason)", "locked": true])
            case .failed(_, let message):
                return Self.json(["error": message])
            }
        } catch {
            let message = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
            return Self.json(["error": String(message.prefix(300))])
        }
    }

    private func mpiaPage(_ key: String, url: URL, page: Int, context: SourceContext) async throws -> String {
        let previous = page == 0 ? nil : books[key]?.book
        if page > 0 && previous == nil { return Self.json(["error": "the reader was closed"]) }
        let adapter = MpiaAdapter()
        let (book, more) = try await adapter.readerPage(url, index: page, existing: previous, context: context)
        let added = book.chapters.dropFirst(previous?.chapters.count ?? 0)
        remember(key, adapter: adapter, book: book)
        return Self.json(["bookname": book.title, "author": book.author ?? "", "engine": SourceID.mpia.rawValue,
                          "chapters": added.map { ["name": $0.title] }, "more": more])
    }

    private func npiaPage(_ key: String, url: URL, page: Int, context: SourceContext) async throws -> String {
        guard (0..<5000).contains(page) else { return Self.json(["error": "invalid chapter list page"]) }
        let adapter: NpiaAdapter
        var book: BookInfo
        if page == 0 {
            adapter = NpiaAdapter()
            book = try await adapter.openBook(NpiaAdapter.canonicalURL(url), context: context)
        } else if let known = books[key], let npia = known.adapter as? NpiaAdapter {
            adapter = npia
            book = known.book
        } else {
            return Self.json(["error": "the reader was closed"])
        }
        var seen = Set(book.chapters.map(\.id))
        let added = try await adapter.chapterPage(book.id, page: page, count: book.chapters.count, seen: &seen, context: context)
        if page == 0 && added.isEmpty { return Self.json(["error": "no chapters found"]) }
        book.chapters.append(contentsOf: added)
        remember(key, adapter: adapter, book: book)
        return Self.json(["chapters": added.map { ["name": $0.title] }, "more": !added.isEmpty && page < 4999])
    }

    private func open(_ key: String, url: URL, type: (any SourceAdapter.Type)?, steps: [SiteEngine],
                      context: SourceContext) async throws -> (any SourceAdapter, BookInfo) {
        if let known = books[key] { return (known.adapter, known.book) }
        let adapter: any SourceAdapter
        let book: BookInfo
        if let type, steps.isEmpty {
            adapter = type.init()
            book = try await adapter.parseBook(type.canonicalURL(url), context: context)
        } else {
            let opened = try await SiteEngineRouter.open(url, context: context, steps: steps)
            adapter = opened.adapter
            book = opened.book
        }
        remember(key, adapter: adapter, book: book)
        return (adapter, book)
    }

    private func remember(_ key: String, adapter: any SourceAdapter, book: BookInfo) {
        books[key] = (adapter, book)
        order.removeAll { $0 == key }
        order.append(key)
        while order.count > 6 { books[order.removeFirst()] = nil }
    }

    private static func mime(_ name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "png": return "image/png"
        case "gif": return "image/gif"
        case "webp": return "image/webp"
        case "avif": return "image/avif"
        default: return "image/jpeg"
        }
    }

    private static func json(_ value: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: value), let text = String(data: data, encoding: .utf8) else {
            return "{\"error\":\"the answer could not be written\"}"
        }
        return text
    }
}
