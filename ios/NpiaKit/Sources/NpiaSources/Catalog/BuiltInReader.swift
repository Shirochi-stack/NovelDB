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

    func handle(op: String, address: String, index: Int, engines: Bool = false, context: SourceContext) async -> String {
        guard let url = URL(string: address) else { return Self.json(["error": "not an address", "skip": true]) }
        let type = SourceCatalog.nativeAdapterType(for: url)
        // The app's site engines that list the address (a download's own order), none of the built-in or generic ones;
        // none on a site the generic fallback reads best (`SiteEngineRouter.genericFirst`): the page's own reads it.
        let steps = engines && !SiteEngineRouter.genericFirst(url)
            ? SiteEngineRouter.plan(for: url, engines: context.settings.engines).filter { $0 != .native && $0 != .generic } : []
        if engines ? steps.isEmpty : type == nil {
            return Self.json(["error": engines ? "no site engine lists this site" : "no built-in scraper for this site", "skip": true])
        }
        do {
            let (adapter, book) = try await open(engines ? "engines|" + address : address, url: url, type: type, steps: steps, context: context)
            if op == "book" {
                if book.chapters.isEmpty { return Self.json(["error": "no chapters found"]) }
                return Self.json([
                    "bookname": book.title, "author": book.author ?? "", "engine": steps.first?.title ?? type?.id.rawValue ?? "",
                    "chapters": book.chapters.map { ["name": $0.title] },
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
                return Self.json(["chapterName": ref.title, "contentHtml": html])
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
        books[key] = (adapter, book)
        order.removeAll { $0 == key }
        order.append(key)
        while order.count > 6 { books[order.removeFirst()] = nil }
        return (adapter, book)
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
