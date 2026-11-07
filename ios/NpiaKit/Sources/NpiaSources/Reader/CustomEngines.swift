import Foundation
import NpiaCore
import ZIPFoundation

/// Import Python engines for the Pyodide host, or enable a bundled adapter for a recognized ZIP.
public enum CustomEngines {
    public struct Info {
        public let id: String
        public let label: String
        public let sites: [String]
        let source: String
    }

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
        return files.filter { $0.pathExtension == "py" }.sorted { $0.lastPathComponent < $1.lastPathComponent }.compactMap { file in
            guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
            return describe(file.lastPathComponent, text)
        }
    }

    static func lists(_ url: URL) -> Bool {
        guard let name = url.host?.lowercased() else { return false }
        let host = name.hasPrefix("www.") ? String(name.dropFirst(4)) : name
        return list().contains { engine in engine.sites.contains { host == $0 || host.hasSuffix("." + $0) } }
    }

    public static func remove(_ id: String) throws {
        guard id.range(of: #"^[a-z0-9_]+$"#, options: .regularExpression) != nil else { return }
        try FileManager.default.removeItem(at: folder.appendingPathComponent(id + ".py"))
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
        default:
            throw ImportError("Choose a Python engine or a ZIP.")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data(entry.source.utf8).write(to: folder.appendingPathComponent(entry.id + ".py"), options: .atomic)
        return nil
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
