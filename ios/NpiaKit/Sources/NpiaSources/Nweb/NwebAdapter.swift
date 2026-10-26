import Foundation
import NpiaCore
import SwiftSoup

/// Korean web-novel portal (webnovel / best / challenge tiers), plus nseries product pages
/// that link to a free nweb edition. Port of `_nweb_*` and `_nweb_series_*` in the desktop
/// `external_scraper.py`.
///
/// Age-restricted works need the user signed in; the HTTP client shares cookies with the
/// web engine, so a sign-in in the `nweb` web profile carries over.
public struct NwebAdapter: SourceAdapter {
    public static let id: SourceID = .nweb
    public static let tier: SourceTier = .http

    static let domain = "\u{6e}\u{61}\u{76}\u{65}\u{72}.com"
    static let novelOrigin = "https://novel.\(domain)"
    static let webProfile = SourceID.nweb.rawValue

    let session = NwebSession()

    public init() {}

    // MARK: - URLs

    /// `(tier, novelID, volumeNo)` for an nweb work or episode URL.
    static func novelParts(_ url: URL) -> (tier: String, novel: String, volume: String)? {
        let host = (url.host ?? "").lowercased()
        guard host == "novel.\(domain)" || host == "m.novel.\(domain)" else { return nil }
        guard let tier = HTTPSourceSupport.firstMatch("^/(webnovel|best|challenge)/(?:list|detail)/?$", in: url.path) else { return nil }
        guard let novel = HTTPSourceSupport.query(url, "novelId"), HTTPSourceSupport.isPositiveInteger(novel) else { return nil }
        return (tier, novel, HTTPSourceSupport.query(url, "volumeNo") ?? "")
    }

    static func isSeries(_ url: URL) -> Bool {
        let host = (url.host ?? "").lowercased()
        guard host == "series.\(domain)" || host == "m.series.\(domain)" else { return false }
        guard url.path.range(of: "^/novel/detail\\.(?:series|nhn)/?$", options: .regularExpression) != nil else { return false }
        return ["productNo", "originalProductId"].contains { HTTPSourceSupport.isPositiveInteger(HTTPSourceSupport.query(url, $0)) }
    }

    public static func matches(_ url: URL) -> Bool {
        novelParts(url) != nil || isSeries(url)
    }

    static let headers = [
        "User-Agent": HTTPSourceSupport.desktopUserAgent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    ]

    static func needsLogin(_ response: HTTPResponse) -> Bool {
        (response.url.host ?? "").lowercased().hasPrefix("nid.\(domain)")
    }

    func fetch(_ url: URL, referer: URL? = nil, http: any HTTPFetching) async throws -> HTTPResponse {
        var headers = Self.headers
        if let referer { headers["Referer"] = referer.absoluteString }
        return try await HTTPSourceSupport.get(url, headers: headers, http: http)
    }

