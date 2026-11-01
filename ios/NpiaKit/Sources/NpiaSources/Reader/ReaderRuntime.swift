import CryptoKit
import Foundation
import JavaScriptCore
import NpiaCore

/// One JavaScriptCore context running the desktop rules bundle.
///
/// Mirrors the desktop's injection order (`gm_stubs.js`, `rules-lib.js`, `bridge.js`) with
/// `reader_env.js`, `reader_dom.js` and `reader_web.js` standing in for the browser page, and
/// `reader_host.js` exposing the calls Swift makes. Rule modules capture the page document when
/// they first load, so use one runtime per book.
///
/// All JavaScript runs on a private serial queue. Requests go through `HTTPFetching`.
final class ReaderRuntime: @unchecked Sendable {
    enum RuntimeError: Error, LocalizedError, Equatable {
        case loadFailed(String)
        case script(String)
        case timedOut(String)

        var errorDescription: String? {
            switch self {
            case .loadFailed(let message): return "The reader rules failed to load: \(message)"
            case .script(let message): return message
            case .timedOut(let call): return "The reader rules did not finish \(call) in time."
            }
        }
    }

    private let queue = DispatchQueue(label: "npia.reader.js")
    private let http: any HTTPFetching
    private let logger: any JobLogging
    private let verbose: Bool
    private var context: JSContext!
    private var lastException: String?
    private var calls: [Int: CheckedContinuation<String, Error>] = [:]
    private var requests: [Int: Task<Void, Never>] = [:]
    private var closed = false

    private static let tokenLock = NSLock()
    private static var lastToken = 0

    init(http: any HTTPFetching, logger: any JobLogging = NullLogger(), verbose: Bool = false) throws {
        self.http = http
        self.logger = logger
        self.verbose = verbose
        try queue.sync { try setUp() }
    }

    deinit {
        for task in requests.values { task.cancel() }
    }

    /// Cancels in-flight requests and fails pending calls. The runtime is unusable afterwards.
    func close() {
        queue.async {
            self.closed = true
            for task in self.requests.values { task.cancel() }
            self.requests.removeAll()
            let pending = self.calls
            self.calls.removeAll()
            for continuation in pending.values { continuation.resume(throwing: CancellationError()) }
        }
    }

    // MARK: - Calls

