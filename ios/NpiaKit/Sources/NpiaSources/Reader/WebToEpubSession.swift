import Foundation
import NpiaCore

/// Runs the WebToEpub parsers (`webtoepub-lib.js`) in the book's page.
///
/// The parsers need a real DOM, so they only run in a web view. The bundle is evaluated in the
/// loaded book page; `window.__W2E_parseBook()` returns the same book shape as the
/// novel-downloader bridge and `window.__W2E_parseChapter(json)` returns `{chapterName, contentHtml,
/// contentText}`, with images kept as absolute `<img src>` URLs. Requests the page's own `fetch`
/// refuses (CORS) go through `window.__W2E_transport`, which the engine page answers from Swift.
final class WebToEpubSession: @unchecked Sendable {
    private let owner: EnginePage
    private let page: any WebPageDriving
    private let pageURL: URL
    let label: String

    private init(owner: EnginePage, page: any WebPageDriving, pageURL: URL, label: String) {
        self.owner = owner
        self.page = page
        self.pageURL = pageURL
        self.label = label
    }

    /// Evaluates the bundle in the shared page and reads the book. The session takes the page over
    /// only when the caller keeps the session (see `EnginePage.handOff`).
    static func openBook(_ url: URL, context: SourceContext, page shared: EnginePage) async throws -> (ReaderBookPayload, WebToEpubSession) {
        let page = try await shared.acquire()
        let bundle = try await inject(into: page, logger: context.logger)
        let data = try await evaluate(page, """
        const data = JSON.parse(await window.__W2E_parseBook());
        data.pageUrl = location.href;
        return data;
        """)
        if let failure = try? JSONDecoder().decode(Failure.self, from: data), let message = failure.error {
            if failure.noParser == true { throw EngineNoMatch(message: message) }
            throw SourceError.parse("WebToEpub: \(message)")
        }
        let payload = try ReaderBookPayload.decode(String(decoding: data, as: UTF8.self))
        let current = await page.currentURL() ?? url
        context.logger.log(.info, "[WebToEpub] \(payload.parser ?? "parser") (parsers \(bundle.label)), \(payload.chapters.count) chapters")
        for warning in payload.warnings ?? [] {
            context.logger.log(.debug, "[WebToEpub] \(warning)")
        }
        return (payload, WebToEpubSession(owner: shared, page: page, pageURL: current, label: bundle.label))
    }

    /// Loads the bundle into `page` and returns the copy that loaded. A downloaded update that
    /// fails to load is set aside and the copy shipped with the app is tried instead.
    static func inject(into page: any WebPageDriving, logger: any JobLogging, store: EngineStore = .shared) async throws -> EngineBundle {
        let engine = RuleEngine.webtoepub
        let candidates = store.candidates(for: engine)
        guard !candidates.isEmpty else {
            throw SourceError.parse("The WebToEpub parsers are missing from this build.")
        }
        var failure = "the bundle did not load"
        for bundle in candidates {
            do {
                let source = try bundle.read()
                let data = try await page.evaluateJSON(loadScript(source))
                let state = try JSONDecoder().decode(LoadState.self, from: data)
                if state.ready { return bundle }
                failure = state.error ?? "the bundle did not load"
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                if let known = error as? WebEngineError {
                    failure = Self.describe(known)
                } else {
                    failure = error.localizedDescription
                }
            }
            if bundle.updated {
                logger.log(.warning, "[WebToEpub] The parsers update (\(bundle.label)) failed to load (\(failure)); using the built-in copy.")
                store.reject(bundle, reason: failure)
            } else {
                logger.log(.warning, "[WebToEpub] The parsers failed to load: \(failure)")
            }
        }
        throw SourceError.parse("The WebToEpub parsers failed to load: \(failure)")
    }

    /// The page-side half of the transport, then the bundle, then its readiness.
    static func loadScript(_ source: String) -> String {
        """
        if (typeof window.__W2E_transport !== "function" && window.webkit && window.webkit.messageHandlers
            && window.webkit.messageHandlers.\(EnginePage.transportName)) {
          window.__W2E_transport = async function (details) {
            const reply = await window.webkit.messageHandlers.\(EnginePage.transportName).postMessage(JSON.stringify(details));
            return JSON.parse(reply);
          };
        }
        try {
          (0, eval)(\(ReaderWebFallback.jsonLiteral(source)));
        } catch (error) {
          return { ready: false, error: String((error && error.message) || error) };
        }
        const ready = window.__W2E_READY === true && window.__W2E_API === \(RuleEngine.webtoepub.api);
        return { ready: ready, error: ready ? null : ((window.__W2E_LOAD_ERROR || {}).message || null) };
        """
    }

    /// Runs a script in the page; a script or navigation failure reads as a WebToEpub failure.
    private static func evaluate(_ page: any WebPageDriving, _ body: String) async throws -> Data {
        do {
            return try await page.evaluateJSON(body)
        } catch let error as WebEngineError {
            throw SourceError.parse("WebToEpub: \(describe(error))")
        }
    }

    private static func describe(_ error: WebEngineError) -> String {
        switch error {
        case .scriptFailed(let message): return message
        case .timeout(let url): return "timed out loading \(url.absoluteString)"
        case .navigationFailed(let message): return message
        case .unavailable: return "no web view is available"
        }
    }

    /// One chapter's text and image blocks, in the shape the novel-downloader engines produce.
    func chapter(_ info: ReaderBookSession.ChapterInfo) async throws -> [RuleBlock] {
        let item = Self.itemJSON(info)
        let data = try await Self.evaluate(page, "return JSON.parse(await window.__W2E_parseChapter(\(ReaderWebFallback.jsonLiteral(item))));")
        return try Self.blocks(from: data, info: info, pageURL: pageURL)
    }

    /// Several chapters in one page call (`__W2E_parseChapterBatch`); a chapter that failed has its own error.
    func chapters(_ infos: [ReaderBookSession.ChapterInfo]) async throws -> [Result<[RuleBlock], Error>] {
        let items = "[" + infos.map { Self.itemJSON($0) }.joined(separator: ",") + "]"
        let data = try await Self.evaluate(page, "return JSON.parse(await window.__W2E_parseChapterBatch(\(ReaderWebFallback.jsonLiteral(items))));")
        // The bridge answers with one JSON string per chapter.
        let replies = try JSONDecoder().decode([String].self, from: data)
        var results: [Result<[RuleBlock], Error>] = []
        for (offset, info) in infos.enumerated() {
            guard offset < replies.count else {
                results.append(.failure(SourceError.parse("WebToEpub gave no result for chapter \(offset + 1).")))
                continue
            }
            results.append(Result { try Self.blocks(from: Data(replies[offset].utf8), info: info, pageURL: pageURL) })
        }
        return results
    }

    func close() async {
        await owner.finish()
    }

    // MARK: - Parsing

    static func itemJSON(_ info: ReaderBookSession.ChapterInfo) -> String {
        let object: [String: Any] = ["url": info.url, "name": info.name]
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self)
    }

    static func blocks(from data: Data, info: ReaderBookSession.ChapterInfo, pageURL: URL) throws -> [RuleBlock] {
        let result = try JSONDecoder().decode(ReaderWebChapter.self, from: data)
        if let error = result.error { throw SourceError.parse("WebToEpub: \(error)") }
        let base = URL(string: info.url, relativeTo: pageURL)?.absoluteURL ?? pageURL
        return try result.blocks(base: base)
    }

    private struct Failure: Decodable {
        var error: String?
        var noParser: Bool?
    }

    private struct LoadState: Decodable {
        var ready: Bool
        var error: String?
    }
}
