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
                let file = root.appendingPathComponent(revision).appendingPathComponent("instant-catalog-\(part).zip")
                if let archive = try? Archive(url: file, accessMode: .read, pathEncoding: nil),
                   let entry = archive[key + ".json.gz"] {
                    var data = Data()
                    if (try? archive.extract(entry, consumer: { data.append($0) })) != nil {
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
            if installed()["revision"] as? String == revision { return }
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
                _ = try Archive(url: temporary, accessMode: .read, pathEncoding: nil)
                let destination = target.appendingPathComponent("instant-catalog-\(part).zip")
                if FileManager.default.fileExists(atPath: destination.path) { try FileManager.default.removeItem(at: destination) }
                try FileManager.default.moveItem(at: temporary, to: destination)
                progress = Double(part + 1) / 2
            }
            try data.write(to: root.appendingPathComponent("installed.json"), options: .atomic)
        } catch { failure = error.localizedDescription }
    }
}
