import Foundation
import NpiaCore

/// The shared page script (`hard-sources.js`) and its resources, copied byte for byte from the Android
/// app (see `ios/docs/hard-sources.md`). Swift only loads the script, calls its entry points and reads
/// the JSON they return.
enum HardScripts {
    static let folder = "HardResources"
    static let script = "hard-sources.js"
    static let qdnFont = "qdn/NotoSansCJKsc-Regular-hanzi.otf"
    static let qdnFrequency = "qdn/zh_char_frequency.txt"
    static let qdnVerified = "qdn/qdn_verified_maps.json"

    /// The script's source text, as the web views run it: the shared file plus the WebKit adjustment below.
    static let source: String = {
        guard let data = try? resource(script) else { return "" }
        return webKitAdjusted(String(decoding: data, as: UTF8.self))
    }()

    /// The glyph decoder tells a character a cipher font draws from one it lacks by drawing it with the font
    /// and with a family that does not exist: a character the font lacks comes out the same both ways. WebKit
    /// draws such a character with its sans-serif fallback when a loaded font is asked for it, but with its
    /// serif one when the family does not exist, so every character the font lacks looked like the font's own
    /// and was matched by shape (some wrongly). The adjustment compares with the generic families too. In
    /// Chromium the first comparison already decides, so the answers there do not change.
    ///
    /// The shared file stays as it is (it is Android's byte for byte); this changes the text only when the
    /// original line is there, so a shared file that carries the fix itself is used as it is.
    static func webKitAdjusted(_ script: String) -> String {
        let original = #"covered = !sameInk(renderGlyph(core.css, ch, 32), renderGlyph('"__qdn_no_such_family"', ch, 32));"#
        let adjusted = #"""
        { const own = renderGlyph(core.css, ch, 32);
              covered = !['"__qdn_no_such_family"', 'sans-serif', 'serif', 'system-ui', 'monospace']
                .some((family) => sameInk(own, renderGlyph(family, ch, 32))); }
        """#
        return script.replacingOccurrences(of: original, with: adjusted)
    }

    /// A resource by its path under `HardResources`, for example `qdn/zh_char_frequency.txt`.
    static func resource(_ name: String) throws -> Data {
        let path = name as NSString
        let directory = (path.deletingLastPathComponent as NSString)
        let subdirectory = directory.length == 0 ? folder : folder + "/" + (directory as String)
        guard let url = Bundle.module.url(forResource: path.lastPathComponent, withExtension: nil, subdirectory: subdirectory) else {
            throw WebEngineError.scriptFailed("missing resource \(name)")
        }
        return try Data(contentsOf: url)
    }

    /// A JavaScript string literal for `value`. JSON is one, except that older engines reject the two
    /// line separators inside a string.
    static func literal(_ value: String) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        let data = (try? encoder.encode([value])) ?? Data("[\"\"]".utf8)
        var text = String(decoding: data, as: UTF8.self)
        text.removeFirst()
        text.removeLast()
        let backslash = "\u{5c}"
        return text
            .replacingOccurrences(of: "\u{2028}", with: backslash + "u2028")
            .replacingOccurrences(of: "\u{2029}", with: backslash + "u2029")
    }

    /// JSON text of a dictionary or array, sorted so the same input gives the same text.
    static func json(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return "null" }
        return String(decoding: data, as: UTF8.self)
    }
}

/// A JSON object the page script returned, read leniently: a missing key, a null or a value of another
/// type gives the default, as the Android side's `coerceInputValues` does.
struct HardJSON: @unchecked Sendable {
    let raw: [String: Any]

    init(_ raw: [String: Any] = [:]) {
        self.raw = raw
    }

    /// The object in `data`; empty when it is null or not an object.
    init(data: Data) {
        let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        self.raw = (value as? [String: Any]) ?? [:]
    }

    init(text: String) {
        self.init(data: Data(text.utf8))
    }

    func has(_ key: String) -> Bool {
        guard let value = raw[key] else { return false }
        return !(value is NSNull)
    }

    func string(_ key: String) -> String {
        HTTPSourceSupport.string(raw[key])
    }

    func int(_ key: String) -> Int {
        HTTPSourceSupport.int(raw[key])
    }

    func bool(_ key: String) -> Bool {
        optionalBool(key) ?? false
    }

    func optionalBool(_ key: String) -> Bool? {
        switch raw[key] {
        case let value as Bool: return value
        case let value as NSNumber: return value.boolValue
        default: return nil
        }
    }

    func strings(_ key: String) -> [String] {
        (raw[key] as? [Any] ?? []).compactMap { $0 as? String }
    }

    func objects(_ key: String) -> [HardJSON] {
        (raw[key] as? [Any] ?? []).compactMap { ($0 as? [String: Any]).map(HardJSON.init) }
    }

    func object(_ key: String) -> HardJSON? {
        (raw[key] as? [String: Any]).map(HardJSON.init)
    }

    func stringMap(_ key: String) -> [String: String] {
        var result: [String: String] = [:]
        for (name, value) in raw[key] as? [String: Any] ?? [:] {
            if let text = value as? String { result[name] = text }
        }
        return result
    }

    /// The raw value of `key` (a nested object or array), for passing back to the script.
    func any(_ key: String) -> Any? {
        guard let value = raw[key], !(value is NSNull) else { return nil }
        return value
    }
}

extension WebPageDriving {
    /// Defines `window.__npiaHard` when the page does not have it yet. It normally does: the script is a
    /// document-start script (`addMainFrameInitScript`), which also puts the font hook in place before the
    /// reader runs. This covers the blank decoder page. Evaluating the script into a page with a strict
    /// content security policy can fail, which is why the document-start route is the main one.
    func ensureHard() async throws {
        let present = try await evaluateJSON("return !!(window.__npiaHard && window.__npiaHard.version);")
        if String(decoding: present, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines) != "true" {
            _ = try await evaluateJSON("(0, eval)(\(HardScripts.literal(HardScripts.source))); return true;")
        }
    }

    /// The JSON text of an entry point's result, for example `qdn.pageState()`.
    func hardData(_ call: String) async throws -> Data {
        try await ensureHard()
        return try await evaluateJSON("return await window.__npiaHard.\(call);")
    }

    /// An entry point that returns an object (empty when it returned something else).
    func hardObject(_ call: String) async throws -> HardJSON {
        HardJSON(data: try await hardData(call))
    }

    /// An entry point that returns an object or null.
    func hardOptionalObject(_ call: String) async throws -> HardJSON? {
        let data = try await hardData(call)
        let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        return (value as? [String: Any]).map(HardJSON.init)
    }

    func hardBool(_ call: String) async throws -> Bool {
        let data = try await hardData(call)
        let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        return (value as? Bool) ?? (value as? NSNumber)?.boolValue ?? false
    }

    func hardStrings(_ call: String) async throws -> [String] {
        let data = try await hardData(call)
        let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        return (value as? [Any] ?? []).compactMap { $0 as? String }
    }

    /// Runs a long entry point as a task and polls it (see `tasks` in the script), so no single script call
    /// outlives the web view's patience. Returns the result's JSON text. `maxPolls` bounds the wait.
    func hardTask(_ path: String, argsJSON: String, pollSeconds: TimeInterval = 0.25, maxPolls: Int = 480) async throws -> String {
        try await ensureHard()
        let started = try await evaluateJSON("return window.__npiaHard.tasks.start(\(HardScripts.literal(path)), \(argsJSON));")
        let ticket = HardJSON(data: started).string("ticket")
        guard !ticket.isEmpty else { throw WebEngineError.scriptFailed("\(path) did not start") }
        for _ in 0..<max(1, maxPolls) {
            try Task.checkCancellation()
            let polled = HardJSON(data: try await evaluateJSON("return window.__npiaHard.tasks.poll(\(HardScripts.literal(ticket)));"))
            switch polled.string("state") {
            case "done":
                return polled.has("json") ? polled.string("json") : "null"
            case "error":
                let message = polled.string("error")
                throw WebEngineError.scriptFailed(message.isEmpty ? "\(path) failed" : message)
            default:
                break
            }
            try await HTTPSourceSupport.sleep(seconds: pollSeconds)
        }
        throw WebEngineError.scriptFailed("\(path) timed out")
    }
}
