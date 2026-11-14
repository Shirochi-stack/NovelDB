import Foundation
import NpiaCore
import ZIPFoundation

/// Import Python engines for the Pyodide host, or enable a bundled adapter for a recognized ZIP.
///
/// A userscript (`.user.js`, or a `.js` with a `==UserScript==` header) is a custom engine too: its name and
/// sites come from its header, and it reads a link in a web view page of its own (`UserscriptEngine`). One that
/// matches every site names none, and is asked last, after the generic fallback.
public enum CustomEngines {
    public struct Info {
        public let id: String
        public let label: String
        public let sites: [String]
        let source: String
        /// A userscript, read in a web view page (not a Python engine).
        public var userscript = false
        /// A userscript that matches every site.
        public var everywhere = false

        /// The engine's file, as the list shows it.
        public var fileName: String { id + (userscript ? CustomEngines.userscriptSuffix : ".py") }
    }

    static let userscriptSuffix = ".user.js"

    private struct ImportError: LocalizedError {
        let errorDescription: String?
        init(_ message: String) { errorDescription = message }
    }

    private static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("custom_engines", isDirectory: true)
    }

    public static func list() -> [Info] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        var found: [Info] = []
        // One engine of a name: the userscript (its files are read first).
        let ordered = files.sorted { a, b in
            let (first, second) = (a.lastPathComponent.hasSuffix(userscriptSuffix), b.lastPathComponent.hasSuffix(userscriptSuffix))
            return first != second ? first : a.lastPathComponent < b.lastPathComponent
        }
        for file in ordered {
            let name = file.lastPathComponent
            guard name.hasSuffix(userscriptSuffix) || file.pathExtension == "py",
                  let text = try? String(contentsOf: file, encoding: .utf8) else { continue }
            let info: Info?
            if name.hasSuffix(userscriptSuffix) {
                info = try? describeUserscript(String(name.dropLast(userscriptSuffix.count)), text)
            } else {
                info = describe(name, text)
            }
            if let info, !found.contains(where: { $0.id == info.id }) { found.append(info) }
        }
        return found.sorted { $0.id < $1.id }
    }

    /// The Python engines among them (the Python page runs these).
    static func python() -> [Info] { list().filter { !$0.userscript } }

    /// A userscript that matches every site was added: the custom step also follows the generic fallback.
    static func anywhere() -> Bool { list().contains { $0.everywhere } }

    /// The userscripts that read `url`: those that name its site, then those that match every site.
    static func userscripts(for url: URL) -> [Info] {
        guard let name = url.host?.lowercased() else { return [] }
        let host = name.hasPrefix("www.") ? String(name.dropFirst(4)) : name
        let scripts = list().filter(\.userscript)
        return scripts.filter { engine in engine.sites.contains { host == $0 || host.hasSuffix("." + $0) } }
            + scripts.filter(\.everywhere)
    }

    /// A Python engine lists `url`'s site (the Python page is not asked for a userscript's sites).
    static func listsPython(_ url: URL) -> Bool {
        guard let name = url.host?.lowercased() else { return false }
        let host = name.hasPrefix("www.") ? String(name.dropFirst(4)) : name
        return python().contains { engine in engine.sites.contains { host == $0 || host.hasSuffix("." + $0) } }
    }

    static func lists(_ url: URL) -> Bool {
        guard let name = url.host?.lowercased() else { return false }
        let host = name.hasPrefix("www.") ? String(name.dropFirst(4)) : name
        return list().contains { engine in engine.sites.contains { host == $0 || host.hasSuffix("." + $0) } }
    }

    public static func remove(_ id: String) throws {
        guard id.range(of: #"^[a-z0-9_]+$"#, options: .regularExpression) != nil else { return }
        let files = [id + ".py", id + userscriptSuffix].map { folder.appendingPathComponent($0) }
            .filter { FileManager.default.fileExists(atPath: $0.path) }
        for file in files { try FileManager.default.removeItem(at: file) }
    }

    /// A bundled engine to enable, or nil when a Python engine was installed.
    public static func add(_ file: URL) throws -> SiteEngine? {
        let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        let entry: Info
        switch file.pathExtension.lowercased() {
        case "zip":
            guard size <= (32 << 20) else { throw ImportError("Engine ZIP exceeds 32 MB.") }
            if let selected = try readArchive(file) {
                entry = selected
            } else {
                return .tako
            }
        case "py":
            guard size <= (2 << 20), let selected = describe(file.lastPathComponent, try String(contentsOf: file, encoding: .utf8)) else {
                throw ImportError("The file must contain a Python class Engine with parse_book and parse_chapter.")
            }
            entry = selected
        case "js":
            guard size <= (2 << 20) else { throw ImportError("The script exceeds 2 MB.") }
            let name = file.lastPathComponent.replacingOccurrences(of: #"(?i)\.user\.js$"#, with: ".js", options: .regularExpression)
            entry = try describeUserscript(identifier(name), try String(contentsOf: file, encoding: .utf8))
        default:
            throw ImportError("Choose a Python engine, a userscript (.user.js) or a ZIP.")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data(entry.source.utf8).write(to: folder.appendingPathComponent(entry.fileName), options: .atomic)
        // One engine of a name: the other kind's file of the same name goes.
        try? FileManager.default.removeItem(at: folder.appendingPathComponent(entry.id + (entry.userscript ? ".py" : userscriptSuffix)))
        return nil
    }

    private static func identifier(_ name: String) -> String {
        let file = (name.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent as NSString
        let stem = file.deletingPathExtension.lowercased()
            .replacingOccurrences(of: "[^a-z0-9_]+", with: "_", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        return stem.isEmpty ? "engine" : stem
    }

    private static func groups(_ pattern: String, in text: String, options: NSRegularExpression.Options = []) -> [[String]] {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: options) else { return [] }
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).map { match in
            (0..<match.numberOfRanges).map { Range(match.range(at: $0), in: text).map { String(text[$0]) } ?? "" }
        }
    }

    /// A userscript's name and sites, from its header (as noveldb/core/engines/userscript_engine.py reads it).
    static func describeUserscript(_ id: String, _ source: String) throws -> Info {
        let source = source.hasPrefix("\u{FEFF}") ? String(source.dropFirst()) : source
        guard let header = groups(#"//\s*==UserScript==(.*?)//\s*==/UserScript=="#, in: source, options: [.dotMatchesLineSeparators]).first?[1] else {
            throw ImportError("The script has no ==UserScript== header.")
        }
        let lines = groups(#"^\s*//\s*@([\w:-]+)[ \t]*(.*?)\s*$"#, in: header, options: [.anchorsMatchLines])
            .map { (key: $0[1], value: $0[2]) }
        let name = lines.first { $0.key == "name" }?.value.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !name.isEmpty else { throw ImportError("The script's header has no @name.") }
        let patterns = lines.filter { $0.key == "match" || $0.key == "include" }.map(\.value).filter { !$0.isEmpty }
        var everywhere = patterns.isEmpty
        var sites: [String] = []
        for pattern in patterns {
            switch patternHost(pattern) {
            case nil: everywhere = true
            case let host?: if !host.isEmpty, !sites.contains(host) { sites.append(host) }
            }
        }
        return Info(id: id, label: name, sites: everywhere ? [] : sites, source: source, userscript: true, everywhere: everywhere)
    }

    /// The site a @match or @include pattern names ("" when it names nothing usable); nil for every site.
    private static func patternHost(_ pattern: String) -> String? {
        let text = pattern.trimmingCharacters(in: .whitespacesAndNewlines)
        if text == "*" || text == "<all_urls>" { return nil }
        guard let found = groups(#"^(?:\*|https?\*?|http\*|file|ftp)://([^/]*)"#, in: text).first?[1] else {
            return text.hasPrefix("/") || text.hasPrefix("*") ? nil : ""
        }
        var host = found.lowercased()
        if host.isEmpty || host == "*" { return nil }
        host = host.replacingOccurrences(of: #"^\*\.?"#, with: "", options: .regularExpression)
        host = String(host.split(separator: ":", omittingEmptySubsequences: false).first ?? "")
        if host.hasPrefix("www.") { host = String(host.dropFirst(4)) }
        if host.contains("*") {
            return host.replacingOccurrences(of: "*", with: "").trimmingCharacters(in: CharacterSet(charactersIn: ".")).isEmpty ? nil : ""
        }
        return host
    }

    private static func readArchive(_ url: URL) throws -> Info? {
        let archive = try Archive(url: url, accessMode: .read, pathEncoding: nil)
        let members = Array(archive.prefix(257))
        let files = members.filter { $0.type == .file }
        guard members.count <= 256, Set(files.map(\.path)).count == files.count else {
            throw ImportError("Engine ZIP has too many or duplicate entries.")
        }
        func read(_ entry: Entry, limit: Int) throws -> Data {
            guard let size = Int(exactly: entry.uncompressedSize), size <= limit else {
                throw ImportError("Engine ZIP entry exceeds its size limit.")
            }
            var data = Data()
            _ = try archive.extract(entry) { chunk in
                guard chunk.count <= limit - data.count else { throw ImportError("Engine ZIP entry exceeds its size limit.") }
                data.append(chunk)
            }
            return data
        }
        func json(_ name: String) throws -> [String: Any] {
            guard let entry = files.first(where: { $0.path == name }) else { return [:] }
            return try JSONSerialization.jsonObject(with: read(entry, limit: 64 << 10)) as? [String: Any] ?? [:]
        }
        let manifest = try json("manifest.json")
        var name = manifest["name"] as? String
        if name == "__MSG_extName__" {
            name = (try json("_locales/en/messages.json")["extName"] as? [String: Any])?["message"] as? String
        }
        if manifest["manifest_version"] as? Int == 3, name == "Tako Manga Downloader",
           Set(["background.js", "offscreen.html", "sidepanel.html"]).isSubset(of: Set(files.map(\.path))) {
            return nil
        }
        if manifest["manifest_version"] != nil {
            throw ImportError("This Chrome extension has no NovelDB adapter.")
        }
        let candidates = files.filter { $0.path.lowercased().hasSuffix(".py") }.compactMap { entry -> Info? in
            guard let data = try? read(entry, limit: 2 << 20), let source = String(data: data, encoding: .utf8) else { return nil }
            return describe(entry.path, source)
        }
        guard candidates.count == 1 else {
            throw ImportError("Engine ZIP must contain exactly one Python file with class Engine.")
        }
        return candidates[0]
    }

    private static func describe(_ name: String, _ source: String) -> Info? {
        let source = source.hasPrefix("\u{FEFF}") ? String(source.dropFirst()) : source
        guard let declaration = source.range(of: #"(?m)^class\s+Engine\b[^:]*:"#, options: .regularExpression) else { return nil }
        let suffix = source[declaration.upperBound...]
        let body = String(suffix.prefix(upTo: suffix.range(of: #"(?m)^\S"#, options: .regularExpression)?.lowerBound ?? suffix.endIndex))
        for method in ["parse_book", "parse_chapter"] {
            guard body.range(of: "(?m)^[ \\t]+def\\s+" + method + "\\s*\\(", options: .regularExpression) != nil else { return nil }
        }
        func captures(_ pattern: String, in text: String, group: Int) -> [String] {
            guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
            return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
                Range($0.range(at: group), in: text).map { String(text[$0]) }
            }
        }
        let file = (name.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent as NSString
        let stem = file.deletingPathExtension.lowercased()
            .replacingOccurrences(of: "[^a-z0-9_]+", with: "_", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        let id = stem.isEmpty ? "engine" : stem
        let label = captures(#"(?m)^[ \t]+label\s*=\s*(['"])(.*?)\1"#, in: body, group: 2).first
        let sitesText = captures(#"(?ms)^[ \t]+sites\s*=\s*[\[(](.*?)[\])]"#, in: body, group: 1).first
            ?? captures(#"(?m)^[ \t]+sites\s*=\s*(['"].*?['"])"#, in: body, group: 1).first ?? ""
        let sites = captures(#"(['"])(.*?)\1"#, in: sitesText, group: 2).map {
            let site = $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            return site.hasPrefix("www.") ? String(site.dropFirst(4)) : site
        }.filter { !$0.isEmpty }
        return Info(id: id, label: label.flatMap { $0.isEmpty ? nil : $0 } ?? id, sites: sites, source: source)
    }
}
