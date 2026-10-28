import Foundation

/// The sites an engine's bundle lists, read from the script text.
///
/// - WebToEpub registers its parsers with `parserFactory.register("host"`; hosts are compared
///   with the URL's host, `www.` stripped on both sides. Parsers that match a page rather than a
///   host (site themes) cannot be listed.
/// - novel-downloader's `getRule()` is one `switch` over `location.host` with a few `if(/regex/.test(e)`
///   tests after it. Hosts compare with the URL's host and port exactly, ignoring case.
final class EngineSiteIndex: @unchecked Sendable {
    let hosts: Set<String>
    private let patterns: [NSRegularExpression]

    init(hosts: Set<String>, patterns: [String] = []) {
        self.hosts = hosts
        self.patterns = patterns.compactMap { try? NSRegularExpression(pattern: $0) }
    }

    func matches(hostAndPort host: String) -> Bool {
        if hosts.contains(host) { return true }
        let range = NSRange(host.startIndex..., in: host)
        return patterns.contains { $0.firstMatch(in: host, range: range) != nil }
    }
}

enum EngineSites {
    private static let lock = NSLock()
    private static var cache: [String: EngineSiteIndex] = [:]

    /// True when the bundle lists the URL's site.
    static func lists(_ bundle: EngineBundle, _ url: URL) -> Bool {
        guard let index = index(for: bundle) else { return false }
        switch bundle.engine {
        case .webtoepub:
            return index.hosts.contains(stripWWW((url.host ?? "").lowercased()))
        case .novelDownloader:
            return index.matches(hostAndPort: hostAndPort(url))
        }
    }

    /// True when the engine's bundle in use (a valid newer download or the shipped copy) lists the site.
    static func lists(_ engine: RuleEngine, _ url: URL, store: EngineStore = .shared) -> Bool {
        guard let bundle = store.current(engine) else { return false }
        return lists(bundle, url)
    }

    /// The URL's host as `location.host` spells it: lower case, with the port when there is one.
    static func hostAndPort(_ url: URL) -> String {
        let host = (url.host ?? "").lowercased()
        if let port = url.port { return "\(host):\(port)" }
        return host
    }

    static func stripWWW(_ host: String) -> String {
        host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
    }

    private static func index(for bundle: EngineBundle) -> EngineSiteIndex? {
        let values = try? bundle.path.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
        let stamp = values?.contentModificationDate?.timeIntervalSince1970 ?? 0
        let key = "\(bundle.engine.rawValue)|\(bundle.path.path)|\(values?.fileSize ?? -1)|\(stamp)"
        lock.lock()
        if let known = cache[key] {
            lock.unlock()
            return known
        }
        lock.unlock()
        guard let data = try? Data(contentsOf: bundle.path) else { return nil }
        let built: EngineSiteIndex
        switch bundle.engine {
        case .webtoepub: built = parseWebToEpub(data)
        case .novelDownloader: built = parseNovelDownloader(data)
        }
        lock.lock()
        cache[key] = built
        lock.unlock()
        return built
    }

    // MARK: - Parsing

    static func parseWebToEpub(_ data: Data) -> EngineSiteIndex {
        let bytes = [UInt8](data)
        let needle = Array(#"parserFactory.register(""#.utf8)
        var hosts = Set<String>()
        for start in positions(of: needle, in: bytes) {
            var end = start + needle.count
            while end < bytes.count, bytes[end] != 0x22 { end += 1 }
            guard end > start + needle.count, end < bytes.count else { continue }
            let host = String(decoding: bytes[(start + needle.count)..<end], as: UTF8.self).lowercased()
            hosts.insert(stripWWW(host))
        }
        return EngineSiteIndex(hosts: hosts)
    }

    static func parseNovelDownloader(_ data: Data) -> EngineSiteIndex {
        let bytes = [UInt8](data)
        let marker = Array(#"throw new Error("Not Found Rule!")"#.utf8)
        let opener = Array("switch(".utf8)
        guard let end = positions(of: marker, in: bytes).first,
              let start = positions(of: opener, in: bytes).last(where: { $0 + opener.count <= end })
        else { return EngineSiteIndex(hosts: []) }
        let region = String(decoding: bytes[start..<end], as: UTF8.self)

        var hosts = Set<String>()
        for raw in captures(#"case"([^"]+)":"#, in: region) {
            hosts.insert(unescape(raw).lowercased())
        }
        var patterns: [String] = []
        for raw in captures(#"if\((/.+?/)\.test\(e\)"#, in: region) where raw.count > 2 {
            patterns.append(String(raw.dropFirst().dropLast()))
        }
        return EngineSiteIndex(hosts: hosts, patterns: patterns)
    }

    /// Decodes JSON string escapes (the script writes some host names as `\uXXXX` runs).
    static func unescape(_ raw: String) -> String {
        let quoted = Data("\"\(raw)\"".utf8)
        if let value = (try? JSONSerialization.jsonObject(with: quoted, options: [.fragmentsAllowed])) as? String {
            return value
        }
        return raw
    }

    /// The first capture group of every match.
    private static func captures(_ pattern: String, in text: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let whole = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: whole).compactMap { match in
            Range(match.range(at: 1), in: text).map { String(text[$0]) }
        }
    }

    /// Start offsets of every non-overlapping occurrence of `needle`.
    private static func positions(of needle: [UInt8], in bytes: [UInt8]) -> [Int] {
        guard let first = needle.first else { return [] }
        var found: [Int] = []
        let last = bytes.count - needle.count
        var index = 0
        while index <= last {
            if bytes[index] == first {
                var offset = 1
                while offset < needle.count, bytes[index + offset] == needle[offset] { offset += 1 }
                if offset == needle.count {
                    found.append(index)
                    index += needle.count
                    continue
                }
            }
            index += 1
        }
        return found
    }
}
