import Foundation
import NpiaCore
import ZIPFoundation

/// One finished file offered to an upload, with what the results need to say about it.
public struct UploadItem: Sendable, Equatable, Hashable {
    public var file: URL
    /// The book title, listed with the ZIP the file ends up in.
    public var title: String?
    /// Names the file when it goes up on its own and its title has no usable ASCII, like
    /// `npia_419368` (`UploadNaming.nameID`).
    public var nameID: String?

    public init(file: URL, title: String? = nil, nameID: String? = nil) {
        self.file = file
        self.title = title
        self.nameID = nameID
    }

    /// The file's name in NFC, which is also its entry name inside a ZIP.
    public var fileName: String { uploadName(file) }

    /// A folder (separate TXT output) is zipped by the single-file path and never packed.
    public var isDirectory: Bool {
        (try? file.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
    }

    public var isEPUB: Bool { file.pathExtension.lowercased() == "epub" }

    public var size: Int64 { (try? fileSize(file)) ?? 0 }
}

/// What goes up as one file: a lone file, or the files packed into one ZIP.
public struct UploadBundle: Sendable, Equatable {
    public var items: [UploadItem]
    /// The ZIP's file name; nil for a file (or folder) that is uploaded as it is.
    public var zipName: String?

    public init(items: [UploadItem], zipName: String? = nil) {
        self.items = items
        self.zipName = zipName
    }

    public var isZip: Bool { zipName != nil }

    /// What the progress text calls it.
    public var displayName: String {
        zipName ?? items.first?.fileName ?? "file"
    }
}

/// Packs several files into as few ZIPs as the size limit allows, and writes them.
///
/// First-fit decreasing by size: the files are placed biggest first, each into the first ZIP it
/// still fits. Every entry counts its size plus an allowance for the ZIP headers (1 KB, more
/// for very long names). A file that cannot fit a ZIP on its own, and a ZIP that would hold one
/// file, go up as the file itself.
public enum UploadBundler {
    /// Allowance per entry for its local header, central directory record and slack.
    public static let entryOverhead: Int64 = 1024
    public static let bytesPerMB: Int64 = 1_048_576

    /// The ZIP size limit: the "Largest ZIP" setting, or the host's own per-file maximum when the
    /// host is known and lower.
    public static func limitBytes(zipMaxMB: Int, hostMaxBytes: Int64?) -> Int64 {
        let setting = Int64(UploadSettings.clampZipMaxMB(zipMaxMB)) * bytesPerMB
        if let hostMaxBytes, hostMaxBytes > 0 { return min(setting, hostMaxBytes) }
        return setting
    }

    /// What one file adds to a ZIP.
    public static func cost(size: Int64, name: String) -> Int64 {
        size + max(entryOverhead, Int64(76 + 2 * name.utf8.count + 64))
    }

    /// First-fit decreasing over `costs`: groups of indexes, the first group holding the biggest
    /// file. A cost above `limit` fills a group by itself. Indexes inside a group are in input order.
    public static func pack(costs: [Int64], limit: Int64) -> [[Int]] {
        let order = costs.indices.sorted { costs[$0] != costs[$1] ? costs[$0] > costs[$1] : $0 < $1 }
        var groups: [(indexes: [Int], used: Int64)] = []
        for index in order {
            let cost = costs[index]
            if let slot = groups.firstIndex(where: { $0.used + cost <= limit }) {
                groups[slot].indexes.append(index)
                groups[slot].used += cost
            } else {
                groups.append(([index], cost))
            }
        }
        return groups.map { $0.indexes.sorted() }
    }

