import Foundation
import ZIPFoundation

/// Zips folder outputs (separate TXT) so hosts that take one file can share them.
enum FolderArchiver {
    /// Writes `<folder name>.zip` into a fresh directory under `temporaryDirectory`. The
    /// caller deletes it after the upload.
    static func zip(_ folder: URL, in temporaryDirectory: URL) throws -> URL {
        let staging = temporaryDirectory.appendingPathComponent("npia-zip-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        let archive = staging.appendingPathComponent(folder.lastPathComponent + ".zip")
        try FileManager.default.zipItem(at: folder, to: archive, shouldKeepParent: true, compressionMethod: .deflate)
        return archive
    }
}
