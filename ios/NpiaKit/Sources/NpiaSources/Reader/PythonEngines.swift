import Foundation
import NpiaCore

/// lightnovel-crawler, FanFicFare and gallery-dl: the desktop's own Python engine modules, run by
/// Pyodide in a hidden web page (scripts/python_host; the page and Pyodide are built into the Reader
/// resources by scripts/fetch_python_host.py).
///
/// The page is served on `noveldb-local://python/` (the page's `serveLocal`): its files from the
/// resources, `bundles/<engine>/` from the engine store (the bundle in use), and `transport?r=` for the
/// engines' requests. The engines' calls block (they are Python), so the page asks synchronously and
/// this answers through the engines' transport (`EngineTransport`). The page is opened once and kept
/// for a few minutes after its last use. Android's `PythonEngines` works the same way.
actor PythonEngines {
    static let shared = PythonEngines()
    static let base = URL(string: "\(LocalResponse.scheme)://python/")!
    private static let idle: UInt64 = 5 * 60 * 1_000_000_000
    private static let startTimeout: TimeInterval = 90

    private var page: (any WebPageDriving)?
    private var starting: Task<any WebPageDriving, Error>?
    private var closer: Task<Void, Never>?
    /// The context of the latest call: its HTTP client and web engine answer the page's requests.
    private var context: SourceContext?

    static func name(_ engine: SiteEngine) -> String? {
        switch engine {
        case .lncrawl: return "lncrawl"
        case .fanficfare: return "fanficfare"
        case .gallerydl: return "gallerydl"
        case .mangapeak: return "mangapeak"
        case .tako: return "tako"
        case .custom: return "custom"
        default: return nil
        }
    }

    /// This build has the Python engines' page (a build made without scripts/fetch_python_host.py has none).
    static var available: Bool { file("python-host.html") != nil }

    static let bundled: Set<SiteEngine> = [.mangapeak, .tako]
    static let mangaK = Module(name: "mangak", title: "MangaK reader")

    struct Module: Sendable {
        let name: String
        let title: String
    }
    private static let mangaKListings: Set<String> = ["about", "advanced-search", "api", "browse", "contact", "dmca",
        "genres", "home", "latest", "library", "login", "manga", "new", "popular", "privacy", "privacy-policy", "register",
        "search", "sitemap", "terms", "terms-of-service", "trending"]

    static func mangaKMatches(_ url: URL) -> Bool {
        guard ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host?.lowercased(), ["mangak.io", "www.mangak.io"].contains(host),
              url.user == nil, url.password == nil, url.port == nil else { return false }
        let path = URLComponents(url: url, resolvingAgainstBaseURL: true)?.percentEncodedPath ?? ""
        let slug = path.split(separator: "/").first.map(String.init) ?? ""
        return !mangaKListings.contains(slug) && path.range(
            of: #"^/[a-z0-9][a-z0-9-]{0,220}(/chapter-[a-z0-9][a-z0-9.-]{0,220})?/?$"#,
            options: .regularExpression) != nil
    }

    static func listsBundled(_ engine: SiteEngine, url: URL) -> Bool {
        guard ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              let hostname = url.host?.lowercased() else { return false }
        let host = hostname.hasPrefix("www.") ? String(hostname.dropFirst(4)) : hostname
        switch (engine, host) {
        case (.mangapeak, "\u{0072}\u{006f}\u{006c}\u{0069}\u{0061}\u{0073}\u{0063}\u{0061}\u{006e}.com"):
            return url.path.hasPrefix("/manga/") || url.path.hasPrefix("/read/")
        case (.tako, "mangadex.org"): return url.path.hasPrefix("/title/") || url.path.hasPrefix("/chapter/")
        case (.tako, "mangahere.cc"), (.tako, "mangahere.com"), (.tako, "newm.mangahere.cc"):
            return url.path.hasPrefix("/manga/")
        default: return false
        }
    }

    /// The book at `url` read by `engine`; a site the engine doesn't list is refused before anything starts.
    func openBook(_ engine: SiteEngine, module: Module, url: URL, context: SourceContext, store: EngineStore = .shared) async throws -> ReaderBookPayload {
        let listed: Bool
        switch engine {
        case .mangapeak, .tako: listed = Self.listsBundled(engine, url: url)
        case .custom: listed = CustomEngines.listsPython(url)
        default:
            listed = engine.ruleEngine.map { EngineSites.lists($0, url, store: store) } ?? false
        }
        guard listed else {
            throw EngineNoMatch(message: "The \(engine.title) have no source for this site.")
        }
        return try await openBook(module, url: url, context: context)
    }

    func openBook(_ module: Module, url: URL, context: SourceContext) async throws -> ReaderBookPayload {
        let data = try await call(context, "parseBook", module.name, url.absoluteString)
        if let failure = try? JSONDecoder().decode(Failure.self, from: data), let message = failure.error {
            if failure.noParser == true { throw EngineNoMatch(message: "\(module.title): \(message)") }
            throw SourceError.parse("\(module.title): \(message)")
        }
        let payload = try ReaderBookPayload.decode(String(decoding: data, as: UTF8.self))
        context.logger.log(.info, "[Reader] \(module.title), \(payload.chapters.count) chapters")
        return payload
    }

    /// One chapter's text and image blocks.
    func chapter(_ module: Module, info: ReaderBookSession.ChapterInfo, bookURL: URL, context: SourceContext) async throws -> [RuleBlock] {
        let item = String(decoding: try JSONSerialization.data(withJSONObject: [
            "url": info.url, "name": info.name, "bookUrl": bookURL.absoluteString,
        ]), as: UTF8.self)
        let data = try await call(context, "parseChapter", module.name, item)
        let result = try JSONDecoder().decode(ReaderWebChapter.self, from: data)
        if let error = result.error { throw SourceError.parse("\(module.title): \(error)") }
        let base = URL(string: info.url, relativeTo: bookURL)?.absoluteURL ?? bookURL
        return try result.blocks(base: base)
    }

    private func call(_ context: SourceContext, _ function: String, _ name: String, _ argument: String) async throws -> Data {
        let page = try await page(context)
        defer { closeLater() }
        let literal = String(decoding: try JSONSerialization.data(withJSONObject: [name, argument]), as: UTF8.self)
        do {
            return try await page.evaluateJSON("const a = \(literal); return JSON.parse(await window.NovelDBPython.\(function)(a[0], a[1]));")
        } catch let error as WebEngineError {
            throw SourceError.parse("The Python engines: \(error)")
        }
    }

    /// Starts the engines in the background (a job whose engine list reaches one): their start overlaps
    /// the engines tried before them. Not at app launch: the running engines take a lot of memory.
    func warm(_ context: SourceContext) async {
        guard Self.available else { return }
        _ = try? await page(context)
        closeLater()
    }

    private func page(_ context: SourceContext) async throws -> any WebPageDriving {
        self.context = context
        closer?.cancel()
        if let page { return page }
        // One start at a time: a call while the engines start waits for that start.
        if let starting { return try await starting.value }
        let task = Task { try await self.start(context) }
        starting = task
        defer { starting = nil }
        let opened = try await task.value
        page = opened
        return opened
    }

    private func start(_ context: SourceContext) async throws -> any WebPageDriving {
        guard Self.available else { throw EngineNoMatch(message: "The Python engines are not in this build.") }
        context.logger.log(.info, "[Reader] Starting the Python engines…")
        let opened = try await context.web.newPage(profile: EnginePage.profile)
        await opened.serveLocal { [weak self] path, query in await self?.serve(path, query) }
        do {
            try await opened.load(Self.base.appendingPathComponent("python-host.html"), until: .load, timeout: Self.startTimeout)
            let data = try await opened.evaluateJSON("return await window.NovelDBPython.start();")
            let state = try JSONDecoder().decode(StartState.self, from: data)
            guard state.ready else { throw SourceError.parse("The Python engines did not start: \(state.error ?? "no answer")") }
            // In a worker the Catalog page keeps working while they run; in the page it pauses with them.
            context.logger.log(.info, state.worker == true ? "[Reader] Python engines started (in the background)"
                : "[Reader] Python engines started (without a worker: the Catalog may pause while they work)")
        } catch {
            await opened.close()
            throw error
        }
        return opened
    }

    private func closeLater() {
        closer?.cancel()
        closer = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.idle)
            guard !Task.isCancelled else { return }
            await self?.closeNow()
        }
    }

    private func closeNow() async {
        let open = page
        page = nil
        await open?.close()
    }

    /// The page's requests to its own address.
    private func serve(_ path: String, _ query: String) async -> LocalResponse? {
        if path == "transport" {
            guard let raw = query.split(separator: "&").first(where: { $0.hasPrefix("r=") })?.dropFirst(2),
                  let json = Self.base64URL(String(raw)) else { return nil }
            if let data = json.data(using: .utf8),
               let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
               request["op"] as? String == "customEngines" {
                let engines = CustomEngines.python().map {
                    ["id": $0.id, "label": $0.label, "source": $0.source, "sites": $0.sites] as [String: Any]
                }
                let reply = (try? JSONSerialization.data(withJSONObject: ["engines": engines])) ?? Data()
                return LocalResponse(mimeType: "application/json", body: reply)
            }
            guard let context else {
                return LocalResponse(mimeType: "application/json", body: Data(#"{"error":"the Python engines have no request context"}"#.utf8))
            }
            let reply = await EngineTransport.handle(json, http: context.http, web: context.web)
            return LocalResponse(mimeType: "application/json", body: Data(reply.utf8))
        }
        guard !path.contains("..") else { return nil }
        if path.hasPrefix("bundles/") {
            let parts = path.split(separator: "/").map(String.init)
            guard parts.count >= 3, let rule = RuleEngine(rawValue: parts[1]), let bundle = EngineStore.shared.current(rule) else { return nil }
            if parts[2] == "manifest.json" {
                var raw = bundle.manifest.raw
                raw["file"] = rule.lib
                guard let data = try? JSONSerialization.data(withJSONObject: raw) else { return nil }
                return LocalResponse(mimeType: "application/json", body: data)
            }
            guard let data = try? Data(contentsOf: bundle.path) else { return nil }
            return LocalResponse(mimeType: "application/zip", body: data)
        }
        guard let file = Self.file(path), let data = try? Data(contentsOf: file) else { return nil }
        return LocalResponse(mimeType: Self.mimeType(file.pathExtension), body: data)
    }

    static func file(_ path: String) -> URL? {
        let name = (path as NSString).deletingPathExtension
        let ext = (path as NSString).pathExtension
        let folder = (name as NSString).deletingLastPathComponent
        let base = (name as NSString).lastPathComponent
        for root in ["Resources/python", "python"] {
            let directory = folder.isEmpty ? root : root + "/" + folder
            if let found = Bundle.module.url(forResource: base, withExtension: ext, subdirectory: directory) { return found }
        }
        return nil
    }

    static func mimeType(_ ext: String) -> String {
        switch ext.lowercased() {
        case "html": return "text/html"
        case "js": return "text/javascript"
        case "json": return "application/json"
        case "wasm": return "application/wasm"
        case "py": return "text/plain"
        case "zip", "whl": return "application/zip"
        default: return "application/octet-stream"
        }
    }

    static func base64URL(_ text: String) -> String? {
        var value = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while value.count % 4 != 0 { value += "=" }
        guard let data = Data(base64Encoded: value) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private struct Failure: Decodable {
        var error: String?
        var noParser: Bool?
    }

    private struct StartState: Decodable {
        var ready: Bool
        var error: String?
        var worker: Bool?
    }
}
