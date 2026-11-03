import Foundation
import NpiaCore

/// How the app speaks to one page-script engine's bundle (the desktop's `PageScript`): the flags it
/// sets when it is ready (`window.<prefix>READY`, `<prefix>API`, `<prefix>LOAD_ERROR`), the transport
/// it calls for requests the page's own `fetch` refuses, and the functions that read a book and a
/// chapter. Each returns the novel-downloader bridge's shapes as JSON text, or `{error}`.
///
/// An engine with a rule `engine` is a downloaded, updatable bundle that lists its sites; one without
/// is a script shipped in `Reader/Resources` (`resource`) that tries any site: the generic fallback.
struct PageScript: Sendable {
    let siteEngine: SiteEngine
    let engine: RuleEngine?
    let prefix: String
    /// The `window` function the bundle calls for requests (answered from Swift).
    let transport: String
    /// Reads the book shown in the page; its result is the bridge's book JSON text.
    let parseBook: String
    /// The chapter function, given the chapter as JSON text (`{url, name}`).
    let parseChapter: String
    /// The bundled script (folder, file name) for an engine without a rule bundle.
    var resource: (folder: String, name: String)? = nil

    var title: String { engine?.title ?? siteEngine.title }

    /// The API number a bundled script without a manifest reports.
    static let bundledAPI = 1

    static let lnreader = PageScript(
        siteEngine: .lnreader, engine: .lnreader, prefix: "__LNR_", transport: "__LNR_transport",
        parseBook: "await window.__LNR_parseBook(location.href)", parseChapter: "window.__LNR_parseChapter"
    )

    /// `scripts/generic/generic-lib.js`, a port of the desktop's generic engine.
    static let generic = PageScript(
        siteEngine: .generic, engine: nil, prefix: "__GEN_", transport: "__GEN_transport",
        parseBook: "await window.__GEN_parseBook(location.href)", parseChapter: "window.__GEN_parseChapter",
        resource: ("generic", "generic-lib")
    )
}

/// A page-script engine (the LNReader plugins, the generic fallback) in the book's page. Its requests go through the
/// page's `fetch`, and through the engine page's transport (Swift) for sites the web view refuses,
/// as WebToEpub's do. Chapters keep their images as absolute `<img src>` URLs.
final class PageScriptSession: @unchecked Sendable {
    private let owner: EnginePage
    private let page: any WebPageDriving
    private let pageURL: URL
    private let script: PageScript
    let label: String

    private init(owner: EnginePage, page: any WebPageDriving, pageURL: URL, script: PageScript, label: String) {
        self.owner = owner
        self.page = page
        self.pageURL = pageURL
        self.script = script
        self.label = label
    }

    /// Evaluates the bundle in the shared page and reads the book. A site the engine doesn't list is
    /// refused before anything loads. The session takes the page over only when the caller keeps it.
    static func openBook(_ url: URL, script: PageScript, context: SourceContext, page shared: EnginePage,
                         store: EngineStore = .shared) async throws -> (ReaderBookPayload, PageScriptSession) {
        if let engine = script.engine, !EngineSites.lists(engine, url, store: store) {
            throw EngineNoMatch(message: "The \(engine.title) have no plugin for this site.")
        }
        let page = try await shared.acquire()
        let label: String
        if let engine = script.engine {
            label = try await inject(script, engine: engine, into: page, logger: context.logger, store: store).label
        } else {
            try await injectBundled(script, into: page)
            label = "built in"
        }
        let data = try await evaluate(page, script, """
        const data = JSON.parse(\(script.parseBook));
        data.pageUrl = data.pageUrl || location.href;
        return data;
        """)
        if let failure = try? JSONDecoder().decode(Failure.self, from: data), let message = failure.error {
            if failure.noParser == true { throw EngineNoMatch(message: message) }
            throw SourceError.parse("\(script.title): \(message)")
        }
        let payload = try ReaderBookPayload.decode(String(decoding: data, as: UTF8.self))
        let current = await page.currentURL() ?? url
        context.logger.log(.info, "[Reader] \(script.title) (\(label)), \(payload.chapters.count) chapters")
        return (payload, PageScriptSession(owner: shared, page: page, pageURL: current, script: script, label: label))
    }

