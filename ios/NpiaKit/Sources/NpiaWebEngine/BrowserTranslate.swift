import Foundation

/// A language the Browser tab can translate pages into (a Google Translate language code).
public struct TranslateLanguage: Sendable, Equatable, Identifiable {
    public let code: String
    public let name: String
    public var id: String { code }

    public init(code: String, name: String) {
        self.code = code
        self.name = name
    }
}

/// How the page translation is going; the Browser tab polls the page for it.
/// How the page translation is going. `unchanged`: the translator changed nothing (the page may be in
/// that language already). `failed`: nothing was translated in time, with no sign the site refused it.
/// `blocked`: the site's security policy refused the translator.
public enum TranslateStatus: Sendable, Equatable {
    case off, working, translated, unchanged, failed, blocked
}

/// Page translation for the Browser tab, which has none of its own.
///
/// The page is translated in place: `browser-translate.js` loads Google's website translator
/// (`translate_a/element.js`) into it, or uses the copy a site loads for itself, and picks the target
/// language through the translator's select box (`.goog-te-combo`), so the real site (sign-in, the
/// app's buttons) stays. Sites whose
/// security policy refuses the translator get a translated copy instead, served by Google from
/// `<site>.translate.goog`; the helpers below build that address and map it back to the original
/// site, which downloads and the link cart need. The Android app (`BrowserTranslate.kt`) does the same.
public enum BrowserTranslate {
    public static let defaultLanguage = "en"
    /// The UserDefaults key of the language setting.
    public static let storageKey = "translateLanguage"

    public static let languages: [TranslateLanguage] = [
        TranslateLanguage(code: "en", name: "English"),
        TranslateLanguage(code: "ko", name: "Korean"),
        TranslateLanguage(code: "ja", name: "Japanese"),
        TranslateLanguage(code: "zh-CN", name: "Chinese (Simplified)"),
        TranslateLanguage(code: "zh-TW", name: "Chinese (Traditional)"),
        TranslateLanguage(code: "es", name: "Spanish"),
        TranslateLanguage(code: "fr", name: "French"),
        TranslateLanguage(code: "de", name: "German"),
        TranslateLanguage(code: "pt", name: "Portuguese"),
        TranslateLanguage(code: "ru", name: "Russian"),
        TranslateLanguage(code: "id", name: "Indonesian"),
        TranslateLanguage(code: "vi", name: "Vietnamese"),
        TranslateLanguage(code: "th", name: "Thai"),
        TranslateLanguage(code: "ar", name: "Arabic"),
    ]

    /// The language for a stored code; English when it is missing or unknown.
    public static func language(_ code: String?) -> TranslateLanguage {
        languages.first { $0.code.caseInsensitiveCompare(code ?? "") == .orderedSame } ?? languages[0]
    }

    private static let proxySuffix = ".translate.goog"
    private static let translateHost = "translate.google.com"

    // MARK: - Addresses

    /// The address of Google's translated copy of `pageURL`.
    public static func copyAddress(of pageURL: String, language: String) -> String {
        "https://" + translateHost + "/translate?sl=auto&tl=" + encode(language) + "&u=" + encode(pageURL)
    }

    public static func copyURL(of pageURL: URL, language: String) -> URL? {
        URL(string: copyAddress(of: pageURL.absoluteString, language: language))
    }

    /// True for a page Google serves as a translated copy (`<site>.translate.goog`, or its `translate` address).
    public static func isTranslatedCopy(_ url: URL) -> Bool {
        originalAddress(url.absoluteString) != nil
    }

    public static func originalURL(of url: URL) -> URL? {
        originalAddress(url.absoluteString).flatMap { URL(string: $0) }
    }

    /// The original site's address for a translated copy, or nil when `address` is not one.
    /// - `global-example-com.translate.goog/novel/1?_x_tr_sl=auto&_x_tr_tl=en` is `https://global.example.com/novel/1`:
    ///   the host's dots are dashes and its own dashes are doubled; Google's `_x_tr_*` query parameters are dropped.
    /// - `translate.google.com/translate?u=<address>` is the address in `u`.
    public static func originalAddress(_ address: String) -> String? {
        guard let parts = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parts.host?.lowercased() else { return nil }
        if host == translateHost {
            guard parts.path == "/translate" else { return nil }
            let target = queryPairs(parts.percentEncodedQuery).first { $0.name == "u" }?.value ?? ""
            return target.hasPrefix("http://") || target.hasPrefix("https://") ? target : nil
        }
        guard host.hasSuffix(proxySuffix) else { return nil }
        let label = String(host.dropLast(proxySuffix.count))
        if label.isEmpty || label.contains(".") { return nil }
        let original = label.components(separatedBy: "--")
            .map { $0.replacingOccurrences(of: "-", with: ".") }
            .joined(separator: "-")
        let query = queryPairs(parts.percentEncodedQuery).filter { !$0.name.hasPrefix("_x_tr_") }
        var result = "https://" + original
        result += parts.percentEncodedPath.isEmpty ? "/" : parts.percentEncodedPath
        if !query.isEmpty { result += "?" + query.map { $0.raw }.joined(separator: "&") }
        if let fragment = parts.percentEncodedFragment { result += "#" + fragment }
        return result
    }

    private struct QueryPair {
        let name: String
        let value: String
        /// As written in the address.
        let raw: String
    }

    private static func queryPairs(_ rawQuery: String?) -> [QueryPair] {
        (rawQuery ?? "").split(separator: "&").map { part in
            let text = String(part)
            let equals = text.firstIndex(of: "=")
            let name = equals.map { String(text[..<$0]) } ?? text
            let value = equals.map { String(text[text.index(after: $0)...]) } ?? ""
            return QueryPair(name: decode(name), value: decode(value), raw: text)
        }
    }

    /// Like Java's URLEncoder: everything but letters, digits and `.-*_` is escaped, a space is `+`.
    private static func encode(_ text: String) -> String {
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-*_")
        let escaped = text.addingPercentEncoding(withAllowedCharacters: allowed) ?? text
        return escaped.replacingOccurrences(of: "%20", with: "+")
    }

    private static func decode(_ text: String) -> String {
        text.replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? text
    }

    // MARK: - The script

    /// `browser-translate.js` followed by the call that starts translating into `language`.
    public static func injection(language: String) -> String {
        let code = language.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return PageScripts.text("browser-translate") + "\nwindow.NpiaTranslate && window.NpiaTranslate.start('" + code + "');"
    }

    /// The script that turns translating off (the app then reloads the page).
    public static let stopScript = "window.NpiaTranslate && window.NpiaTranslate.stop()"

    /// What the page says about the translation; 'loading' until the script is in.
    public static let statusScript = "window.NpiaTranslate ? window.NpiaTranslate.status() : 'loading'"
}
