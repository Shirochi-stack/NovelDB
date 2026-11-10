import XCTest
import NpiaCore
@testable import NpiaOutput

final class LibraryUpdateTests: XCTestCase {
    func testReplacementPreservesOldBookUntilACompleteCandidateExists() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let target = try await EPUBWriter().write(makeWriterBundle(), baseName: "saved-name", to: directory)
        let original = try Data(contentsOf: target)
        var shorter = makeWriterBundle()
        shorter.chapters = Array(shorter.chapters.prefix(1))
        let incomplete = try await EPUBWriter().write(shorter, baseName: "incomplete", to: directory)
        XCTAssertThrowsError(try LibraryEpub.replace(incomplete, target: target))
        XCTAssertEqual(try Data(contentsOf: target), original)
        var longer = makeWriterBundle()
        longer.chapters.append(ChapterContent(ref: ChapterRef(id: "new", index: 3, title: "New chapter"), html: "<p>New text</p>"))
        let candidate = try await EPUBWriter().write(longer, baseName: "new-title", to: directory)
        try LibraryEpub.replace(candidate, target: target)
        XCTAssertNotEqual(try Data(contentsOf: target), original)
        XCTAssertFalse(FileManager.default.fileExists(atPath: candidate.path))
        XCTAssertEqual(target.lastPathComponent, "saved-name.epub")
    }
}