    /// Calls `__npia_host[name](...args)` and returns its result (JSON text unless it returned a string).
    func call(_ name: String, _ arguments: [Any], timeout: TimeInterval = 300) async throws -> String {
        let argsJSON = String(decoding: try JSONSerialization.data(withJSONObject: arguments), as: UTF8.self)
        let token = Self.makeToken()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
                queue.async {
                    if self.closed {
                        continuation.resume(throwing: CancellationError())
                        return
                    }
                    self.calls[token] = continuation
                    self.queue.asyncAfter(deadline: .now() + timeout) {
                        self.finish(token, .failure(RuntimeError.timedOut(name)))
                    }
                    self.lastException = nil
                    self.context.objectForKeyedSubscript("__npia_call")?.call(withArguments: [token, name, argsJSON])
                    if let exception = self.lastException {
                        self.finish(token, .failure(RuntimeError.script(exception)))
                    }
                }
            }
        } onCancel: {
            queue.async { self.finish(token, .failure(CancellationError())) }
        }
    }

    /// Like `call`, but blocks the caller. Only for calls that settle without network or timers.
    func callSync(_ name: String, _ arguments: [Any]) throws -> String {
        let argsJSON = String(decoding: try JSONSerialization.data(withJSONObject: arguments), as: UTF8.self)
        let token = Self.makeToken()
        return try queue.sync {
            var outcome: Result<String, Error>?
            syncResults[token] = { outcome = $0 }
            defer { syncResults[token] = nil }
            lastException = nil
            context.objectForKeyedSubscript("__npia_call")?.call(withArguments: [token, name, argsJSON])
            // Promise jobs run when the outermost call returns; pump in case a host defers them.
            var spins = 0
            while outcome == nil, spins < 8 {
                context.evaluateScript("void 0")
                spins += 1
            }
            if let exception = lastException, outcome == nil { throw RuntimeError.script(exception) }
            guard let outcome else { throw RuntimeError.timedOut(name) }
            return try outcome.get()
        }
    }

    private var syncResults: [Int: (Result<String, Error>) -> Void] = [:]

    /// Evaluates a script on the runtime queue (tests and diagnostics).
    func evaluate(_ script: String) -> String? {
        queue.sync { context.evaluateScript(script)?.toString() }
    }

    private static func makeToken() -> Int {
        tokenLock.lock()
        defer { tokenLock.unlock() }
        lastToken += 1
        return lastToken
    }

    /// Queue-confined.
    private func finish(_ token: Int, _ result: Result<String, Error>) {
        if let sync = syncResults[token] {
            sync(result)
            return
        }
        guard let continuation = calls.removeValue(forKey: token) else { return }
        continuation.resume(with: result)
    }

    // MARK: - Setup

    /// The rules did not initialise; a downloaded copy that does this is set aside.
    private struct RulesFailure: Error {
        var message: String
    }

    private func setUp() throws {
        let store = EngineStore.shared
        let candidates = store.candidates(for: .novelDownloader)
        guard !candidates.isEmpty else { throw RuntimeError.loadFailed("rules-lib.js is missing") }
        var failure = RuntimeError.loadFailed("rules-lib.js did not initialise")
        for bundle in candidates {
            do {
                try setUp(rules: bundle)
                return
            } catch let error as RulesFailure {
                failure = RuntimeError.loadFailed(error.message)
                if bundle.updated {
                    logger.log(.warning, "[Reader] The rules update (\(bundle.label)) failed to load (\(error.message)); using the built-in copy.")
                    store.reject(bundle, reason: error.message)
                }
            }
        }
        throw failure
    }

    /// One attempt in a fresh context: the scripts in injection order with `rules` standing in
    /// for `rules-lib.js`. Only a failure of the rules themselves counts against them.
    private func setUp(rules: EngineBundle) throws {
        guard let context = JSContext() else { throw RuntimeError.loadFailed("JavaScriptCore is unavailable") }
        self.context = context
        context.name = "npia reader"
        context.exceptionHandler = { [weak self] _, exception in
            let message = exception?.toString() ?? "unknown error"
            let stack = exception?.objectForKeyedSubscript("stack")?.toString() ?? ""
            self?.lastException = message
            self?.logger.log(.warning, "[Reader] \(message) \(stack.prefix(300))")
        }
        context.setObject(makeNative(context), forKeyedSubscript: "__npia_native" as NSString)
        for script in try ReaderScripts.load(rules: rules) {
            let isRules = script.name == "rules-lib.js"
            lastException = nil
            context.evaluateScript(script.source, withSourceURL: URL(string: "npia-reader:///\(script.name)"))
            if let exception = lastException {
                if isRules { throw RulesFailure(message: exception) }
                throw RuntimeError.loadFailed("\(script.name): \(exception)")
            }
            if isRules {
                // rules-lib.js finishes initialising in promise jobs after its script returns.
                context.evaluateScript("void 0")
                guard context.evaluateScript("window.__ND_READY === true")?.toBool() == true else {
                    throw RulesFailure(message: "rules-lib.js did not initialise")
                }
            }
        }
        context.evaluateScript("void 0")
        guard context.evaluateScript("window.__ND_READY === true && window.__ND_BRIDGE_READY === true")?.toBool() == true else {
            throw RuntimeError.loadFailed("rules-lib.js did not initialise")
        }
    }

    private func invoke(_ function: String, _ arguments: [Any]) {
        guard !closed else { return }
        context.objectForKeyedSubscript(function)?.call(withArguments: arguments)
    }

    private func makeNative(_ context: JSContext) -> JSValue {
        let native = JSValue(newObjectIn: context)!

        let log: @convention(block) (String, String) -> Void = { [weak self] level, message in
            guard let self else { return }
            let parsed = LogLevel(rawValue: level) ?? .info
            if parsed == .debug && !self.verbose { return }
            self.logger.log(parsed, "[Reader] \(message)")
        }
        native.setObject(log, forKeyedSubscript: "log" as NSString)

        let timer: @convention(block) (Int, Double) -> Void = { [weak self] id, milliseconds in
            guard let self else { return }
            self.queue.asyncAfter(deadline: .now() + max(0, milliseconds) / 1000) { [weak self] in
                self?.invoke("__npia_fireTimer", [id])
            }
        }
        native.setObject(timer, forKeyedSubscript: "timer" as NSString)

        let decode: @convention(block) (String, String) -> String? = { base64, charset in
            guard let data = Data(base64Encoded: base64) else { return "" }
            return ReaderText.decode(data, charset: charset)
        }
        native.setObject(decode, forKeyedSubscript: "decode" as NSString)

        let digest: @convention(block) (String, String) -> String? = { algorithm, base64 in
            guard let data = Data(base64Encoded: base64) else { return nil }
            return ReaderText.digest(algorithm, data)?.base64EncodedString()
        }
        native.setObject(digest, forKeyedSubscript: "digest" as NSString)

        let cookies: @convention(block) (String) -> String = { urlString in
            guard let url = URL(string: urlString) else { return "" }
            return (HTTPCookieStorage.shared.cookies(for: url) ?? [])
                .filter { !$0.isHTTPOnly }
                .map { "\($0.name)=\($0.value)" }
                .joined(separator: "; ")
        }
        native.setObject(cookies, forKeyedSubscript: "cookies" as NSString)

        let setCookie: @convention(block) (String, String) -> Void = { urlString, value in
            guard let url = URL(string: urlString) else { return }
            let parsed = HTTPCookie.cookies(withResponseHeaderFields: ["Set-Cookie": value], for: url)
            HTTPCookieStorage.shared.setCookies(parsed, for: url, mainDocumentURL: nil)
        }
        native.setObject(setCookie, forKeyedSubscript: "setCookie" as NSString)

        let fetch: @convention(block) (String) -> Void = { [weak self] json in
            self?.startRequest(json)
        }
        native.setObject(fetch, forKeyedSubscript: "fetch" as NSString)

        let settle: @convention(block) (Int, Bool, String) -> Void = { [weak self] token, ok, value in
            guard let self else { return }
            self.finish(token, ok ? .success(value) : .failure(RuntimeError.script(value)))
        }
        native.setObject(settle, forKeyedSubscript: "settle" as NSString)

        return native
    }

    // MARK: - Requests

    private struct RequestSpec: Decodable {
        var id: Int
        var url: String
        var method: String?
        var headers: [String: String]?
        var body: String?
        var timeout: Double?
    }

    /// Queue-confined: called from JavaScript.
    private func startRequest(_ json: String) {
        guard let spec = try? JSONDecoder().decode(RequestSpec.self, from: Data(json.utf8)) else { return }
        guard let url = URL(string: spec.url) else {
            invoke("__npia_fetchDone", [spec.id, Self.encode(["error": "invalid URL \(spec.url)"])])
            return
        }
        let request = HTTPRequest(
            url: url,
            method: spec.method ?? "GET",
            headers: spec.headers ?? [:],
            body: spec.body.flatMap { Data(base64Encoded: $0) },
            timeout: (spec.timeout ?? 30000) / 1000
        )
        if verbose { logger.log(.debug, "[Reader] \(request.method) \(url.absoluteString)") }
        let http = self.http
        let id = spec.id
        let task = Task { [weak self] in
            let payload: [String: Any]
            do {
                let response = try await http.send(request)
                payload = [
                    "status": response.status,
                    "url": response.url.absoluteString,
                    "headers": Dictionary(response.headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { first, _ in first }),
                    "body": response.body.base64EncodedString(),
                ]
            } catch HTTPError.status(let status, let finalURL) {
                payload = ["status": status, "url": finalURL.absoluteString, "headers": [String: String](), "body": ""]
            } catch {
                payload = ["error": error.localizedDescription]
            }
            let text = Self.encode(payload)
            self?.queue.async {
                guard let self else { return }
                self.requests[id] = nil
                self.invoke("__npia_fetchDone", [id, text])
            }
        }
        requests[id] = task
    }

    private static func encode(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return "{\"error\":\"encoding\"}" }
        return String(decoding: data, as: UTF8.self)
    }
}

