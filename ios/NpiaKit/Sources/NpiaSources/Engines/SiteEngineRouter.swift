import Foundation
import NpiaCore

/// Opens a book link with the site engines in the user's order, falling back to the next one when
/// an engine fails or finds no chapters (the desktop's `parse_book`).
///
/// - The engines are the built-in scrapers (`native`), the novel-downloader rules and the
///   WebToEpub parsers; the user can reorder them and switch them off.
/// - When a built-in scraper owns the link, the JavaScript engines only follow it when they
///   list the site (see `EngineSites`), so a clear failure such as DRM or a missing sign-in is
///   not followed by browser attempts that cannot work. A link no built-in scraper owns tries
///   every enabled JavaScript engine.
/// - A built-in scraper that fails with a sign-in or block error (its abort signal) ends the
///   fallback, and the error reaches the caller. Cancellation always does.
/// - The JavaScript engines share one page load.
public enum SiteEngineRouter {
    /// A book and the adapter that reads its chapters.
    public struct Opened: Sendable {
        public var adapter: any SourceAdapter
        public var book: BookInfo
        public var engine: SiteEngine
    }

    public static func open(_ url: URL, context: SourceContext) async throws -> Opened {
        let enabled = context.settings.engines.enabled()
        guard !enabled.isEmpty else {
            throw SourceError.parse("Every site engine is switched off in Settings > Site engines.")
        }
        let nativeType = SourceCatalog.nativeAdapterType(for: url)
        let steps = plan(for: url, engines: context.settings.engines)
        guard !steps.isEmpty else { throw SourceError.unsupportedURL(url) }
        let page = EnginePage(url: url, context: context)
        let opened: Opened
        do {
            opened = try await run(steps: steps, log: { context.logger.log(.info, $0) }) { engine in
                switch engine {
                case .native:
                    guard let type = nativeType else { throw SourceError.unsupportedURL(url) }
                    let adapter = type.init()
                    let book = try await adapter.parseBook(url, context: context)
                    return Opened(adapter: adapter, book: book, engine: .native)
                case .novelDownloader, .webtoepub:
                    let session = try await ReaderSessions.shared.open(url, context: context, engines: [engine], page: page)
                    return Opened(adapter: ReaderAdapter(), book: session.book, engine: engine)
                }
            }
        } catch {
            await page.closeUnlessHandedOff()
            throw error
        }
        await page.closeUnlessHandedOff()
        return opened
    }

    // MARK: - Planning

    /// The engines `url` goes through with these settings, in order; empty when none can try it
    /// (Android's `SourceCatalog.resolve`). A web page no built-in scraper owns goes to every
    /// enabled JavaScript engine, even when no site list names it, because WebToEpub also
    /// recognises some sites by their page.
    public static func plan(for url: URL, engines: EngineSettings) -> [SiteEngine] {
        let isWebPage = ["http", "https"].contains(url.scheme?.lowercased() ?? "")
        let steps = plan(order: engines.enabled(), nativeOwnsURL: SourceCatalog.nativeAdapterType(for: url) != nil) { engine in
            guard let rules = engine.ruleEngine else { return false }
            return EngineSites.lists(rules, url)
        }
        return steps.filter { $0 == .native || isWebPage }
    }

    /// Some enabled site engine can try `url`: the links the Add screen accepts and a download needs.
    public static func canTry(_ url: URL, engines: EngineSettings) -> Bool {
        !plan(for: url, engines: engines).isEmpty
    }

    /// The engines to try, in order. Without a built-in scraper for the link (or with the
    /// built-in scrapers switched off) the native step is dropped and every JavaScript engine
    /// stays; with one, the JavaScript engines stay only when `lists` says they list the site.
    static func plan(order: [SiteEngine], nativeOwnsURL: Bool, lists: (SiteEngine) -> Bool) -> [SiteEngine] {
        var seen: [SiteEngine] = []
        for engine in order where !seen.contains(engine) { seen.append(engine) }
        let nativeOwner = nativeOwnsURL && seen.contains(.native)
        return seen.filter { engine in
            if engine == .native { return nativeOwner }
            return !nativeOwner || lists(engine)
        }
    }

    // MARK: - Running

    /// Runs `attempt` for each step until one returns a book with chapters.
    ///
    /// A book without chapters is kept and returned when nothing better turns up. An error moves
    /// on to the next step, except for cancellation and, from a built-in scraper, a sign-in or
    /// block error. When every step fails, the most useful error is thrown.
    static func run(
        steps: [SiteEngine],
        log: (String) -> Void,
        attempt: (SiteEngine) async throws -> Opened
    ) async throws -> Opened {
        var fallback: Opened?
        var errors: [(engine: SiteEngine, error: Error)] = []
        for (position, engine) in steps.enumerated() {
            try Task.checkCancellation()
            do {
                let opened = try await attempt(engine)
                if !opened.book.chapters.isEmpty { return opened }
                if fallback == nil { fallback = opened }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                if engine == .native, isAbort(error) { throw error }
                errors.append((engine, error))
                log("\(engine.title): \(error.localizedDescription)")
            }
            if position + 1 < steps.count {
                log("\(engine.title) found no chapters; trying \(steps[position + 1].title) next.")
            }
        }
        if let fallback { return fallback }
        throw surface(errors)
    }

    /// Errors that end the fallback when a built-in scraper raises them.
    static func isAbort(_ error: Error) -> Bool {
        switch error as? SourceError {
        case .loginRequired?, .blocked?: return true
        default: return false
        }
    }

    /// The error to show when every engine failed: the first real failure, else the first
    /// "no rule / no parser" answer.
    static func surface(_ errors: [(engine: SiteEngine, error: Error)]) -> Error {
        if let real = errors.first(where: { !($0.error is EngineNoMatch) }) { return real.error }
        if let first = errors.first { return first.error }
        return SourceError.parse("No site engine could read this page.")
    }
}
