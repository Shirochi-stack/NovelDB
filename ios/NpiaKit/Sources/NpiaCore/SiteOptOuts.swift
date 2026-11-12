import CryptoKit
import Foundation

/// Sites the app leaves alone because their owners asked.
///
/// - The list holds SHA-256 digests of host names, not the names. A host also covers its subdomains.
/// - `matches` is asked wherever a link is about to be read: downloads, the Catalog's reader and its
///   searches. The caller logs or shows `message` and reads nothing.
///
/// The desktop (`noveldb/core/site_optouts.py`) and Android (`SiteOptOuts.kt`) keep the same list;
/// keep the three in step.
public enum SiteOptOuts {
    public static let message = "Support for this site has been disabled at the request of the site owner."

    static let digests: Set<String> = [
        "83aea3497826ee11580bef0f3b60a7fe6c7de15b927319f536433ea7e89b865b",
    ]

    /// The address belongs to a site whose owner opted out.
    public static func matches(_ url: URL) -> Bool {
        matches(url.absoluteString)
    }

    /// The address (or bare host) belongs to a site whose owner opted out.
    public static func matches(_ address: String) -> Bool {
        matches(address, digests: digests)
    }

    /// `matches` against another list, for tests.
    static func matches(_ address: String, digests: Set<String>) -> Bool {
        guard let host = host(of: address) else { return false }
        let labels = host.split(separator: ".").map(String.init)
        // The host and each domain above it, down to two labels ("a.b.example.com", "b.example.com", "example.com").
        return (0..<max(labels.count - 1, 1)).contains { start in
            digests.contains(sha256(labels[start...].joined(separator: ".")))
        }
    }

    /// The host of an address, read from its text (`URL` refuses some addresses that a page can still ask for).
    private static func host(of address: String) -> String? {
        var text = Substring(address.trimmingCharacters(in: .whitespacesAndNewlines))
        guard !text.isEmpty else { return nil }
        if let slashes = text.range(of: "//") {
            // "scheme://host/..." or "//host/...": anything else before the slashes is a path of a bare host.
            let scheme = text[..<slashes.lowerBound]
            let isScheme = scheme.isEmpty || (scheme.last == ":" && scheme.first?.isLetter == true
                && scheme.dropLast().allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || "+.-".contains($0)) })
            if isScheme { text = text[slashes.upperBound...] }
        }
        var authority = text.prefix { !"/?#\\".contains($0) }
        if let at = authority.lastIndex(of: "@") { authority = authority[authority.index(after: at)...] }
        if let colon = authority.firstIndex(of: ":") { authority = authority[..<colon] }
        var host = authority.lowercased()
        while host.hasSuffix(".") { host.removeLast() }
        return host.isEmpty ? nil : host
    }

    private static func sha256(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}