    /// Decides what is uploaded: with `zipBundle` on and two or more files, ZIPs of at most
    /// `limit` bytes and the files that stay alone; otherwise one upload per item, in the order
    /// given. A path listed twice counts once. Folders are never packed.
    public static func plan(_ items: [UploadItem], zipBundle: Bool, limit: Int64) -> [UploadBundle] {
        var seen = Set<String>()
        let unique = items.filter { seen.insert($0.file.standardizedFileURL.path).inserted }
        let files = unique.filter { !$0.isDirectory }
        guard zipBundle, files.count >= 2 else {
            return unique.map { UploadBundle(items: [$0]) }
        }
        let costs = files.map { cost(size: $0.size, name: $0.fileName) }
        var bundles = pack(costs: costs, limit: limit).map { group in
            UploadBundle(items: group.map { files[$0] })
        }
        let kinds = bundles.filter { $0.items.count > 1 }.map { bundle in
            (count: bundle.items.count, allEPUB: bundle.items.allSatisfy(\.isEPUB))
        }
        var names = zipNames(kinds).makeIterator()
        for index in bundles.indices where bundles[index].items.count > 1 {
            bundles[index].zipName = names.next()
        }
        bundles += unique.filter(\.isDirectory).map { UploadBundle(items: [$0]) }
        return bundles
    }

    /// `NovelDB_<n>_EPUBs.zip` when every entry is an EPUB, else `NovelDB_<n>_files.zip`. When
    /// several ZIPs of one upload would share a name, the later ones get `_2`, `_3`, ...
    public static func zipNames(_ kinds: [(count: Int, allEPUB: Bool)]) -> [String] {
        var used: [String: Int] = [:]
        return kinds.map { kind in
            let base = "NovelDB_\(kind.count)_" + (kind.allEPUB ? "EPUBs" : "files")
            let number = (used[base] ?? 0) + 1
            used[base] = number
            return number == 1 ? base + ".zip" : "\(base)_\(number).zip"
        }
    }

    /// Entries that are compressed already are stored; text and HTML are deflated.
    public static func isStored(_ fileName: String) -> Bool {
        ["epub", "cbz", "pdf", "zip"].contains((fileName as NSString).pathExtension.lowercased())
    }

    /// The entry names for `items`: the original file names, no folders; a name used twice gets
    /// " (2)", " (3)" before its extension.
    public static func entryNames(for items: [UploadItem]) -> [String] {
        var used = Set<String>()
        return items.map { item in
            let name = item.fileName
            var candidate = name
            var number = 1
            while !used.insert(candidate.lowercased()).inserted {
                number += 1
                let base = (name as NSString).deletingPathExtension
                let ext = (name as NSString).pathExtension
                candidate = ext.isEmpty ? "\(base) (\(number))" : "\(base) (\(number)).\(ext)"
            }
            return candidate
        }
    }

    /// Writes the bundle's ZIP into a fresh folder under `temporaryDirectory` and returns it; the
    /// caller deletes the folder (`url.deletingLastPathComponent()`) after the upload. Entries
    /// are copied in small pieces, so memory stays flat for big files.
    public static func writeZip(_ bundle: UploadBundle, in temporaryDirectory: URL) throws -> URL {
        guard let name = bundle.zipName else {
            throw UploadError.notConfigured("A ZIP name")
        }
        let staging = temporaryDirectory.appendingPathComponent("npia-zip-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        let url = staging.appendingPathComponent(name)
        do {
            let archive = try Archive(url: url, accessMode: .create, pathEncoding: nil)
            let names = entryNames(for: bundle.items)
            for (item, entry) in zip(bundle.items, names) {
                try add(item.file, as: entry, to: archive)
            }
        } catch {
            try? FileManager.default.removeItem(at: staging)
            throw error
        }
        return url
    }

    private static func add(_ file: URL, as entry: String, to archive: Archive) throws {
        let size = try fileSize(file)
        let reader = try FileHandle(forReadingFrom: file)
        defer { try? reader.close() }
        try archive.addEntry(
            with: entry, type: .file, uncompressedSize: size,
            compressionMethod: isStored(entry) ? .none : .deflate,
            provider: { position, count in
                try reader.seek(toOffset: UInt64(position))
                return try reader.read(upToCount: count) ?? Data()
            }
        )
    }
}