    /// Loads a script shipped in `Reader/Resources` into `page`.
    static func injectBundled(_ script: PageScript, into page: any WebPageDriving) async throws {
        guard let resource = script.resource else {
            throw SourceError.parse("The \(script.title) are not in this build.")
        }
        let file = ["Resources/" + resource.folder, resource.folder].lazy
            .compactMap { Bundle.module.url(forResource: resource.name, withExtension: "js", subdirectory: $0) }
            .first
        guard let file, let source = try? String(contentsOf: file, encoding: .utf8) else {
            throw SourceError.parse("The \(script.title) are missing from this build.")
        }
        let data = try await evaluate(page, script, loadScript(script, source, api: PageScript.bundledAPI))
        let state = try JSONDecoder().decode(LoadState.self, from: data)
        guard state.ready else {
            throw SourceError.parse("The \(script.title) failed to load: \(state.error ?? "the script did not load")")
        }
    }

    /// Loads the bundle into `page` and returns the copy that loaded. A downloaded update that fails
    /// to load is set aside and the copy shipped with the app is tried instead.
    static func inject(_ script: PageScript, engine: RuleEngine, into page: any WebPageDriving, logger: any JobLogging,
                       store: EngineStore = .shared) async throws -> EngineBundle {
        let candidates = store.candidates(for: engine)
        guard !candidates.isEmpty else {
            throw SourceError.parse("The \(engine.title) are missing from this build.")
        }
        var failure = "the bundle did not load"
        for bundle in candidates {
            do {
                let source = try bundle.read()
                let data = try await page.evaluateJSON(loadScript(script, source, api: engine.api))
                let state = try JSONDecoder().decode(LoadState.self, from: data)
                if state.ready { return bundle }
                failure = state.error ?? "the bundle did not load"
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                failure = (error as? WebEngineError).map(describe) ?? error.localizedDescription
            }
            if bundle.updated {
                logger.log(.warning, "[Reader] The \(engine.title) update (\(bundle.label)) failed to load (\(failure)); using the built-in copy.")
                store.reject(bundle, reason: failure)
            } else {
                logger.log(.warning, "[Reader] The \(engine.title) failed to load: \(failure)")
            }
        }
        throw SourceError.parse("The \(engine.title) failed to load: \(failure)")
    }

    /// The page-side half of the transport, then the bundle, then its readiness.
    static func loadScript(_ script: PageScript, _ source: String, api: Int) -> String {
        let p = script.prefix
        return """
        if (typeof window.\(script.transport) !== "function" && window.webkit && window.webkit.messageHandlers
            && window.webkit.messageHandlers.\(EnginePage.transportName)) {
          window.\(script.transport) = async function (details) {
            const reply = await window.webkit.messageHandlers.\(EnginePage.transportName).postMessage(JSON.stringify(details));
            return JSON.parse(reply);
          };
        }
        if (!(window.\(p)READY === true && window.\(p)API === \(api))) {
          try {
            (0, eval)(\(ReaderWebFallback.jsonLiteral(source)));
          } catch (error) {
            return { ready: false, error: String((error && error.message) || error) };
          }
        }
        const ready = window.\(p)READY === true && window.\(p)API === \(api);
        return { ready: ready, error: ready ? null : ((window.\(p)LOAD_ERROR || {}).message || null) };
        """
    }

    private static func evaluate(_ page: any WebPageDriving, _ script: PageScript, _ body: String) async throws -> Data {
        do {
            return try await page.evaluateJSON(body)
        } catch let error as WebEngineError {
            throw SourceError.parse("\(script.title): \(describe(error))")
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
        let item = WebToEpubSession.itemJSON(info)
        let data = try await Self.evaluate(page, script, "return JSON.parse(await \(script.parseChapter)(\(ReaderWebFallback.jsonLiteral(item))));")
        let result = try JSONDecoder().decode(ReaderWebChapter.self, from: data)
        if let error = result.error { throw SourceError.parse("\(script.title): \(error)") }
        let base = URL(string: info.url, relativeTo: pageURL)?.absoluteURL ?? pageURL
        return try result.blocks(base: base)
    }

    func close() async {
        await owner.finish()
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
