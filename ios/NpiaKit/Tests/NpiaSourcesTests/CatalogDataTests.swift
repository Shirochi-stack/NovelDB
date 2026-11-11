import Foundation
import XCTest
import ZIPFoundation
@testable import NpiaSources

final class CatalogDataTests: XCTestCase {
    func testDataIsAbsentUntilInstalledAndRemovedIndependently() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let store = CatalogData(directory: folder)
        let initial = await store.handle(["action": "status"])
        XCTAssertTrue(initial.contains("\"version\":\"\""))
        let version = folder.appendingPathComponent("fixture")
        try FileManager.default.createDirectory(at: version, withIntermediateDirectories: true)
        let archive = try Archive(url: version.appendingPathComponent("instant-catalog-0.zip"), accessMode: .create, pathEncoding: nil)
        let bytes = Data("catalogue fixture".utf8)
        try archive.addEntry(with: "manifest.json.gz", type: .file, uncompressedSize: Int64(bytes.count), provider: { position, size in
            bytes.subdata(in: Int(position)..<Int(position) + size)
        })
        try Data("{\"revision\":\"fixture\",\"books\":12,\"built\":\"2026-11-11\"}".utf8)
            .write(to: folder.appendingPathComponent("installed.json"))
        let result = await store.handle(["action": "read", "version": "fixture", "key": "manifest"])
        XCTAssertTrue(result.contains(bytes.base64EncodedString()))
        let invalid = await store.handle(["action": "read", "version": "../fixture", "key": "manifest"])
        XCTAssertEqual(invalid, "{}")
        _ = await store.handle(["action": "remove"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.path))
    }
}
