import XCTest
import NpiaCore
@testable import NpiaUpload

private final class BatchEventBox: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [UploadBatchEvent] = []

    func add(_ event: UploadBatchEvent) {
        lock.lock(); values.append(event); lock.unlock()
    }

    var all: [UploadBatchEvent] {
        lock.lock(); defer { lock.unlock() }
        return values
    }
}

/// `UploaderCatalog.uploadBatch` against the stub host: no test touches a live one.
final class UploadBatchTests: UploadTestCase {
    private func settings(zip: Bool = true, hosts: [String] = ["tempsh"]) -> UploadSettings {
        var settings = UploadSettings()
        settings.hosts = hosts
        settings.zipBundle = zip
        return settings
    }

    private func items(_ names: [String], bytes: Int = 3_000) throws -> [UploadItem] {
        try names.map { name in
            UploadItem(file: try makeFile(name, bytes: bytes), title: (name as NSString).deletingPathExtension, nameID: "npia_5")
        }
    }

    private func leftovers() throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: directory.path).filter { $0.hasPrefix("npia-") }
    }

    func testSeveralFilesGoUpAsOneZipNamedByTheirCount() async throws {
        let batch = try items(["소설 하나.epub", "Second Book.epub", "Third.epub"])
        StubURLProtocol.handler = { _ in .text("https://temp.sh/Zq/NovelDB_3_EPUBs.zip") }
        let configured = settings()
        let history = UploadHistoryStore(fileURL: directory.appendingPathComponent("history.json"))
        let box = BatchEventBox()

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: history, events: { box.add($0) }
        )

        XCTAssertEqual(StubURLProtocol.seen.count, 1, "one request for three books")
        XCTAssertEqual(outcome.failures, [])
        let result = try XCTUnwrap(outcome.uploads.first)
        XCTAssertEqual(outcome.uploads.count, 1)
        XCTAssertEqual(result.fileName, "NovelDB_3_EPUBs.zip")
        XCTAssertEqual(result.summary, "NovelDB_3_EPUBs.zip (3 books)")
        XCTAssertEqual(result.titlesInside, ["소설 하나", "Second Book", "Third"])
        XCTAssertEqual(result.contents?.map(\.fileName), ["소설 하나.epub", "Second Book.epub", "Third.epub"])
        XCTAssertEqual(result.linkLine, "NovelDB_3_EPUBs.zip (3 books): https://temp.sh/Zq/NovelDB_3_EPUBs.zip")
        XCTAssertEqual(history.all, [result], "the history keeps what the ZIP holds")
        if let body = StubURLProtocol.seen.first?.body {
            XCTAssertNotNil(String(decoding: body, as: UTF8.self).range(of: #"filename="NovelDB_3_EPUBs.zip""#))
        }

        let events = box.all
        XCTAssertEqual(events.first, .starting(index: 1, of: 1, name: "NovelDB_3_EPUBs.zip"))
        XCTAssertTrue(events.contains(.preparing(zip: 1, of: 1, name: "NovelDB_3_EPUBs.zip")))
        XCTAssertTrue(events.contains(.host(.trying(host: "temp.sh", position: 1, of: 1))))
        XCTAssertEqual(events.last, .uploaded(result))
        XCTAssertEqual(try leftovers(), [], "the ZIP and the multipart body are deleted")
    }

    func testTheZipsOfABatchAreBuiltOneAtATimeAndNumberedInProgress() async throws {
        let batch = try items(["a.epub", "b.epub", "c.epub", "d.epub"])
        StubURLProtocol.handler = { _ in
            .text("https://temp.sh/\(StubURLProtocol.seen.count)/zip.zip")
        }
        let configured = settings()
        let box = BatchEventBox()
        // Room for two files (data plus headers) but not three.
        let limit = Int64(2 * (3_000 + 1_024) + 100)

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: nil, limitBytes: limit, events: { box.add($0) }
        )

        XCTAssertEqual(outcome.uploads.map(\.fileName), ["NovelDB_2_EPUBs.zip", "NovelDB_2_EPUBs_2.zip"])
        XCTAssertEqual(Set(outcome.uploads.map(\.link)).count, 2, "one link per ZIP")
        let events = box.all
        XCTAssertTrue(events.contains(.preparing(zip: 1, of: 2, name: "NovelDB_2_EPUBs.zip")))
        XCTAssertTrue(events.contains(.preparing(zip: 2, of: 2, name: "NovelDB_2_EPUBs_2.zip")))
        XCTAssertEqual(try leftovers(), [])
    }

    func testALoneFileKeepsItsOwnNameAndMissesTheZip() async throws {
        let batch = try items(["소설 하나.epub", "소설 둘.epub", "소설 셋.epub"])
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/file") }
        let configured = settings()
        // Room for two of the three files: a ZIP of two and one file on its own.
        let limit = Int64(2 * (3_000 + 1_024) + 100)

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: nil, limitBytes: limit, events: nil
        )

        XCTAssertEqual(outcome.uploads.map(\.fileName), ["NovelDB_2_EPUBs.zip", "소설 셋.epub"])
        XCTAssertNil(outcome.uploads[1].contents, "a lone file is not a ZIP")
        XCTAssertEqual(outcome.uploads[1].summary, "소설 셋.epub")
        // The name a host sees for the lone Korean file: <source>_<book id>.
        var named = context(settings: configured)
        named.nameID = batch[2].nameID
        XCTAssertEqual(named.hostFileName(for: batch[2].file), "npia_5.epub")
        if StubURLProtocol.seen.count == 2, let body = StubURLProtocol.seen.last?.body {
            XCTAssertNotNil(String(decoding: body, as: UTF8.self).range(of: #"filename="npia_5.epub""#))
        }
    }

    func testBundlingOffUploadsEachFileOnItsOwn() async throws {
        let batch = try items(["a.epub", "b.epub", "c.epub"])
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/\(UUID().uuidString)") }
        let configured = settings(zip: false)

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: nil, events: nil
        )

        XCTAssertEqual(StubURLProtocol.seen.count, 3)
        XCTAssertEqual(outcome.uploads.map(\.fileName), ["a.epub", "b.epub", "c.epub"])
        XCTAssertTrue(outcome.uploads.allSatisfy { $0.contents == nil })
    }

    func testASingleFileIsUploadedAsItIs() async throws {
        let batch = try items(["only.epub"])
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/only.epub") }
        let configured = settings()

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: nil, events: nil
        )

        XCTAssertEqual(outcome.uploads.map(\.fileName), ["only.epub"])
        XCTAssertNil(outcome.uploads.first?.contents)
    }

    func testOneFailedZipDoesNotStopTheOthers() async throws {
        let batch = try items(["a.epub", "b.epub", "c.epub", "d.epub"])
        StubURLProtocol.handler = { _ in
            StubURLProtocol.seen.count == 1 ? .text("busy", status: 503) : .text("https://temp.sh/y/second.zip")
        }
        let configured = settings()
        let box = BatchEventBox()
        let limit = Int64(2 * (3_000 + 1_024) + 100)

        let outcome = try await UploaderCatalog.uploadBatch(
            batch, settings: configured, context: context(settings: configured), history: nil, limitBytes: limit, events: { box.add($0) }
        )

        XCTAssertEqual(outcome.uploads.count, 1)
        XCTAssertEqual(outcome.uploads.first?.fileName, "NovelDB_2_EPUBs_2.zip")
        XCTAssertEqual(outcome.failures.count, 1)
        XCTAssertEqual(outcome.failures.first?.name, "NovelDB_2_EPUBs.zip")
        XCTAssertTrue(outcome.failures.first?.message.contains("503") == true)
        XCTAssertTrue(box.all.contains { if case .failed(let name, _) = $0 { return name == "NovelDB_2_EPUBs.zip" } else { return false } })
        XCTAssertEqual(try leftovers(), [], "a ZIP is deleted even when its upload failed")
    }

    func testTheHostLimitSizesTheZips() async throws {
        // Two sparse 60 MB files: one ZIP for a 1 GB host, two uploads for a 100 MB host.
        func sparse(_ name: String) throws -> UploadItem {
            let url = directory.appendingPathComponent(name)
            XCTAssertTrue(FileManager.default.createFile(atPath: url.path, contents: nil))
            let handle = try FileHandle(forWritingTo: url)
            try handle.truncate(atOffset: 60 * 1_048_576)
            try handle.close()
            return UploadItem(file: url)
        }
        let batch = [try sparse("a.epub"), try sparse("b.epub")]
        var configured = settings(hosts: ["tmpfiles"])
        XCTAssertEqual(UploaderCatalog.planBatch(batch, settings: configured, context: context(settings: configured)).count, 2)
        configured.hosts = ["krakenfiles"]
        XCTAssertEqual(UploaderCatalog.planBatch(batch, settings: configured, context: context(settings: configured)).map(\.zipName), ["NovelDB_2_EPUBs.zip"])
    }

    func testCancellingStopsTheBatchAndRemovesTheZip() async throws {
        let batch = try items(["a.epub", "b.epub"])
        StubURLProtocol.handler = { _ in throw URLError(.cancelled) }
        let configured = settings()
        do {
            _ = try await UploaderCatalog.uploadBatch(
                batch, settings: configured, context: context(settings: configured), history: nil, events: nil
            )
            XCTFail("expected cancellation")
        } catch is CancellationError {
            // expected
        }
        XCTAssertEqual(try leftovers(), [])
    }
}
