import XCTest
@testable import NpiaCore

final class LibraryUpdatesTests: XCTestCase {
    func testRangeDefaultsAndExplicitChoiceSurviveRoundTrip() throws {
        var state = LibraryUpdateState()
        state.books["full.epub"] = LibraryUpdateEntry(url: "https://example.test/1", ranged: false)
        state.books["range.epub"] = LibraryUpdateEntry(url: "https://example.test/2", ranged: true)
        XCTAssertTrue(state.books["full.epub"]!.enabled)
        XCTAssertFalse(state.books["range.epub"]!.enabled)
        XCTAssertFalse(LibraryUpdateEntry(url: "https://example.test/old").enabled)
        state.books["range.epub"]?.autoUpdate = true
        state.books["full.epub"]?.autoUpdate = false
        state.enabled = false
        state.background = true
        state.uploadDrive = true
        let saved = try JSONDecoder().decode(LibraryUpdateState.self, from: JSONEncoder().encode(state))
        XCTAssertEqual(saved, state)
        XCTAssertFalse(saved.enabled)
        XCTAssertTrue(saved.books["range.epub"]!.enabled)
        XCTAssertFalse(saved.books["full.epub"]!.enabled)
    }

    func testSourceAliasesShareTheirQueue() {
        XCTAssertEqual(librarySourceKey(URL(string: "https://www.example.test/book/1")!),
                       librarySourceKey(URL(string: "https://011.example.test/book/2")!))
        XCTAssertNotEqual(librarySourceKey(URL(string: "https://other.test/1")!),
                          librarySourceKey(URL(string: "https://example.test/1")!))
    }
}