/// The bundled scripts, read once. `rules-lib.js` is the one engine script that can be replaced
/// by a downloaded update, so it comes from an `EngineBundle` instead.
enum ReaderScripts {
    struct Script {
        var name: String
        var source: String
    }

    /// Injection order; see `reader_env.js`.
    static let order = [
        "reader_env.js", "reader_dom.js", "reader_web.js",
        "gm_stubs.js", "reader_gm_patch.js", "rules-lib.js", "bridge.js",
        "reader_host.js",
    ]

    private static let cached: Result<[String: String], Error> = Result(catching: {
        var sources: [String: String] = [:]
        for name in order where name != "rules-lib.js" {
            let base = (name as NSString).deletingPathExtension
            guard let url = Bundle.module.url(forResource: base, withExtension: "js", subdirectory: "Resources")
                ?? Bundle.module.url(forResource: base, withExtension: "js")
            else {
                throw ReaderRuntime.RuntimeError.loadFailed("missing resource \(name)")
            }
            sources[name] = try String(contentsOf: url, encoding: .utf8)
        }
        return sources
    })

    /// One of the bundled scripts other than `rules-lib.js`.
    static func source(_ name: String) throws -> String {
        guard let source = try cached.get()[name] else {
            throw ReaderRuntime.RuntimeError.loadFailed("missing resource \(name)")
        }
        return source
    }

