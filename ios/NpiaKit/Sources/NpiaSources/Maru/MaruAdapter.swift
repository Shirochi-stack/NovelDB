import Foundation
import NpiaCore

/// Numbered-domain novel site read through its public reader API.
/// Port of `_maru_*` in the desktop `external_scraper.py`.
public struct MaruAdapter: SourceAdapter {
    public static let id: SourceID = .maru
    public static let tier: SourceTier = .http

    /// The numbered site domain keeps moving; the reader API host does not.
    static let api = URL(string: "https://api.storagewebtoon.store/api/v1/reader/A2/")!

    public init() {}

    /// The numbered domain keeps moving, so the site is recognised by its name pattern.
    static let hostPattern = "^(?:www\\.)?marumaru\\d*\\.[a-z]{2,}$"

    public static func ownsSite(_ host: String) -> Bool {
        host.range(of: hostPattern, options: [.regularExpression, .caseInsensitive]) != nil
    }

    /// `(workID, episodeID)` for a novel or episode URL.
    static func parts(_ url: URL) -> (work: String, episode: String)? {
        guard let host = url.host, ownsSite(host) else {
            return nil
        }
        let groups = HTTPSourceSupport.allMatches("^/novel/(\\d+)(?:/(\\d+))?", in: url.path).first
        guard let groups, let work = groups.first, !work.isEmpty else { return nil }
        return (work, groups.count > 1 ? groups[1] : "")
    }

    public static func matches(_ url: URL) -> Bool {
        parts(url) != nil
    }

    func apiGet(_ path: String, http: any HTTPFetching) async throws -> [String: Any] {
        // No Referer: the API refuses retired domains but accepts requests naming no site.
        let url = URL(string: path, relativeTo: Self.api)!.absoluteURL
        let response = try await HTTPSourceSupport.get(url, headers: [
            "User-Agent": HTTPSourceSupport.desktopUserAgent,
            "Accept": "application/json",
        ], http: http)
        let data = try HTTPSourceSupport.jsonObject(response.body)
        if let ok = data["ok"] as? Bool, !ok {
            throw SourceError.parse(HTTPSourceSupport.string(data["message"]).isEmpty ? "bad reply" : HTTPSourceSupport.string(data["message"]))
        }
        return data
    }

    static func episodeName(_ title: String, workTitle: String, number: String) -> String {
        var title = title.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        let work = workTitle.trimmingCharacters(in: .whitespaces)
        if !work.isEmpty, title.hasPrefix(work + " ") {
            title = String(title.dropFirst(work.count)).trimmingCharacters(in: .whitespaces)
        }
        return title.isEmpty ? "\(number)화" : title
    }

    public func parseBook(_ url: URL, context: SourceContext) async throws -> BookInfo {
        guard let parts = Self.parts(url) else { throw SourceError.unsupportedURL(url) }
        let data = try await apiGet("works/\(parts.work)", http: context.http)
        let item = data["item"] as? [String: Any] ?? [:]
        let rows = (data["episodes"] as? [[String: Any]] ?? []).filter { $0["id"] != nil && !($0["id"] is NSNull) }
            .sorted {
                let left = (HTTPSourceSupport.double($0["number"]), HTTPSourceSupport.int($0["id"]))
                let right = (HTTPSourceSupport.double($1["number"]), HTTPSourceSupport.int($1["id"]))
                return left < right
            }
        let title = HTTPSourceSupport.string(item["title"]).trimmingCharacters(in: .whitespaces)
        let workTitle = title.isEmpty ? "Maru \(parts.work)" : title
        let origin = "\(url.scheme ?? "https")://\(url.host ?? "")"
        let bookURL = URL(string: "\(origin)/novel/\(parts.work)")!

        var chapters: [ChapterRef] = []
        for row in rows {
            let id = HTTPSourceSupport.string(row["id"])
            let number = HTTPSourceSupport.string(row["number"])
            let name = Self.episodeName(HTTPSourceSupport.string(row["title"]), workTitle: workTitle, number: number)
            chapters.append(ChapterRef(id: id, index: chapters.count + 1, title: name, url: bookURL.appendingPathComponent(id)))
        }
        guard !chapters.isEmpty else { throw SourceError.parse("no episodes found") }

        let synopsis = HTTPSourceSupport.string(data["synopsis"]).trimmingCharacters(in: .whitespacesAndNewlines)
        let genres = HTTPSourceSupport.string(item["genres"])
            .split(whereSeparator: { $0 == "," || $0 == "/" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let author = HTTPSourceSupport.string(item["author"]).trimmingCharacters(in: .whitespaces)
        let status = HTTPSourceSupport.string(item["status"])
        return BookInfo(
            source: Self.id, id: parts.work, url: bookURL, title: workTitle,
            author: author.isEmpty ? nil : author,
            synopsis: synopsis.isEmpty ? nil : synopsis,
            coverURL: URL(string: HTTPSourceSupport.string(item["cover_url"])),
            tags: genres, status: status.isEmpty ? nil : status,
            language: "ko", chapters: chapters
        )
    }

    public func parseChapter(_ ref: ChapterRef, book: BookInfo, context: SourceContext) async throws -> ChapterResult {
        let episode = ref.url.flatMap { Self.parts($0)?.episode }.flatMap { $0.isEmpty ? nil : $0 } ?? ref.id
        let data = try await apiGet("episodes/\(episode)", http: context.http)
        return try await Self.chapter(from: data, ref: ref, http: context.http)
    }

    static func chapter(from data: [String: Any], ref: ChapterRef, http: any HTTPFetching) async throws -> ChapterResult {
        let item = data["item"] as? [String: Any] ?? [:]
        let body = item["body"] as? [[String: Any]] ?? []
        if body.contains(where: { $0["kind"] as? String == "paid" }) {
            return .refused(ref, reason: "point-purchase episode")
        }
        var blocks: [ReaderBlock] = []
        for part in body {
            let kind = part["kind"] as? String
            let text = HTTPSourceSupport.string(part["text"])
            if kind == "narration" || kind == "text", !text.isEmpty {
                blocks += ReaderBlocks.fromPlainText(text)
            } else if kind == "image", let url = ReaderBlocks.resolve(HTTPSourceSupport.string(part["url"]), base: nil) {
                blocks.append(.image(url))
            }
        }
        let ready = (item["ready"] as? Bool) != false
        let embed = HTTPSourceSupport.string(item["embed_url"])
        if blocks.isEmpty, !embed.isEmpty, ready {
            guard let embedURL = ReaderBlocks.resolve(embed, base: api) else {
                return .failed(ref, message: "invalid embedded reader URL")
            }
            let response = try await HTTPSourceSupport.get(embedURL, headers: [
                "User-Agent": HTTPSourceSupport.desktopUserAgent,
                "Accept": "text/plain",
            ], http: http)
            var text = response.text
            if text.hasPrefix("\u{feff}") { text.removeFirst() }
            if text.range(of: "^\\s*<(?:!doctype|html|head|body)\\b", options: [.regularExpression, .caseInsensitive]) != nil {
                return .failed(ref, message: "embedded reader returned an HTML page")
            }
            blocks = ReaderBlocks.fromPlainText(text)
        }
        if blocks.isEmpty {
            if !ready || body.contains(where: { $0["kind"] as? String == "processing" }) {
                return .refused(ref, reason: "still processing on the site")
            }
            return .failed(ref, message: "empty episode")
        }
        return .content(ReaderBlocks.content(blocks, ref: ref, cssClass: "maru-content"))
    }
}
