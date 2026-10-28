import Foundation
import NpiaCore

/// Runs the rules in a real web view, exactly like the desktop's Playwright path: load the book
/// page, inject `gm_stubs.js`, `rules-lib.js` and `bridge.js`, then call `__ND_parseBook()` and
/// `__ND_parseChapter()`.
///
/// Used when the script engine cannot read a book, for example sites that build their chapter
/// list with their own JavaScript. The page is the one the engines share (`EnginePage`), so the
/// WebToEpub parsers can use it next when these rules find no chapters. Unverified until
/// `NpiaWebEngine` provides pages.
final class ReaderWebFallback: @unchecked Sendable {
    static let profile = EnginePage.profile

    private let owner: EnginePage
    private let page: any WebPageDriving
    private let pageURL: URL

    private init(owner: EnginePage, page: any WebPageDriving, pageURL: URL) {
        self.owner = owner
        self.page = page
        self.pageURL = pageURL
    }

    static func openBook(_ url: URL, context: SourceContext, page shared: EnginePage) async throws -> (ReaderBookPayload, ReaderWebFallback) {
        let page = try await shared.acquire()
        try await inject(into: page, logger: context.logger)
        let body = """
        const data = JSON.parse(await window.__ND_parseBook());
        data.pageUrl = location.href;
        data.charset = document.characterSet;
        return data;
        """
        let data = try await page.evaluateJSON(body)
        let payload = try ReaderBookPayload.decode(String(decoding: data, as: UTF8.self))
        let current = await page.currentURL() ?? url
        return (payload, ReaderWebFallback(owner: shared, page: page, pageURL: current))
    }

    /// Evaluates the stubs, the rules and the bridge in the page. A downloaded rules update that
    /// does not initialise is set aside and the copy shipped with the app is tried instead.
    static func inject(into page: any WebPageDriving, logger: any JobLogging, store: EngineStore = .shared) async throws {
        let ready = try await page.evaluateJSON("return !!window.__ND_BRIDGE_READY;")
        if (try? JSONDecoder().decode(Bool.self, from: ready)) == true { return }
        let stubs = try ReaderScripts.source("gm_stubs.js")
        let bridge = try ReaderScripts.source("bridge.js")
        _ = try await page.evaluateJSON("(0, eval)(\(jsonLiteral(stubs))); return true;")

        let candidates = store.candidates(for: .novelDownloader)
        guard !candidates.isEmpty else {
            throw SourceError.parse("The reader rules are missing from this build.")
        }
        var failure = "rules-lib.js did not initialise"
        var loaded = false
        for bundle in candidates {
            do {
                let source = try bundle.read()
                let data = try await page.evaluateJSON(rulesScript(source))
                let state = try JSONDecoder().decode(LoadState.self, from: data)
                if state.ready {
                    loaded = true
                    break
                }
                failure = state.error ?? failure
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                failure = error.localizedDescription
            }
            if bundle.updated {
                logger.log(.warning, "[Reader] The rules update (\(bundle.label)) failed to load (\(failure)); using the built-in copy.")
                store.reject(bundle, reason: failure)
            }
        }
        guard loaded else { throw SourceError.parse("The reader rules failed to load: \(failure)") }
        _ = try await page.evaluateJSON("(0, eval)(\(jsonLiteral(bridge))); return true;")
    }

    /// Evaluates `rules-lib.js` and waits for it to finish initialising.
    static func rulesScript(_ source: String) -> String {
        """
        try {
          (0, eval)(\(jsonLiteral(source)));
        } catch (error) {
          return { ready: false, error: String((error && error.message) || error) };
        }
        for (let i = 0; i < 200 && !window.__ND_READY; i++) await new Promise((r) => setTimeout(r, 50));
        return { ready: window.__ND_READY === true, error: null };
        """
    }

    func chapter(_ info: ReaderBookSession.ChapterInfo) async throws -> [RuleBlock] {
        let isPaid = info.isPaid.map { $0 ? "true" : "false" } ?? "undefined"
        let body = """
        return JSON.parse(await window.__ND_parseChapter(\(Self.jsonLiteral(info.url)), \(Self.jsonLiteral(info.name)), \(info.isVIP), \(isPaid)));
        """
        let data = try await page.evaluateJSON(body)
        let result = try JSONDecoder().decode(ReaderWebChapter.self, from: data)
        if let error = result.error { throw SourceError.parse(error) }
        let base = URL(string: info.url, relativeTo: pageURL)?.absoluteURL ?? pageURL
        return try result.blocks(base: base)
    }

    func close() async {
        await owner.finish()
    }

    private struct LoadState: Decodable {
        var ready: Bool
        var error: String?
    }

    static func jsonLiteral(_ value: Any) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed, .withoutEscapingSlashes])) ?? Data("null".utf8)
        // U+2028/U+2029 are line terminators in older JavaScript string literals.
        return String(decoding: data, as: UTF8.self)
            .replacingOccurrences(of: "\u{2028}", with: "\\u2028")
            .replacingOccurrences(of: "\u{2029}", with: "\\u2029")
    }
}

/// A chapter as the engines' bridges return it: `{chapterName, contentHtml, contentText, images}`
/// or `{error}`. The HTML is split into text and image blocks by the rules runtime, so images
/// keep their absolute `<img src>` URLs and the pipeline downloads them like any other image.
struct ReaderWebChapter: Decodable {
    struct Image: Decodable {
        var name: String?
        var url: String?
    }

    var error: String?
    var chapterName: String?
    var contentHtml: String?
    var contentText: String?
    var images: [Image]?

    func blocks(base: URL) throws -> [RuleBlock] {
        var attachments: [String: String] = [:]
        for image in images ?? [] {
            if let name = image.name, let url = image.url { attachments[name] = url }
        }
        let blocks = try ReaderRules.shared.blocks(html: contentHtml ?? "", baseURL: base, attachments: attachments)
        if blocks.isEmpty, let text = contentText {
            return text.components(separatedBy: .newlines)
                .map { $0.replacingOccurrences(of: "\u{a0}", with: " ").trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
                .map(RuleBlock.text)
        }
        return blocks
    }
}