    /// Every script in injection order, with `rules` as `rules-lib.js`.
    static func load(rules: EngineBundle) throws -> [Script] {
        let sources = try cached.get()
        return try order.map { name -> Script in
            if name == "rules-lib.js" { return Script(name: name, source: try rules.read()) }
            guard let source = sources[name] else {
                throw ReaderRuntime.RuntimeError.loadFailed("missing resource \(name)")
            }
            return Script(name: name, source: source)
        }
    }
}

/// Charset decoding and digests for the JavaScript side.
enum ReaderText {
    static func encoding(for charset: String) -> String.Encoding? {
        let name = charset.trimmingCharacters(in: .whitespaces).lowercased()
        switch name {
        case "", "utf-8", "utf8", "unicode-1-1-utf-8": return .utf8
        // Decode with the supersets browsers use for these labels.
        case "gbk", "gb2312", "gb_2312-80", "x-gbk", "gb18030", "chinese", "csgb2312", "iso-ir-58":
            return cfEncoding(.GB_18030_2000)
        case "big5", "big5-hkscs", "x-x-big5", "cn-big5":
            return cfEncoding(.big5_HKSCS_1999)
        case "euc-kr", "ks_c_5601-1987", "korean", "cp949", "windows-949", "ksc5601":
            return cfEncoding(.dosKorean)
        case "shift_jis", "shift-jis", "sjis", "ms_kanji", "windows-31j", "cp932", "x-sjis":
            return cfEncoding(.dosJapanese)
        case "iso-8859-1", "latin1", "us-ascii", "ascii", "windows-1252", "cp1252":
            return .windowsCP1252
        default:
            let cf = CFStringConvertIANACharSetNameToEncoding(name as CFString)
            guard cf != kCFStringEncodingInvalidId else { return nil }
            return String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(cf))
        }
    }

    private static func cfEncoding(_ encoding: CFStringEncodings) -> String.Encoding {
        String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(encoding.rawValue)))
    }

    static func decode(_ data: Data, charset: String) -> String {
        guard let encoding = encoding(for: charset), encoding != .utf8 else {
            return String(decoding: data, as: UTF8.self)
        }
        if let text = String(data: data, encoding: encoding) { return text }
        // Strict decoders reject stray bytes; fall back to a lossy pass.
        if let text = NSString(data: data, encoding: encoding.rawValue) { return text as String }
        return String(decoding: data, as: UTF8.self)
    }

    static func digest(_ algorithm: String, _ data: Data) -> Data? {
        switch algorithm.uppercased().replacingOccurrences(of: "-", with: "") {
        case "SHA1": return Data(Insecure.SHA1.hash(data: data))
        case "SHA256": return Data(SHA256.hash(data: data))
        case "SHA384": return Data(SHA384.hash(data: data))
        case "SHA512": return Data(SHA512.hash(data: data))
        default: return nil
        }
    }
}
