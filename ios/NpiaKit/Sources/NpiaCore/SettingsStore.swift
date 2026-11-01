import Foundation

/// Reads and writes `AppSettings` as JSON. Keys missing from a stored file fall back to the
/// defaults, so workstreams can add fields without migrations.
public enum SettingsCoding {
    public static func decode(_ data: Data) throws -> AppSettings {
        let defaults = try JSONSerialization.jsonObject(with: encode(AppSettings()))
        let stored = try JSONSerialization.jsonObject(with: data)
        let merged = merge(defaults, stored)
        return try JSONDecoder().decode(AppSettings.self, from: JSONSerialization.data(withJSONObject: merged))
    }

    public static func encode(_ settings: AppSettings) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try encoder.encode(settings)
    }

    static func merge(_ base: Any, _ overlay: Any) -> Any {
        guard let base = base as? [String: Any], let overlay = overlay as? [String: Any] else {
            return overlay
        }
        var result = base
        for (key, value) in overlay {
            result[key] = result[key].map { merge($0, value) } ?? value
        }
        return result
    }
}

public final class SettingsStore: @unchecked Sendable {
    public let fileURL: URL
    private let lock = NSLock()
    private var cached: AppSettings?

    public init(fileURL: URL) {
        self.fileURL = fileURL
    }

    public static func appDefault() -> SettingsStore {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return SettingsStore(fileURL: base.appendingPathComponent("settings.json"))
    }

    public func load() -> AppSettings {
        lock.lock(); defer { lock.unlock() }
        if let cached { return cached }
        let value = (try? Data(contentsOf: fileURL)).flatMap { try? SettingsCoding.decode($0) } ?? AppSettings()
        cached = value
        return value
    }

    public func save(_ settings: AppSettings) throws {
        lock.lock(); defer { lock.unlock() }
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try SettingsCoding.encode(settings).write(to: fileURL, options: .atomic)
        cached = settings
    }
}
