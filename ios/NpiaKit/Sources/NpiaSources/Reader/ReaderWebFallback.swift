import Foundation
import NpiaCore

/// Runs the rules in a real web view, exactly like the desktop's Playwright path: load the book
/// page, inject `gm_stubs.js`, `rules-lib.js` and `bridge.js`, then call `__ND_parseBook()` and
/// `__ND_parseChapter()`.
///
/// Used when the script engine cannot read a book, for example sites that build their chapter
/// list with their own JavaScript. Unverified until `NpiaWebEngine` provides pages.
final class ReaderWebFallback: @unchecked Sendable {
    static let profile = "reader"
    static let injected = ["gm_stubs.js", "rules-lib.js", "bridge.js"]

    private let page: any WebPageDriving
    private let pageURL: URL

    private init(page: any WebPageDriving, pageURL: URL) {
        self.page = page
        self.pageURL = pageURL
    }

    static func openBook(_ url: URL, context: SourceContext) async throws -> (ReaderBookPayload, ReaderWebFallback) {
        let page = try await context.web.newPage(profile: profile)
        do {
            try await page.load(url, until: .domContentLoaded, timeout: 30)
            let sources = try ReaderScripts.load().filter { injected.contains($0.name) }.map(\.source)
            let body = """
            if (!window.__ND_BRIDGE_READY) {
              const sources = \(jsonLiteral(sources));
              for (const source of sources) (0, eval)(source);
            }
            for (let i = 0; i < 200 && !window.__ND_READY; i++) await new Promise((r) => setTimeout(r, 50));
            const data = JSON.parse(await window.__ND_parseBook());
            data.pageUrl = location.href;
            data.charset = document.characterSet;
            return data;
            """
            let data = try await page.evaluateJSON(body)
            let payload = try ReaderBookPayload.decode(String(decoding: data, as: UTF8.self))
            let current = await page.currentURL() ?? url
            return (payload, ReaderWebFallback(page: page, pageURL: current))
        } catch {
            await page.close()
            throw error
        }
    }

    func chapter(_ info: ReaderBookSession.ChapterInfo) async throws -> [ReaderBlock] {
        let isPaid = info.isPaid.map { $0 ? "true" : "false" } ?? "undefined"
        let body = """
        return JSON.parse(await window.__ND_parseChapter(\(Self.jsonLiteral(info.url)), \(Self.jsonLiteral(info.name)), \(info.isVIP), \(isPaid)));
        """
        let data = try await page.evaluateJSON(body)
        let result = try JSONDecoder().decode(WebChapter.self, from: data)
        if let error = result.error { throw SourceError.parse(error) }
        var attachments: [String: String] = [:]
        for image in result.images ?? [] {
            if let name = image.name, let url = image.url { attachments[name] = url }
        }
        let base = URL(string: info.url, relativeTo: pageURL)?.absoluteURL ?? pageURL
        let blocks = try ReaderRules.shared.blocks(html: result.contentHtml ?? "", baseURL: base, attachments: attachments)
        if blocks.isEmpty, let text = result.contentText {
            return text.components(separatedBy: .newlines)
                .map { $0.replacingOccurrences(of: "\u{a0}", with: " ").trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
                .map(ReaderBlock.text)
        }
        return blocks
    }

    func close() async {
        await page.close()
    }

    private struct WebChapter: Decodable {
        struct Image: Decodable {
            var name: String?
            var url: String?
        }

        var error: String?
        var contentHtml: String?
        var contentText: String?
        var images: [Image]?
    }

    private static func jsonLiteral(_ value: Any) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed])) ?? Data("null".utf8)
        // U+2028/U+2029 are line terminators in older JavaScript string literals.
        return String(decoding: data, as: UTF8.self)
            .replacingOccurrences(of: "\u{2028}", with: "\\u2028")
            .replacingOccurrences(of: "\u{2029}", with: "\\u2029")
    }
}
