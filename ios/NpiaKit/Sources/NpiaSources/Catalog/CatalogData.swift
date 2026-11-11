import CryptoKit
import Foundation
import ZIPFoundation

/// Versioned catalogue downloads live in application support, separately from the app bundle.
actor CatalogData {
    static let shared = CatalogData()
    private let base = URL(string: "https://github.com/Shirochi-stack/NovelDB/releases/download/engines/")!
    private let root: URL
    private var busy = false
    private var progress = 0.0
    private var failure = ""
    private var archives: [String: (archive: Archive, entries: [String: Entry])] = [:]

    init(directory: URL? = nil) {
        root = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("catalogue-data", isDirectory: true)
    }

    private func installed() -> [String: Any] {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("installed.json")),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return value
    }

    private func validRevision(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{1,64}$", options: .regularExpression) != nil
    }

    private func status() -> [String: Any] {
        let saved = installed()
        return ["busy": busy, "progress": progress, "error": failure, "version": saved["revision"] ?? "",
                "built": saved["built"] ?? "", "books": saved["books"] ?? 0]
    }

    func handle(_ details: [String: Any]) -> String {
        var reply = status()
        switch details["action"] as? String {
        case "download":
            if !busy {
                busy = true
                progress = 0
                failure = ""
                Task { await download() }
            }
            reply = status()
        case "remove":
            if !busy {
                do {
                    archives.removeAll()
                    if FileManager.default.fileExists(atPath: root.path) { try FileManager.default.removeItem(at: root) }
                    progress = 0
                    failure = ""
                } catch { failure = error.localizedDescription }
            }
            reply = status()
        case "read":
            reply = [:]
            if let revision = details["version"] as? String, validRevision(revision),
               let key = details["key"] as? String,
               key.range(of: "^(manifest|top|[bc]/[0-9]{1,6})$", options: .regularExpression) != nil {
                let part = (Int(key.split(separator: "/").last ?? "0") ?? 0) % 2
                let piece = root.appendingPathComponent(revision).appendingPathComponent("pieces/" + key + ".bin")
                if let data = try? Data(contentsOf: piece) {
                    return "{\"base64\":\"" + data.base64EncodedString() + "\"}"
                }
                let file = root.appendingPathComponent(revision).appendingPathComponent("instant-catalog-\(part).zip")
                let archiveKey = revision + "/" + String(part)
                if archives[archiveKey] == nil, let archive = try? Archive(url: file, accessMode: .read, pathEncoding: nil) {
                    archives[archiveKey] = (archive, Dictionary(archive.map { ($0.path, $0) }, uniquingKeysWith: { first, _ in first }))
                }
                if let cached = archives[archiveKey],
                   let entry = cached.entries[key + ".json.gz"] {
                    var data = Data()
                    if (try? cached.archive.extract(entry, consumer: { data.append($0) })) != nil {
                        reply = ["base64": data.base64EncodedString()]
                    }
                }
            }
        default: break
        }
        return String(decoding: (try? JSONSerialization.data(withJSONObject: reply)) ?? Data("{}".utf8), as: UTF8.self)
    }

    private func download() async {
        defer { busy = false }
        do {
            let (data, response) = try await URLSession.shared.data(from: base.appendingPathComponent("noveldb-local.json"))
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let manifest = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let revision = manifest["revision"] as? String, validRevision(revision),
                  let files = manifest["files"] as? [[String: Any]], files.count == 2 else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let saved = installed()
            let previous = saved["revision"] as? String
            if previous == revision && saved["storage"] as? String == "pieces" { return }
            let target = root.appendingPathComponent(revision, isDirectory: true)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            for (part, entry) in files.enumerated() {
                guard let name = entry["name"] as? String,
                      name.range(of: "^noveldb-local-[A-Za-z0-9_-]+-[01]\\.zip$", options: .regularExpression) != nil,
                      let size = entry["size"] as? Int, let expected = entry["sha256"] as? String else {
                    throw CocoaError(.fileReadCorruptFile)
                }
                let (temporary, response) = try await URLSession.shared.download(from: base.appendingPathComponent(name))
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
                defer { try? FileManager.default.removeItem(at: temporary) }
                let handle = try FileHandle(forReadingFrom: temporary)
                defer { try? handle.close() }
                var digest = SHA256()
                var count = 0
                while let bytes = try handle.read(upToCount: 1024 * 1024), !bytes.isEmpty {
                    digest.update(data: bytes)
                    count += bytes.count
                }
                guard count == size, digest.finalize().map({ String(format: "%02x", $0) }).joined() == expected else {
                    throw CocoaError(.fileReadCorruptFile)
                }
                try unpack(temporary, revision: revision)
                progress = Double(part + 1) / 2
            }
            var installedManifest = manifest
            installedManifest["storage"] = "pieces"
            try JSONSerialization.data(withJSONObject: installedManifest).write(to: root.appendingPathComponent("installed.json"), options: .atomic)
            archives = archives.filter { key, _ in key.hasPrefix(revision + "/") || (previous.map { key.hasPrefix($0 + "/") } ?? false) }
            for old in (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey])) ?? [] {
                if validRevision(old.lastPathComponent), old.lastPathComponent != revision, old.lastPathComponent != previous,
                   (try? old.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                    try? FileManager.default.removeItem(at: old)
                }
            }
        } catch { failure = error.localizedDescription }
    }

    func unpack(_ file: URL, revision: String) throws {
        guard validRevision(revision) else { throw CocoaError(.fileReadCorruptFile) }
        let archive = try Archive(url: file, accessMode: .read, pathEncoding: nil)
        let folder = root.appendingPathComponent(revision).appendingPathComponent("pieces")
        for entry in archive {
            guard entry.path.range(of: "^(manifest|top|[bc]/[0-9]{1,6})\\.json\\.gz$", options: .regularExpression) != nil else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let destination = folder.appendingPathComponent(String(entry.path.dropLast(".json.gz".count)) + ".bin")
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
            _ = try archive.extract(entry, to: destination)
        }
    }
}
