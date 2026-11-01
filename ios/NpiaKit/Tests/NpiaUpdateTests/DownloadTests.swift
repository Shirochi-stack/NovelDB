import XCTest
@testable import NpiaUpdate

/// Collects the progress callbacks, which arrive on the download's own queue.
private final class ProgressLog: @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [(received: Int64, total: Int64)] = []

    func add(_ received: Int64, _ total: Int64) {
        lock.lock(); entries.append((received, total)); lock.unlock()
    }

    var all: [(received: Int64, total: Int64)] {
        lock.lock(); defer { lock.unlock() }
        return entries
    }
}

final class DownloadTests: UpdateTestCase {
    private let wrongDigest = String(repeating: "0", count: 64)

    /// Runs the download and returns what it threw (nil when it succeeded).
    private func failure(of release: FakeRelease, entry: [String: Any] = [:], serving reply: StubURLProtocol.Reply) async throws -> Error? {
        let update = try offer(release, entry: entry)
        StubURLProtocol.handler = { _ in reply }
        do {
            _ = try await makeChecker().download(update)
            return nil
        } catch {
            return error
        }
    }

    func testAMatchingDownloadKeepsItsNameAndReportsProgress() async throws {
        let release = FakeRelease(bytes: 5_000)
        let update = try offer(release)
        StubURLProtocol.handler = { _ in .file(release.payload, chunkSize: 1_000) }
        // Older downloads are cleared first.
        try FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
        try Data("stale".utf8).write(to: downloads.appendingPathComponent("NovelDB-old.ipa"))

        let log = ProgressLog()
        let file = try await makeChecker().download(update) { received, total in log.add(received, total) }

        XCTAssertEqual(file.lastPathComponent, "NovelDB-unsigned.ipa")
        XCTAssertEqual(try Data(contentsOf: file), release.payload)
        XCTAssertEqual(leftovers, ["NovelDB-unsigned.ipa"], "no partial file, and the stale download is gone")
        XCTAssertEqual(StubURLProtocol.seen.first?.url, update.downloadURL)
        XCTAssertEqual(StubURLProtocol.seen.first?.value(forHTTPHeaderField: "User-Agent"), "NovelDB")

        let progress = log.all
        XCTAssertFalse(progress.isEmpty)
        XCTAssertEqual(progress.last?.received, 5_000)
        XCTAssertTrue(progress.allSatisfy { $0.total == 5_000 })
        let seen = progress.map { $0.received }
        XCTAssertEqual(seen, seen.sorted(), "progress never goes back")
    }

    func testAWrongChecksumDeletesTheFile() async throws {
        let release = FakeRelease()
        let error = try await failure(of: release, entry: ["sha256": wrongDigest], serving: .file(release.payload))
        XCTAssertEqual(error as? AppUpdateError, .checksumMismatch)
        XCTAssertEqual(leftovers, [])
    }

    func testChangedContentOfTheRightSizeDeletesTheFile() async throws {
        let release = FakeRelease()
        var tampered = release.payload
        tampered[100] ^= 0xFF
        let error = try await failure(of: release, serving: .file(tampered))
        XCTAssertEqual(error as? AppUpdateError, .checksumMismatch)
        XCTAssertEqual(leftovers, [])
    }

    func testAWrongSizeDeletesTheFileEvenWhenTheChecksumMatches() async throws {
        let release = FakeRelease()
        let error = try await failure(of: release, entry: ["size": release.payload.count + 1], serving: .file(release.payload))
        XCTAssertEqual(error as? AppUpdateError, .checksumMismatch)
        XCTAssertEqual(leftovers, [])
    }

    func testAShortDownloadDeletesTheFile() async throws {
        let release = FakeRelease(bytes: 5_000)
        let error = try await failure(of: release, serving: .file(release.payload.prefix(4_000)))
        XCTAssertEqual(error as? AppUpdateError, .checksumMismatch)
        XCTAssertEqual(leftovers, [])
    }

    func testALongDownloadIsStoppedAndDeleted() async throws {
        let release = FakeRelease(bytes: 5_000)
        let longer = release.payload + Data(repeating: 1, count: 3_000)
        let error = try await failure(of: release, serving: .file(longer, chunkSize: 1_000))
        XCTAssertEqual(error as? AppUpdateError, .checksumMismatch)
        XCTAssertEqual(leftovers, [])
    }

    func testAnHTTPErrorLeavesNothingBehind() async throws {
        let release = FakeRelease()
        for status in [404, 500] {
            let error = try await failure(of: release, serving: .file(Data("Not found".utf8), status: status))
            XCTAssertEqual(error as? AppUpdateError, .http(status))
            XCTAssertEqual(leftovers, [])
        }
    }

    func testANetworkErrorLeavesNothingBehind() async throws {
        let release = FakeRelease()
        let update = try offer(release)
        StubURLProtocol.handler = { _ in throw URLError(.networkConnectionLost) }
        do {
            _ = try await makeChecker().download(update)
            XCTFail("the download should have failed")
        } catch let error as AppUpdateError {
            guard case .network = error else { return XCTFail("expected a network error, got \(error)") }
        }
        XCTAssertEqual(leftovers, [])
    }

    func testCancellingStopsTheTransferAndDeletesThePartialFile() async throws {
        let release = FakeRelease(bytes: 20_000)
        let update = try offer(release)
        // 20 pieces, 0.3 s apart: far longer than the test waits.
        StubURLProtocol.handler = { _ in .file(release.payload, chunkSize: 1_000, pause: 0.3) }
        let checker = makeChecker()
        let started = expectation(description: "the first bytes arrived")
        started.assertForOverFulfill = false

        let task = Task { try await checker.download(update) { _, _ in started.fulfill() } }
        await fulfillment(of: [started], timeout: 15)
        task.cancel()

        do {
            _ = try await task.value
            XCTFail("a cancelled download should not finish")
        } catch is CancellationError {
            // expected
        } catch {
            XCTFail("expected CancellationError, got \(error)")
        }
        XCTAssertEqual(leftovers, [])
    }

    func testTheErrorTextsAreThePlainOnes() {
        XCTAssertEqual(
            AppUpdateError.checksumMismatch.localizedDescription,
            "The download did not match its checksum; nothing was installed."
        )
        XCTAssertEqual(AppUpdateError.http(404).localizedDescription, "The download failed: the server answered HTTP 404.")
    }

    func testDiscardingDownloadsRemovesTheFolder() async throws {
        let release = FakeRelease()
        let update = try offer(release)
        StubURLProtocol.handler = { _ in .file(release.payload) }
        let checker = makeChecker()
        _ = try await checker.download(update)
        XCTAssertEqual(leftovers, ["NovelDB-unsigned.ipa"])
        await checker.discardDownloads()
        XCTAssertFalse(FileManager.default.fileExists(atPath: downloads.path))
    }
}
