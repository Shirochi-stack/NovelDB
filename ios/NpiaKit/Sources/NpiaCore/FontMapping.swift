import Foundation

/// npia "Font Mapping" (desktop `font_mapper.py`): a JSON object mapping each obfuscated
/// character to its real one. The imported file lives in Application Support; it is a local
/// file, so it is not part of the synced settings.
public enum FontMapping {
    public static var fileURL: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("font-mapping.json")
    }

    /// Parses mapping JSON; throws when it is not an object of strings.
    public static func parse(_ data: Data) throws -> [Character: String] {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw SourceError.parse("font mapping must be a JSON object")
        }
        var map: [Character: String] = [:]
        for (key, value) in object {
            guard key.count == 1, let character = key.first, let text = value as? String else { continue }
            map[character] = text
        }
        return map
    }

    /// Validates and stores a mapping file; returns how many characters it maps.
    @discardableResult
    public static func importFile(_ source: URL) throws -> Int {
        let data = try Data(contentsOf: source)
        let map = try parse(data)
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: fileURL, options: .atomic)
        return map.count
    }

    public static func clear() {
        try? FileManager.default.removeItem(at: fileURL)
    }

    /// The stored mapping, or nil when none is imported.
    public static func load() -> [Character: String]? {
        guard let data = try? Data(contentsOf: fileURL), let map = try? parse(data), !map.isEmpty else { return nil }
        return map
    }

    /// `FontMapper.decode`: replaces each mapped character.
    public static func decode(_ text: String, with map: [Character: String]) -> String {
        guard !map.isEmpty else { return text }
        var out = ""
        out.reserveCapacity(text.count)
        for character in text {
            if let replacement = map[character] { out += replacement } else { out.append(character) }
        }
        return out
    }
}
