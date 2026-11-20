import Foundation
import NpiaCore
import ZIPFoundation

/// Cuts chapters out of files a download already saved, for "Upload chapters" (desktop `chapter_cut`).
///
/// A saved EPUB, chapter ZIP/CBZ or chapter folder names each chapter by its number in the book
/// (`chapter0004.xhtml`, `0004 Title/`), so the chapters of a range are copied out of it into a new
/// file named after that range. Nothing is downloaded: what is not in the saved file is not in the
/// cut. A PDF, a single TXT or an HTML file is one undivided document and cannot be cut.
public enum ChapterCut {
    private static func regex(_ pattern: String, dotAll: Bool = false) -> NSRegularExpression {
        // The patterns are fixed text, checked by the tests.
        try! NSRegularExpression(pattern: pattern, options: dotAll ? [.caseInsensitive, .dotMatchesLineSeparators] : [.caseInsensitive])
    }

    private static let leading = regex(#"^\s*(\d+)"#)
    private static let chapterDoc = regex(#"(?:chapter|chap|ch)[_\- ]?(\d+)\.x?html?$"#)
    private static let link = regex(#"(?:src|href|xlink:href)\s*=\s*["']([^"'#]+)"#)
    private static let item = regex(#"<item\b[^>]*?/?>(?:\s*</item>)?"#)
    private static let itemref = regex(#"<itemref\b[^>]*?/?>(?:\s*</itemref>)?"#)
    private static let navPoint = regex(#"<navPoint\b(?:(?!<navPoint\b).)*?</navPoint>\s*"#, dotAll: true)
    private static let listItem = regex(#"<li\b(?:(?!<li\b).)*?</li>\s*"#, dotAll: true)
    private static let rootfile = regex(#"<rootfile\b[^>]*>"#)
    private static let ranged = regex(#"\s*\((\d+(?:-(?:\d+|end))?(?:,\d+(?:-\d+)?)*)\)$"#)

    /// `Title` for a name `Title (4-7)` a ranged download was saved under.
    public static func baseName(_ name: String) -> String {
        guard let found = matches(ranged, name).first, let range = Range(found.range, in: name) else { return name }
        let base = String(name[..<range.lowerBound])
        return base.trimmingCharacters(in: .whitespaces).isEmpty ? name : base
    }

    /// Where the cut of `source` goes: beside it, named after the book and the range.
    public static func target(for source: URL, from: Int, to: Int, list: String) -> URL {
        let name = source.lastPathComponent
        var isDirectory: ObjCBool = false
        FileManager.default.fileExists(atPath: source.path, isDirectory: &isDirectory)
        let ext = (name as NSString).pathExtension
        let hasSuffix = !ext.isEmpty && (!isDirectory.boolValue || ext.lowercased() == "folder")
        let stem = hasSuffix ? (name as NSString).deletingPathExtension : name
        let label = ChapterRangeText.named(from: from, to: to, list: list)
        let cut = "\(baseName(stem)) (\(label))" + (hasSuffix ? "." + ext : "")
        return source.deletingLastPathComponent().appendingPathComponent(cut, isDirectory: isDirectory.boolValue)
    }

    /// Writes the chapters `from...to` (and comma list) of the saved `source` beside it and returns the
    /// new file; `source` itself when it already is that range; nil when the format cannot be cut or
    /// holds none of those chapters.
    public static func cut(_ source: URL, from: Int, to: Int, list: String) -> URL? {
        let fm = FileManager.default
        var isDirectory: ObjCBool = false
        guard fm.fileExists(atPath: source.path, isDirectory: &isDirectory) else { return nil }
        let destination = target(for: source, from: from, to: to, list: list)
        if destination.lastPathComponent == source.lastPathComponent { return source }
        let part = destination.deletingLastPathComponent().appendingPathComponent(destination.lastPathComponent + ".part")
        try? fm.removeItem(at: part)
        let wanted: (Int) -> Bool = { number in
            number >= from && number <= to && ChapterRangeText.inList(from: from, to: to, list: list, index: number)
        }
        var count = 0
        do {
            if isDirectory.boolValue {
                count = try cutFolder(source, part, wanted)
            } else {
                switch source.pathExtension.lowercased() {
                case "epub": count = try cutEpub(source, part, wanted)
                case "zip", "cbz": count = try cutArchive(source, part, wanted)
                default: count = 0
                }
            }
            guard count > 0 else {
                try? fm.removeItem(at: part)
                return nil
            }
            try? fm.removeItem(at: destination)
            try fm.moveItem(at: part, to: destination)
            return destination
        } catch {
            try? fm.removeItem(at: part)
            return nil
        }
    }

    // MARK: - Formats

    private static func cutFolder(_ source: URL, _ part: URL, _ wanted: (Int) -> Bool) throws -> Int {
        let fm = FileManager.default
        // One folder per chapter (pages), or one file per chapter (separate TXT).
        let keep = try fm.contentsOfDirectory(atPath: source.path).sorted().filter { name in
            chapterNumber(name).map(wanted) ?? false
        }
        guard !keep.isEmpty else { return 0 }
        try fm.createDirectory(at: part, withIntermediateDirectories: true)
        for name in keep {
            try fm.copyItem(at: source.appendingPathComponent(name), to: part.appendingPathComponent(name))
        }
        return keep.count
    }

    private static func cutArchive(_ source: URL, _ part: URL, _ wanted: (Int) -> Bool) throws -> Int {
        let archive = try Archive(url: source, accessMode: .read)
        let output = try Archive(url: part, accessMode: .create, pathEncoding: nil)
        var kept = Set<Int>()
        for entry in archive where entry.type == .file {
            // Files beside the chapter folders (a cover, the comic's info) stay.
            if let slash = entry.path.firstIndex(of: "/") {
                guard let number = chapterNumber(String(entry.path[..<slash])), wanted(number) else { continue }
                kept.insert(number)
            }
            try add(output, entry.path, try read(archive, entry))
        }
        return kept.count
    }

    private static func cutEpub(_ source: URL, _ part: URL, _ wanted: (Int) -> Bool) throws -> Int {
        let archive = try Archive(url: source, accessMode: .read)
        func text(_ path: String) throws -> String {
            guard let entry = archive[path] else { return "" }
            return String(decoding: try read(archive, entry), as: UTF8.self)
        }
        var names: [String] = []
        for entry in archive where entry.type == .file { names.append(entry.path) }
        let container = try text("META-INF/container.xml")
        guard let root = matches(rootfile, container).first, let rootRange = Range(root.range, in: container) else { return 0 }
        let package = attribute(String(container[rootRange]), "full-path")
        guard !package.isEmpty else { return 0 }
        let base = folder(of: package)
        var opf = try text(package)
        let tags = strings(item, opf)

        var chapters: [String: Int] = [:]
        for tag in tags {
            let href = attribute(tag, "href")
            let file = (href as NSString).lastPathComponent
            guard let found = matches(chapterDoc, file).first, let number = Int(group(found, 1, file)) else { continue }
            chapters[resolve(base, href)] = number
        }
        let keep = Set(chapters.filter { wanted($0.value) }.keys)
        guard !keep.isEmpty else { return 0 }
        let dropped = Set(chapters.keys).subtracting(keep)

        // Files only the dropped chapters use (their pages' images) go with them.
        var used = Set<String>()
        for name in names where !dropped.contains(name) && name != package {
            let lower = name.lowercased()
            if lower.hasSuffix(".xhtml") || lower.hasSuffix(".html") || lower.hasSuffix(".htm") || lower.hasSuffix(".css") {
                used.formUnion(links(try text(name), folder(of: name)))
            }
        }
        var cover = Set<String>()
        for tag in tags where (attribute(tag, "id") + attribute(tag, "properties")).lowercased().contains("cover") {
            cover.insert(resolve(base, attribute(tag, "href")))
        }
        var gone = dropped
        for name in dropped {
            gone.formUnion(links(try text(name), folder(of: name)).subtracting(used).subtracting(cover).subtracting(keep))
        }

        var ids = Set<String>()
        opf = replacing(item, in: opf) { tag in
            guard gone.contains(resolve(base, attribute(tag, "href"))) else { return tag }
            ids.insert(attribute(tag, "id"))
            return ""
        }
        opf = replacing(itemref, in: opf) { tag in ids.contains(attribute(tag, "idref")) ? "" : tag }
        var navigation = Set(names.filter { $0.lowercased().hasSuffix(".ncx") })
        for tag in strings(item, opf) where attribute(tag, "properties").lowercased().split(separator: " ").contains("nav") {
            navigation.insert(resolve(base, attribute(tag, "href")))
        }

        let output = try Archive(url: part, accessMode: .create, pathEncoding: nil)
        // The mimetype entry comes first and uncompressed, as readers require.
        let ordered = names.filter { $0 == "mimetype" } + names.filter { $0 != "mimetype" }
        for name in ordered where !gone.contains(name) {
            guard let entry = archive[name] else { continue }
            var data = try read(archive, entry)
            if name == package {
                data = Data(opf.utf8)
            } else if navigation.contains(name) {
                let place = folder(of: name)
                let pattern = name.lowercased().hasSuffix(".ncx") ? navPoint : listItem
                let pruned = replacing(pattern, in: String(decoding: data, as: UTF8.self)) { block in
                    links(block, place).isDisjoint(with: gone) ? block : ""
                }
                data = Data(pruned.utf8)
            }
            try add(output, name, data)
        }
        return keep.count
    }

    // MARK: - Helpers

    private static let storedExtensions: Set<String> = ["jpg", "jpeg", "png", "webp", "gif", "avif", "bmp"]

    private static func add(_ archive: Archive, _ path: String, _ data: Data) throws {
        let stored = path == "mimetype" || storedExtensions.contains((path as NSString).pathExtension.lowercased())
        try archive.addEntry(
            with: path, type: .file, uncompressedSize: Int64(data.count),
            compressionMethod: stored ? .none : .deflate,
            provider: { position, size in data.subdata(in: Int(position)..<Int(position) + size) }
        )
    }

    private static func read(_ archive: Archive, _ entry: Entry) throws -> Data {
        var data = Data()
        _ = try archive.extract(entry) { data.append($0) }
        return data
    }

    private static func matches(_ pattern: NSRegularExpression, _ text: String) -> [NSTextCheckingResult] {
        pattern.matches(in: text, range: NSRange(text.startIndex..., in: text))
    }

    private static func group(_ match: NSTextCheckingResult, _ index: Int, _ text: String) -> String {
        Range(match.range(at: index), in: text).map { String(text[$0]) } ?? ""
    }

    private static func strings(_ pattern: NSRegularExpression, _ text: String) -> [String] {
        matches(pattern, text).map { group($0, 0, text) }
    }

    /// `text` with every match of `pattern` replaced by what `transform` returns for it.
    private static func replacing(_ pattern: NSRegularExpression, in text: String, _ transform: (String) -> String) -> String {
        var result = ""
        var last = text.startIndex
        for match in matches(pattern, text) {
            guard let range = Range(match.range, in: text) else { continue }
            result.append(contentsOf: text[last..<range.lowerBound])
            result.append(transform(String(text[range])))
            last = range.upperBound
        }
        result.append(contentsOf: text[last...])
        return result
    }

    private static func chapterNumber(_ name: String) -> Int? {
        guard let found = matches(leading, name).first else { return nil }
        return Int(group(found, 1, name))
    }

    private static func attribute(_ tag: String, _ name: String) -> String {
        let pattern = regex("\\b" + name + #"\s*=\s*["']([^"']*)["']"#)
        guard let found = matches(pattern, tag).first else { return "" }
        return group(found, 1, tag)
    }

    private static func links(_ text: String, _ folder: String) -> Set<String> {
        var found = Set<String>()
        for match in matches(link, text) {
            let value = group(match, 1, text)
            if value.contains("://") || value.hasPrefix("data:") || value.hasPrefix("mailto:") { continue }
            found.insert(resolve(folder, value))
        }
        return found
    }

    private static func folder(of path: String) -> String {
        guard let slash = path.lastIndex(of: "/") else { return "" }
        return String(path[..<slash])
    }

    /// `folder/href` with `.` and `..` worked out, as the name the archive stores.
    private static func resolve(_ folder: String, _ href: String) -> String {
        var parts: [String] = []
        for piece in (folder.isEmpty ? href : folder + "/" + href).split(separator: "/", omittingEmptySubsequences: true) {
            if piece == "." { continue }
            if piece == ".." {
                if !parts.isEmpty { parts.removeLast() }
            } else {
                parts.append(String(piece))
            }
        }
        return parts.joined(separator: "/")
    }
}
