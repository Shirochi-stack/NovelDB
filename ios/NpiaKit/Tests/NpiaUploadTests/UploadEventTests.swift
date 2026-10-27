import XCTest
import NpiaCore
@testable import NpiaUpload

private final class EventBox: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [UploadEvent] = []

    func add(_ event: UploadEvent) {
        lock.lock(); values.append(event); lock.unlock()
    }

    var all: [UploadEvent] {
        lock.lock(); defer { lock.unlock() }
        return values
    }
}

final class UploadEventTests: UploadTestCase {
    private func settings(_ hosts: [String]) -> UploadSettings {
        var settings = UploadSettings()
        settings.hosts = hosts
        return settings
    }

    func testEventsNameTheHostBeingTriedAndEachFallback() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { request in
            switch request.url?.host {
            case "temp.sh": return .text("busy", status: 503)
            case "upload.gofile.io": return .json(#"{"status":"ok","data":{"downloadPage":"https://gofile.io/d/AbC123"}}"#)
            default: throw URLError(.cannotFindHost)
            }
        }
        let box = EventBox()
        let configured = settings(["tempsh", "gofile"])
        let result = try await UploaderCatalog.upload(
            file, settings: configured, context: context(settings: configured), history: nil, events: { box.add($0) }
        )
        XCTAssertEqual(result.hostID, "gofile")

        let events = box.all
        XCTAssertEqual(events.first, .trying(host: "temp.sh", position: 1, of: 2))
        let busy = UploadError.badResponse(host: "temp.sh", status: 503, body: "busy").localizedDescription
        let failedIndex = try XCTUnwrap(events.firstIndex(of: .failed(host: "temp.sh", message: busy)))
        let secondIndex = try XCTUnwrap(events.firstIndex(of: .trying(host: "Gofile", position: 2, of: 2)))
        XCTAssertLessThan(failedIndex, secondIndex, "the failure is reported before the next host starts")

        let progressHosts = events.compactMap { event -> String? in
            if case .progress(let host, _) = event { return host }
            return nil
        }
        XCTAssertEqual(progressHosts.last, "Gofile")
        if case .progress(_, let last)? = events.last {
            XCTAssertEqual(last.sentBytes, last.totalBytes)
            XCTAssertGreaterThanOrEqual(last.totalBytes, 1_000)
        } else {
            XCTFail("the last event is the final progress report: \(String(describing: events.last))")
        }
    }

    func testHostThatCannotBeUsedIsReportedAsFailed() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/book.epub") }
        let box = EventBox()
        let configured = settings(["pixeldrain", "tempsh"])
        let result = try await UploaderCatalog.upload(
            file, settings: configured, context: context(settings: configured), history: nil, events: { box.add($0) }
        )
        XCTAssertEqual(result.hostID, "tempsh")
        let events = box.all
        XCTAssertEqual(events.first, .failed(host: "Pixeldrain", message: UploadError.notSignedIn("Pixeldrain").localizedDescription))
        XCTAssertTrue(events.contains(.trying(host: "temp.sh", position: 2, of: 2)))
    }

    func testProgressCallbackStillWorksAlongsideEvents() async throws {
        let file = try makeFile()
        StubURLProtocol.handler = { _ in .text("https://temp.sh/x/book.epub") }
        let progress = ProgressBox()
        let box = EventBox()
        let configured = settings(["tempsh"])
        _ = try await UploaderCatalog.upload(
            file, settings: configured, context: context(settings: configured), history: nil,
            events: { box.add($0) }, progress: { progress.add($0) }
        )
        XCTAssertEqual(progress.last?.sentBytes, progress.last?.totalBytes)
        XCTAssertFalse(box.all.isEmpty)
    }
}