    // MARK: - Book

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        if Self.isSeries(url) {
            let response = try await fetch(url, http: context.http)
            if Self.needsLogin(response) { throw SourceError.loginRequired(Self.id) }
            guard let webURL = Self.seriesWebEdition(response.text) else {
                throw SourceError.blocked("nseries episodes are app-only (DRM) and this product has no nweb edition to download.")
            }
            context.logger.log(.info, "nseries episodes are app-only (DRM). Downloading the free nweb edition: \(webURL.absoluteString)")
            return try await parseNovel(webURL, context: context)
        }
        return try await parseNovel(url, context: context)
    }

    /// The nweb edition a series product page links to.
    static func seriesWebEdition(_ page: String) -> URL? {
        let escaped = NSRegularExpression.escapedPattern(for: domain)
        let pattern = "href=\"((?:https?:)?//novel\\.\(escaped)/(?:webnovel|best|challenge)/list\\?novelId=\\d+)\""
        guard var href = HTTPSourceSupport.firstMatch(pattern, in: page) else { return nil }
        if href.hasPrefix("//") { href = "https:" + href }
        if href.hasPrefix("http://") { href = "https://" + href.dropFirst(7) }
        return URL(string: href)
    }

    func parseNovel(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let parts = Self.novelParts(url) else { throw SourceError.unsupportedURL(url) }
        let listURL = URL(string: "\(Self.novelOrigin)/\(parts.tier)/list?novelId=\(parts.novel)")!
        let response = try await fetch(listURL, http: context.http)
        if Self.needsLogin(response) { throw SourceError.loginRequired(Self.id) }
        var page = try Self.listPage(response.text, tier: parts.tier, novel: parts.novel, listURL: listURL)

        let perPage = page.rows.count
        if perPage > 0, page.total > perPage {
            let pageCount = (page.total + perPage - 1) / perPage
            context.logger.log(.info, "Reading \(pageCount) episode list pages (\(page.total) episodes)...")
            let rows = try await withThrowingTaskGroup(of: (Int, [NwebRow]).self) { group in
                for number in 2...pageCount {
                    group.addTask {
                        let pageURL = URL(string: listURL.absoluteString + "&page=\(number)")!
                        let html = try await fetch(pageURL, referer: listURL, http: context.http).text
                        return (number, try Self.rows(SwiftSoup.parse(html), tier: parts.tier, novel: parts.novel))
                    }
                }
                var collected: [(Int, [NwebRow])] = []
                for try await result in group { collected.append(result) }
                return collected.sorted { $0.0 < $1.0 }.flatMap(\.1)
            }
            page.rows += rows
        }

        var seen = Set<Int>()
        let unique = page.rows.filter { seen.insert($0.volume).inserted }.sorted { $0.volume < $1.volume }
        guard !unique.isEmpty else { throw SourceError.parse("no readable episodes were listed") }
        if page.total > 0, unique.count != page.total {
            context.logger.log(.warning, "The page reports \(page.total) episodes but \(unique.count) were listed.")
        }
        var book = page.book
        book.chapters = unique.enumerated().map { offset, row in
            let name = row.name.isEmpty ? "\(row.volume)화" : row.name
            let chapterURL = URL(string: "\(Self.novelOrigin)/\(parts.tier)/detail?novelId=\(parts.novel)&volumeNo=\(row.volume)")!
            return ChapterRef(id: String(row.volume), index: offset + 1, title: name, url: chapterURL)
        }
        await detectEndImages(book.chapters, referer: listURL, http: context.http, logger: context.logger)
        return book
    }

    struct ListPage {
        var book: BookInfo
        var total: Int
        var rows: [NwebRow]
    }

    static func listPage(_ html: String, tier: String, novel: String, listURL: URL) throws -> ListPage {
        let document = try SwiftSoup.parse(html, listURL.absoluteString)
        guard let info = try document.select(".section_area_info").first(),
              let titleNode = try info.select("h2.title").first() else {
            let message = try document.select("#content").first()?.text() ?? ""
            throw SourceError.parse("work page not found or not public" + (message.isEmpty ? "" : ": \(message.prefix(120))"))
        }
        let title = try titleNode.text()
        let authors = try info.select("a[href*=target=author]").array().map { try $0.text() }.filter { !$0.isEmpty }
        let genre = try info.select(".info_top .info_group .item").first()?.text() ?? ""
        var synopsis = ""
        if let summary = try info.select("p.summary").first() {
            try summary.select("a, button").remove()
            synopsis = textLines(summary).joined(separator: "\n")
                .replacingOccurrences(of: "\n{3,}", with: "\n\n", options: .regularExpression)
        }
        var cover = try document.select("meta[property=og:image]").first().map { try $0.attr("content") } ?? ""
        if let image = try info.select(".thumbnail img[src]").first() {
            cover = try image.absUrl("src")
        }
        var tags: [String] = []
        if !genre.isEmpty { tags.append(genre) }
        for link in try document.select(".end_tag_area .tag_collection a.tag").array() {
            let tag = try link.text().trimmingCharacters(in: CharacterSet(charactersIn: "#").union(.whitespaces))
            if !tag.isEmpty, !tags.contains(tag) { tags.append(tag) }
        }
        var total = 0
        for heading in try document.select(".cont_sub .component_head h3.title").array() {
            if let count = HTTPSourceSupport.firstMatch("작품\\s*회차\\s*\\(([\\d,]+)\\)", in: try heading.text()) {
                total = Int(count.replacingOccurrences(of: ",", with: "")) ?? 0
                break
            }
        }
        let completed = try info.select(".bullet_comp, .bullet_comp_ex").first() != nil
        let book = BookInfo(
            source: id, id: novel, url: listURL, title: title,
            author: authors.first, synopsis: synopsis.isEmpty ? nil : synopsis,
            coverURL: URL(string: cover), tags: tags, status: completed ? "완결" : "연재", language: "ko"
        )
        return ListPage(book: book, total: total, rows: try rows(document, tier: tier, novel: novel))
    }

    static func rows(_ document: Document, tier: String, novel: String) throws -> [NwebRow] {
        let pattern = "/\(tier)/detail\\?novelId=\(novel)&(?:amp;)?volumeNo=(\\d+)"
        var rows: [NwebRow] = []
        for link in try document.select("li.volumeComment a.list_item[href]").array() {
            guard let volume = HTTPSourceSupport.firstMatch(pattern, in: try link.attr("href")).flatMap({ Int($0) }) else { continue }
            var name = ""
            if let subject = try link.select(".subj").first() {
                try subject.select(".bullet_wrap").remove()
                name = try subject.text()
            }
            name = name.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces)
            rows.append(NwebRow(volume: volume, name: name))
        }
        return rows
    }

    /// Text nodes of `element`, trimmed, one per line (BeautifulSoup `get_text('\n', strip=True)`).
    static func textLines(_ element: Element) -> [String] {
        var lines: [String] = []
        func walk(_ node: Node) {
            for child in node.getChildNodes() {
                if let text = child as? TextNode {
                    let value = text.getWholeText().trimmingCharacters(in: .whitespacesAndNewlines)
                    if !value.isEmpty { lines.append(value) }
                } else {
                    walk(child)
                }
            }
        }
        walk(element)
        return lines
    }

    // MARK: - Chapters

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        guard let url = ref.url else { return .failed(ref, message: "missing episode URL") }
        guard let blocks = try await episodeBlocks(url, referer: book.url, http: context.http) else {
            return .failed(ref, message: "episode is not readable")
        }
        if case .needsLogin = blocks {
            return .refused(ref, reason: "needs a signed-in, age-verified account")
        }
        guard case .blocks(let list) = blocks else { return .failed(ref, message: "episode is not readable") }
        let kept = await dropEndImages(list, referer: book.url, http: context.http)
        guard !kept.isEmpty else { return .failed(ref, message: "empty episode") }
        return .content(ReaderBlocks.content(kept, ref: ref, cssClass: "nweb-novel-content"))
    }

    enum Episode {
        case blocks([ReaderBlock])
        case needsLogin
    }

    func episodeBlocks(_ url: URL, referer: URL?, http: any HTTPFetching) async throws -> Episode? {
        let response = try await fetch(url, referer: referer, http: http)
        if Self.needsLogin(response) { return .needsLogin }
        return try Self.episode(response.text, url: url)
    }

    static func episode(_ html: String, url: URL) throws -> Episode? {
        let document = try SwiftSoup.parse(html, url.absoluteString)
        guard let content = try document.select(".detail_view_content").first() else { return nil }
        return .blocks(try ReaderBlocks.fromHTML(try content.outerHtml(), baseURL: url))
    }

    // MARK: - Publisher logo banners

    /// The same upload is served with different `?type=` resize hints.
    static func imageKey(_ url: URL) -> String {
        (url.host ?? "").lowercased() + url.path
    }

    static func imageName(_ key: String) -> String {
        (key.components(separatedBy: "/").last ?? "").removingPercentEncoding?.lowercased() ?? ""
    }

    static func trailingImages(_ blocks: [ReaderBlock]) -> [URL] {
        var trailing: [URL] = []
        for block in blocks.reversed() {
            guard case .image(let url) = block else { break }
            trailing.insert(url, at: 0)
        }
        return trailing
    }

    func signature(_ url: URL, referer: URL?, http: any HTTPFetching) async -> NwebImageSignature? {
        let key = Self.imageKey(url)
        if let cached = await session.cachedSignature(key) { return cached }
        var signature: NwebImageSignature?
        if let response = try? await HTTPSourceSupport.get(url, headers: [
            "User-Agent": HTTPSourceSupport.desktopUserAgent,
            "Referer": referer?.absoluteString ?? Self.novelOrigin,
        ], http: http) {
            signature = NwebImageSignature(imageData: response.body)
        }
        await session.storeSignature(signature, for: key)
        return signature
    }

    /// Finds publisher logo banners to drop from every episode. Some works close each episode
    /// with the publisher's logo, often a fresh upload per episode. A banner-shaped image that
    /// closes at least two sampled episodes with the same appearance is a logo.
    func detectEndImages(_ chapters: [ChapterRef], referer: URL, http: any HTTPFetching, logger: any JobLogging) async {
        guard chapters.count >= 2 else { return }
        let count = chapters.count
        let picks = Array(Set([0, count / 4, count / 2, (3 * count) / 4, count - 1])).sorted()
        var banners: [(sample: Int, url: URL, signature: NwebImageSignature)] = []
        for (sample, index) in picks.enumerated() {
            guard !Task.isCancelled, let url = chapters[index].url,
                  case .blocks(let blocks)? = try? await episodeBlocks(url, referer: referer, http: http) else { continue }
            for image in Self.trailingImages(blocks) {
                if let signature = await signature(image, referer: referer, http: http), signature.isBanner {
                    banners.append((sample, image, signature))
                }
            }
        }
        var keys = Set<String>()
        var names = Set<String>()
        var logos: [NwebImageSignature] = []
        for banner in banners {
            let samples = Set(banners.filter { banner.signature.matches($0.signature) }.map(\.sample))
            guard samples.count >= 2 else { continue }
            let key = Self.imageKey(banner.url)
            keys.insert(key)
            names.insert(Self.imageName(key))
            if !logos.contains(where: { banner.signature.matches($0) }) { logos.append(banner.signature) }
        }
        await session.setLogos(keys: keys, names: names, signatures: logos)
        if !keys.isEmpty {
            logger.log(.info, "Removing the publisher logo from every episode: \(names.sorted().joined(separator: ", "))")
        }
    }

    func isEndImage(_ url: URL, trailing: Bool, referer: URL?, http: any HTTPFetching) async -> Bool {
        let key = Self.imageKey(url)
        let logos = await session.logos
        if logos.keys.contains(key) { return true }
        // Only images named like a logo or closing the episode are downloaded for comparison.
        if !trailing, !logos.names.contains(Self.imageName(key)) { return false }
        guard let signature = await signature(url, referer: referer, http: http), signature.isBanner else { return false }
        return logos.signatures.contains { signature.matches($0) }
    }

    /// Removes every copy of a detected publisher logo from an episode.
    func dropEndImages(_ blocks: [ReaderBlock], referer: URL?, http: any HTTPFetching) async -> [ReaderBlock] {
        let logos = await session.logos
        guard !logos.keys.isEmpty || !logos.signatures.isEmpty else { return blocks }
        let lastText = blocks.lastIndex { if case .text = $0 { return true } else { return false } } ?? -1
        var kept: [ReaderBlock] = []
        for (index, block) in blocks.enumerated() {
            if case .image(let url) = block, await isEndImage(url, trailing: index > lastText, referer: referer, http: http) {
                continue
            }
            kept.append(block)
        }
        // An episode that is nothing but the logo keeps it rather than becoming empty.
        return kept.isEmpty ? blocks : kept
    }
}

struct NwebRow: Sendable {
    var volume: Int
    var name: String
}

/// Per-job logo detection results and the image signature cache.
actor NwebSession {
    struct Logos: Sendable {
        var keys: Set<String> = []
        var names: Set<String> = []
        var signatures: [NwebImageSignature] = []
    }

    private(set) var logos = Logos()
    private var signatures: [String: NwebImageSignature?] = [:]

    func setLogos(keys: Set<String>, names: Set<String>, signatures: [NwebImageSignature]) {
        logos = Logos(keys: keys, names: names, signatures: signatures)
    }

    func cachedSignature(_ key: String) -> NwebImageSignature?? {
        signatures[key]
    }

    func storeSignature(_ signature: NwebImageSignature?, for key: String) {
        signatures[key] = .some(signature)
    }
}
