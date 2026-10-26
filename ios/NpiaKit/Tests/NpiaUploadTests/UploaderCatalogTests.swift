import XCTest
import NpiaCore
@testable import NpiaUpload

final class UploaderCatalogTests: UploadTestCase {
    func testIDsAreUnique() {
        let ids = UploaderCatalog.all.map(\.id)
        XCTAssertEqual(Set(ids).count, ids.count)
    }

    func testDefaultHostsExist() {
        for id in UploadSettings().hosts {
            XCTAssertNotNil(UploaderCatalog.uploader(id: id), id)
        }
    }

    func testRenamedIDsResolve() {
        XCTAssertEqual(UploaderCatalog.uploader(id: "sendcm")?.id, "sendnow")
    }

    func testTooLargeFileIsRejectedBeforeUpload() throws {
        let file = try makeFile(bytes: 2048)
        struct Tiny: Uploader {
            let id = "tiny", displayName = "Tiny", maxBytes: Int64? = 1024, retention: TimeInterval? = nil, requiresAccount = false
            func upload(_ file: URL, context: UploadContext, progress: (@Sendable (UploadProgress) -> Void)?) async throws -> UploadResult {
                XCTFail("should not upload"); throw UploadError.notImplemented(id)
            }
        }
        XCTAssertThrowsError(try Tiny().check(file)) { error in
            XCTAssertEqual(error as? UploadError, .tooLarge(host: "Tiny", limitBytes: 1024))
        }
    }

    func testFallsBackToNextHostAndRecordsHistory() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { request in
            switch request.url?.host {
            case "temp.sh": return .text("busy", status: 503)
            case "upload.gofile.io": return .json(#"{"status":"ok","data":{"downloadPage":"https://gofile.io/d/AbC123","guestToken":"t"}}"#)
            default: throw URLError(.cannotFindHost)
            }
        }
        var settings = UploadSettings()
        settings.hosts = ["tempsh", "gofile"]
        let history = UploadHistoryStore(fileURL: directory.appendingPathComponent("history.json"))

        let result = try await UploaderCatalog.upload(file, settings: settings, context: context(settings: settings), history: history)

        XCTAssertEqual(result.hostID, "gofile")
        XCTAssertEqual(result.link.absoluteString, "https://gofile.io/d/AbC123")
        XCTAssertEqual(result.expiresAt, fixedNow.addingTimeInterval(10 * 86_400))
        XCTAssertEqual(StubURLProtocol.seen.map { $0.request.url?.host }, ["temp.sh", "upload.gofile.io"])
        XCTAssertEqual(history.all, [result])
    }

    func testAllHostsFailingThrowsLastError() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .text("nope", status: 500) }
        var settings = UploadSettings()
        settings.hosts = ["tempsh"]
        do {
            _ = try await UploaderCatalog.upload(file, settings: settings, context: context(settings: settings), history: nil)
            XCTFail("expected failure")
        } catch {
            XCTAssertEqual(error as? UploadError, .badResponse(host: "temp.sh", status: 500, body: "nope"))
        }
    }

    func testAccountHostWithoutCredentialsIsSkipped() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in XCTFail("no request expected"); return .text("", status: 500) }
        var settings = UploadSettings()
        settings.hosts = ["pixeldrain", "gdrive"]
        do {
            _ = try await UploaderCatalog.upload(file, settings: settings, context: context(settings: settings), history: nil)
            XCTFail("expected failure")
        } catch {
            XCTAssertEqual(error as? UploadError, .notSignedIn("Google Drive"))
        }
        XCTAssertTrue(StubURLProtocol.seen.isEmpty)
    }

    func testFolderOutputIsZippedBeforeUpload() async throws {
        let folder = directory.appendingPathComponent("Book TXT", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("one".utf8).write(to: folder.appendingPathComponent("001.txt"))
        try Data("two".utf8).write(to: folder.appendingPathComponent("002.txt"))
        StubURLProtocol.handler = { _ in .text("https://temp.sh/xyz/Book TXT.zip") }
        var settings = UploadSettings()
        settings.hosts = ["tempsh"]

        let result = try await UploaderCatalog.upload(folder, settings: settings, context: context(settings: settings), history: nil)

        XCTAssertEqual(result.fileName, "Book TXT.zip")
        if let body = StubURLProtocol.seen.first?.body {
            XCTAssertNotNil(String(decoding: body, as: UTF8.self).range(of: #"filename="Book TXT.zip""#))
        }
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: directory.path).filter { $0.hasPrefix("npia-") }
        XCTAssertEqual(leftovers, [], "staged zip and multipart bodies are removed")
    }

    func testFolderArchiverKeepsFiles() throws {
        let folder = directory.appendingPathComponent("parts", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data("hello".utf8).write(to: folder.appendingPathComponent("a.txt"))
        let zip = try FolderArchiver.zip(folder, in: directory)
        XCTAssertEqual(zip.lastPathComponent, "parts.zip")
        let out = directory.appendingPathComponent("out", isDirectory: true)
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        try FileManager.default.unzipItem(at: zip, to: out)
        XCTAssertEqual(try String(contentsOf: out.appendingPathComponent("parts/a.txt"), encoding: .utf8), "hello")
    }
}
